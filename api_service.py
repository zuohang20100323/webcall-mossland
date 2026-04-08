# api_service.py — TTS API 服务（用户系统 + 余额 + 卡密 + 计费）
# 数据持久化：本地 JSON + GitHub 私有仓库同步

import os
import time
import hashlib
import hmac
import os


def _hash_password(password: str, salt: bytes = None) -> str:
    """PBKDF2-SHA256 密码哈希，返回 'pbkdf2$<hex_salt>$<hex_hash>'"""
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """验证密码。兼容旧的 sha256 裸哈希和新的 PBKDF2 格式"""
    if stored_hash.startswith("pbkdf2$"):
        parts = stored_hash.split("$")
        if len(parts) != 3:
            return False
        salt = bytes.fromhex(parts[1])
        expected = parts[2]
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(dk.hex(), expected)
    else:
        # 旧格式：sha256 裸哈希（向后兼容）
        return hmac.compare_digest(
            hashlib.sha256(password.encode()).hexdigest(), stored_hash
        )
import threading
import logging
import secrets
import string
import base64

logger = logging.getLogger(__name__)

# ── 数据存储（内存 + JSON 持久化 + GitHub 同步）──
# HF Spaces 持久化目录，重启不丢
try:
    from config_store import get_with_env
    _DATA_DIR = get_with_env("DATA_DIR", "data", "dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
except Exception:
    _DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
_USERS_FILE = os.path.join(_DATA_DIR, "users.json")
_CARDS_FILE = os.path.join(_DATA_DIR, "cards.json")
_USAGE_FILE = os.path.join(_DATA_DIR, "usage.json")

# GitHub 同步配置
try:
    from config_store import get_with_env
    _GITHUB_TOKEN = get_with_env("GITHUB_TOKEN", "github", "token", default="")
    _GITHUB_REPO = get_with_env("GITHUB_DATA_REPO", "github", "data_repo", default="")
except Exception:
    _GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    _GITHUB_REPO = os.environ.get("GITHUB_DATA_REPO", "")
_GITHUB_API = f"https://api.github.com/repos/{_GITHUB_REPO}/contents"

_lock = threading.Lock()

# 用户: { api_key: { nickname, balance_li, created, total_chars, total_calls } }
_users: dict = {}
# 卡密: { card_code: { amount_li, created, used, used_by, used_at } }
_cards: dict = {}
# 使用记录: [ { api_key, chars, cost_li, model, time } ]  (最近1000条)
_usage: list = []

# MiniMax 价格 (厘/万字符, 1元=1000厘)
PRICE_MAP = {
    "speech-2.8-hd": 3500,      # 3.5 元/万字符
    "speech-2.6-hd": 3500,
    "speech-02-hd": 3500,
    "speech-2.8-turbo": 2000,   # 2 元/万字符
    "speech-2.6-turbo": 2000,
    "speech-02-turbo": 2000,
    "speech-02": 2000,
}
DEFAULT_PRICE = 3500  # 默认按 hd 计费


def _ensure_data_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def _github_headers():
    return {
        "Authorization": f"token {_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def _github_get_file(filename):
    """从 GitHub 获取文件内容，返回 (content_str, sha) 或 (None, None)"""
    if not _GITHUB_TOKEN:
        return None, None
    import requests
    try:
        resp = requests.get(f"{_GITHUB_API}/{filename}", headers=_github_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
        return None, None
    except Exception as e:
        logger.error("GitHub 读取 %s 失败: %s", filename, e)
        return None, None


def _github_put_file(filename, content_str, sha=None):
    """写文件到 GitHub，sha 为 None 表示新建"""
    if not _GITHUB_TOKEN:
        return
    import requests
    try:
        body = {
            "message": f"sync {filename}",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        resp = requests.put(f"{_GITHUB_API}/{filename}", headers=_github_headers(),
                           json=body, timeout=15)
        if resp.status_code in (200, 201):
            logger.info("GitHub 同步 %s 成功", filename)
        else:
            logger.error("GitHub 同步 %s 失败: %d %s", filename, resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("GitHub 同步 %s 异常: %s", filename, e)


# GitHub SHA 缓存（用于更新时传 sha）
_github_shas: dict = {}


def _save_to_github(filename, data_dict_or_list):
    """异步保存到 GitHub"""
    import json
    content = json.dumps(data_dict_or_list, ensure_ascii=False, indent=2)
    sha = _github_shas.get(filename)

    def _do():
        _github_put_file(filename, content, sha)
        # 更新 sha
        _, new_sha = _github_get_file(filename)
        if new_sha:
            _github_shas[filename] = new_sha

    threading.Thread(target=_do, daemon=True).start()


def _save_users():
    _ensure_data_dir()
    import json
    try:
        with open(_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(_users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存用户数据失败: %s", e)
    _save_to_github("users.json", _users)


def _save_cards():
    _ensure_data_dir()
    import json
    try:
        with open(_CARDS_FILE, "w", encoding="utf-8") as f:
            json.dump(_cards, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存卡密数据失败: %s", e)
    _save_to_github("cards.json", _cards)


def _save_usage():
    _ensure_data_dir()
    import json
    try:
        with open(_USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(_usage[-1000:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存使用记录失败: %s", e)
    _save_to_github("usage.json", _usage[-1000:])


def _migrate_cents_to_li():
    """迁移旧数据：balance_cents → balance_li, amount_cents → amount_li, cost_cents → cost_li"""
    migrated = False
    # 用户数据
    for info in _users.values():
        if "balance_cents" in info and "balance_li" not in info:
            info["balance_li"] = info.pop("balance_cents") * 10  # 1分 = 10厘
            migrated = True
        elif "balance_cents" in info:
            info.pop("balance_cents", None)
    # 卡密数据
    for info in _cards.values():
        if "amount_cents" in info and "amount_li" not in info:
            info["amount_li"] = info.pop("amount_cents") * 10
            migrated = True
        elif "amount_cents" in info:
            info.pop("amount_cents", None)
    # 使用记录
    for record in _usage:
        if "cost_cents" in record and "cost_li" not in record:
            record["cost_li"] = record.pop("cost_cents") * 10
            migrated = True
        elif "cost_cents" in record:
            record.pop("cost_cents", None)
    if migrated:
        logger.info("已迁移旧数据字段 (cents → li)")
        _save_users()
        _save_cards()


def _load_data():
    """启动时从 GitHub 加载数据（优先），本地文件作为降级"""
    import json
    global _users, _cards, _usage
    _ensure_data_dir()

    # 尝试从 GitHub 加载
    for filename, local_path, target_name in [
        ("users.json", _USERS_FILE, "_users"),
        ("cards.json", _CARDS_FILE, "_cards"),
        ("usage.json", _USAGE_FILE, "_usage"),
    ]:
        loaded = False
        content, sha = _github_get_file(filename)
        if content:
            try:
                data = json.loads(content)
                if target_name == "_users":
                    _users = data
                elif target_name == "_cards":
                    _cards = data
                elif target_name == "_usage":
                    _usage = data
                _github_shas[filename] = sha
                loaded = True
                logger.info("从 GitHub 加载 %s 成功 (%d 条)", filename,
                           len(data) if isinstance(data, (dict, list)) else 0)
                # 同时写到本地
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                logger.error("解析 GitHub %s 失败: %s", filename, e)

        # 降级到本地文件
        if not loaded:
            try:
                if os.path.exists(local_path):
                    with open(local_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if target_name == "_users":
                        _users = data
                    elif target_name == "_cards":
                        _cards = data
                    elif target_name == "_usage":
                        _usage = data
                    logger.info("从本地加载 %s 成功", filename)
            except Exception as e:
                logger.error("加载本地 %s 失败: %s", filename, e)

    # 兼容旧数据迁移
    _migrate_cents_to_li()


# 启动时加载
_load_data()


def _gen_api_key():
    """生成 API Key: tts-开头 + 32位随机"""
    alphabet = string.ascii_letters + string.digits
    return "tts-" + "".join(secrets.choice(alphabet) for _ in range(32))


def _gen_card_code(length=12):
    """生成卡密: 大写字母+数字"""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _calc_cost(text: str, model: str) -> tuple:
    """计算费用，返回 (字符数, 费用厘)"""
    char_count = len(text)
    price_per_10k = PRICE_MAP.get(model, DEFAULT_PRICE)
    # 真实计算，四舍五入，最少1厘
    cost_li = max(1, round(char_count * price_per_10k / 10000))
    return char_count, cost_li


# =====================================================================
# 用户管理
# =====================================================================

def register_user(nickname: str, password: str = "") -> dict:
    """注册新用户，返回 { api_key, nickname }"""
    if not password or len(password) < 6:
        return {"error": "密码至少6位"}
    with _lock:
        # 检查昵称是否已存在
        for info in _users.values():
            if info["nickname"] == nickname:
                return {"error": "昵称已被使用"}
        api_key = _gen_api_key()
        while api_key in _users:
            api_key = _gen_api_key()
        pw_hash = _hash_password(password)
        _users[api_key] = {
            "nickname": nickname,
            "password_hash": pw_hash,
            "balance_li": 0,
            "created": int(time.time()),
            "total_chars": 0,
            "total_calls": 0,
        }
        _save_users()
    return {"api_key": api_key, "nickname": nickname}


def login_user(nickname: str, password: str) -> dict:
    """用昵称+密码登录，返回 { api_key, nickname }"""
    for api_key, info in _users.items():
        if info["nickname"] == nickname:
            stored_hash = info.get("password_hash", "")
            if _verify_password(password, stored_hash):
                # 如果是旧格式 sha256，自动升级为 PBKDF2
                if not stored_hash.startswith("pbkdf2$"):
                    info["password_hash"] = _hash_password(password)
                    _save_users()
                    logger.info("用户 %s 密码哈希已升级为 PBKDF2", nickname)
                return {"api_key": api_key, "nickname": nickname}
            else:
                return {"error": "昵称或密码错误"}
    return {"error": "昵称或密码错误"}


def get_user(api_key: str) -> dict | None:
    """获取用户信息"""
    return _users.get(api_key)


def get_user_balance(api_key: str) -> dict | None:
    """获取用户余额和统计"""
    user = _users.get(api_key)
    if not user:
        return None
    return {
        "nickname": user["nickname"],
        "balance_li": user["balance_li"],
        "balance_yuan": round(user["balance_li"] / 1000, 3),
        "total_chars": user["total_chars"],
        "total_calls": user["total_calls"],
    }


# =====================================================================
# 卡密管理
# =====================================================================

def generate_cards(count: int, amount_li: int) -> list:
    """批量生成卡密，amount_li 为面额（厘）"""
    cards = []
    with _lock:
        for _ in range(count):
            code = _gen_card_code()
            while code in _cards:
                code = _gen_card_code()
            _cards[code] = {
                "amount_li": amount_li,
                "created": int(time.time()),
                "used": False,
                "used_by": None,
                "used_at": None,
            }
            cards.append({"code": code, "amount_yuan": round(amount_li / 1000, 3)})
        _save_cards()
    return cards


def redeem_card(api_key: str, card_code: str) -> dict:
    """兑换卡密，返回 { ok, amount_yuan, balance_yuan } 或 { error }"""
    card_code = card_code.strip().upper()
    with _lock:
        if card_code not in _cards:
            return {"error": "卡密无效"}
        card = _cards[card_code]
        if card["used"]:
            return {"error": "该卡密已被使用"}
        user = _users.get(api_key)
        if not user:
            return {"error": "用户不存在"}

        # 充值
        amount = card["amount_li"]
        user["balance_li"] += amount
        card["used"] = True
        card["used_by"] = api_key
        card["used_at"] = int(time.time())
        _save_users()
        _save_cards()

    return {
        "ok": True,
        "amount_yuan": round(amount / 1000, 3),
        "balance_yuan": round(user["balance_li"] / 1000, 3),
    }


def list_cards(only_unused=False) -> list:
    """列出所有卡密"""
    result = []
    for code, info in _cards.items():
        if only_unused and info["used"]:
            continue
        result.append({
            "code": code,
            "amount_yuan": round(info["amount_li"] / 1000, 3),
            "used": info["used"],
            "used_by": info.get("used_by"),
            "used_at": info.get("used_at"),
        })
    return result


# =====================================================================
# TTS 调用 + 计费
# =====================================================================

def call_tts(api_key: str, text: str, model: str = "speech-2.8-hd",
             voice_id: str = None) -> dict:
    """
    调用 TTS 并扣费。
    成功返回 { audio_bytes, chars, cost_yuan }
    失败返回 { error }
    """
    from tts import synthesize as tts_synthesize
    from llm import get_tts_defaults

    user = _users.get(api_key)
    if not user:
        return {"error": "API Key 无效"}

    if not text or not text.strip():
        return {"error": "文本不能为空"}

    # 计算费用
    char_count, cost_li = _calc_cost(text, model)

    # 检查余额
    if user["balance_li"] < cost_li:
        return {
            "error": f"余额不足（需要 {round(cost_li/1000, 3)} 元，当前余额 {round(user['balance_li']/1000, 3)} 元）"
        }

    # 获取内置 TTS 配置
    defaults = get_tts_defaults()
    tts_api_key = defaults.get("api_key")
    tts_group_id = defaults.get("group_id")
    if not voice_id:
        voice_id = defaults.get("voice_id")

    if not tts_api_key or not tts_group_id:
        return {"error": "TTS 服务未配置"}

    # 调用 TTS
    try:
        audio_bytes = tts_synthesize(
            text=text,
            api_key=tts_api_key,
            voice_id=voice_id,
            group_id=tts_group_id,
            model=model,
        )
    except Exception as e:
        return {"error": f"TTS 合成失败: {str(e)}"}

    if audio_bytes is None:
        return {"error": "文本为纯标点或空白"}

    # 扣费
    with _lock:
        user["balance_li"] -= cost_li
        user["total_chars"] += char_count
        user["total_calls"] += 1
        _usage.append({
            "api_key": api_key[:10] + "...",
            "nickname": user["nickname"],
            "chars": char_count,
            "cost_li": cost_li,
            "model": model,
            "time": int(time.time()),
        })
        if len(_usage) > 1000:
            _usage[:] = _usage[-1000:]
        _save_users()
        _save_usage()

    return {
        "audio_bytes": audio_bytes,
        "chars": char_count,
        "cost_yuan": round(cost_li / 1000, 4),
        "balance_yuan": round(user["balance_li"] / 1000, 3),
    }


# =====================================================================
# 管理接口
# =====================================================================

def admin_list_users() -> list:
    """列出所有用户"""
    result = []
    for api_key, info in _users.items():
        result.append({
            "api_key": api_key[:10] + "..." + api_key[-4:],
            "api_key_full": api_key,
            "nickname": info["nickname"],
            "balance_yuan": round(info["balance_li"] / 1000, 3),
            "total_chars": info["total_chars"],
            "total_calls": info["total_calls"],
            "created": info["created"],
        })
    return result


def admin_adjust_balance(api_key: str, amount_li: int) -> dict:
    """手动调整用户余额（正数加，负数减），单位厘"""
    with _lock:
        user = _users.get(api_key)
        if not user:
            return {"error": "用户不存在"}
        user["balance_li"] += amount_li
        if user["balance_li"] < 0:
            user["balance_li"] = 0
        _save_users()
    return {"ok": True, "balance_yuan": round(user["balance_li"] / 1000, 3)}


def admin_delete_user(api_key: str) -> dict:
    """删除用户"""
    with _lock:
        if api_key in _users:
            del _users[api_key]
            _save_users()
            return {"ok": True}
    return {"error": "用户不存在"}


def admin_get_usage(limit=50) -> list:
    """获取最近使用记录"""
    return list(reversed(_usage[-limit:]))


def get_user_usage(api_key: str, limit=20) -> list:
    """获取指定用户的调用记录"""
    key_prefix = api_key[:10] + "..."
    records = [r for r in _usage if r.get("api_key") == key_prefix]
    return list(reversed(records[-limit:]))


# =====================================================================
# 用户设定（加密存储）
# =====================================================================

def save_user_settings(api_key: str, encrypted: str) -> dict:
    """保存用户设定密文（前端加密后的字符串）"""
    with _lock:
        user = _users.get(api_key)
        if not user:
            return {"error": "用户不存在"}
        user["encrypted_settings"] = encrypted
        _save_users()
    return {"ok": True}


def load_user_settings(api_key: str) -> dict:
    """读取用户设定密文"""
    user = _users.get(api_key)
    if not user:
        return {"error": "用户不存在"}
    return {"ok": True, "encrypted": user.get("encrypted_settings", "")}
