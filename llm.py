# llm.py — LLM 调用模块（支持管理面板热配置 + GitHub 持久化）

import os
import logging
import threading
import requests
from openai import OpenAI

logger = logging.getLogger(__name__)

# 浏览器伪装头
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "X-Stainless-Lang": "",
    "X-Stainless-Package-Version": "",
    "X-Stainless-OS": "",
    "X-Stainless-Arch": "",
    "X-Stainless-Runtime": "",
    "X-Stainless-Runtime-Version": "",
}

# ── 内置 API 配置（内存管理，支持热替换）──
# 每个 provider: { "name": str, "base_url": str, "keys": [str], "enabled": bool }
_builtin_providers: list = []
_builtin_default_model: str = ""
_builtin_lock = threading.Lock()
_key_rotation_index: dict = {}  # provider_name → int

# ── MiniMax TTS 配置（内存管理）──
_tts_config: dict = {}
_tts_lock = threading.Lock()

# ── 配置变更回调（app.py 注册，用于 GitHub 持久化）──
_on_config_changed = None  # callable or None


def register_config_callback(fn):
    """注册配置变更回调。每次管理面板修改配置后自动调用。"""
    global _on_config_changed
    _on_config_changed = fn


def _notify_changed():
    """配置发生变更，通知外部持久化"""
    if _on_config_changed:
        try:
            _on_config_changed()
        except Exception as e:
            logger.error("配置变更回调失败: %s", e)


def _load_from_env():
    """启动时从环境变量加载初始配置（仅当没有持久化数据时使用）"""
    global _builtin_default_model

    try:
        from config_store import get_with_env
        _builtin_default_model = get_with_env("LLM_MODEL", "llm", "model", default="claude-sonnet-4-20250514")
    except Exception:
        _builtin_default_model = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

    # 组3（多key轮换）
    p3_url = os.environ.get("LLM_PROVIDER3_URL", "")
    p3_keys_raw = os.environ.get("LLM_PROVIDER3_KEYS", "")
    if p3_url and p3_keys_raw:
        keys = [k.strip() for k in p3_keys_raw.split(",") if k.strip()]
        if keys:
            _builtin_providers.append({
                "name": "provider3",
                "base_url": p3_url,
                "keys": keys,
                "enabled": True,
            })

    # 组1
    try:
        from config_store import get_with_env
        p1_url = get_with_env("LLM_PROVIDER1_URL", "llm", "api_base", default="")
        p1_key = get_with_env("LLM_PROVIDER1_KEY", "llm", "api_key", default="")
    except Exception:
        p1_url = os.environ.get("LLM_PROVIDER1_URL", "")
        p1_key = os.environ.get("LLM_PROVIDER1_KEY", "")
    if p1_url and p1_key:
        _builtin_providers.append({
            "name": "provider1",
            "base_url": p1_url,
            "keys": [p1_key],
            "enabled": True,
        })

    # 组2
    p2_url = os.environ.get("LLM_PROVIDER2_URL", "")
    p2_key = os.environ.get("LLM_PROVIDER2_KEY", "")
    if p2_url and p2_key:
        _builtin_providers.append({
            "name": "provider2",
            "base_url": p2_url,
            "keys": [p2_key],
            "enabled": True,
        })

    # MiniMax TTS
    try:
        from config_store import get_with_env
        tts_key = get_with_env("MINIMAX_API_KEY", "tts", "minimax_api_key", default="")
        tts_group = get_with_env("MINIMAX_GROUP_ID", "tts", "minimax_group_id", default="")
        tts_voice = get_with_env("MINIMAX_VOICE_ID", "tts", "minimax_voice_id", default="")
        tts_model = get_with_env("MINIMAX_TTS_MODEL", "tts", "minimax_model", default="")
    except Exception:
        tts_key = os.environ.get("MINIMAX_API_KEY", "")
        tts_group = os.environ.get("MINIMAX_GROUP_ID", "")
        tts_voice = os.environ.get("MINIMAX_VOICE_ID", "")
        tts_model = os.environ.get("MINIMAX_TTS_MODEL", "")
    if tts_key:
        _tts_config["api_key"] = tts_key
    if tts_group:
        _tts_config["group_id"] = tts_group
    if tts_voice:
        _tts_config["voice_id"] = tts_voice
    if tts_model:
        _tts_config["model"] = tts_model


def load_from_persistent(data: dict):
    """从持久化数据恢复配置（优先于环境变量）。
    data 格式: { "default_model": str, "providers": [...], "tts": {...} }
    """
    global _builtin_default_model

    with _builtin_lock:
        _builtin_providers.clear()
        for p in data.get("providers", []):
            _builtin_providers.append({
                "name": p["name"],
                "base_url": p["base_url"],
                "keys": list(p.get("keys", [])),
                "enabled": p.get("enabled", True),
            })

    _builtin_default_model = data.get("default_model", "") or os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

    with _tts_lock:
        _tts_config.clear()
        tts = data.get("tts", {})
        for k, v in tts.items():
            if v:
                _tts_config[k] = v

    with _stt_lock:
        _stt_config.clear()
        _stt_config["groq_api_key"] = ""  # default
        stt = data.get("stt", {})
        for k, v in stt.items():
            if v:
                _stt_config[k] = v

    logger.info("从持久化数据恢复 API 配置: %d 个 provider, model=%s",
                len(data.get("providers", [])), _builtin_default_model)


def get_serializable_config():
    """导出当前配置为可序列化的 dict（用于持久化保存）"""
    with _builtin_lock:
        providers = []
        for p in _builtin_providers:
            providers.append({
                "name": p["name"],
                "base_url": p["base_url"],
                "keys": list(p["keys"]),
                "enabled": p["enabled"],
            })
    with _tts_lock:
        tts = dict(_tts_config)
    with _stt_lock:
        stt = dict(_stt_config)
    return {
        "default_model": _builtin_default_model,
        "providers": providers,
        "tts": tts,
        "stt": stt,
    }


# 不要在模块加载时立即调用 _load_from_env()
# app.py 会先尝试从 GitHub 加载持久化数据，只有失败时才 fallback 到 _load_from_env()


# ── 管理接口（供 app.py 调用）──

def get_builtin_config():
    """获取当前所有内置配置（管理面板用）"""
    with _builtin_lock:
        providers = []
        for p in _builtin_providers:
            providers.append({
                "name": p["name"],
                "base_url": p["base_url"],
                "keys": p["keys"],
                "key_count": len(p["keys"]),
                "enabled": p["enabled"],
            })
    with _tts_lock:
        tts = dict(_tts_config)
    with _stt_lock:
        stt = dict(_stt_config)
    return {
        "default_model": _builtin_default_model,
        "providers": providers,
        "tts": tts,
        "stt": stt,
    }


def set_default_model(model: str):
    global _builtin_default_model
    _builtin_default_model = model
    _notify_changed()


def add_provider(name: str, base_url: str, keys: list, enabled: bool = True):
    with _builtin_lock:
        # 如果同名已存在，更新
        for p in _builtin_providers:
            if p["name"] == name:
                p["base_url"] = base_url
                p["keys"] = keys
                p["enabled"] = enabled
                break
        else:
            _builtin_providers.append({
                "name": name,
                "base_url": base_url,
                "keys": keys,
                "enabled": enabled,
            })
    _notify_changed()


def update_provider(name: str, **kwargs):
    found = False
    with _builtin_lock:
        for p in _builtin_providers:
            if p["name"] == name:
                if "base_url" in kwargs:
                    p["base_url"] = kwargs["base_url"]
                if "keys" in kwargs:
                    p["keys"] = kwargs["keys"]
                if "add_keys" in kwargs:
                    # 追加 key（去重）
                    existing = set(p["keys"])
                    for k in kwargs["add_keys"]:
                        if k and k not in existing:
                            p["keys"].append(k)
                            existing.add(k)
                if "remove_keys" in kwargs:
                    # 删除指定 key
                    to_remove = set(kwargs["remove_keys"])
                    p["keys"] = [k for k in p["keys"] if k not in to_remove]
                if "enabled" in kwargs:
                    p["enabled"] = kwargs["enabled"]
                found = True
                break
    if found:
        _notify_changed()
    return found


def delete_provider(name: str):
    with _builtin_lock:
        _builtin_providers[:] = [p for p in _builtin_providers if p["name"] != name]
    _notify_changed()


def update_tts_config(**kwargs):
    with _tts_lock:
        for k, v in kwargs.items():
            if v:
                _tts_config[k] = v
            elif k in _tts_config:
                del _tts_config[k]
    _notify_changed()


def get_tts_defaults():
    """返回内置 TTS 配置（tts.py 调用时使用）"""
    with _tts_lock:
        return dict(_tts_config)


# ── STT 配置（内存管理）──
try:
    from config_store import get_with_env
    _stt_config = {
        "groq_api_key": get_with_env("GROQ_API_KEY", "stt", "groq_api_key", default=""),
        "zhipu_api_key": get_with_env("ZHIPU_API_KEY", "stt", "zhipu_api_key", default=""),
    }
except Exception:
    _stt_config = {
        "groq_api_key": "",
        "zhipu_api_key": "",
    }
_stt_lock = threading.Lock()


def get_stt_defaults():
    """返回内置 STT 配置（stt.py 调用时使用）"""
    with _stt_lock:
        return dict(_stt_config)


def update_stt_config(groq_api_key=None, zhipu_api_key=None):
    with _stt_lock:
        if groq_api_key is not None:
            _stt_config["groq_api_key"] = groq_api_key
        if zhipu_api_key is not None:
            _stt_config["zhipu_api_key"] = zhipu_api_key
    _notify_changed()


# ── 核心调用逻辑 ──

def get_providers(custom_api=None):
    """构建 provider 列表。custom_api 优先，否则按内置配置排列。"""
    if custom_api:
        return [{
            "name": "custom",
            "base_url": custom_api["base_url"],
            "api_key": custom_api["api_key"],
        }]

    result = []
    with _builtin_lock:
        for p in _builtin_providers:
            if not p["enabled"]:
                continue
            keys = p["keys"]
            if not keys:
                continue
            name = p["name"]
            if len(keys) > 1:
                # 多key轮换
                idx = _key_rotation_index.get(name, 0) % len(keys)
                rotated = [keys[(i + idx) % len(keys)] for i in range(len(keys))]
                for ki, key in enumerate(rotated):
                    result.append({
                        "name": f"{name}-key{(idx + ki) % len(keys)}",
                        "base_url": p["base_url"],
                        "api_key": key,
                    })
            else:
                result.append({
                    "name": name,
                    "base_url": p["base_url"],
                    "api_key": keys[0],
                })
    return result


def stream_chat(messages, model=None, custom_api=None):
    """
    流式调用LLM，遍历 provider 列表直到成功。
    yield 每个 chunk 的文本片段。
    """
    if model is None:
        if custom_api and custom_api.get("model"):
            model = custom_api["model"]
        else:
            model = _builtin_default_model or "claude-sonnet-4-20250514"

    use_model = model
    if custom_api and custom_api.get("model"):
        use_model = custom_api["model"]

    providers = get_providers(custom_api)
    if not providers:
        raise Exception("所有API均不可用")

    last_error = None
    for provider in providers:
        try:
            client = OpenAI(
                base_url=provider["base_url"],
                api_key=provider["api_key"],
                default_headers=_BROWSER_HEADERS,
            )
            response = client.chat.completions.create(
                model=use_model,
                messages=messages,
                stream=True,
                temperature=0.85,
                max_tokens=4000,
                timeout=30,
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            return
        except Exception as e:
            last_error = e
            logger.error("Provider %s failed: %s", provider["name"], e)
            # 多key轮换时更新 index
            pname = provider["name"].rsplit("-key", 1)[0]
            with _builtin_lock:
                for p in _builtin_providers:
                    if p["name"] == pname and len(p["keys"]) > 1:
                        _key_rotation_index[pname] = _key_rotation_index.get(pname, 0) + 1
                        break
            continue

    raise Exception(f"所有API均不可用 (last error: {last_error})")


def fetch_models(base_url, api_key):
    """获取指定API端点的可用模型列表。"""
    try:
        url = base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
    except Exception as e:
        logger.error("Failed to fetch models from %s: %s", base_url, e)
        return []
