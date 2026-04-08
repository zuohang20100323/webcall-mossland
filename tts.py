# tts.py — MiniMax TTS 语音合成模块

import os
import re
import requests
import logging

logger = logging.getLogger(__name__)

DEFAULT_VOICE_ID = "male-qn-qingse"
DEFAULT_MODEL = "speech-2.8-hd"

# MiniMax speech-2.8 原生支持的语气词标签
_MINIMAX_SUPPORTED_TAGS = {
    'laughs', 'chuckle', 'coughs', 'clear-throat', 'groans', 'breath',
    'pant', 'inhale', 'exhale', 'gasps', 'sniffs', 'sighs', 'snorts',
    'burps', 'lip-smacking', 'humming', 'hissing', 'emm', 'whistles',
    'sneezes', 'crying', 'applause',
}

# AI 可能输出的变体标签 → MiniMax 格式映射
_TAG_MAPPING = {
    'chuckles': 'chuckle',
    'chuckling': 'chuckle',
    'laugh': 'laughs',
    'laughing': 'laughs',
    'sigh': 'sighs',
    'sighing': 'sighs',
    'gasp': 'gasps',
    'gasping': 'gasps',
    'cough': 'coughs',
    'coughing': 'coughs',
    'sniff': 'sniffs',
    'sniffing': 'sniffs',
    'snort': 'snorts',
    'snorting': 'snorts',
    'sneeze': 'sneezes',
    'sneezing': 'sneezes',
    'hmm': 'emm',
    'hm': 'emm',
    'um': 'emm',
    'umm': 'emm',
    'hums': 'humming',
    'hum': 'humming',
    'clears throat': 'clear-throat',
    'clears-throat': 'clear-throat',
    'throat clear': 'clear-throat',
    'giggles': 'chuckle',
    'giggle': 'chuckle',
    'exhale': 'exhale',
    'exhales': 'exhale',
    'inhales': 'inhale',
    'breathing': 'breath',
    'panting': 'pant',
    'pants': 'pant',
    'groaning': 'groans',
    'groan': 'groans',
    'burp': 'burps',
    'burping': 'burps',
    'hissing sound': 'hissing',
    'hiss': 'hissing',
}


def _normalize_voice_tags(text: str) -> str:
    """将 AI 输出的语气标签转换为 MiniMax 支持的格式，不支持的删掉。"""
    def _replace_tag(m):
        tag = m.group(1).strip().lower()
        # 已经是支持的标签
        if tag in _MINIMAX_SUPPORTED_TAGS:
            return f'({tag})'
        # 尝试映射
        mapped = _TAG_MAPPING.get(tag)
        if mapped and mapped in _MINIMAX_SUPPORTED_TAGS:
            return f'({mapped})'
        # 不支持的标签，删掉
        return ''

    result = re.sub(r'\(([a-zA-Z\s\-]+)\)', _replace_tag, text)
    # 合并多余空格
    result = re.sub(r'\s{2,}', ' ', result)
    return result.strip()


def _convert_pause_tags(text: str) -> str:
    """将停顿标签 <#x#> 和 <#x> 转为中文标点（MiniMax 不认这个格式）。"""
    def _pause_to_punct(m):
        seconds = float(m.group(1))
        return '……' if seconds >= 0.5 else '，'
    # 兼容 <#0.5#> 和 <#1> 两种格式
    return re.sub(r'<#([\d.]+)#?>', _pause_to_punct, text)


def synthesize(text, api_key=None, voice_id=None, group_id=None, model=None):
    """
    将文本合成为 MP3 语音。

    参数:
        text:     要合成的文字
        api_key:  MiniMax API Key，默认从环境变量 MINIMAX_API_KEY 读取
        voice_id: 音色 ID，默认从环境变量 MINIMAX_VOICE_ID 读取
        group_id: Group ID，默认从环境变量 MINIMAX_GROUP_ID 读取
        model:    TTS 模型名，默认从环境变量 MINIMAX_TTS_MODEL 读取

    返回:
        mp3 bytes — 合成成功时返回音频二进制数据
        None      — 输入文本为纯标点/空白时返回 None

    异常:
        ValueError  — 参数缺失（如 API Key 未提供）
        Exception   — API 调用失败或返回错误
    """

    # ── 参数处理 ──
    if not text or not isinstance(text, str):
        raise ValueError("text 参数不能为空")

    # 将语气标签转为 MiniMax 支持的格式
    text = _normalize_voice_tags(text)
    # 将停顿标签转为中文标点
    text = _convert_pause_tags(text)

    # 过滤纯标点/空白
    stripped = re.sub(r'[^\w]', '', text, flags=re.UNICODE)
    if not stripped:
        logger.debug("文本为纯标点或空白，跳过合成: %r", text)
        return None

    if api_key is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            api_key = _defaults.get("api_key") or get_with_env("MINIMAX_API_KEY", "tts", "minimax_api_key", default="")
        except Exception:
            api_key = _defaults.get("api_key") or os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise ValueError(
            "未提供 api_key，且环境变量 MINIMAX_API_KEY 未设置"
        )

    if group_id is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            group_id = _defaults.get("group_id") or get_with_env("MINIMAX_GROUP_ID", "tts", "minimax_group_id", default="")
        except Exception:
            group_id = _defaults.get("group_id") or os.environ.get("MINIMAX_GROUP_ID", "")
    if not group_id:
        raise ValueError(
            "未提供 group_id，且环境变量 MINIMAX_GROUP_ID 未设置"
        )

    if voice_id is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            voice_id = _defaults.get("voice_id") or get_with_env("MINIMAX_VOICE_ID", "tts", "minimax_voice_id", default=DEFAULT_VOICE_ID)
        except Exception:
            voice_id = _defaults.get("voice_id") or os.environ.get("MINIMAX_VOICE_ID", DEFAULT_VOICE_ID)

    if model is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            model = _defaults.get("model") or get_with_env("MINIMAX_TTS_MODEL", "tts", "minimax_model", default=DEFAULT_MODEL)
        except Exception:
            model = _defaults.get("model") or os.environ.get("MINIMAX_TTS_MODEL", DEFAULT_MODEL)

    # ── 构造请求 ──
    api_url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={group_id}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "text": text,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "format": "mp3",
            "sample_rate": 32000,
        },
    }

    # ── 发送请求 ──
    logger.info("MiniMax TTS: model=%s, voice=%s, group=%s, len=%d",
                model, voice_id, group_id, len(text))
    try:
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=60,
        )
    except requests.exceptions.Timeout:
        raise Exception("MiniMax TTS 请求超时（30s）")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"MiniMax TTS 连接失败: {e}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"MiniMax TTS 请求异常: {e}")

    # ── HTTP 状态码检查 ──
    if resp.status_code != 200:
        raise Exception(
            f"MiniMax TTS HTTP 错误: {resp.status_code} — {resp.text[:500]}"
        )

    # ── 解析响应 JSON ──
    try:
        result = resp.json()
    except ValueError:
        raise Exception(
            f"MiniMax TTS 返回非 JSON 响应: {resp.text[:500]}"
        )

    # ── 业务状态码检查 ──
    base_resp = result.get("base_resp", {})
    status_code = base_resp.get("status_code")
    status_msg = base_resp.get("status_msg", "未知错误")

    if status_code != 0:
        raise Exception(
            f"MiniMax TTS 业务错误 (code={status_code}): {status_msg}"
        )

    # ── 提取音频数据 ──
    data = result.get("data")
    if not data:
        raise Exception("MiniMax TTS 响应中缺少 data 字段")

    audio_hex = data.get("audio")
    if not audio_hex:
        raise Exception("MiniMax TTS 响应中缺少 data.audio 字段")

    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError as e:
        raise Exception(f"MiniMax TTS 音频 hex 解码失败: {e}")

    if len(audio_bytes) == 0:
        raise Exception("MiniMax TTS 返回的音频数据为空")

    logger.info("TTS 合成成功，音频大小=%d bytes", len(audio_bytes))
    return audio_bytes
