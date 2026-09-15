# -*- coding: utf-8 -*-
"""
文本分析模块

提供中日文文本检测、字符集分析、LRC 语言检测等功能。
"""

from __future__ import annotations
import re
from typing import Optional


# ==================== 字符集常量 ====================

# 日文假名范围
HIRAGANA_RANGE = r'\u3040-\u309f'
KATAKANA_RANGE = r'\u30a0-\u30ff'
KATAKANA_PHONETIC_EXT = r'\u31f0-\u31ff'
KATAKANA_HALF = r'\uff66-\uff9f'
# CJK 统一汉字
CJK_RANGE = r'\u4e00-\u9fff'

JAPANESE_KANA_PATTERN = re.compile(
    f'[{HIRAGANA_RANGE}{KATAKANA_RANGE}{KATAKANA_PHONETIC_EXT}{KATAKANA_HALF}]'
)


CHINESE_CHAR_ONLY_PATTERN = re.compile(f'[{CJK_RANGE}]')


# ==================== 语言检测 ====================

def detect_japanese(text: str) -> bool:
    """检测文本是否包含日文

    判断标准：存在日文假名（平假名/片假名）

    参数:
        text: 待检测文本

    返回:
        True 如果包含日文假名
    """
    return bool(JAPANESE_KANA_PATTERN.search(text))


def detect_chinese(text: str) -> bool:
    """检测文本是否包含中文

    注意：中日共用汉字的 Unicode 范围重叠，此函数无法精准区分。
    配合 detect_japanese 使用：如果有假名则为日文，否则为中文。

    参数:
        text: 待检测文本

    返回:
        True 如果包含 CJK 汉字
    """
    return bool(CHINESE_CHAR_ONLY_PATTERN.search(text))


# ==================== LRC 语言检测 ====================

def detect_lrc_language(lrc_content: str) -> str:
    """检测 LRC 歌词文件的语言

    通过分析所有歌词行中的字符来判断。

    参数:
        lrc_content: LRC 文件内容

    返回: "ja" | "zh" | "en" | "mixed" | "unknown"
    """
    # 提取歌词行（去除时间标签）
    lyrics_lines: list[str] = []
    time_tag_pattern = re.compile(r'\[\d{2}:\d{2}\.\d{2,3}\]')

    for line in lrc_content.split('\n'):
        # 去除时间标签
        lyric = time_tag_pattern.sub('', line).strip()
        # 去除元数据标签
        if lyric.startswith('[ti:') or lyric.startswith('[ar:') or \
           lyric.startswith('[al:') or lyric.startswith('[by:') or \
           lyric.startswith('[offset:'):
            continue
        if lyric:
            lyrics_lines.append(lyric)

    if not lyrics_lines:
        return "unknown"

    combined = ' '.join(lyrics_lines)

    has_kana = detect_japanese(combined)
    has_cjk = detect_chinese(combined)

    # 检测英文
    latin_count = len(re.findall(r'[a-zA-Z]', combined))
    total_len = len(combined.replace(' ', ''))

    if total_len == 0:
        return "unknown"

    latin_ratio = latin_count / total_len if total_len > 0 else 0

    if has_kana:
        if has_cjk and latin_ratio < 0.3:
            return "ja"
        elif latin_ratio > 0.5:
            return "mixed"
        return "ja"
    elif has_cjk:
        if latin_ratio < 0.3:
            return "zh"
        else:
            return "mixed"
    elif latin_ratio > 0.7:
        return "en"
    else:
        return "unknown"


# ==================== fugashi 可用性 ====================

# fugashi 延迟初始化
_FUGASHI_AVAILABLE = None


def _init_tagger():
    """探测 fugashi 是否可用（延迟初始化）"""
    global _FUGASHI_AVAILABLE
    if _FUGASHI_AVAILABLE is not None:
        return
    try:
        import fugashi  # noqa: F401
        _FUGASHI_AVAILABLE = True
    except Exception:
        _FUGASHI_AVAILABLE = False


def is_fugashi_available() -> bool:
    """检查 fugashi 是否可用"""
    _init_tagger()
    return _FUGASHI_AVAILABLE


# ==================== 日语文本批量分析 ====================

def analyze_japanese_texts(texts: list[str]) -> dict:
    """对一批日语文本行进行统计分析

    参数:
        texts: 日文文本行列表

    返回:
        {
            "japanese_lines": int,   # 包含日文假名的行数
            "total_lines": int,       # 总行数
            "total_chars": int,       # 总字符数（去除空白）
            "unique_words": int,      # 唯一词汇数（按空格/CJK分隔）
            "top_words": list[str],   # 高频词列表（出现 >= 2 次）
        }
    """
    import re as _re
    from collections import Counter

    total_chars = 0
    japanese_lines = 0
    all_words: list[str] = []

    for line in texts:
        stripped = line.strip()
        if not stripped:
            continue

        # 统计字符
        clean = _re.sub(r'\s+', '', stripped)
        total_chars += len(clean)

        # 检测日文
        if detect_japanese(stripped):
            japanese_lines += 1

        # 简单分词：按空格和标点切分，保留长度 >= 2 的片段
        tokens = _re.split(r'[\s,.\!?、。！？…　]+', stripped)
        for t in tokens:
            t = t.strip()
            if len(t) >= 2:
                all_words.append(t)

    # 统计词频
    word_counter = Counter(all_words)
    # 只保留出现 >= 2 次且长度 >= 2 的词作为"高频词"
    top_words = [
        word for word, cnt in word_counter.most_common(50)
        if cnt >= 2 and len(word) >= 2
    ]

    return {
        "japanese_lines": japanese_lines,
        "total_lines": len(texts),
        "total_chars": total_chars,
        "unique_words": len(word_counter),
        "top_words": top_words,
    }
