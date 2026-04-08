# app.py — 公开版语音通话 Flask 主应用
# 路由逻辑，业务代码在 llm.py/tts.py/stt.py/utils.py

import os
import json
import time
import base64
import hashlib
import logging
import threading
import queue as queue_mod
import tempfile
import subprocess
from flask import Flask, request, jsonify, Response, render_template, redirect, url_for

import re as _re

from utils import SentenceSplitter, VOICE_INSTRUCTION
from llm import stream_chat, fetch_models
from tts import synthesize as tts_synthesize
from stt import recognize as stt_recognize
from crypto_utils import encrypt_data, decrypt_data, encrypt_binary, decrypt_binary


# ── 思维链清理 ──

# 内置清理正则（编译一次，复用）
_THINKING_PATTERNS = [
    _re.compile(r'<think>.*?</think>', _re.DOTALL),
    _re.compile(r'<thinking>.*?</thinking>', _re.DOTALL),
    _re.compile(r'\[thinking\].*?\[/thinking\]', _re.DOTALL),
    _re.compile(r'<reasoning>.*?</reasoning>', _re.DOTALL),
    # markdown 思维块: > [!thinking] 开头的连续引用段落
    _re.compile(r'(?:^|\n)>\s*\[!thinking\][^\n]*(?:\n>[^\n]*)*', _re.MULTILINE),
]


def _clean_thinking(text, filter_rules=None):
    """
    清理思维链内容和用户自定义过滤规则。
    filter_rules: [{"pattern": "正则", "replace": "替换文本(可选)"}]
    """
    # 1. 内置清理
    for pat in _THINKING_PATTERNS:
        text = pat.sub('', text)

    # 2. 用户自定义规则
    if filter_rules:
        for rule in filter_rules:
            try:
                pattern = rule.get('pattern', '')
                replace = rule.get('replace', '')
                if pattern:
                    text = _re.sub(pattern, replace, text, flags=_re.DOTALL)
            except Exception:
                pass  # 忽略无效正则

    # 3. 清理多余空白
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class ThinkingFilter:
    """
    流式思维链过滤器。
    跟踪 <think>/<thinking>/[thinking]/<reasoning> 标签的开闭状态，
    在流式 delta 中实时过滤掉思维链内容。
    """

    # 开始标签 → 对应结束标签
    _TAG_PAIRS = [
        ('<think>',    '</think>'),
        ('<thinking>', '</thinking>'),
        ('[thinking]', '[/thinking]'),
        ('<reasoning>','</reasoning>'),
    ]

    def __init__(self):
        self._inside = False       # 是否在思维块内
        self._end_tag = ''         # 当前等待的结束标签
        self._buffer = ''          # 缓冲区（用于检测跨 delta 的标签）

    def feed(self, delta: str) -> str:
        """
        输入一段 delta 文本，返回过滤后的文本（可能为空字符串）。
        """
        self._buffer += delta
        output_parts = []

        while self._buffer:
            if self._inside:
                # 在思维块内，寻找结束标签
                end_pos = self._buffer.find(self._end_tag)
                if end_pos >= 0:
                    # 找到结束标签，跳过思维内容
                    self._buffer = self._buffer[end_pos + len(self._end_tag):]
                    self._inside = False
                    self._end_tag = ''
                else:
                    # 结束标签可能跨 delta，保留尾部可能的部分匹配
                    keep = len(self._end_tag) - 1
                    if keep > 0 and len(self._buffer) > keep:
                        self._buffer = self._buffer[-keep:]
                    else:
                        self._buffer = self._buffer[-keep:] if keep > 0 else ''
                    break
            else:
                # 不在思维块内，寻找开始标签
                earliest_pos = -1
                earliest_end_tag = ''
                earliest_start_tag = ''

                for start_tag, end_tag in self._TAG_PAIRS:
                    pos = self._buffer.find(start_tag)
                    if pos >= 0 and (earliest_pos < 0 or pos < earliest_pos):
                        earliest_pos = pos
                        earliest_end_tag = end_tag
                        earliest_start_tag = start_tag

                if earliest_pos >= 0:
                    # 输出开始标签之前的内容
                    output_parts.append(self._buffer[:earliest_pos])
                    self._buffer = self._buffer[earliest_pos + len(earliest_start_tag):]
                    self._inside = True
                    self._end_tag = earliest_end_tag
                else:
                    # 没找到任何开始标签
                    # 保留尾部可能的部分标签匹配
                    max_tag_len = max(len(t[0]) for t in self._TAG_PAIRS) - 1
                    safe_len = len(self._buffer) - max_tag_len
                    if safe_len > 0:
                        output_parts.append(self._buffer[:safe_len])
                        self._buffer = self._buffer[safe_len:]
                    break

        return ''.join(output_parts)

    def flush(self) -> str:
        """流结束时，输出剩余缓冲区（如果不在思维块内）。"""
        if self._inside:
            self._buffer = ''
            return ''
        remaining = self._buffer
        self._buffer = ''
        return remaining

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 过滤 werkzeug 日志中的敏感 URL 参数
class _SanitizeFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            import re
            # 脱敏 user_info, extra_prompt, topic, custom_llm, custom_tts 等参数
            record.msg = re.sub(
                r'(user_info|extra_prompt|topic|custom_llm|custom_tts|tts_api_key|custom_api)=[^&\s"]+',
                r'\1=[REDACTED]', record.msg
            )
        return True

logging.getLogger('werkzeug').addFilter(_SanitizeFilter())

app = Flask(__name__)

# ── 主题路由 ──
def _get_theme():
    """暂时锁定 iOS 风格"""
    return 'ios'



# 密钥管理（启动时从 GitHub 加载，管理面板热增删，变更自动同步到 GitHub）
_active_tokens: set = set()
_token_times: dict = {}    # token → 创建时间戳（所有token）
_account_tokens: dict = {}  # {token: {api_key, nickname, created}} — 账号登录，永不过期

SESSION_TIMEOUT = 1800  # 30 分钟

# 注册开关
_registration_enabled = True  # 默认开放
_registration_lock = threading.Lock()

# 充值窗口控制
_recharge_window = {
    "open": False,
    "close_at": 0,
    "qr_url": "/static/qr-pay.png",
    "note": "支付宝扫码付款，备注你注册的账号名，否则无法到账。手动充值有延时，十分钟以上未到账再联系我。体验使用建议充值1元。",
}
_recharge_lock = threading.Lock()

# 口令兑换系统
_redeem_codes: dict = {}   # code → { daily_limit, today_count, today_date, key_length }
_redeem_ip_map: dict = {}  # IP → { date, count, token } — 每IP每天限领1次，重复返回原token
_redeem_lock = threading.Lock()

# 系统公告
_announcement = {"text": "", "updated": 0}
_announcement_lock = threading.Lock()

# 意见反馈
_feedbacks: list = []  # [{text, contact, time, ip}]
_feedbacks_lock = threading.Lock()

# ── GitHub 持久化（密钥 + 口令）──
try:
    from config_store import get_with_env
    _GITHUB_TOKEN = get_with_env("GITHUB_TOKEN", "github", "token", default="")
    _GITHUB_DATA_REPO = get_with_env("GITHUB_DATA_REPO", "github", "data_repo", default="")
except Exception:
    _GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    _GITHUB_DATA_REPO = os.environ.get("GITHUB_DATA_REPO", "")
_GITHUB_DATA_API = f"https://api.github.com/repos/{_GITHUB_DATA_REPO}/contents"
_gh_shas: dict = {}  # filename → sha


def _gh_headers():
    return {
        "Authorization": f"token {_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def _gh_get_file(filename):
    """从 GitHub 获取文件内容，自动解密。返回 (content_str, sha) 或 (None, None)"""
    if not _GITHUB_TOKEN:
        return None, None
    import requests
    try:
        resp = requests.get(f"{_GITHUB_DATA_API}/{filename}",
                            headers=_gh_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            # 自动解密
            content = decrypt_data(content, filename)
            return content, data["sha"]
        return None, None
    except Exception as e:
        logger.error("GitHub 读取 %s 失败: %s", filename, e)
        return None, None


def _gh_put_file(filename, content_str, sha=None):
    """写文件到 GitHub（自动加密敏感文件），返回 True/False"""
    if not _GITHUB_TOKEN:
        return False
    import requests
    try:
        # 自动加密
        encrypted = encrypt_data(content_str, filename)
        body = {
            "message": f"sync {filename}",
            "content": base64.b64encode(encrypted.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        resp = requests.put(f"{_GITHUB_DATA_API}/{filename}",
                            headers=_gh_headers(), json=body, timeout=15)
        if resp.status_code in (200, 201):
            logger.info("GitHub 同步 %s 成功", filename)
            return True
        else:
            logger.error("GitHub 同步 %s 失败: %d %s", filename,
                         resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.error("GitHub 同步 %s 异常: %s", filename, e)
        return False




def _save_account_tokens_to_github():
    """异步保存账号 token 到 GitHub"""
    data = dict(_account_tokens)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    sha = _gh_shas.get("account_tokens.json")

    def _do():
        _gh_put_file("account_tokens.json", content, sha)
        _, new_sha = _gh_get_file("account_tokens.json")
        if new_sha:
            _gh_shas["account_tokens.json"] = new_sha

    threading.Thread(target=_do, daemon=True).start()


def _save_announcement_to_github():
    """异步保存公告到 GitHub"""
    with _announcement_lock:
        data = dict(_announcement)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    sha = _gh_shas.get("announcement.json")

    def _do():
        _gh_put_file("announcement.json", content, sha)
        _, new_sha = _gh_get_file("announcement.json")
        if new_sha:
            _gh_shas["announcement.json"] = new_sha

    threading.Thread(target=_do, daemon=True).start()


def _save_feedbacks_to_github():
    """异步保存反馈到 GitHub"""
    with _feedbacks_lock:
        data = list(_feedbacks)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    sha = _gh_shas.get("feedbacks.json")

    def _do():
        _gh_put_file("feedbacks.json", content, sha)
        _, new_sha = _gh_get_file("feedbacks.json")
        if new_sha:
            _gh_shas["feedbacks.json"] = new_sha

    threading.Thread(target=_do, daemon=True).start()


def _save_registration_to_github():
    """异步保存注册开关状态到 GitHub"""
    with _registration_lock:
        data = {"enabled": _registration_enabled}
    content = json.dumps(data, ensure_ascii=False, indent=2)
    sha = _gh_shas.get("registration_config.json")

    def _do():
        _gh_put_file("registration_config.json", content, sha)
        _, new_sha = _gh_get_file("registration_config.json")
        if new_sha:
            _gh_shas["registration_config.json"] = new_sha

    threading.Thread(target=_do, daemon=True).start()


def _save_redeem_to_github():
    """异步保存口令数据到 GitHub"""
    with _redeem_lock:
        data = {}
        for code, info in _redeem_codes.items():
            data[code] = {
                "daily_limit": info["daily_limit"],
                "today_count": info["today_count"],
                "today_date": info["today_date"],
                "key_length": info.get("key_length", 8),
            }
    content = json.dumps(data, ensure_ascii=False, indent=2)
    sha = _gh_shas.get("redeem.json")

    def _do():
        _gh_put_file("redeem.json", content, sha)
        _, new_sha = _gh_get_file("redeem.json")
        if new_sha:
            _gh_shas["redeem.json"] = new_sha

    threading.Thread(target=_do, daemon=True).start()


def _load_redeem():
    """启动时从 GitHub 加载口令和账号 token"""
    global _account_tokens

    # 加载口令
    content, sha = _gh_get_file("redeem.json")
    if content:
        try:
            data = json.loads(content)
            with _redeem_lock:
                for code, info in data.items():
                    _redeem_codes[code] = {
                        "daily_limit": info.get("daily_limit", 10),
                        "today_count": info.get("today_count", 0),
                        "today_date": info.get("today_date", ""),
                        "key_length": info.get("key_length", 8),
                    }
            _gh_shas["redeem.json"] = sha
            logger.info("从 GitHub 加载口令成功: %d 个", len(_redeem_codes))
        except Exception as e:
            logger.error("解析 redeem.json 失败: %s", e)
    else:
        logger.info("GitHub 无 redeem.json，口令为空（请通过管理面板添加）")

    # 加载账号 token
    content, sha = _gh_get_file("account_tokens.json")
    if content:
        try:
            _account_tokens = json.loads(content)
            _gh_shas["account_tokens.json"] = sha
            logger.info("从 GitHub 加载账号 token 成功: %d 个", len(_account_tokens))
        except Exception as e:
            logger.error("解析 account_tokens.json 失败: %s", e)
    else:
        logger.info("GitHub 无 account_tokens.json，账号 token 为空")

    # 加载公告
    content, sha = _gh_get_file("announcement.json")
    if content:
        try:
            data = json.loads(content)
            with _announcement_lock:
                _announcement.update(data)
            _gh_shas["announcement.json"] = sha
            logger.info("从 GitHub 加载公告成功")
        except Exception as e:
            logger.error("解析 announcement.json 失败: %s", e)

    # 加载反馈
    content, sha = _gh_get_file("feedbacks.json")
    if content:
        try:
            data = json.loads(content)
            with _feedbacks_lock:
                _feedbacks.clear()
                _feedbacks.extend(data[-200:])
            _gh_shas["feedbacks.json"] = sha
            logger.info("从 GitHub 加载反馈成功: %d 条", len(_feedbacks))
        except Exception as e:
            logger.error("解析 feedbacks.json 失败: %s", e)

    # 加载注册开关状态
    global _registration_enabled
    content, sha = _gh_get_file("registration_config.json")
    if content:
        try:
            data = json.loads(content)
            with _registration_lock:
                _registration_enabled = bool(data.get("enabled", True))
            _gh_shas["registration_config.json"] = sha
            logger.info("从 GitHub 加载注册开关成功: %s", "开放" if _registration_enabled else "关闭")
        except Exception as e:
            logger.error("解析 registration_config.json 失败: %s", e)


# 启动时加载持久化数据
_load_redeem()

# ── API 配置持久化（LLM providers + TTS）──
def _load_api_config():
    """启动时从 GitHub 加载 API 配置，失败则 fallback 到环境变量"""
    from llm import load_from_persistent, _load_from_env, register_config_callback
    content, sha = _gh_get_file("api_config.json")
    if content:
        try:
            data = json.loads(content)
            load_from_persistent(data)
            _gh_shas["api_config.json"] = sha
            logger.info("从 GitHub 加载 API 配置成功")
        except Exception as e:
            logger.error("解析 api_config.json 失败: %s，fallback 到环境变量", e)
            _load_from_env()
    else:
        logger.info("GitHub 无 api_config.json，从环境变量加载初始配置")
        _load_from_env()

    # 注册变更回调：每次管理面板改配置后自动保存到 GitHub
    register_config_callback(_save_api_config_to_github)


def _save_api_config_to_github():
    """异步保存当前 API 配置到 GitHub"""
    from llm import get_serializable_config
    config = get_serializable_config()
    content = json.dumps(config, ensure_ascii=False, indent=2)
    sha = _gh_shas.get("api_config.json")

    def _do():
        _gh_put_file("api_config.json", content, sha)
        _, new_sha = _gh_get_file("api_config.json")
        if new_sha:
            _gh_shas["api_config.json"] = new_sha

    threading.Thread(target=_do, daemon=True).start()


_load_api_config()

# 会话管理（内存，不持久化）
_sessions: dict = {}
_sessions_lock = threading.Lock()

# 内置提示词（从 prompt.md 读取）
_builtin_prompt = ""
_prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt.md")
if os.path.exists(_prompt_path):
    with open(_prompt_path, "r", encoding="utf-8") as f:
        _builtin_prompt = f.read()



def _check_token(token: str) -> bool:
    """检查 token 是否有效（口令token有过期，账号token永不过期）"""
    if not token:
        return False
    if token in _account_tokens:
        return True
    if token in _active_tokens:
        if token in _token_times and time.time() - _token_times[token] > SESSION_TIMEOUT:
            _active_tokens.discard(token)
            _token_times.pop(token, None)
            return False
        return True
    return False

# =====================================================================
# 首次部署配置向导（config.json）
# =====================================================================

try:
    from config_store import is_setup_complete, load_config, save_config
except Exception:
    is_setup_complete = None
    load_config = None
    save_config = None


@app.before_request
def _setup_wizard_guard():
    """如果未完成配置，则强制跳转到 /setup-wizard。"""
    try:
        if is_setup_complete and not is_setup_complete():
            # 放行向导自身与静态资源
            if request.path.startswith("/setup-wizard") or request.path.startswith("/static"):
                return None
            # 放行健康检查等（如有）
            if request.path.startswith("/favicon"):
                return None
            return redirect("/setup-wizard")
    except Exception:
        return None
    return None


@app.route("/setup-wizard", methods=["GET", "POST"])
def setup_wizard():
    """首次配置向导：保存到 config.json。"""
    if not load_config or not save_config:
        return "config_store 未就绪", 500

    cfg = load_config()
    error = ""
    ok = ""

    if request.method == "POST":
        step = request.form.get("step", "1")
        # 写入配置
        cfg.setdefault("llm", {})
        cfg["llm"]["api_base"] = (request.form.get("llm_api_base", "") or "").strip()
        cfg["llm"]["api_key"] = (request.form.get("llm_api_key", "") or "").strip()
        cfg["llm"]["model"] = (request.form.get("llm_model", "") or "").strip()

        cfg.setdefault("tts", {})
        cfg["tts"]["minimax_api_key"] = (request.form.get("tts_api_key", "") or "").strip()
        cfg["tts"]["minimax_group_id"] = (request.form.get("tts_group_id", "") or "").strip()
        cfg["tts"]["minimax_voice_id"] = (request.form.get("tts_voice_id", "") or "").strip()
        cfg["tts"]["minimax_model"] = (request.form.get("tts_model", "") or "").strip()

        cfg.setdefault("stt", {})
        cfg["stt"]["zhipu_api_key"] = (request.form.get("stt_zhipu_key", "") or "").strip()
        cfg["stt"]["groq_api_key"] = (request.form.get("stt_groq_key", "") or "").strip()

        cfg.setdefault("admin", {})
        cfg["admin"]["password"] = (request.form.get("admin_password", "") or "").strip()

        cfg.setdefault("github", {})
        cfg["github"]["token"] = (request.form.get("github_token", "") or "").strip()
        cfg["github"]["data_repo"] = (request.form.get("github_repo", "") or "").strip()

        cfg["character_name"] = (request.form.get("character_name", "") or "").strip()

        if not cfg["llm"]["api_key"]:
            error = "LLM API Key 不能为空。"
        else:
            save_config(cfg)
            ok = "配置已保存。"
            return redirect("/")

    # GET or POST with error
    theme = _get_theme()
    # 只做 ios 风格向导（与现有主题一致），其它主题也复用这个页面
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"
    return render_template("ios/setup_wizard.html", cfg=cfg, error=error, ok=ok, character_name=character_name)


# =====================================================================
# 页面路由
# =====================================================================

@app.route("/")
def index():
    theme = _get_theme()
    cfg = load_config() if load_config else {}
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"
    return render_template(f"{theme}/login.html", character_name=character_name)


@app.route("/setup")
def setup():
    """设置页 — 需要有效 token"""
    token = request.args.get("token", "")
    if not _check_token(token):
        return redirect("/")
    theme = _get_theme()
    cfg = load_config() if load_config else {}
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"
    return render_template(f"{theme}/setup.html", character_name=character_name)


@app.route("/call")
def call():
    """通话页 — 需要有效 token"""
    token = request.args.get("token", "")
    if not _check_token(token):
        return redirect("/")
    theme = _get_theme()
    cfg = load_config() if load_config else {}
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"
    return render_template(f"{theme}/call.html", character_name=character_name)


@app.route("/tts-api")
def tts_api_page():
    """TTS API 用户自助页面"""
    cfg = load_config() if load_config else {}
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"
    tts_voice_id = (
        os.environ.get("MINIMAX_VOICE_ID")
        or (cfg.get("tts", {}) if isinstance(cfg, dict) else {}).get("minimax_voice_id")
        or ""
    ).strip()
    return render_template("tts_api.html", character_name=character_name, tts_voice_id=tts_voice_id)


# =====================================================================
# API 路由
# =====================================================================


@app.route("/api/set-theme", methods=["POST"])
def set_theme_api():
    """设置 UI 主题"""
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "ios")
    if theme not in ("ios", "y2k"):
        theme = "ios"
    resp = jsonify({"ok": True, "theme": theme})
    resp.set_cookie("ui_theme", theme, max_age=365*24*3600, samesite="Lax")
    return resp







# IP 注册限制
_register_ip_map: dict = {}  # {ip: {"date": str, "count": int}}
_REGISTER_DAILY_LIMIT = 3    # 每 IP 每天最多注册 3 个

@app.route("/api/account/register", methods=["POST"])
def api_account_register():
    """账号注册"""
    if not _registration_enabled:
        return jsonify({"error": "注册已关闭，请联系管理员"}), 403
    data = request.get_json(force=True, silent=True) or {}
    nickname = (data.get("nickname") or "").strip()
    password = (data.get("password") or "").strip()
    if not nickname:
        return jsonify({"error": "请输入昵称"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "密码至少6位"}), 400

    # IP 限制
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
                or request.headers.get("X-Real-Ip", "") \
                or request.remote_addr or "unknown"
    today = time.strftime("%Y-%m-%d")
    ip_rec = _register_ip_map.get(client_ip)
    if ip_rec and ip_rec["date"] == today and ip_rec["count"] >= _REGISTER_DAILY_LIMIT:
        return jsonify({"error": f"注册太频繁，每天最多注册{_REGISTER_DAILY_LIMIT}个账号"}), 429
    
    result = _api_svc.register_user(nickname, password)
    if "error" in result:
        return jsonify(result), 400

    # 更新 IP 计数
    if not ip_rec or ip_rec["date"] != today:
        _register_ip_map[client_ip] = {"date": today, "count": 1}
    else:
        ip_rec["count"] += 1

    token = hashlib.sha256(f"acct_{result['api_key']}_{time.time()}".encode()).hexdigest()[:16]
    _account_tokens[token] = {
        "api_key": result["api_key"],
        "nickname": nickname,
        "created": time.time(),
    }
    _save_account_tokens_to_github()
    return jsonify({"token": token, "api_key": result["api_key"], "nickname": nickname})


@app.route("/api/account/login", methods=["POST"])
def api_account_login():
    """账号登录"""
    data = request.get_json(force=True, silent=True) or {}
    nickname = (data.get("nickname") or "").strip()
    password = (data.get("password") or "").strip()
    if not nickname or not password:
        return jsonify({"error": "请输入昵称和密码"}), 400
    result = _api_svc.login_user(nickname, password)
    if "error" in result:
        return jsonify(result), 401
    token = hashlib.sha256(f"acct_{result['api_key']}_{time.time()}".encode()).hexdigest()[:16]
    _account_tokens[token] = {
        "api_key": result["api_key"],
        "nickname": result["nickname"],
        "created": time.time(),
    }
    _save_account_tokens_to_github()
    return jsonify({"token": token, "api_key": result["api_key"], "nickname": result["nickname"]})


@app.route("/api/account/save-settings", methods=["POST"])
def api_account_save_settings():
    """保存账号用户的个人设定到 GitHub"""
    token = request.args.get("token", "").strip()
    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未授权"}), 401
    data = request.get_json(force=True, silent=True) or {}
    user_key = acct["api_key"]
    settings = {
        "user_id": data.get("user_id", ""),
        "user_info": data.get("user_info", ""),
        "topic": data.get("topic", ""),
        "extra_prompt": data.get("extra_prompt", ""),
    }
    path = f"user_settings/{user_key}.json"
    _gh_put_file(path, json.dumps(settings, ensure_ascii=False, indent=2))
    return jsonify({"ok": True})


@app.route("/api/account/load-settings", methods=["GET"])
def api_account_load_settings():
    """加载账号用户的个人设定"""
    token = request.args.get("token", "").strip()
    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未授权"}), 401
    user_key = acct["api_key"]
    content, _ = _gh_get_file(f"user_settings/{user_key}.json")
    if content:
        try:
            return jsonify(json.loads(content))
        except Exception:
            pass
    return jsonify({})


@app.route("/api/account/settings", methods=["GET"])
def api_account_settings_get():
    """读取用户加密设定"""
    token = request.args.get("token", "").strip()
    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未登录"}), 401
    result = _api_svc.load_user_settings(acct["api_key"])
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/account/settings", methods=["POST"])
def api_account_settings_save():
    """保存用户加密设定"""
    token = request.args.get("token", "").strip()
    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未登录"}), 401
    data = request.get_json(force=True, silent=True) or {}
    encrypted = data.get("encrypted", "")
    if not encrypted or len(encrypted) > 50000:
        return jsonify({"error": "数据无效"}), 400
    result = _api_svc.save_user_settings(acct["api_key"], encrypted)
    return jsonify(result)


@app.route("/api/account/balance", methods=["GET"])
def api_account_balance():
    """账号用户查询余额"""
    token = request.args.get("token", "").strip()
    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未登录"}), 401
    info = _api_svc.get_user_balance(acct["api_key"])
    if not info:
        return jsonify({"error": "用户不存在"}), 404
    return jsonify(info)


@app.route("/api/account/redeem-card", methods=["POST"])
def api_account_redeem_card():
    """账号用户使用卡密充值"""
    token = request.args.get("token", "").strip()
    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未登录"}), 401
    data = request.get_json(force=True, silent=True) or {}
    card_code = (data.get("card_code") or data.get("code") or "").strip()
    if not card_code:
        return jsonify({"error": "请输入卡密"}), 400
    result = _api_svc.redeem_card(acct["api_key"], card_code)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/redeem", methods=["POST"])
def api_redeem():
    """
    口令兑换密钥。每个口令每天有限额，每个IP每天限领1次。
    重复领取不消耗名额，返回之前的token。
    """
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "请输入口令"}), 400

    # 获取真实 IP（兼容反代）
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
                or request.headers.get("X-Real-Ip", "") \
                or request.remote_addr or "unknown"

    today = time.strftime("%Y-%m-%d")

    with _redeem_lock:
        if code not in _redeem_codes:
            return jsonify({"error": "口令无效"}), 403

        info = _redeem_codes[code]

        # 日期翻转，重置计数
        if info["today_date"] != today:
            info["today_date"] = today
            info["today_count"] = 0

        # 检查这个 IP 今天是否已经领过
        ip_key = f"{client_ip}:{code}"
        ip_record = _redeem_ip_map.get(ip_key)
        if ip_record and ip_record["date"] == today:
            # 已经领过了，返回之前的 token（如果还有效的话）
            old_token = ip_record.get("token", "")
            if old_token and old_token in _active_tokens:
                # token 还活着，直接返回
                return jsonify({"token": old_token, "remaining": info["daily_limit"] - info["today_count"], "reused": True})
            else:
                # token 过期了，重新生成一个，但不消耗名额
                new_token = hashlib.sha256(f"reissue_{client_ip}_{time.time()}".encode()).hexdigest()[:16]
                _active_tokens.add(new_token)
                _token_times[new_token] = time.time()
                ip_record["token"] = new_token
                return jsonify({"token": new_token, "remaining": info["daily_limit"] - info["today_count"], "reused": True})

        # 检查今日总名额
        if info["today_count"] >= info["daily_limit"]:
            return jsonify({"error": f"今日名额已满（每天限{info['daily_limit']}个），明天再来吧～"}), 429

        # 直接生成 token
        token = hashlib.sha256(f"redeem_{code}_{client_ip}_{time.time()}".encode()).hexdigest()[:16]
        _active_tokens.add(token)
        _token_times[token] = time.time()

        info["today_count"] += 1
        remaining = info["daily_limit"] - info["today_count"]

        # 记录这个 IP
        _redeem_ip_map[ip_key] = {"date": today, "token": token}

    _save_redeem_to_github()
    return jsonify({"token": token, "remaining": remaining})


def _describe_image(image_b64: str, vision_api: dict) -> str:
    """用独立的识图模型描述图片内容，返回描述文字"""
    import requests as _req
    base_url = vision_api["base_url"].rstrip("/")
    api_key = vision_api["api_key"]
    model = vision_api.get("model", "gpt-4o-mini")

    # 确保 base_url 包含正确路径
    if "/v1" in base_url:
        api_url = f"{base_url}/chat/completions"
    else:
        api_url = f"{base_url}/v1/chat/completions"

    logger.info("_describe_image: url=%s model=%s image_size=%d", api_url, model, len(image_b64))

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请详细描述这张图片中的所有内容，用中文。包括：人物的外貌特征、表情、姿态、穿着；环境场景、光线、背景物品；屏幕上的文字或图案；任何值得注意的细节。尽可能完整地描述你看到的一切。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }
    ]

    try:
        resp = _req.post(
            api_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": 800},
            timeout=15,
        )
        logger.info("_describe_image response: status=%d", resp.status_code)
        if resp.status_code == 200:
            result = resp.json()
            text = result["choices"][0]["message"]["content"].strip()
            logger.info("_describe_image 成功: %s", text[:100])
            return text
        else:
            err_text = resp.text[:500]
            logger.error("识图 API 错误: %d %s", resp.status_code, err_text)
            return ""
    except Exception as e:
        logger.error("识图 API 异常: %s", e)
        return ""


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    流式聊天 + TTS，返回 SSE。
    body: { text, session_id, token, custom_api?, custom_tts?, custom_prompt? }
    """
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权或会话已过期，请重新登录"}), 401

    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "缺少文本"}), 400

    session_id = data.get("session_id") or token
    custom_api = data.get("custom_api")       # dict | None
    custom_tts = data.get("custom_tts")       # dict | None
    tts_api_key = (data.get("tts_api_key") or "").strip()  # TTS API Key（计费模式）

    # 判断是否为注册用户（需要扣费）
    _is_account_user = token in _account_tokens
    _account_api_key = _account_tokens[token]["api_key"] if _is_account_user else None

    custom_prompt = data.get("custom_prompt")  # str | None
    req_model = data.get("model")             # str | None — 前端选择的模型
    max_history = min(100, max(1, int(data.get("max_history") or 20)))  # 1-100轮
    filter_rules = data.get("filter_rules")   # list | str | None — 自定义过滤规则
    # filter_rules 可能是 JSON 字符串或列表
    if isinstance(filter_rules, str):
        try:
            filter_rules = json.loads(filter_rules)
        except (json.JSONDecodeError, TypeError):
            filter_rules = None
    if not isinstance(filter_rules, list):
        filter_rules = None

    # 构建 system prompt
    parts = []
    if _builtin_prompt:
        parts.append(_builtin_prompt)
    if custom_prompt:
        parts.append(custom_prompt)

    # 通话记忆：账号用户自动加载最近通话总结
    history_count = data.get("history_count")
    acct_for_history = _account_tokens.get(token)
    if acct_for_history:
        if history_count:
            try:
                history_count = min(10, max(1, int(history_count)))
            except (ValueError, TypeError):
                history_count = 3
        else:
            history_count = 3  # 默认加载最近3次
        try:
            if acct_for_history:
                _history_user_key = acct_for_history["api_key"]
                summaries = _get_user_call_summaries(_history_user_key, history_count)
                if summaries:
                    memory_parts = ["# 之前的通话记忆"]
                    for idx, s in enumerate(summaries, 1):
                        st = s.get("start_time", "")
                        # 格式化时间显示
                        if st:
                            try:
                                # ISO 格式转为友好格式
                                st_display = st.replace("T", " ")[:16]
                            except Exception:
                                st_display = st
                        else:
                            st_display = "未知时间"
                        memory_parts.append(f"## 第{idx}次通话 ({st_display})")
                        memory_parts.append(s.get("summary", ""))
                        memory_parts.append("")
                    parts.append("\n".join(memory_parts))
        except (ValueError, TypeError):
            pass  # history_count 无效，忽略

    parts.append(VOICE_INSTRUCTION)
    system_prompt = "\n\n".join(parts)

    # 获取 / 创建 session history（限制条数）
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = []
        full_history = _sessions[session_id]
        max_msgs = max_history * 2  # 每轮一问一答
        history = list(full_history[-max_msgs:]) if len(full_history) > max_msgs else list(full_history)

    # 视频截图（可选）
    image_b64 = (data.get("image") or "").strip()
    vision_api = data.get("vision_api")  # 独立识图模型配置 {base_url, api_key, model}

    # 给用户消息加上时间戳
    timestamp = data.get("timestamp", "")
    if timestamp:
        user_content = f"[{timestamp}] {text}"
    else:
        user_content = text

    # 处理图片：一体模式 vs 分离模式
    image_description = ""
    use_inline_image = False  # 是否直接把图片塞进对话消息
    vision_log = ""  # 识图过程日志，发给前端

    if image_b64:
        logger.info("收到视频截图: %d 字节 base64, vision_api=%s", len(image_b64), "自定义" if vision_api else "内置")
        if vision_api and vision_api.get("base_url") and vision_api.get("api_key"):
            # 分离模式：用用户自定义的识图模型
            vision_log = f"识图中... (自定义模型: {vision_api.get('model','?')})"
            try:
                image_description = _describe_image(image_b64, vision_api)
                if image_description:
                    user_content += f"\n\n[画面描述] {image_description}"
                    vision_log = f"✅ 识图完成: {image_description[:60]}"
                    logger.info("自定义识图描述: %s", image_description[:100])
                else:
                    vision_log = "⚠️ 识图返回空结果"
            except Exception as e:
                vision_log = f"⚠️ 自定义识图失败: {e}"
                logger.error("自定义识图失败: %s", e)
        else:
            # 默认模式：用当前对话的 LLM provider 做识图
            try:
                _vision_cfg = {}
                _vision_model = ""
                if custom_api and custom_api.get("base_url") and custom_api.get("api_key"):
                    _vision_model = custom_api.get("model") or req_model or "gpt-4o-mini"
                    _vision_cfg = {
                        "base_url": custom_api["base_url"],
                        "api_key": custom_api["api_key"],
                        "model": _vision_model,
                    }
                else:
                    # 用内置 provider
                    from llm import get_providers
                    _provs = get_providers()
                    if _provs:
                        _p = _provs[0]
                        from llm import _builtin_default_model as _bdm
                        _vision_model = req_model or _bdm or "gpt-4o-mini"
                        _vision_cfg = {
                            "base_url": _p["base_url"],
                            "api_key": _p["api_key"],
                            "model": _vision_model,
                        }
                    else:
                        vision_log = "⚠️ 无可用 provider，跳过识图"
                if _vision_cfg:
                    vision_log = f"识图中... (内置: {_vision_model})"
                    logger.info("调用识图: base_url=%s model=%s", _vision_cfg["base_url"], _vision_model)
                    image_description = _describe_image(image_b64, _vision_cfg)
                    if image_description:
                        user_content += f"\n\n[画面描述] {image_description}"
                        vision_log = f"✅ 识图完成: {image_description[:60]}"
                        logger.info("内置识图描述: %s", image_description[:100])
                    else:
                        vision_log = "⚠️ 识图返回空结果（模型可能不支持 vision）"
            except Exception as e:
                vision_log = f"⚠️ 内置识图失败: {e}"
                logger.error("内置识图失败: %s, 尝试一体模式", e)
                use_inline_image = True

    # 构建 user message
    if use_inline_image:
        user_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_content},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }
    else:
        user_message = {"role": "user", "content": user_content}

    # 构建 messages
    messages = [{"role": "system", "content": system_prompt}] + history + [user_message]

    q: queue_mod.Queue = queue_mod.Queue()

    def _worker():
        start_time = time.time()
        first_token_time = None
        full_text_parts: list[str] = []
        sentence_index = 0

        try:
            # 1. user confirmed
            q.put(json.dumps({"type": "user_confirmed", "text": text}, ensure_ascii=False))

            # 1.5 vision log（如果有视频截图）
            if vision_log:
                q.put(json.dumps({"type": "vision_log", "text": vision_log}, ensure_ascii=False))

            # 2. status
            q.put(json.dumps({"type": "status", "step": "llm_start", "message": "正在思考..."}, ensure_ascii=False))

            # 3. stream LLM
            stream_kwargs: dict = {"messages": messages}
            if custom_api:
                stream_kwargs["custom_api"] = custom_api
                if custom_api.get("model"):
                    stream_kwargs["model"] = custom_api["model"]
                elif req_model:
                    stream_kwargs["model"] = req_model
            elif req_model:
                stream_kwargs["model"] = req_model

            splitter = SentenceSplitter()
            thinking_filter = ThinkingFilter()

            def _do_tts(sentence: str, idx: int):
                """合成一句 TTS 并推到队列"""
                try:
                    if tts_api_key:
                        # TTS API Key 模式：走计费
                        result = _api_svc.call_tts(tts_api_key, sentence)
                        if "error" in result:
                            logger.error("TTS API error for sentence %d: %s", idx, result["error"])
                            q.put(json.dumps({"type": "tts_error", "text": result["error"]}, ensure_ascii=False))
                            return
                        mp3_bytes = result["audio_bytes"]
                    elif custom_tts and custom_tts.get("api_key"):
                        # 用户自带 MiniMax：不扣费
                        tts_kwargs: dict = {"text": sentence}
                        tts_kwargs["api_key"] = custom_tts["api_key"]
                        if custom_tts.get("voice_id"):
                            tts_kwargs["voice_id"] = custom_tts["voice_id"]
                        if custom_tts.get("group_id"):
                            tts_kwargs["group_id"] = custom_tts["group_id"]
                        if custom_tts.get("model"):
                            tts_kwargs["model"] = custom_tts["model"]
                        mp3_bytes = tts_synthesize(**tts_kwargs)
                    elif _is_account_user and _account_api_key:
                        # 注册用户：走计费扣余额
                        result = _api_svc.call_tts(_account_api_key, sentence)
                        if "error" in result:
                            logger.error("Account TTS error for sentence %d: %s", idx, result["error"])
                            q.put(json.dumps({"type": "tts_error", "text": result["error"]}, ensure_ascii=False))
                            return
                        mp3_bytes = result["audio_bytes"]
                    else:
                        # 口令用户：免费用内置 TTS
                        mp3_bytes = tts_synthesize(text=sentence)
                    if mp3_bytes is None:
                        return  # 纯标点/空白，跳过
                    if len(mp3_bytes) < 100:
                        logger.warning("TTS sentence %d 返回音频过小 (%d bytes)，可能无效", idx, len(mp3_bytes))
                        q.put(json.dumps({"type": "tts_error", "text": f"第{idx+1}句语音数据异常（{len(mp3_bytes)}字节）"}, ensure_ascii=False))
                        return
                    audio_b64 = base64.b64encode(mp3_bytes).decode("ascii")
                    logger.debug("TTS sentence %d 合成成功, %d bytes", idx, len(mp3_bytes))
                    q.put(json.dumps({
                        "type": "audio",
                        "index": idx,
                        "text": sentence,
                        "audio": audio_b64,
                        "format": "mp3",
                    }, ensure_ascii=False))
                except Exception as e:
                    err_msg = str(e)
                    logger.error("TTS error for sentence %d: %s", idx, err_msg)
                    q.put(json.dumps({"type": "tts_error", "text": f"第{idx+1}句语音合成失败: {err_msg}"}, ensure_ascii=False))

            # 4. iterate deltas
            for delta in stream_chat(**stream_kwargs):
                if first_token_time is None:
                    first_token_time = time.time() - start_time

                full_text_parts.append(delta)

                # 通过思维链过滤器过滤 delta
                filtered_delta = thinking_filter.feed(delta)

                if filtered_delta:
                    # push text delta（仅推送过滤后的文本）
                    q.put(json.dumps({"type": "text_delta", "text": filtered_delta}, ensure_ascii=False))

                    # feed splitter（仅对过滤后的文本做 TTS）
                    sentences = splitter.feed(filtered_delta)
                    for sent in sentences:
                        _do_tts(sent, sentence_index)
                        sentence_index += 1

            llm_end_time = time.time()
            llm_time = llm_end_time - start_time

            # flush 思维链过滤器剩余内容
            flushed_thinking = thinking_filter.flush()
            if flushed_thinking:
                # 把剩余文本也发给前端显示
                q.put(json.dumps({"type": "text_delta", "text": flushed_thinking}, ensure_ascii=False))
                sentences_extra = splitter.feed(flushed_thinking)
                for sent in sentences_extra:
                    _do_tts(sent, sentence_index)
                    sentence_index += 1

            # 6. flush remaining text
            remaining = splitter.flush()
            for sent in remaining:
                _do_tts(sent, sentence_index)
                sentence_index += 1

            # 对完整文本做最终清理（内置规则 + 自定义规则）
            raw_full_text = "".join(full_text_parts)
            full_text = _clean_thinking(raw_full_text, filter_rules)
            total_time = time.time() - start_time

            # 7. done
            q.put(json.dumps({
                "type": "done",
                "full_text": full_text,
                "stats": {
                    "total_time": round(total_time, 3),
                    "first_token_time": round(first_token_time, 3) if first_token_time is not None else None,
                    "llm_time": round(llm_time, 3),
                    "sentences": sentence_index,
                },
            }, ensure_ascii=False))

            # 8. update session history
            with _sessions_lock:
                _sessions.setdefault(session_id, [])
                _sessions[session_id].append({"role": "user", "content": user_content})
                _sessions[session_id].append({"role": "assistant", "content": full_text})
                # 限制历史长度（最多保留 200 条 = 100 轮）
                if len(_sessions[session_id]) > 200:
                    _sessions[session_id] = _sessions[session_id][-200:]

        except Exception as exc:
            logger.exception("chat worker error")
            try:
                q.put(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False))
            except Exception:
                pass
        finally:
            # 9. sentinel
            q.put(None)

    threading.Thread(target=_worker, daemon=True).start()

    def _generate():
        while True:
            item = q.get()
            if item is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {item}\n\n"

    return Response(_generate(), mimetype="text/event-stream")


@app.route("/api/summary", methods=["POST"])
def api_summary():
    """通话结束后生成小结"""
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    session_id = data.get("session_id") or token
    custom_api = data.get("custom_api")
    req_model = data.get("model")

    # 获取通话历史
    with _sessions_lock:
        history = list(_sessions.get(session_id, []))

    if not history:
        return jsonify({"summary": "这通电话太短了，下次多聊会儿。"})

    # 构建通话记录文本
    conversation_lines = []
    for msg in history:
        role = "用户" if msg["role"] == "user" else "AI"
        conversation_lines.append(f"{role}: {msg['content']}")
    conversation_text = "\n".join(conversation_lines)

    # 小结提示词（注入人设）
    summary_prompt = f"""{_builtin_prompt}

---

你刚刚与用户结束了一通语音通话。请基于通话记录，生成一条约 50 字的收尾消息。
要求：
- 语气自然、像真实聊天
- 提到通话中的关键内容或有趣的点
- 结尾可以带一句温暖的话
- 只输出消息内容，不要加引号或前缀

通话记录：
{conversation_text}"""

    messages = [{"role": "user", "content": summary_prompt}]

    try:
        stream_kwargs = {"messages": messages}
        if custom_api:
            stream_kwargs["custom_api"] = custom_api
            if custom_api.get("model"):
                stream_kwargs["model"] = custom_api["model"]
            elif req_model:
                stream_kwargs["model"] = req_model
        elif req_model:
            stream_kwargs["model"] = req_model

        full_text = ""
        for delta in stream_chat(**stream_kwargs):
            full_text += delta

        return jsonify({"summary": full_text.strip()})
    except Exception as e:
        logger.exception("summary error")
        return jsonify({"summary": "信号不太好，下次再聊。"})


@app.route("/api/recognize", methods=["POST"])
def api_recognize():
    """语音识别"""
    if "audio" not in request.files:
        return jsonify({"error": "缺少音频文件"}), 400
    audio_file = request.files["audio"]
    try:
        result = stt_recognize(audio_file)
        return jsonify(result)
    except Exception as exc:
        logger.exception("STT error")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/models", methods=["GET"])
def api_models():
    """获取可用模型列表"""
    base_url = request.args.get("base_url", "")
    api_key = request.args.get("api_key", "")
    if not base_url or not api_key:
        return jsonify({"error": "缺少 base_url 或 api_key"}), 400
    try:
        models = fetch_models(base_url, api_key)
        return jsonify({"models": models})
    except Exception as exc:
        logger.exception("fetch_models error")
        return jsonify({"error": str(exc)}), 500


@app.route('/api/builtin-models')
def builtin_models():
    """获取内置中转站的模型列表"""
    from llm import get_providers, fetch_models
    providers = get_providers()  # 不传 custom_api，获取内置中转站列表
    all_models = []
    seen = set()
    for p in providers:
        try:
            models = fetch_models(p['base_url'], p['api_key'])
            for m in models:
                if m not in seen:
                    seen.add(m)
                    all_models.append(m)
            if all_models:
                break  # 第一个能用的中转站就够了
        except Exception:
            continue
    return jsonify({'models': all_models})


# =====================================================================
# 管理员路由 (ADMIN_PASSWORD 环境变量保护)
# =====================================================================

import secrets as _secrets
import string as _string

def _check_admin(req):
    """检查管理员密码，支持 header 和 query 参数"""
    try:
        from config_store import get_with_env
        admin_pw = get_with_env("ADMIN_PASSWORD", "admin", "password", default="")
    except Exception:
        admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pw:
        return False
    pw = req.headers.get("X-Admin-Password", "") or req.args.get("pw", "")
    return pw == admin_pw




@app.route("/admin")
def admin_page():
    """管理员页面"""
    if not _check_admin(request):
        return "未授权", 403
    return render_template("admin.html")





@app.route("/api/admin/sessions", methods=["GET"])
def admin_sessions_list():
    """查看活跃会话"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    now = time.time()
    sessions = []
    for token in list(_active_tokens):
        created = _token_times.get(token, 0)
        elapsed = now - created if created else 0
        remaining = max(0, SESSION_TIMEOUT - elapsed)
        is_guest = False
        expired = remaining <= 0
        sessions.append({
            "token": token[:6] + "...",
            "type": "访客" if is_guest else "密钥",
            "created": int(created),
            "elapsed_min": round(elapsed / 60, 1),
            "remaining_min": round(remaining / 60, 1),
            "expired": expired,
        })
    return jsonify({"sessions": sessions, "total": len(sessions)})


@app.route("/api/admin/sessions/clear-expired", methods=["POST"])
def admin_sessions_clear_expired():
    """清理过期会话"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    now = time.time()
    cleared = 0
    for token in list(_active_tokens):
        created = _token_times.get(token, 0)
        if created and now - created > SESSION_TIMEOUT:
            _active_tokens.discard(token)
            _token_times.pop(token, None)
            cleared += 1
    return jsonify({"cleared": cleared})


@app.route("/api/admin/redeem", methods=["GET"])
def admin_redeem_list():
    """查看所有口令及状态"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    today = time.strftime("%Y-%m-%d")
    with _redeem_lock:
        codes = []
        for code, info in _redeem_codes.items():
            today_count = info["today_count"] if info["today_date"] == today else 0
            codes.append({
                "code": code,
                "daily_limit": info["daily_limit"],
                "today_used": today_count,
                "today_remaining": info["daily_limit"] - today_count,
                "key_length": info.get("key_length", 8),
            })
    return jsonify({"codes": codes})


@app.route("/api/admin/redeem/add", methods=["POST"])
def admin_redeem_add():
    """添加新口令"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "口令不能为空"}), 400
    daily_limit = min(100, max(1, int(data.get("daily_limit", 10))))
    key_length = min(32, max(6, int(data.get("key_length", 8))))
    with _redeem_lock:
        _redeem_codes[code] = {
            "daily_limit": daily_limit,
            "today_count": 0,
            "today_date": "",
            "key_length": key_length,
        }
    _save_redeem_to_github()
    return jsonify({"ok": True, "code": code, "daily_limit": daily_limit})


@app.route("/api/admin/redeem/update", methods=["POST"])
def admin_redeem_update():
    """修改口令限额"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    with _redeem_lock:
        if code not in _redeem_codes:
            return jsonify({"error": "口令不存在"}), 404
        if "daily_limit" in data:
            _redeem_codes[code]["daily_limit"] = min(100, max(1, int(data["daily_limit"])))
        if "key_length" in data:
            _redeem_codes[code]["key_length"] = min(32, max(6, int(data["key_length"])))
    _save_redeem_to_github()
    return jsonify({"ok": True})


@app.route("/api/admin/redeem/delete", methods=["POST"])
def admin_redeem_delete():
    """删除口令"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    found = False
    with _redeem_lock:
        if code in _redeem_codes:
            del _redeem_codes[code]
            found = True
    if found:
        _save_redeem_to_github()
        return jsonify({"ok": True})
    return jsonify({"error": "口令不存在"}), 404


@app.route("/api/admin/redeem/reset", methods=["POST"])
def admin_redeem_reset():
    """重置口令今日计数"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    found = False
    with _redeem_lock:
        if code in _redeem_codes:
            _redeem_codes[code]["today_count"] = 0
            _redeem_codes[code]["today_date"] = ""
            found = True
    if found:
        _save_redeem_to_github()
        return jsonify({"ok": True})
    return jsonify({"error": "口令不存在"}), 404


# =====================================================================
# 系统公告 + 意见反馈
# =====================================================================

@app.route("/api/announcement", methods=["GET"])
def api_announcement():
    """获取当前公告（公开）"""
    with _announcement_lock:
        return jsonify(_announcement)


@app.route("/api/admin/announcement", methods=["POST"])
def admin_announcement_set():
    """设置系统公告"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    with _announcement_lock:
        _announcement["text"] = text
        _announcement["updated"] = int(time.time())
    _save_announcement_to_github()
    return jsonify({"ok": True})


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """用户提交反馈"""
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    contact = (data.get("contact") or "").strip()
    if not text:
        return jsonify({"error": "内容不能为空"}), 400
    if len(text) > 2000:
        return jsonify({"error": "内容过长"}), 400
    entry = {
        "text": text,
        "contact": contact,
        "time": int(time.time()),
        "ip": request.remote_addr or "",
    }
    with _feedbacks_lock:
        _feedbacks.append(entry)
        if len(_feedbacks) > 200:
            _feedbacks[:] = _feedbacks[-200:]
    _save_feedbacks_to_github()
    return jsonify({"ok": True})


@app.route("/api/admin/feedbacks", methods=["GET"])
def admin_feedbacks():
    """查看反馈列表"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    with _feedbacks_lock:
        return jsonify({"feedbacks": list(reversed(_feedbacks))})


# =====================================================================
# 内置 API 配置管理
# =====================================================================

@app.route("/api/admin/api-config", methods=["GET"])
def admin_api_config_get():
    """获取所有内置 API 配置"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    from llm import get_builtin_config
    return jsonify(get_builtin_config())


@app.route("/api/admin/api-config/model", methods=["POST"])
def admin_api_config_model():
    """设置默认模型"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"error": "模型名不能为空"}), 400
    from llm import set_default_model
    set_default_model(model)
    return jsonify({"ok": True, "model": model})


@app.route("/api/admin/api-config/provider/add", methods=["POST"])
def admin_api_provider_add():
    """添加/更新 LLM provider"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    keys_raw = data.get("keys", [])
    if isinstance(keys_raw, str):
        keys_raw = [k.strip() for k in keys_raw.split(",") if k.strip()]
    enabled = data.get("enabled", True)
    if not name or not base_url or not keys_raw:
        return jsonify({"error": "name, base_url, keys 必填"}), 400
    from llm import add_provider
    add_provider(name, base_url, keys_raw, enabled)
    return jsonify({"ok": True})


@app.route("/api/admin/api-config/provider/update", methods=["POST"])
def admin_api_provider_update():
    """更新 LLM provider（支持追加/删除key）"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name 必填"}), 400
    kwargs = {}
    if "base_url" in data:
        kwargs["base_url"] = data["base_url"].strip()
    if "keys" in data:
        keys_raw = data["keys"]
        if isinstance(keys_raw, str):
            keys_raw = [k.strip() for k in keys_raw.split(",") if k.strip()]
        kwargs["keys"] = keys_raw
    if "add_keys" in data:
        ak = data["add_keys"]
        if isinstance(ak, str):
            ak = [k.strip() for k in ak.split(",") if k.strip()]
        kwargs["add_keys"] = ak
    if "remove_keys" in data:
        rk = data["remove_keys"]
        if isinstance(rk, str):
            rk = [k.strip() for k in rk.split(",") if k.strip()]
        kwargs["remove_keys"] = rk
    if "enabled" in data:
        kwargs["enabled"] = bool(data["enabled"])
    from llm import update_provider
    if update_provider(name, **kwargs):
        return jsonify({"ok": True})
    return jsonify({"error": "provider 不存在"}), 404


@app.route("/api/admin/api-config/provider/delete", methods=["POST"])
def admin_api_provider_delete():
    """删除 LLM provider"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name 必填"}), 400
    from llm import delete_provider
    delete_provider(name)
    return jsonify({"ok": True})


@app.route("/api/admin/api-config/tts", methods=["POST"])
def admin_api_tts_update():
    """更新 MiniMax TTS 配置"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    from llm import update_tts_config
    update_tts_config(
        api_key=data.get("api_key", ""),
        group_id=data.get("group_id", ""),
        voice_id=data.get("voice_id", ""),
        model=data.get("model", ""),
    )
    return jsonify({"ok": True})


@app.route("/api/admin/api-config/stt", methods=["POST"])
def admin_api_stt_update():
    """更新 STT 配置"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    from llm import update_stt_config
    update_stt_config(
        groq_api_key=data.get("groq_api_key"),
        zhipu_api_key=data.get("zhipu_api_key"),
    )
    return jsonify({"ok": True})


# =====================================================================
# TTS API 服务（用户注册/充值/调用）
# =====================================================================

import api_service as _api_svc

@app.route("/api/v1/register", methods=["POST"])
def tts_api_register():
    """用户注册，获取 API Key"""
    data = request.get_json(force=True, silent=True) or {}
    nickname = (data.get("nickname") or "").strip()
    password = data.get("password", "")
    if not nickname:
        return jsonify({"error": "请输入昵称"}), 400
    if len(nickname) > 20:
        return jsonify({"error": "昵称最长20字"}), 400
    result = _api_svc.register_user(nickname, password)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/v1/login", methods=["POST"])
def tts_api_login():
    """用户登录，返回 API Key"""
    data = request.get_json(force=True, silent=True) or {}
    nickname = (data.get("nickname") or "").strip()
    password = data.get("password", "")
    if not nickname or not password:
        return jsonify({"error": "请输入昵称和密码"}), 400
    result = _api_svc.login_user(nickname, password)
    if "error" in result:
        return jsonify(result), 401
    return jsonify(result)


@app.route("/api/v1/balance", methods=["GET"])
def tts_api_balance():
    """查询余额"""
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not api_key:
        api_key = request.args.get("key", "").strip()
    if not api_key:
        return jsonify({"error": "缺少 API Key"}), 401
    info = _api_svc.get_user_balance(api_key)
    if not info:
        return jsonify({"error": "API Key 无效"}), 401
    return jsonify(info)


@app.route("/api/v1/redeem", methods=["POST"])
def tts_api_redeem():
    """卡密充值"""
    data = request.get_json(force=True, silent=True) or {}
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not api_key:
        api_key = (data.get("api_key") or "").strip()
    card_code = (data.get("card_code") or data.get("code") or "").strip()
    if not api_key:
        return jsonify({"error": "缺少 API Key"}), 401
    if not card_code:
        return jsonify({"error": "缺少卡密"}), 400
    result = _api_svc.redeem_card(api_key, card_code)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/v1/tts", methods=["POST"])
def tts_api_synthesize():
    """TTS 合成接口（计费）"""
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not api_key:
        return jsonify({"error": "缺少 API Key，请在 Header 中传 Authorization: Bearer YOUR_KEY"}), 401

    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    model = data.get("model", "speech-2.8-hd")
    voice_id = data.get("voice_id")

    if not text:
        return jsonify({"error": "缺少 text 参数"}), 400

    result = _api_svc.call_tts(api_key, text, model, voice_id)
    if "error" in result:
        status = 402 if "余额不足" in result["error"] else 400
        return jsonify(result), status

    # 返回音频
    audio_bytes = result.pop("audio_bytes")
    resp = Response(audio_bytes, mimetype="audio/mpeg")
    resp.headers["X-Chars"] = str(result["chars"])
    resp.headers["X-Cost"] = str(result["cost_yuan"])
    resp.headers["X-Balance"] = str(result["balance_yuan"])
    return resp


@app.route("/api/v1/pricing", methods=["GET"])
def tts_api_pricing():
    """查看价格表"""
    return jsonify({
        "pricing": [
            {"model": "speech-2.8-hd", "price_yuan_per_10k_chars": 3.5, "desc": "高清·支持语气词"},
            {"model": "speech-2.8-turbo", "price_yuan_per_10k_chars": 2.0, "desc": "快速·支持语气词"},
        ],
        "note": "按字符数计费，价格与 MiniMax 官方一致"
    })


@app.route("/api/v1/usage", methods=["GET"])
def tts_api_usage():
    """查看自己的调用记录"""
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not api_key:
        api_key = request.args.get("key", "").strip()
    if not api_key:
        return jsonify({"error": "缺少 API Key"}), 401
    if not _api_svc.get_user(api_key):
        return jsonify({"error": "API Key 无效"}), 401
    limit = min(50, max(5, int(request.args.get("limit", 20))))
    return jsonify({"records": _api_svc.get_user_usage(api_key, limit)})


# ── 管理面板：TTS API 管理 ──

@app.route("/api/admin/tts-users", methods=["GET"])
def admin_tts_users():
    """查看所有 TTS API 用户"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    return jsonify({"users": _api_svc.admin_list_users()})


@app.route("/api/admin/tts-cards", methods=["GET"])
def admin_tts_cards():
    """查看所有卡密"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    only_unused = request.args.get("unused", "") == "1"
    return jsonify({"cards": _api_svc.list_cards(only_unused)})


@app.route("/api/admin/tts-cards/generate", methods=["POST"])
def admin_tts_cards_generate():
    """生成卡密"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    count = min(50, max(1, int(data.get("count", 10))))
    amount_yuan = float(data.get("amount", 5))
    amount_li = int(amount_yuan * 1000)
    cards = _api_svc.generate_cards(count, amount_li)
    return jsonify({"cards": cards, "count": len(cards)})


@app.route("/api/admin/tts-users/adjust", methods=["POST"])
def admin_tts_users_adjust():
    """手动调整用户余额"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    api_key = (data.get("api_key") or "").strip()
    amount_yuan = float(data.get("amount", 0))
    amount_li = int(amount_yuan * 1000)
    if not api_key:
        return jsonify({"error": "缺少 api_key"}), 400
    result = _api_svc.admin_adjust_balance(api_key, amount_li)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/admin/tts-users/delete", methods=["POST"])
def admin_tts_users_delete():
    """删除用户"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    api_key = (data.get("api_key") or "").strip()
    result = _api_svc.admin_delete_user(api_key)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/admin/tts-usage", methods=["GET"])
def admin_tts_usage():
    """查看使用记录"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    limit = min(100, max(10, int(request.args.get("limit", 50))))
    return jsonify({"records": _api_svc.admin_get_usage(limit)})


@app.route("/api/registration-status", methods=["GET"])
def registration_status_public():
    """公开接口：返回注册是否开放"""
    return jsonify({"enabled": _registration_enabled})


@app.route("/api/admin/registration", methods=["GET"])
def admin_registration_status():
    """获取注册开关状态"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    return jsonify({"enabled": _registration_enabled})


@app.route("/api/admin/registration/toggle", methods=["POST"])
def admin_registration_toggle():
    """切换注册开关"""
    global _registration_enabled
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    with _registration_lock:
        _registration_enabled = bool(data.get("enabled", not _registration_enabled))
    # 持久化到 GitHub
    _save_registration_to_github()
    return jsonify({"ok": True, "enabled": _registration_enabled})


# =====================================================================
# 充值窗口管理
# =====================================================================

@app.route("/api/admin/recharge", methods=["GET"])
def admin_recharge_status():
    """查看充值窗口状态"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    with _recharge_lock:
        # 自动关闭检查
        if _recharge_window["open"] and _recharge_window["close_at"] > 0:
            if time.time() > _recharge_window["close_at"]:
                _recharge_window["open"] = False
        return jsonify(dict(_recharge_window))


@app.route("/api/admin/recharge/toggle", methods=["POST"])
def admin_recharge_toggle():
    """开启/关闭充值窗口"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    with _recharge_lock:
        _recharge_window["open"] = bool(data.get("open", not _recharge_window["open"]))
        # 自动关闭时间（分钟），0=不自动关
        duration_min = int(data.get("duration_min", 60))
        if _recharge_window["open"] and duration_min > 0:
            _recharge_window["close_at"] = time.time() + duration_min * 60
        else:
            _recharge_window["close_at"] = 0
        if "qr_url" in data:
            _recharge_window["qr_url"] = data["qr_url"]
        if "note" in data:
            _recharge_window["note"] = data["note"]
    return jsonify({"ok": True, **_recharge_window})


# =====================================================================
# 加密状态 & 数据迁移
# =====================================================================

@app.route("/api/admin/encryption-status", methods=["GET"])
def admin_encryption_status():
    """查看加密状态"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    from crypto_utils import _get_key, ENCRYPTED_FILES
    try:
        from config_store import get_with_env
        has_key = bool(get_with_env("DATA_ENCRYPTION_KEY", "data", "encryption_key", default=""))
    except Exception:
        has_key = bool(os.environ.get("DATA_ENCRYPTION_KEY", ""))
    return jsonify({
        "has_key": has_key,
        "encrypted_files": list(ENCRYPTED_FILES),
        "note": "新写入的数据会自动加密。旧数据需要通过迁移接口重新加密。"
    })


@app.route("/api/admin/migrate-encrypt", methods=["POST"])
def admin_migrate_encrypt():
    """将所有现有明文数据重新加密保存（一次性迁移）"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403

    from crypto_utils import ENCRYPTED_FILES, _should_encrypt
    migrated = []
    errors = []

    # 迁移顶层文件
    for fname in ENCRYPTED_FILES:
        try:
            content, sha = _gh_get_file(fname)  # 读取时自动解密（或明文）
            if content:
                # 重新写入（写入时自动加密）
                _gh_shas[fname] = sha
                ok = _gh_put_file(fname, content, sha)
                if ok:
                    migrated.append(fname)
                    # 更新 sha
                    _, new_sha = _gh_get_file(fname)
                    if new_sha:
                        _gh_shas[fname] = new_sha
                else:
                    errors.append(f"{fname}: 写入失败")
        except Exception as e:
            errors.append(f"{fname}: {str(e)}")

    return jsonify({
        "migrated": migrated,
        "errors": errors,
        "note": "通话记录(call_logs/)会在下次访问/保存时自动加密，无需单独迁移"
    })


@app.route("/api/admin/recharge/config", methods=["POST"])
def admin_recharge_config():
    """设置充值窗口配置（收款码、提示语）"""
    if not _check_admin(request):
        return jsonify({"error": "未授权"}), 403
    data = request.get_json(force=True, silent=True) or {}
    with _recharge_lock:
        if "qr_url" in data:
            _recharge_window["qr_url"] = data["qr_url"]
        if "note" in data:
            _recharge_window["note"] = data["note"]
    return jsonify({"ok": True})


@app.route("/api/recharge/status", methods=["GET"])
def recharge_status():
    """用户端：查看充值窗口是否开放"""
    with _recharge_lock:
        # 自动关闭检查
        if _recharge_window["open"] and _recharge_window["close_at"] > 0:
            if time.time() > _recharge_window["close_at"]:
                _recharge_window["open"] = False
        remaining_min = 0
        if _recharge_window["open"] and _recharge_window["close_at"] > 0:
            remaining_min = max(0, int((_recharge_window["close_at"] - time.time()) / 60))
        return jsonify({
            "open": _recharge_window["open"],
            "qr_url": _recharge_window["qr_url"] if _recharge_window["open"] else "",
            "note": _recharge_window["note"] if _recharge_window["open"] else "",
            "remaining_min": remaining_min,
        })


# =====================================================================
# 通话记录保存 & 历史加载
# =====================================================================


def _resolve_user_key(token):
    """根据 token 确定用户存储 key：账号用户用 api_key，否则用 session_id 或 token 本身"""
    acct = _account_tokens.get(token)
    if acct:
        return acct["api_key"]
    return None  # 调用方需要自己决定 fallback


def _gh_put_file_binary(filename, content_bytes, sha=None):
    """写二进制文件到 GitHub（自动加密音频等敏感文件）"""
    if not _GITHUB_TOKEN:
        return False
    import requests
    try:
        # 加密音频/二进制文件
        if filename.startswith("call_logs/"):
            content_bytes = encrypt_binary(content_bytes)
        body = {
            "message": f"sync {filename}",
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        resp = requests.put(f"{_GITHUB_DATA_API}/{filename}",
                            headers=_gh_headers(), json=body, timeout=30)
        if resp.status_code in (200, 201):
            logger.info("GitHub 同步二进制 %s 成功", filename)
            return True
        else:
            logger.error("GitHub 同步二进制 %s 失败: %d %s", filename,
                         resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.error("GitHub 同步二进制 %s 异常: %s", filename, e)
        return False


def _gh_get_file_binary(filename):
    """从 GitHub 获取二进制文件内容，自动解密。返回 bytes 或 None
    优先使用 Raw URL 下载（支持大文件），fallback 到 Contents API。
    """
    if not _GITHUB_TOKEN:
        return None
    import requests
    try:
        # 优先用 Raw URL（无 1MB 限制）
        raw_url = f"https://raw.githubusercontent.com/{_GITHUB_DATA_REPO}/main/{filename}"
        resp = requests.get(raw_url, headers=_gh_headers(), timeout=30)
        if resp.status_code == 200:
            return decrypt_binary(resp.content)
        # fallback: Contents API（仅适用于 <1MB 文件）
        resp2 = requests.get(f"{_GITHUB_DATA_API}/{filename}",
                             headers=_gh_headers(), timeout=15)
        if resp2.status_code == 200:
            data = resp2.json()
            if data.get("content"):
                raw = base64.b64decode(data["content"])
                return decrypt_binary(raw)
        return None
    except Exception as e:
        logger.error("GitHub 读取二进制 %s 失败: %s", filename, e)
        return None


def _gh_list_dir(dirpath):
    """列出 GitHub 目录下的文件，返回 [{name, path, type, size, sha}] 或 []"""
    if not _GITHUB_TOKEN:
        return []
    import requests
    try:
        resp = requests.get(f"{_GITHUB_DATA_API}/{dirpath}",
                            headers=_gh_headers(), timeout=15)
        if resp.status_code == 200:
            items = resp.json()
            if isinstance(items, list):
                return items
        return []
    except Exception as e:
        logger.error("GitHub 列目录 %s 失败: %s", dirpath, e)
        return []


# --- 通话记录缓存 ---
_call_history_cache: dict = {}   # user_key → {"calls": [...], "ts": float}
_CALL_HISTORY_TTL = 120          # 缓存 2 分钟


def _gh_list_dir_via_tree(dirpath):
    """用 Git Tree API 列出目录文件（不受 1000 文件限制），返回 [{path, sha, size}] 或 []"""
    if not _GITHUB_TOKEN:
        return []
    import requests
    try:
        # 先获取 default branch 的最新 commit SHA
        resp = requests.get(
            f"https://api.github.com/repos/{_GITHUB_DATA_REPO}/git/ref/heads/main",
            headers=_gh_headers(), timeout=10)
        if resp.status_code != 200:
            logger.warning("Git ref 查询失败: %d", resp.status_code)
            return _gh_list_dir(dirpath)  # fallback
        commit_sha = resp.json().get("object", {}).get("sha", "")
        if not commit_sha:
            return _gh_list_dir(dirpath)

        # 用 recursive tree 获取所有文件
        resp2 = requests.get(
            f"https://api.github.com/repos/{_GITHUB_DATA_REPO}/git/trees/{commit_sha}",
            headers=_gh_headers(), params={"recursive": "1"}, timeout=30)
        if resp2.status_code != 200:
            logger.warning("Git tree 查询失败: %d", resp2.status_code)
            return _gh_list_dir(dirpath)

        tree_data = resp2.json()
        tree = tree_data.get("tree", [])
        # 过滤出指定目录下的文件
        prefix = dirpath.rstrip("/") + "/"
        result = []
        for item in tree:
            if item.get("type") == "blob" and item.get("path", "").startswith(prefix):
                fname = item["path"][len(prefix):]
                if "/" not in fname:  # 只取直接子文件
                    result.append({"name": fname, "path": item["path"],
                                   "sha": item.get("sha"), "size": item.get("size", 0)})
        return result
    except Exception as e:
        logger.error("Git tree 列目录 %s 失败: %s", dirpath, e)
        return _gh_list_dir(dirpath)  # fallback


def _concat_audio_chunks(audio_chunks):
    """
    用 ffmpeg 把 audio_chunks 拼接成一个完整的 mp3 文件。
    返回 mp3 bytes 或 None（失败时）。
    """
    if not audio_chunks:
        return None

    tmpdir = tempfile.mkdtemp(prefix="vc_audio_")
    wav_files = []

    try:
        # 1. 每个 chunk 解码 → 临时文件 → 转 wav
        for i, chunk in enumerate(audio_chunks):
            audio_b64 = chunk.get("audio_b64", "")
            fmt = chunk.get("format", "webm")
            if not audio_b64:
                continue

            # 解码为临时文件
            ext = fmt if fmt in ("webm", "mp3", "wav", "ogg") else "webm"
            src_path = os.path.join(tmpdir, f"chunk_{i:04d}.{ext}")
            with open(src_path, "wb") as f:
                f.write(base64.b64decode(audio_b64))

            # 转为 wav (16kHz mono)
            wav_path = os.path.join(tmpdir, f"chunk_{i:04d}.wav")
            cmd = [
                "ffmpeg", "-y", "-i", src_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", wav_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                logger.error("ffmpeg 转换 chunk %d 失败: %s", i, result.stderr[:200])
                continue
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
                wav_files.append(wav_path)

        if not wav_files:
            return None

        # 1.5 在需要的位置插入静音段（文字输入的轮次）
        final_wav_files = []
        for j, wp in enumerate(wav_files):
            # 检查是否需要在前面插入静音
            if j > 0:
                curr_chunk = audio_chunks[j] if j < len(audio_chunks) else {}
                prev_chunk = audio_chunks[j-1] if j-1 < len(audio_chunks) else {}
                curr_ts = curr_chunk.get("timestamp", 0)
                prev_ts = prev_chunk.get("timestamp", 0)
                # 如果当前是 AI 音频且前一个也是 AI 音频（中间没有 user），插入静音
                if curr_chunk.get("type") == "ai" and prev_chunk.get("type") == "ai":
                    gap_seconds = min(5.0, max(0.5, (curr_ts - prev_ts) / 1000.0)) if curr_ts > prev_ts else 1.0
                    silence_path = os.path.join(tmpdir, f"silence_{j:04d}.wav")
                    silence_cmd = [
                        "ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"anullsrc=r=16000:cl=mono",
                        "-t", str(round(gap_seconds, 1)),
                        "-ar", "16000", "-ac", "1", silence_path
                    ]
                    silence_result = subprocess.run(silence_cmd, capture_output=True, timeout=10)
                    if silence_result.returncode == 0 and os.path.exists(silence_path):
                        final_wav_files.append(silence_path)
            final_wav_files.append(wp)
        wav_files = final_wav_files

        # 2. 生成 concat 列表文件
        list_path = os.path.join(tmpdir, "concat_list.txt")
        with open(list_path, "w") as f:
            for wp in wav_files:
                f.write(f"file '{wp}'\n")

        # 3. 拼接并输出 mp3
        output_path = os.path.join(tmpdir, "output.mp3")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-codec:a", "libmp3lame", "-b:a", "128k",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            logger.error("ffmpeg 拼接失败: %s", result.stderr[:200])
            return None

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            with open(output_path, "rb") as f:
                return f.read()
        return None

    except Exception as e:
        logger.error("音频拼接异常: %s", e)
        return None
    finally:
        # 清理临时目录
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def _generate_call_summary(history, custom_api=None, req_model=None):
    """生成通话小结（非流式），返回一段口语化收尾消息"""
    if not history:
        return "这通电话太短了，下次多聊会儿。"

    cfg = load_config() if load_config else {}
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"

    conversation_lines = []
    for msg in history:
        role = "她" if msg["role"] == "user" else character_name
        conversation_lines.append(f"{role}: {msg['content']}")
    conversation_text = "\n".join(conversation_lines)

    summary_prompt = f"""{_builtin_prompt}

---

你是{character_name}。刚刚与用户结束了一通语音通话。挂断后，你打算再发一条50字左右的消息。
要求：
- 用第一人称视角
- 语气自然亲切，像发给对方的微信消息
- 提到通话中的关键内容或有趣的点
- 结尾可以带一句温暖的话
- 只输出消息内容，不要加引号或前缀

通话记录：
{conversation_text}"""

    messages = [{"role": "user", "content": summary_prompt}]

    try:
        stream_kwargs = {"messages": messages}
        if custom_api:
            stream_kwargs["custom_api"] = custom_api
            if custom_api.get("model"):
                stream_kwargs["model"] = custom_api["model"]
            elif req_model:
                stream_kwargs["model"] = req_model
        elif req_model:
            stream_kwargs["model"] = req_model

        full_text = ""
        for delta in stream_chat(**stream_kwargs):
            full_text += delta

        return full_text.strip()
    except Exception as e:
        logger.exception("生成通话小结失败")
        return "信号不太好，下次再聊。"


def _generate_call_memory(history, custom_api=None, req_model=None):
    """生成记忆总结（非流式），返回记忆文本——以第一人称视角的情感记忆"""
    if not history:
        return ""

    cfg = load_config() if load_config else {}
    character_name = (os.environ.get("CHARACTER_NAME") or cfg.get("character_name") or "AI助手").strip() or "AI助手"

    conversation_lines = []
    # 从历史消息中提取用户称呼
    userid = ""
    for msg in history:
        content = msg.get("content", "")
        # 尝试从时间戳格式中提取纯文本
        if msg["role"] == "user":
            role = "她"
            # 检查是否有用户称呼标记
            if not userid and "用户称呼" in content:
                pass  # 不从消息中提取
        else:
            role = character_name
        conversation_lines.append(f"{role}: {content}")
    conversation_text = "\n".join(conversation_lines)

    # 构建记忆提示词（含人设）
    persona = _builtin_prompt if _builtin_prompt else ""
    memory_prompt = f"""{persona}

---

你是{character_name}，刚刚与用户结束了一轮语音通话，请对以下通话记录进行深度记忆总结。

你的任务是提取这次通话中个人的感受，值得长期记住的内容，生成"记忆"与"备忘录"两个板块。

记忆部分以第一人称口吻，模仿日记记录风格，高度口语化，内容充满你的主观情感、想法与评价。包括但不限于：
- 通话前后在做什么，这通电话对你有什么影响，这段聊天的整体感受（例如本来在开会有点烦，听她聊自己的生活放松了许多）
- 重要的事实信息（对方通话时在做什么，与你聊了什么，提到的人、地点、事件、计划、偏好、习惯）
- 彼此的情绪感知（对方的情绪变化、开心/难过/生气的原因，你的情绪感受及原因）
- 关系动态（两人之间发生了什么，是否有约定、承诺）

备忘录部分用分点式记录需要跟进的事项，例如：
- 约定和承诺（答应对方做的事）
- 待办事项（需要记住或执行的事）
- 重要日期（对方提到的生日、纪念日、考试等）

要求：
- 用第一人称记录，带有主观情感色彩
- 记录有价值的信息与情感，重要内容可以复述，但非必要必须提炼概括
- 100字至500字之间
- 如果没有需要备忘的事项，可以省略备忘录板块

通话记录：
{conversation_text}"""

    messages = [{"role": "user", "content": memory_prompt}]

    try:
        stream_kwargs = {"messages": messages}
        if custom_api:
            stream_kwargs["custom_api"] = custom_api
            if custom_api.get("model"):
                stream_kwargs["model"] = custom_api["model"]
            elif req_model:
                stream_kwargs["model"] = req_model
        elif req_model:
            stream_kwargs["model"] = req_model

        full_text = ""
        for delta in stream_chat(**stream_kwargs):
            full_text += delta
        return full_text.strip()
    except Exception as e:
        logger.exception("生成记忆总结失败")
        raise Exception(f"记忆生成失败: {e}")


def _get_user_call_summaries(user_key, count):
    """获取用户最近 N 次通话的总结，返回 [{start_time, summary}]"""
    items = _gh_list_dir(f"call_logs/{user_key}")
    # 只要 JSON 文件
    json_files = [it for it in items if it.get("name", "").endswith(".json")]
    # 按文件名（时间戳）倒序
    json_files.sort(key=lambda x: x.get("name", ""), reverse=True)
    json_files = json_files[:count]

    summaries = []
    for jf in json_files:
        content, _ = _gh_get_file(f"call_logs/{user_key}/{jf['name']}")
        if content:
            try:
                data = json.loads(content)
                # 优先使用 memory（记忆总结），没有则 fallback 到 summary（通话小结）
                mem = data.get("memory", "") or data.get("summary", "")
                if mem:
                    summaries.append({
                        "start_time": data.get("start_time", ""),
                        "summary": mem,
                    })
            except Exception:
                pass
    return summaries


@app.route("/api/end-call", methods=["POST"])
def api_end_call():
    """通话结束：保存通话记录 + 音频拼接 + 上传 GitHub"""
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权或会话已过期"}), 401

    session_id = data.get("session_id") or token
    audio_chunks = data.get("audio_chunks") or []
    custom_api = data.get("custom_api")
    req_model = data.get("model")

    # 确定 user_key
    acct = _account_tokens.get(token)
    if acct:
        user_key = acct["api_key"]
    else:
        user_key = session_id

    # 获取对话历史
    with _sessions_lock:
        history = list(_sessions.get(session_id, []))

    # 生成通话小结
    summary = _generate_call_summary(history, custom_api, req_model)

    # 如果请求了自动生成记忆（前端传 auto_memory=true）
    memory = ""
    if data.get("auto_memory"):
        try:
            memory = _generate_call_memory(history, custom_api, req_model)
        except Exception:
            memory = ""

    # 提前生成时间戳，分段保存和最终记录共用
    call_timestamp = str(int(time.time()))

    # 分段保存音频片段到 GitHub
    audio_segments_meta = []
    for i, chunk in enumerate(audio_chunks):
        chunk_b64 = chunk.get("audio_b64", "")
        chunk_fmt = chunk.get("format", "webm")
        chunk_type = chunk.get("type", "unknown")  # "user" 或 "ai"
        chunk_ts = chunk.get("timestamp", 0)
        if not chunk_b64:
            continue
        ext = chunk_fmt if chunk_fmt in ("webm", "mp3", "wav", "ogg") else "webm"
        chunk_path = f"call_logs/{user_key}/{call_timestamp}_chunk_{i:03d}_{chunk_type}.{ext}"
        try:
            chunk_bytes = base64.b64decode(chunk_b64)
            # 只保存小于 5MB 的片段
            if len(chunk_bytes) < 5 * 1024 * 1024:
                _gh_put_file_binary(chunk_path, chunk_bytes)
                seg_meta = {
                    "index": i,
                    "type": chunk_type,
                    "format": ext,
                    "timestamp": chunk_ts,
                    "path": chunk_path,
                    "size": len(chunk_bytes),
                }
                # 透传前端附带的 msg_index（assistant 消息序号）
                if "msg_index" in chunk:
                    seg_meta["msg_index"] = chunk["msg_index"]
                audio_segments_meta.append(seg_meta)
        except Exception as e:
            logger.error("保存音频片段 %d 失败: %s", i, e)

    # 音频拼接
    audio_bytes = None
    has_audio = False
    try:
        audio_bytes = _concat_audio_chunks(audio_chunks)
        if audio_bytes:
            has_audio = True
    except Exception as e:
        logger.error("音频拼接失败，跳过: %s", e)

    # 准备通话记录
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    # 计算时长：如果有历史，用第一条和最后一条的大致时间；否则用 audio_chunks 的时间戳
    duration = 0
    if audio_chunks:
        timestamps = [c.get("timestamp", 0) for c in audio_chunks if c.get("timestamp")]
        if len(timestamps) >= 2:
            duration = int((max(timestamps) - min(timestamps)) / 1000)  # 前端 Date.now() 是毫秒

    call_record = {
        "session_id": session_id,
        "start_time": now_iso,
        "duration": duration,
        "messages": history,
        "summary": summary,
        "memory": memory,
        "has_audio": has_audio,
        "audio_segments": audio_segments_meta,
    }

    # 同步保存到 GitHub
    json_path = f"call_logs/{user_key}/{call_timestamp}.json"
    save_ok = _gh_put_file(json_path, json.dumps(call_record, ensure_ascii=False, indent=2))

    # 清除通话记录缓存，下次查询拿最新数据
    _call_history_cache.pop(user_key, None)

    # 音频上传
    audio_url = None
    if has_audio and audio_bytes:
        # 检查大小：50MB 限制
        if len(audio_bytes) < 50 * 1024 * 1024:
            mp3_path = f"call_logs/{user_key}/{call_timestamp}.mp3"
            ok = _gh_put_file_binary(mp3_path, audio_bytes)
            if ok:
                audio_url = f"/api/call-audio/{user_key}/{call_timestamp}"
            else:
                has_audio = False
        else:
            logger.warning("音频文件过大 (%d bytes)，跳过上传", len(audio_bytes))
            has_audio = False

    # 清理 session 内存数据
    with _sessions_lock:
        _sessions.pop(session_id, None)

    result = {
        "summary": summary,
        "call_id": call_timestamp,
        "has_audio": has_audio,
        "save_ok": save_ok,
    }
    if not save_ok:
        result["save_error"] = "通话记录保存失败，请稍后重试"
    if memory:
        result["memory"] = memory
    if audio_url:
        result["audio_url"] = audio_url

    return jsonify(result)


@app.route("/api/call-audio/<user_key>/<call_id>", methods=["GET"])
def api_call_audio(user_key, call_id):
    """获取通话录音 mp3"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    mp3_path = f"call_logs/{user_key}/{call_id}.mp3"
    audio_data = _gh_get_file_binary(mp3_path)
    if not audio_data:
        return jsonify({"error": "录音不存在"}), 404

    resp = Response(audio_data, mimetype="audio/mpeg")
    resp.headers["Content-Disposition"] = f'attachment; filename="call_{call_id}.mp3"'
    return resp


@app.route("/api/audio-segment", methods=["GET"])
def api_audio_segment():
    """获取单段音频片段"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    path = request.args.get("path", "").strip()
    if not path or ".." in path or not path.startswith("call_logs/"):
        return jsonify({"error": "无效路径"}), 400

    audio_data = _gh_get_file_binary(path)
    if not audio_data:
        return jsonify({"error": "音频不存在"}), 404

    # 根据扩展名设置 MIME
    if path.endswith(".mp3"):
        mime = "audio/mpeg"
    elif path.endswith(".webm"):
        mime = "audio/webm"
    elif path.endswith(".wav"):
        mime = "audio/wav"
    else:
        mime = "application/octet-stream"

    return Response(audio_data, mimetype=mime)


@app.route("/api/call-history", methods=["GET"])
def api_call_history():
    """获取通话历史列表"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    # 确定 user_key
    acct = _account_tokens.get(token)
    if acct:
        user_key = acct["api_key"]
    else:
        # 对于非账号用户，无法确定 user_key，返回空
        return jsonify({"calls": []})

    force_refresh = request.args.get("refresh") == "1"

    # 检查缓存
    now = time.time()
    cached = _call_history_cache.get(user_key)
    if cached and not force_refresh and (now - cached["ts"]) < _CALL_HISTORY_TTL:
        return jsonify({"calls": cached["calls"]})

    # 用 Git Tree API 获取目录（支持大量文件）
    items = _gh_list_dir_via_tree(f"call_logs/{user_key}")
    json_files = [it for it in items if it.get("name", "").endswith(".json")]

    # 按文件名（时间戳）倒序
    json_files.sort(key=lambda x: x.get("name", ""), reverse=True)
    json_files = json_files[:20]

    calls = []
    for jf in json_files:
        content, _ = _gh_get_file(f"call_logs/{user_key}/{jf['name']}")
        if content:
            try:
                data = json.loads(content)
                call_id = jf["name"].replace(".json", "")
                messages = data.get("messages", [])
                # 计算对话轮次（一问一答算一轮）
                rounds = len([m for m in messages if m.get("role") == "user"])
                calls.append({
                    "call_id": call_id,
                    "start_time": data.get("start_time", ""),
                    "duration": data.get("duration", 0),
                    "summary": data.get("summary", ""),
                    "memory": data.get("memory", ""),
                    "has_audio": data.get("has_audio", False),
                    "rounds": rounds,
                    "user_key": user_key,
                })
            except Exception as e:
                logger.error("解析通话记录 %s 失败: %s", jf["name"], e)

    # 写入缓存
    _call_history_cache[user_key] = {"calls": calls, "ts": now}

    return jsonify({"calls": calls})


# =====================================================================
# 通话记录详情 / 编辑 / 删除
# =====================================================================


def _gh_delete_file(filepath, sha):
    """删除 GitHub 上的文件"""
    if not _GITHUB_TOKEN:
        return False
    import requests
    try:
        resp = requests.delete(
            f"{_GITHUB_DATA_API}/{filepath}",
            headers=_gh_headers(),
            json={"message": f"delete {filepath}", "sha": sha},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("GitHub 删除 %s 成功", filepath)
            return True
        else:
            logger.error("GitHub 删除 %s 失败: %d %s", filepath,
                         resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.error("GitHub 删除 %s 异常: %s", filepath, e)
        return False


@app.route("/api/call-detail/<call_id>", methods=["GET"])
def api_call_detail_get(call_id):
    """获取通话记录详情（含完整 messages）"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未授权"}), 401
    user_key = acct["api_key"]

    content, _ = _gh_get_file(f"call_logs/{user_key}/{call_id}.json")
    if not content:
        return jsonify({"error": "记录不存在"}), 404

    try:
        data = json.loads(content)
        data["user_key"] = user_key
        return jsonify(data)
    except Exception:
        return jsonify({"error": "记录不存在"}), 404


@app.route("/api/call-detail/<call_id>", methods=["PUT"])
def api_call_detail_update(call_id):
    """编辑通话记录（messages / summary）"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未授权"}), 401
    user_key = acct["api_key"]

    filepath = f"call_logs/{user_key}/{call_id}.json"
    content, sha = _gh_get_file(filepath)
    if not content:
        return jsonify({"error": "记录不存在"}), 404

    try:
        record = json.loads(content)
    except Exception:
        return jsonify({"error": "记录不存在"}), 404

    body = request.get_json(force=True, silent=True) or {}
    if "messages" in body:
        record["messages"] = body["messages"]
    if "summary" in body:
        record["summary"] = body["summary"]

    _gh_put_file(filepath, json.dumps(record, ensure_ascii=False, indent=2), sha)
    return jsonify({"status": "saved"})


@app.route("/api/call-detail/<call_id>", methods=["DELETE"])
def api_call_detail_delete(call_id):
    """删除通话记录及对应录音"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "未授权"}), 401
    user_key = acct["api_key"]

    # 删除 JSON 记录
    json_path = f"call_logs/{user_key}/{call_id}.json"
    content, json_sha = _gh_get_file(json_path)
    if not content or not json_sha:
        return jsonify({"error": "记录不存在"}), 404

    _gh_delete_file(json_path, json_sha)

    # 清除通话记录缓存
    _call_history_cache.pop(user_key, None)

    # 尝试删除对应的 mp3 文件（不存在也不报错）
    mp3_path = f"call_logs/{user_key}/{call_id}.mp3"
    mp3_content, mp3_sha = _gh_get_file(mp3_path)
    if mp3_sha:
        _gh_delete_file(mp3_path, mp3_sha)

    return jsonify({"status": "deleted"})


@app.route("/api/generate-memory/<call_id>", methods=["POST"])
def api_generate_memory(call_id):
    """手动生成/重新生成通话记忆总结"""
    token = request.args.get("token", "").strip()
    if not _check_token(token):
        return jsonify({"error": "未授权"}), 401

    acct = _account_tokens.get(token)
    if not acct:
        return jsonify({"error": "需要账号登录"}), 401
    user_key = acct["api_key"]

    # 读取通话记录
    json_path = f"call_logs/{user_key}/{call_id}.json"
    content, sha = _gh_get_file(json_path)
    if not content:
        return jsonify({"error": "记录不存在"}), 404

    try:
        data = json.loads(content)
    except Exception:
        return jsonify({"error": "记录格式错误"}), 500

    history = data.get("messages", [])
    if not history:
        return jsonify({"error": "通话记录为空"}), 400

    # 读取请求中的自定义 API
    req_data = request.get_json(force=True, silent=True) or {}
    custom_api = req_data.get("custom_api")
    req_model = req_data.get("model")

    # 生成记忆
    try:
        memory = _generate_call_memory(history, custom_api, req_model)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not memory:
        return jsonify({"error": "记忆生成返回空，请重试"}), 500

    # 更新记录
    data["memory"] = memory
    _gh_put_file(json_path, json.dumps(data, ensure_ascii=False, indent=2), sha)

    return jsonify({"memory": memory})


# =====================================================================
# 启动
# =====================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False)
