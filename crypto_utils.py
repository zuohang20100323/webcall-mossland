# crypto_utils.py — 数据加密/解密模块（AES-256-GCM）
#
# 所有存到 GitHub 的敏感数据都经过此模块加密。
# 密钥从环境变量 DATA_ENCRYPTION_KEY 读取（base64 编码的 32 字节密钥）。
# 如果环境变量未设置，自动生成一个并打印到日志（仅首次部署时）。

import os
import base64
import json
import logging

logger = logging.getLogger(__name__)

_KEY: bytes | None = None

# 需要加密的文件列表（GitHub 路径）
ENCRYPTED_FILES = {
    "users.json",
    "account_tokens.json",
    "api_config.json",
    "keys.json",
    "cards.json",
    "redeem.json",
    "usage.json",
    "feedbacks.json",
}

# call_logs/ 和 user_settings/ 下的 JSON 也需要加密
ENCRYPTED_DIRS = {"call_logs", "user_settings"}


def _get_key() -> bytes:
    """获取加密密钥"""
    global _KEY
    if _KEY is not None:
        return _KEY

    try:
        from config_store import get_with_env
        key_b64 = get_with_env("DATA_ENCRYPTION_KEY", "data", "encryption_key", default="")
    except Exception:
        key_b64 = os.environ.get("DATA_ENCRYPTION_KEY", "")
    if key_b64:
        try:
            _KEY = base64.b64decode(key_b64)
            if len(_KEY) != 32:
                raise ValueError(f"密钥长度必须为 32 字节，当前 {len(_KEY)}")
            logger.info("加密密钥已从环境变量加载")
            return _KEY
        except Exception as e:
            logger.error("解析 DATA_ENCRYPTION_KEY 失败: %s", e)
            raise

    # 未设置密钥 — 生成一个新的
    _KEY = os.urandom(32)
    new_key_b64 = base64.b64encode(_KEY).decode("ascii")
    logger.warning(
        "⚠️ DATA_ENCRYPTION_KEY 未设置！已自动生成新密钥。\n"
        "请将以下密钥添加到 HF Space 环境变量中：\n"
        "DATA_ENCRYPTION_KEY=%s\n"
        "⚠️ 丢失此密钥将无法解密已加密的数据！",
        new_key_b64,
    )
    return _KEY


def _should_encrypt(filepath: str) -> bool:
    """判断某个文件路径是否需要加密"""
    # 顶层敏感文件
    if filepath in ENCRYPTED_FILES:
        return True
    # 目录下的文件
    parts = filepath.split("/")
    if len(parts) >= 2 and parts[0] in ENCRYPTED_DIRS:
        # 只加密 JSON 文件
        if filepath.endswith(".json"):
            return True
    return False


def encrypt_data(plaintext: str, filepath: str = "") -> str:
    """
    加密字符串数据。
    返回 base64 编码的密文（格式：ENC1:<base64(nonce + ciphertext + tag)>）
    如果文件不在加密列表中，原样返回。
    """
    if filepath and not _should_encrypt(filepath):
        return plaintext

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_key()
    nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # nonce (12) + ciphertext + tag (16, appended by AESGCM)
    raw = nonce + ciphertext
    return "ENC1:" + base64.b64encode(raw).decode("ascii")


def decrypt_data(data: str, filepath: str = "") -> str:
    """
    解密数据。
    如果数据以 ENC1: 开头，解密并返回明文。
    否则原样返回（兼容未加密的旧数据）。
    """
    if not data or not data.startswith("ENC1:"):
        # 未加密的数据，原样返回（向后兼容）
        return data

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_key()
    try:
        raw = base64.b64decode(data[5:])  # 去掉 "ENC1:" 前缀
        nonce = raw[:12]
        ciphertext = raw[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
    except Exception as e:
        logger.error("解密失败 (filepath=%s): %s", filepath, e)
        # 如果解密失败，可能是旧的未加密数据被误判
        # 尝试当作明文返回
        if filepath:
            logger.warning("尝试将数据当作未加密明文处理")
        return data


def encrypt_binary(data: bytes) -> bytes:
    """加密二进制数据（如音频文件），返回加密后的字节"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    # 前缀 "ENC1" (4 bytes) + nonce (12) + ciphertext
    return b"ENC1" + nonce + ciphertext


def decrypt_binary(data: bytes) -> bytes:
    """解密二进制数据，如果不是加密数据则原样返回"""
    if not data or not data[:4] == b"ENC1":
        return data

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_key()
    try:
        nonce = data[4:16]
        ciphertext = data[16:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        logger.error("二进制解密失败: %s", e)
        return data  # fallback
