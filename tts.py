# tts.py — Mossland + MiniMax 双引擎 TTS 语音合成
# 环境变量 TTS_ENGINE=mossland（默认）/minimax

import os
import re
import requests
import logging
import json

logger = logging.getLogger(__name__)

# ── Mossland 默认值 ──
DEFAULT_MOSSLAND_VOICE_ID = "1f4af4c7-eb64-49c3-bc72-de9c6ff585bc"
DEFAULT_MOSSLAND_MODEL = "moss-speech-turbo"
DEFAULT_MOSSLAND_BASE_URL = "https://api.mosi.cn/v1"

# ── MiniMax 默认值 ──
MINIMAX_DEFAULT_VOICE_ID = "male-qn-qingse"
MINIMAX_DEFAULT_MODEL = "speech-2.8-hd"

# MiniMax speech-2.8 原生支持的语气词标签
_MINIMAX_SUPPORTED_TAGS = {
    'laughs', 'chuckle', 'coughs', 'clear-throat', 'groans', 'breath',
    'pant', 'inhale', 'exhale', 'gasps', 'sniffs', 'sighs', 'snorts',
    'burps', 'lip-smacking', 'humming', 'hissing', 'emm', 'whistles',
    'sneezes', 'crying', 'applause',
}

_TAG_MAPPING = {
    'chuckles': 'chuckle', 'chuckling': 'chuckle',
    'laugh': 'laughs', 'laughing': 'laughs',
    'sigh': 'sighs', 'sighing': 'sighs',
    'gasp': 'gasps', 'gasping': 'gasps',
    'cough': 'coughs', 'coughing': 'coughs',
    'sniff': 'sniffs', 'sniffing': 'sniffs',
    'snort': 'snorts', 'snorting': 'snorts',
    'sneeze': 'sneezes', 'sneezing': 'sneezes',
    'hmm': 'emm', 'hm': 'emm', 'um': 'emm', 'umm': 'emm',
    'hums': 'humming', 'hum': 'humming',
    'clears throat': 'clear-throat', 'clears-throat': 'clear-throat',
    'throat clear': 'clear-throat', 'giggles': 'chuckle', 'giggle': 'chuckle',
    'exhale': 'exhale', 'exhales': 'exhale',
    'inhales': 'inhale', 'breathing': 'breath',
    'panting': 'pant', 'pants': 'pant',
    'groaning': 'groans', 'groan': 'groans',
    'burp': 'burps', 'burping': 'burps',
    'hissing sound': 'hissing', 'hiss': 'hissing',
}


def _get_tts_defaults():
    """从 llm.defaults 或 config_store 获取 TTS 默认配置（优先 config.json）。"""
    try:
        from config_store import get_with_env
        return {
            "api_key": get_with_env("MOSSLAND_API_KEY", "tts", "mossland_api_key", default=""),
            "voice_id": get_with_env("MOSSLAND_VOICE_ID", "tts", "mossland_voice_id", default=DEFAULT_MOSSLAND_VOICE_ID),
            "api_base": get_with_env("MOSSLAND_BASE_URL", "tts", "mossland_base_url", default=DEFAULT_MOSSLAND_BASE_URL),
            "model": get_with_env("MOSSLAND_TTS_MODEL", "tts", "mossland_model", default=DEFAULT_MOSSLAND_MODEL),
        }
    except Exception:
        return {}


def _normalize_voice_tags(text: str) -> str:
    """将 AI 输出中的语气标签转换为 MiniMax（或通用）格式。"""
    def _replace_tag(m):
        tag = m.group(1).strip().lower()
        if tag in _MINIMAX_SUPPORTED_TAGS:
            return f'({tag})'
        mapped = _TAG_MAPPING.get(tag)
        if mapped and mapped in _MINIMAX_SUPPORTED_TAGS:
            return f'({mapped})'
        return ''
    result = re.sub(r'\(([a-zA-Z\s\-]+)\)', _replace_tag, text)
    result = re.sub(r'\s{2,}', ' ', result)
    return result.strip()


def _convert_pause_tags(text: str) -> str:
    """将停顿标签 <#x#> / <#x> 转为中文标点。"""
    def _pause_to_punct(m):
        seconds = float(m.group(1))
        return '……' if seconds >= 0.5 else '，'
    return re.sub(r'<#([\d.]+)#?>', _pause_to_punct, text)


def _preprocess_text(text: str) -> str:
    """通用文本预处理：标签清洗、停顿转换。"""
    text = _normalize_voice_tags(text)
    text = _convert_pause_tags(text)
    # 过滤纯标点/空白
    stripped = re.sub(r'[^\w]', '', text, flags=re.UNICODE)
    if not stripped:
        logger.debug("文本为纯标点或空白，跳过合成: %r", text)
        return None
    return text


# ══════════════════════════════════════════════════════════════
# Mossland TTS 后端（OpenAI 兼容接口）
# ══════════════════════════════════════════════════════════════

def _synthesize_mossland(text, api_key=None, voice_id=None, model=None, base_url=None):
    """使用 Mossland / Mosi API 合成语音（OpenAI TTS 兼容）。"""
    if api_key is None:
        api_key = os.environ.get("MOSSLAND_API_KEY", "")
    if not api_key:
        raise ValueError("未提供 api_key，且环境变量 MOSSLAND_API_KEY 未设置")

    voice_id = voice_id or os.environ.get("MOSSLAND_VOICE_ID", DEFAULT_MOSSLAND_VOICE_ID)
    model = model or os.environ.get("MOSSLAND_TTS_MODEL", DEFAULT_MOSSLAND_MODEL)
    base_url = base_url or os.environ.get("MOSSLAND_BASE_URL", DEFAULT_MOSSLAND_BASE_URL)

    # 确保 base_url 不含尾部 /
    base_url = base_url.rstrip("/")
    tts_url = f"{base_url}/audio/speech"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "input": text,
        "voice": voice_id,
        "response_format": "mp3",
        "speed": 1.0,
    }

    logger.info("Mossland TTS: url=%s, model=%s, voice=%s, text_len=%d",
                tts_url, model, voice_id, len(text))

    try:
        resp = requests.post(tts_url, headers=headers, json=payload, timeout=60)
    except requests.exceptions.Timeout:
        raise Exception("Mossland TTS 请求超时（60s）")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"Mossland TTS 连接失败: {e}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Mossland TTS 请求异常: {e}")

    if resp.status_code != 200:
        raise Exception(
            f"Mossland TTS HTTP 错误: {resp.status_code} — {resp.text[:500]}"
        )

    audio_bytes = resp.content
    if len(audio_bytes) == 0:
        raise Exception("Mossland TTS 返回的音频数据为空")

    logger.info("Mossland TTS 合成成功，%d bytes", len(audio_bytes))
    return audio_bytes


# ══════════════════════════════════════════════════════════════
# MiniMax TTS 后端（原始实现，作为降级/备选）
# ══════════════════════════════════════════════════════════════

def _synthesize_minimax(text, api_key=None, voice_id=None, group_id=None, model=None):
    """使用 MiniMax TTS API 合成语音。"""
    # 获取配置
    if api_key is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            api_key = _defaults.get("api_key") or get_with_env("MINIMAX_API_KEY", "tts", "minimax_api_key", default="")
        except Exception:
            api_key = _defaults.get("api_key") or os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise ValueError("未提供 api_key，且环境变量 MINIMAX_API_KEY 未设置")

    if group_id is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            group_id = _defaults.get("group_id") or get_with_env("MINIMAX_GROUP_ID", "tts", "minimax_group_id", default="")
        except Exception:
            group_id = _defaults.get("group_id") or os.environ.get("MINIMAX_GROUP_ID", "")
    if not group_id:
        raise ValueError("未提供 group_id，且环境变量 MINIMAX_GROUP_ID 未设置")

    if voice_id is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            voice_id = _defaults.get("voice_id") or get_with_env("MINIMAX_VOICE_ID", "tts", "minimax_voice_id", default=MINIMAX_DEFAULT_VOICE_ID)
        except Exception:
            voice_id = _defaults.get("voice_id") or os.environ.get("MINIMAX_VOICE_ID", MINIMAX_DEFAULT_VOICE_ID)

    if model is None:
        from llm import get_tts_defaults
        _defaults = get_tts_defaults()
        try:
            from config_store import get_with_env
            model = _defaults.get("model") or get_with_env("MINIMAX_TTS_MODEL", "tts", "minimax_model", default=MINIMAX_DEFAULT_MODEL)
        except Exception:
            model = _defaults.get("model") or os.environ.get("MINIMAX_TTS_MODEL", MINIMAX_DEFAULT_MODEL)

    # 特殊参数：api_service.py 中需要 group_id 参数
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

    logger.info("MiniMax TTS: model=%s, voice=%s, group=%s, len=%d",
                model, voice_id, group_id, len(text))

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
    except requests.exceptions.Timeout:
        raise Exception("MiniMax TTS 请求超时（60s）")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"MiniMax TTS 连接失败: {e}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"MiniMax TTS 请求异常: {e}")

    if resp.status_code != 200:
        raise Exception(f"MiniMax TTS HTTP 错误: {resp.status_code} — {resp.text[:500]}")

    try:
        result = resp.json()
    except ValueError:
        raise Exception(f"MiniMax TTS 返回非 JSON 响应: {resp.text[:500]}")

    base_resp = result.get("base_resp", {})
    status_code = base_resp.get("status_code")
    status_msg = base_resp.get("status_msg", "未知错误")
    if status_code != 0:
        raise Exception(f"MiniMax TTS 业务错误 (code={status_code}): {status_msg}")

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

    logger.info("MiniMax TTS 合成成功，音频大小=%d bytes", len(audio_bytes))
    return audio_bytes


# ══════════════════════════════════════════════════════════════
# 主入口：synthesize — 自动路由
# ══════════════════════════════════════════════════════════════

def synthesize(text, api_key=None, voice_id=None, group_id=None, model=None):
    """
    合成语音（主入口）。
    根据环境变量 TTS_ENGINE 自动路由：
      TTS_ENGINE=mossland（默认）→ 使用 Mossland
      TTS_ENGINE=minimax      → 使用 MiniMax

    参数:
        text:     要合成的文字
        api_key:  API Key（Mossland: MOSSLAND_API_KEY; MiniMax: MINIMAX_API_KEY）
        voice_id: 音色 ID
        group_id: MiniMax Group ID（仅 MiniMax 模式需要）
        model:    TTS 模型名

    返回:
        mp3 bytes — 合成成功时返回音频二进制数据
        None      — 输入文本为纯标点/空白时返回 None

    异常:
        ValueError — 参数缺失
        Exception  — API 调用失败
    """
    if not text or not isinstance(text, str):
        raise ValueError("text 参数不能为空")

    engine = os.environ.get("TTS_ENGINE", "mossland").lower().strip()

    # 通用文本预处理
    text = _preprocess_text(text)
    if text is None:
        return None

    if engine == "minimax":
        return _synthesize_minimax(
            text,
            api_key=api_key,
            voice_id=voice_id,
            group_id=group_id,
            model=model,
        )
    else:
        # Mossland 模式（默认）
        return _synthesize_mossland(
            text,
            api_key=api_key,
            voice_id=voice_id or os.environ.get("MOSSLAND_VOICE_ID", DEFAULT_MOSSLAND_VOICE_ID),
            model=model or os.environ.get("MOSSLAND_TTS_MODEL", DEFAULT_MOSSLAND_MODEL),
            base_url=os.environ.get("MOSSLAND_BASE_URL", DEFAULT_MOSSLAND_BASE_URL),
        )
