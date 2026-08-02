# -*- coding: utf-8 -*-
"""
字幕格式处理器（IO适配层）

处理时间轴字幕格式的解析和生成：
- 支持 LRC / SRT / VTT 三种字幕格式
- 解析现有字幕获取时间戳和文本
- 合并翻译文本与时间轴
- 时间戳格式转换
- 语言检测
"""

from __future__ import annotations
import re
from pathlib import Path
from dataclasses import dataclass


# ==================== 时间戳类型 ====================

@dataclass
class LrcLine:
    """字幕单行"""
    time_ms: int        # 时间戳（毫秒）
    text: str           # 文本内容
    index: int          # 在文件中的索引

    @property
    def timestamp_str(self) -> str:
        """生成 [mm:ss.xx] 格式的时间字符串"""
        total_s = self.time_ms / 1000.0
        m = int(total_s // 60)
        s = total_s % 60
        return f"[{m:02d}:{s:05.2f}]"


# ==================== 字幕扩展名 ====================

SUBTITLE_EXTS = ('.lrc', '.srt', '.vtt')


# ==================== LRC 解析 ====================

# LRC 时间戳正则：[mm:ss.xx] 或 [mm:ss.xxx]
LRC_PATTERN = re.compile(r'^\[(\d+):(\d+(?:\.\d+)?)\]')
# LRC 标签正则（匹配所有 [xxx] 格式标签）
LRC_TAG_PATTERN = re.compile(r'^(\[.*?\])\s*(.*)')
# LRC 时间标签正则
LRC_TIME_PATTERN = re.compile(r'\[\d{1,3}:\d{2}\.\d{2,3}\]')


def parse_lrc(text: str) -> list[LrcLine]:
    """
    解析 LRC 文本获取时间轴

    参数:
        text: LRC 原始文本

    返回:
        时间轴行列表（仅包含有时间戳的行）
    """
    lines = text.strip().split('\n')
    result = []
    index = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        m = LRC_PATTERN.match(line)
        if m:
            minutes = int(m.group(1))
            seconds = float(m.group(2))
            time_ms = int((minutes * 60 + seconds) * 1000)

            # 提取时间戳后的文本
            text = LRC_PATTERN.sub('', line).strip()

            result.append(LrcLine(
                time_ms=time_ms,
                text=text,
                index=index,
            ))
            index += 1

    return result


def parse_lrc_file(path: Path) -> list[LrcLine]:
    """从文件解析 LRC"""
    text = path.read_text(encoding='utf-8')
    return parse_lrc(text)


# ==================== LRC 生成 ====================

def generate_lrc(
    lrc_lines: list[LrcLine],
    translations: list[str],
) -> str:
    """
    根据时间轴和翻译文本生成 LRC

    参数:
        lrc_lines: 原始 LRC 时间轴（parse_lrc 结果）
        translations: 对应行的翻译文本

    返回:
        完整 LRC 文本（双语格式）
    """
    result_lines = []

    for i, (lrc_line, trans) in enumerate(zip(lrc_lines, translations)):
        ts = lrc_line.timestamp_str
        original = lrc_line.text

        # 格式: [02:15.30]原文 / 译文
        if trans and trans != original:
            result_lines.append(f"{ts}{original}／{trans}")
        else:
            result_lines.append(f"{ts}{original}")

    return '\n'.join(result_lines)


def generate_single_language_lrc(
    lrc_lines: list[LrcLine],
    texts: list[str],
) -> str:
    """
    生成单语言 LRC（纯时间轴）

    参数:
        lrc_lines: 时间轴
        texts: 要显示的文本

    返回:
        纯 LRC 文本
    """
    result_lines = []

    for lrc_line, text in zip(lrc_lines, texts):
        result_lines.append(f"{lrc_line.timestamp_str}{text}")

    return '\n'.join(result_lines)


def save_lrc(
    path: Path,
    lrc_lines: list[LrcLine],
    translations: list[str],
    *,
    name_suffix: str = '',
) -> None:
    """
    保存双语 LRC 文件

    参数:
        path: 输出路径
        lrc_lines: 时间轴
        translations: 翻译文本
        name_suffix: 文件名后缀（如 '_双语'）
    """
    content = generate_lrc(lrc_lines, translations)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


# ==================== 时间戳工具 ====================

def parse_timestamp(ts_str: str) -> int:
    """
    解析时间戳字符串为毫秒

    支持格式:
    - [mm:ss.xx]
    - mm:ss.xx
    """
    ts_str = ts_str.strip('[]')
    m = re.match(r'(\d+):(\d+(?:\.\d+)?)', ts_str)
    if m:
        minutes = int(m.group(1))
        seconds = float(m.group(2))
        return int((minutes * 60 + seconds) * 1000)
    return 0


def format_timestamp(ms: int, bracket: bool = True) -> str:
    """格式化毫秒为时间戳字符串"""
    total_s = ms / 1000.0
    m = int(total_s // 60)
    s = total_s % 60
    ts = f"{m:02d}:{s:05.2f}"
    return f"[{ts}]" if bracket else ts


def parse_chapter_lrc(text: str, time_threshold_ms: int = 1000) -> list[LrcLine]:
    """
    从 LRC 标记解析章节点

    只保留间隔 >= threshold 的关键时间点，过滤假的小间隔。

    参数:
        text: LRC 文本（如 '00:00 标题\\n01:30 第1节\\n...'）
        time_threshold_ms: 最小间隔，小于此值的时间戳视作假标记

    返回:
        有效的时间轴节点
    """
    all_lines = parse_lrc(text)
    if not all_lines:
        return []

    result = [all_lines[0]]
    for i in range(1, len(all_lines)):
        if all_lines[i].time_ms - result[-1].time_ms >= time_threshold_ms:
            result.append(all_lines[i])

    return result


# ==================== 通用字幕文本提取（LRC/SRT/VTT） ====================

def extract_subtitle_texts(content: str, ext: str) -> list[str]:
    """从字幕内容中提取纯文本行（去除时间戳/序号等元数据）

    支持 .lrc / .srt / .vtt 三种格式。

    参数:
        content: 字幕文件原始内容
        ext: 文件扩展名（含点号，如 '.lrc'）

    返回:
        纯文本行列表
    """
    lines = content.split('\n')
    lyric_texts: list[str] = []

    if ext == '.lrc':
        tag_pattern = re.compile(r'^(\[.*?\])\s*(.*)')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = tag_pattern.match(line)
            if match:
                tags = match.group(1)
                text_after = match.group(2)
                if re.search(r'\[\d+:\d{2}\.\d{2,3}\]', tags):
                    lyric_texts.append(text_after)

    elif ext == '.srt':
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.isdigit():
                i += 1
                if i < len(lines) and '-->' in lines[i]:
                    i += 1
                    while i < len(lines) and lines[i].strip():
                        lyric_texts.append(lines[i].strip())
                        i += 1
            i += 1

    elif ext == '.vtt':
        i = 0
        # 跳过 WEBVTT 头部
        while i < len(lines) and not lines[i].strip().startswith('00:'):
            i += 1
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('NOTE'):
                i += 1
                continue
            if '-->' in line:
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if not next_line or '-->' in next_line:
                        break
                    lyric_texts.append(next_line)
                    i += 1
            else:
                i += 1

    return lyric_texts


def detect_subtitle_language(file_path: Path) -> str:
    """检测字幕文件的主要语言

    参数:
        file_path: 字幕文件路径

    返回: "japanese" | "chinese" | "empty" | "unknown"
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (UnicodeDecodeError, Exception):
        return "unknown"

    ext = file_path.suffix.lower()
    lyric_texts = extract_subtitle_texts(content, ext)
    if not lyric_texts:
        return "empty"

    all_text = '\n'.join(lyric_texts)
    chinese_chars = len(re.findall(r'[一-鿿]', all_text))
    japanese_chars = len(re.findall(r'[぀-ゟ゠-ヺヽ-ヿ]', all_text))

    if japanese_chars > 0:
        if chinese_chars > 0:
            if japanese_chars > chinese_chars * 0.1:
                return "japanese"
            else:
                return "chinese"
        else:
            return "japanese"
    elif chinese_chars > 0:
        return "chinese"
    else:
        return "unknown"


# ==================== 通用字幕解析（保留结构） ====================

@dataclass
class SubtitleFile:
    """解析后的字幕文件（保留原始结构）"""
    final_lines: list[str | None]   # 输出行模板（None = 待替换的文本行）
    lyric_indices: list[int]        # 待替换行在 final_lines 中的索引
    original_lyrics: list[str]      # 原始文本行
    time_tags: list[str]            # 时间标签（LRC格式有值，SRT/VTT为空）
    ext: str                        # 文件扩展名


def parse_subtitle_file(path: Path) -> SubtitleFile | None:
    """解析字幕文件，保留原始结构以便翻译后重组

    支持 .lrc / .srt / .vtt 三种格式。

    参数:
        path: 字幕文件路径

    返回:
        SubtitleFile 对象，无可翻译内容时返回 None
    """
    ext = path.suffix.lower()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except UnicodeDecodeError:
        print(f"  跳过非 UTF-8 文件: {path}")
        return None

    final_lines: list[str | None] = []
    lyric_indices: list[int] = []
    original_lyrics: list[str] = []
    time_tags: list[str] = []

    if ext == '.lrc':
        for line in raw_lines:
            line_stripped = line.rstrip('\n\r')
            match = LRC_TAG_PATTERN.match(line_stripped)
            if match:
                tags = match.group(1)
                text_after = match.group(2)
                if LRC_TIME_PATTERN.search(tags):
                    final_lines.append(None)
                    lyric_indices.append(len(final_lines) - 1)
                    original_lyrics.append(text_after)
                    time_tags.append(tags)
                else:
                    # 元数据标签 [ti:], [ar:] 等
                    final_lines.append(line_stripped + '\n')
            else:
                # 续行：拼接到上一行文本
                if lyric_indices and line_stripped.strip():
                    original_lyrics[-1] += " " + line_stripped.strip()
                    continue
                final_lines.append(line_stripped + '\n')

    elif ext == '.srt':
        i = 0
        while i < len(raw_lines):
            stripped = raw_lines[i].strip()
            if stripped.isdigit():
                final_lines.append(raw_lines[i])  # 序号
                i += 1
                if i < len(raw_lines) and '-->' in raw_lines[i]:
                    final_lines.append(raw_lines[i])  # 时间轴
                    i += 1
                    while i < len(raw_lines) and raw_lines[i].strip():
                        text = raw_lines[i].rstrip('\n\r')
                        final_lines.append(None)
                        lyric_indices.append(len(final_lines) - 1)
                        original_lyrics.append(text)
                        time_tags.append("")
                        i += 1
                continue
            final_lines.append(raw_lines[i])
            i += 1

    elif ext == '.vtt':
        i = 0
        while i < len(raw_lines):
            stripped = raw_lines[i].strip()
            if not stripped or stripped.startswith('NOTE') or stripped == 'WEBVTT' or stripped.startswith('STYLE'):
                final_lines.append(raw_lines[i])
                i += 1
                continue
            if '-->' in stripped:
                final_lines.append(raw_lines[i])  # 时间轴
                i += 1
                while i < len(raw_lines):
                    next_stripped = raw_lines[i].strip()
                    if not next_stripped or '-->' in next_stripped:
                        break
                    text = raw_lines[i].rstrip('\n\r')
                    final_lines.append(None)
                    lyric_indices.append(len(final_lines) - 1)
                    original_lyrics.append(text)
                    time_tags.append("")
                    i += 1
                continue
            final_lines.append(raw_lines[i])
            i += 1
    else:
        print(f"  不支持的字幕格式: {ext}")
        return None

    if not original_lyrics:
        return None

    return SubtitleFile(
        final_lines=final_lines,
        lyric_indices=lyric_indices,
        original_lyrics=original_lyrics,
        time_tags=time_tags,
        ext=ext,
    )


def write_subtitle_file(sub_file: SubtitleFile, translated_lyrics: list[str], target_path: Path) -> None:
    """将翻译结果写回字幕文件（保留原始结构）

    参数:
        sub_file: parse_subtitle_file 的解析结果
        translated_lyrics: 翻译后的文本行（与 original_lyrics 一一对应）
        target_path: 输出路径
    """
    final_lines = list(sub_file.final_lines)

    for idx, tags, new_lyric in zip(sub_file.lyric_indices, sub_file.time_tags, translated_lyrics):
        if sub_file.ext == '.lrc':
            final_lines[idx] = f"{tags}{new_lyric}\n" if new_lyric else f"{tags}\n"
        else:
            final_lines[idx] = f"{new_lyric}\n" if new_lyric else '\n'

    # None 占位转空行
    final_lines = [line if line is not None else '\n' for line in final_lines]

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.writelines(final_lines)


# ==================== 语言检测 ====================

def extract_subtitle_texts(content: str, ext: str) -> list[str]:
    """从字幕内容中提取纯文本行（去掉时间戳）"""
    lines = content.split('\n')
    lyric_texts = []

    if ext == '.lrc':
        tag_pattern = re.compile(r'^(\[.*?\])\s*(.*)')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = tag_pattern.match(line)
            if match:
                tags = match.group(1)
                text_after = match.group(2)
                if re.search(r'\[\d+:\d{2}\.\d{2,3}\]', tags):
                    lyric_texts.append(text_after)

    elif ext == '.srt':
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.isdigit():
                i += 1
                if i < len(lines) and '-->' in lines[i]:
                    i += 1
                    while i < len(lines) and lines[i].strip():
                        lyric_texts.append(lines[i].strip())
                        i += 1
            i += 1

    elif ext == '.vtt':
        i = 0
        while i < len(lines) and not lines[i].strip().startswith('00:'):
            i += 1
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('NOTE'):
                i += 1
                continue
            if '-->' in line:
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if not next_line or '-->' in next_line:
                        break
                    lyric_texts.append(next_line)
                    i += 1
            else:
                i += 1

    return lyric_texts


def detect_lrc_language(file_path: Path) -> str:
    """检测字幕文件的语言

    返回:
        "japanese" - 日文为主
        "chinese"  - 中文为主
        "empty"    - 无文本内容
        "unknown"  - 无法识别
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (UnicodeDecodeError, Exception):
        return "unknown"

    ext = file_path.suffix.lower()
    lyric_texts = extract_subtitle_texts(content, ext)
    if not lyric_texts:
        return "empty"

    all_text = '\n'.join(lyric_texts)
    chinese_chars = len(re.findall(r'[一-鿿]', all_text))
    japanese_chars = len(re.findall(r'[぀-ゟ゠-ヺヽ-ヿ]', all_text))

    if japanese_chars > 0:
        if chinese_chars > 0:
            if japanese_chars > chinese_chars * 0.1:
                return "japanese"
            else:
                return "chinese"
        else:
            return "japanese"
    elif chinese_chars > 0:
        return "chinese"
    else:
        return "unknown"