import json
import os
import threading
from typing import Any, Dict


_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_LOCK = threading.Lock()


DEFAULT_CONFIG: Dict[str, Any] = {
    "character_name": "AI助手",
    "llm": {
        "api_base": "",
        "api_key": "",
        "model": "",
    },
    "tts": {
        "minimax_api_key": "",
        "minimax_group_id": "",
        "minimax_voice_id": "",
        "minimax_model": "",
    },
    "stt": {
        "zhipu_api_key": "",
        "groq_api_key": "",
    },
    "admin": {
        "password": "",
    },
    "github": {
        "token": "",
        "data_repo": "",
    },
}


def config_path() -> str:
    return _CONFIG_PATH


def config_exists() -> bool:
    return os.path.exists(_CONFIG_PATH)


def load_config() -> Dict[str, Any]:
    with _LOCK:
        if not os.path.exists(_CONFIG_PATH):
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            return json.loads(json.dumps(DEFAULT_CONFIG))

        merged = json.loads(json.dumps(DEFAULT_CONFIG))

        def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]):
            for k, v in (src or {}).items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    _deep_update(dst[k], v)
                else:
                    dst[k] = v

        _deep_update(merged, data)
        return merged


def save_config(data: Dict[str, Any]) -> None:
    with _LOCK:
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def get(*keys: str, default: Any = "") -> Any:
    """Get nested config value: get('llm','api_key')."""
    data = load_config()
    cur: Any = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    if cur is None or cur == "":
        return default
    return cur


def set_value(*keys: str, value: Any) -> Dict[str, Any]:
    data = load_config()
    cur = data
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value
    save_config(data)
    return data


def get_with_env(env_key: str, *cfg_keys: str, default: Any = "") -> Any:
    """Prefer config.json then fallback to environment variable."""
    v = get(*cfg_keys, default="")
    if v not in (None, ""):
        return v
    ev = os.environ.get(env_key, "")
    if ev in (None, ""):
        return default
    return ev


def is_setup_complete() -> bool:
    # minimal requirement: LLM api_key present
    api_key = get_with_env("LLM_PROVIDER1_KEY", "llm", "api_key", default="")
    return bool(str(api_key).strip())
