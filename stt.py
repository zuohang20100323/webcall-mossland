# stt.py — 语音识别模块（GLM-ASR 首选 + Groq Whisper 降级）

import os
import re
import time
import tempfile
import subprocess
import requests
import logging

from utils import is_whisper_hallucination

logger = logging.getLogger(__name__)

# 浏览器伪装 User-Agent
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# ── 智谱 GLM-ASR 配置 ──
_ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"

# ── Groq Whisper 配置 ──
_GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# ── 阿里 DashScope Fun-ASR 配置（非流式同步） ──
_DASHSCOPE_ASR_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"


def _clean_glm_tags(text: str) -> str:
    """清理 GLM-ASR 返回文本中的特殊标签（情感、事件等）"""
    # 移除 <|Speech|> <|/Speech|> <|NEUTRAL|> 等标签
    text = re.sub(r'<\|/?[A-Za-z_0-9]+\|>', '', text)
    # 合并多余空格
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _recognize_glm(audio_path: str, audio_ext: str, audio_mime: str) -> dict | None:
    """
    使用智谱 GLM-ASR 进行语音识别。
    返回 dict: 成功 {text, time, engine} 或失败 {error, engine, fallback}
    返回 None 仅当未配置 API Key。
    """
    # 优先使用管理面板配置的 key
    api_key = ""
    try:
        from llm import get_stt_defaults
        api_key = get_stt_defaults().get("zhipu_api_key", "")
    except Exception:
        pass
    if not api_key:
        api_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not api_key:
        # 未配置时返回 None，让上层决定是否降级/提示
        return None

    start_time = time.time()

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    try:
        filename = "audio" + audio_ext
        with open(audio_path, "rb") as f:
            files = {
                "file": (filename, f, audio_mime),
            }
            data = {
                "model": "glm-asr",
            }

            resp = requests.post(
                _ZHIPU_API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=30,
            )

        elapsed = round(time.time() - start_time, 2)

        if resp.status_code != 200:
            err_msg = f"GLM-ASR HTTP {resp.status_code}: {resp.text[:200]}"
            logger.error(err_msg)
            return {"error": err_msg, "engine": "glm-asr", "fallback": True}

        try:
            result = resp.json()
        except Exception:
            logger.error("GLM-ASR 返回了无效的 JSON")
            return {"error": "GLM-ASR 返回无效 JSON", "engine": "glm-asr", "fallback": True}

        # GLM-ASR 返回格式: {"text": "识别结果", ...}
        text = result.get("text", "").strip()

        # 清理特殊标签
        text = _clean_glm_tags(text)

        # 幻觉过滤
        if is_whisper_hallucination(text):
            return {"text": "", "time": elapsed, "engine": "glm-asr"}

        return {"text": text, "time": elapsed, "engine": "glm-asr"}

    except requests.exceptions.Timeout:
        logger.error("GLM-ASR 超时")
        return {"error": "GLM-ASR 超时", "engine": "glm-asr", "fallback": True}
    except requests.exceptions.ConnectionError:
        logger.error("GLM-ASR 连接失败")
        return {"error": "GLM-ASR 连接失败", "engine": "glm-asr", "fallback": True}
    except Exception as e:
        logger.error("GLM-ASR 异常: %s", e)
        return {"error": f"GLM-ASR 异常: {e}", "engine": "glm-asr", "fallback": True}


def _recognize_dashscope(audio_path: str, audio_ext: str, audio_mime: str) -> dict | None:
    """
    使用阿里 DashScope Fun-ASR 非流式同步识别。
    Fun-ASR 的 Recognition.call() 需要 dashscope SDK，
    这里用纯 REST 方式调用实时识别的 WebSocket 不方便，
    所以走录音文件异步接口——但它需要公网 URL。
    
    对于 HF Space 部署场景，音频文件没有公网 URL，
    所以这个引擎暂时不启用，留作未来扩展。
    """
    return None  # 暂不启用


def _recognize_groq(audio_path: str, audio_ext: str, audio_mime: str) -> dict | None:
    """
    使用 Groq Whisper 进行语音识别（降级方案）。
    """
    # 优先使用管理面板配置的 key
    api_key = ""
    try:
        from llm import get_stt_defaults
        api_key = get_stt_defaults().get("groq_api_key", "")
    except Exception:
        pass
    if not api_key:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        api_key = ""  # 需要配置 GROQ_API_KEY 环境变量
    if not api_key:
        return None

    start_time = time.time()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": _USER_AGENT,
    }

    try:
        filename = "audio" + audio_ext
        with open(audio_path, "rb") as f:
            files = {
                "file": (filename, f, audio_mime),
            }
            data = {
                "model": "whisper-large-v3-turbo",
                "language": "zh",
                "prompt": "以下是一段中文对话，请使用正确的标点符号。",
                "response_format": "json",
            }

            resp = requests.post(
                _GROQ_API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=30,
            )

        elapsed = round(time.time() - start_time, 2)

        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", resp.text)
            except Exception:
                err_msg = resp.text
            return {"error": f"Groq API 错误 ({resp.status_code}): {err_msg}"}

        try:
            result = resp.json()
        except Exception:
            return {"error": "Groq API 返回了无效的 JSON"}

        text = result.get("text", "").strip()

        if is_whisper_hallucination(text):
            return {"text": "", "time": elapsed, "engine": "groq-whisper"}

        return {"text": text, "time": elapsed, "engine": "groq-whisper"}

    except requests.exceptions.Timeout:
        return {"error": "语音识别超时，请重试"}
    except requests.exceptions.ConnectionError:
        return {"error": "无法连接语音识别服务"}
    except Exception as e:
        return {"error": f"语音识别异常: {str(e)}"}


def _convert_to_wav(src_path: str) -> str | None:
    """用 ffmpeg 将音频转为 16kHz mono WAV，返回新文件路径。失败返回 None。"""
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            logger.error("ffmpeg 转换失败: %s", result.stderr.decode(errors="replace")[:300])
            os.remove(wav_path)
            return None
        return wav_path
    except FileNotFoundError:
        logger.warning("ffmpeg 未安装，跳过转换")
        os.remove(wav_path)
        return None
    except Exception as e:
        logger.error("ffmpeg 异常: %s", e)
        if os.path.exists(wav_path):
            os.remove(wav_path)
        return None


def recognize(audio_file):
    """
    语音识别入口。按优先级尝试：GLM-ASR → Groq Whisper。

    参数:
        audio_file: Flask request.files 中的文件对象

    返回:
        dict — 成功: { text, time, engine }
               失败: { error } 或 { text: "", message: "录音太短" }
    """
    tmp_path = None
    wav_path = None
    try:
        # 1. 保存到临时文件
        fd, tmp_path = tempfile.mkstemp(suffix=".webm")
        os.close(fd)
        audio_file.save(tmp_path)

        # 2. 检查文件大小
        file_size = os.path.getsize(tmp_path)
        if file_size < 1000:
            return {"text": "", "message": "录音太短"}

        # 3. 用 ffmpeg 转为 WAV（兼容性更好）
        wav_path = _convert_to_wav(tmp_path)
        # 优先用 wav，转换失败就用原始 webm
        audio_path = wav_path if wav_path else tmp_path
        audio_ext = ".wav" if wav_path else ".webm"
        audio_mime = "audio/wav" if wav_path else "audio/webm"

        # 4. 按优先级尝试各引擎
        engines = [
            ("GLM-ASR", _recognize_glm),
            ("Groq-Whisper", _recognize_groq),
        ]

        errors = []
        for name, engine_fn in engines:
            result = engine_fn(audio_path, audio_ext, audio_mime)
            if result is None:
                continue  # 未配置，跳过
            # 如果是带 fallback 标记的失败，记录错误并继续尝试下一个
            if result.get("fallback"):
                errors.append(f"{name}: {result.get('error', '未知错误')}")
                logger.warning("引擎 %s 失败，尝试下一个: %s", name, result.get('error'))
                continue
            # 成功（可能有 text 或 error）
            logger.info("语音识别使用引擎: %s", name)
            return result

        # 5. 所有引擎都失败
        if errors:
            return {"error": "语音识别失败: " + " | ".join(errors)}
        return {"error": "语音识别未配置（需设置 ZHIPU_API_KEY 或 GROQ_API_KEY）"}

    except Exception as e:
        return {"error": f"语音识别异常: {str(e)}"}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        if wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass
