# utils.py — 工具函数

import re

# ---------------------------------------------------------------------------
# 1. SentenceSplitter — 流式文本按标点切分成句子
# ---------------------------------------------------------------------------

# 匹配中英文句末标点及换行
SPLIT_RE = re.compile(r"([。！？!?\n])")


class SentenceSplitter:
    """流式喂入文本，按标点切分成完整句子。"""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str):
        """喂入增量文本，yield 切好的句子（含末尾标点）。"""
        self._buf += text
        parts = SPLIT_RE.split(self._buf)
        # parts 形如 ["句子", "。", "句子", "！", "剩余"]
        i = 0
        while i + 1 < len(parts):
            sentence = parts[i] + parts[i + 1]
            sentence = sentence.strip()
            if sentence:
                yield sentence
            i += 2
        # 最后一段（没有匹配到标点的尾巴）留在缓冲区
        self._buf = parts[-1] if len(parts) % 2 == 1 else ""

    def flush(self) -> list[str]:
        """返回缓冲区中剩余的文本（无标点结尾的尾巴），作为列表。"""
        remaining = self._buf.strip()
        self._buf = ""
        if remaining:
            return [remaining]
        return []


# ---------------------------------------------------------------------------
# 2. Whisper 幻觉过滤
# ---------------------------------------------------------------------------

_HALLUCINATION_KEYWORDS: list[str] = [
    "请不吝点赞", "订阅", "转发", "打赏",
    "感谢收看", "感谢观看", "感谢聆听",
    "谢谢收看", "谢谢观看",
    "字幕制作", "字幕提供", "字幕由", "Amara.org",
    "下期再见", "我们下期", "下次再见", "下集再见",
    "请订阅", "别忘了订阅", "记得订阅", "点击订阅",
    "喜欢的话", "喜欢就点", "一键三连",
    "Thank you for watching", "Please subscribe",
    "thanks for watching", "like and subscribe",
    "Subtitles by", "Translation by",
    "小铃铛", "开启小铃铛", "通知铃铛",
    "谢谢大家", "谢谢你们", "谢谢各位",
    "支持明镜", "点点栏目",
]

# 匹配纯重复字符，例如 "啊啊啊啊啊" / "哈哈哈哈"
_REPEAT_RE = re.compile(r"^(.)\1{3,}$")


def is_whisper_hallucination(text: str) -> bool:
    """判断 Whisper 转录文本是否为幻觉输出。

    检测规则：
    1. 空白或纯空格 → True
    2. 去除空白后长度 ≤ 1 → True（太短，无意义）
    3. 包含幻觉关键词 → True
    4. 纯重复单字符 → True
    """
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) <= 1:
        return True
    # 关键词匹配（不区分大小写）
    lower = stripped.lower()
    for kw in _HALLUCINATION_KEYWORDS:
        if kw.lower() in lower:
            return True
    # 纯重复字符
    if _REPEAT_RE.match(stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# 3. clean_voice_tags — 清理语气词标签和停顿标签
# ---------------------------------------------------------------------------

# 匹配 (laughs) (sighs) (clears throat) 等语气词标签
_VOICE_TAG_RE = re.compile(r"\([a-zA-Z\s]+\)")
# 匹配停顿标签 <#0.5#> <#1.2#> 等
_PAUSE_TAG_RE = re.compile(r"<#[\d.]+#>")


def clean_voice_tags(text: str) -> str:
    """去掉语气词标签和停顿标签，返回纯文本。"""
    text = _VOICE_TAG_RE.sub("", text)
    text = _PAUSE_TAG_RE.sub("", text)
    # 合并多余空格
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 4. VOICE_INSTRUCTION — 语音通话模式系统指令
# ---------------------------------------------------------------------------

VOICE_INSTRUCTION = """\
你现在处于语音通话模式，请遵守以下规则：

1. 回复简短自然，控制在1-3句话，像真人打电话聊天一样。
2. 不要使用 markdown 格式、emoji 表情、星号或任何富文本标记。
3. 使用口语化表达，避免书面语和长句。
4. 绝对不要说"作为AI"、"作为一个语言模型"之类的话。
5. 你可以在回复中使用以下语气词标签来让语音更自然（必须严格使用以下格式，不要自创标签）：
   (laughs) (chuckle) (coughs) (clear-throat) (groans) (breath)
   (pant) (inhale) (exhale) (gasps) (sniffs) (sighs) (snorts)
   (burps) (lip-smacking) (humming) (hissing) (emm) (whistles)
   (sneezes) (crying) (applause)
6. 你可以使用停顿标签 <#x#> 来插入停顿，x 为秒数，例如 <#0.5#> 表示停顿半秒。
7. 示例回复：
   "(emm) <#0.3#> 我觉得这个想法挺好的，你可以试试看。"
   "(chuckle) 好吧好吧，那就这么定了。"
   "(breath) <#0.5#> 原来是这样啊，我明白了。"\
"""
