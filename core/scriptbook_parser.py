# -*- coding: utf-8 -*-
"""
台本解析器

提供台本文件发现、加载和内容解析功能。
从 scriptbook_utils.py 提取核心逻辑，不包含逐行匹配（已被全量台本策略取代）。
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

from .layout_analyzer import detect_vertical_layout, sort_vertical_layout, sort_horizontal_layout


# ==================== 台本发现 ====================

def find_scriptbooks_in_dir(
    work_dir: Path,
    track_count: int = 0,
) -> list[Path]:
    """
    在工作目录中查找台本文件

    策略：
    1. 优先查找「台本」子文件夹
    2. 递归扫描根目录
    3. 按编号排序

    返回: 台本文件路径列表（已排序）
    """
    import os
    from utils.scriptbook_patterns import SCRIPTBOOK_EXTS

    candidates: list[Path] = []

    # 优先检查「台本」子文件夹（精确匹配 + 包含匹配，如 05.台本）
    for scriptbook_dir_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
        sb_dir = work_dir / scriptbook_dir_name
        if sb_dir.is_dir():
            for ext in SCRIPTBOOK_EXTS:
                for f in sorted(sb_dir.glob(f'*{ext}')):
                    if _is_scriptbook_file(f):
                        candidates.append(f)
            if candidates:
                return _sort_scriptbook_files(candidates)

    # 回退：检查名称中包含「台本」的子目录（如 05.台本）
    if not candidates:
        try:
            for entry in sorted(work_dir.iterdir()):
                if entry.is_dir() and '台本' in entry.name:
                    for ext in SCRIPTBOOK_EXTS:
                        for f in sorted(entry.glob(f'*{ext}')):
                            if _is_scriptbook_file(f):
                                candidates.append(f)
                    if candidates:
                        return _sort_scriptbook_files(candidates)
        except Exception:
            pass

    # 回退：递归扫描根目录
    for ext in SCRIPTBOOK_EXTS:
        for f in sorted(work_dir.rglob(f'*{ext}')):
            if _is_scriptbook_file(f):
                candidates.append(f)

    return _sort_scriptbook_files(candidates)


def collect_all_scriptbook_candidates(work_dir: Path) -> list[Path]:
    """收集工作目录中所有 .txt 和 .pdf 文件作为台本备选

    不做任何关键词/正则过滤，仅排除程序自身输出文件。
    筛选工作交由 LLM 完成。

    返回: 按相对路径排序的备选文件列表
    """
    excluded_stems = {'_cleaned', '_processed', '_export', '_scriptbook_export'}
    candidates: list[Path] = []

    for ext in ('.txt', '.pdf'):
        for f in sorted(work_dir.rglob(f'*{ext}')):
            stem = f.stem
            # 排除程序输出文件
            if any(ex in stem for ex in excluded_stems):
                continue
            candidates.append(f)

    # 按相对路径排序（保持可读性）
    def _sort_key(p: Path) -> str:
        try:
            return str(p.relative_to(work_dir))
        except ValueError:
            return str(p)

    return sorted(candidates, key=_sort_key)


def _sort_scriptbook_files(files: list[Path]) -> list[Path]:
    """按文件名中的数字编号排序台本文件"""
    import os

    def _extract_number(p: Path) -> tuple[int, str]:
        stem = p.stem
        # 尝试提取数字
        m = re.search(r'[０-９0-9]+', stem)
        if m:
            num_str = m.group()
            # 全角转半角
            num_str_normalized = num_str.translate(
                str.maketrans('０１２３４５６７８９', '0123456789')
            )
            try:
                return (int(num_str_normalized), stem)
            except ValueError:
                pass
        return (999999, stem)

    # 按父目录分组，每组内按编号排序
    from itertools import groupby
    groups: dict[str, list[Path]] = {}
    for f in files:
        parent_key = str(f.parent)
        groups.setdefault(parent_key, []).append(f)

    result: list[Path] = []
    for parent_key in sorted(groups.keys()):
        result.extend(sorted(groups[parent_key], key=_extract_number))

    return result


# ==================== 台本识别 ====================

def _is_scriptbook_file(file_path: Path) -> bool:
    """检测文件是否为台本文件

    判断依据：
    1. 文件扩展名为 .txt 或 .pdf
    2. 文件名包含台本关键词
    3. 文件名符合台本命名模式
    4. 排除特殊用途文件
    """
    from utils.scriptbook_patterns import (
        SCRIPTBOOK_EXTS, SCRIPTBOOK_KEYWORDS, NON_SCRIPTBOOK_KEYWORDS,
    )

    if file_path.suffix.lower() not in SCRIPTBOOK_EXTS:
        return False

    filename = file_path.name
    filename_lower = filename.lower()
    stem = file_path.stem

    # 排除程序生成的输出文件
    for exclusion in ('_cleaned', '_processed', '_export', '_scriptbook_export'):
        if exclusion in stem:
            return False
    # 排除特殊用途文件（直接子串匹配，不做 CJK 边界检查）
    for keyword in NON_SCRIPTBOOK_KEYWORDS:
        if keyword.lower() in filename_lower:
            return False

    # 台本关键词
    for keyword in SCRIPTBOOK_KEYWORDS:
        if keyword.lower() in filename_lower:
            return True

    # 台本命名模式
    track_patterns = [
        r'^トラック[０-９0-9]+',
        r'^track[０-９0-9]+',
        r'^tr[０-９0-9]+',
    ]
    for pattern in track_patterns:
        if re.match(pattern, stem, re.IGNORECASE):
            return True

    # 纯数字在台本文件夹中
    parent_name = file_path.parent.name.lower()
    if parent_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
        if re.match(r'^[０-９0-9]+$', stem):
            return True
        if re.match(r'^[０-９0-9]+[-_][０-９0-9]+', stem):
            return True

    # 父目录名包含「台本」（如 05.台本）→ 目录内所有 txt/pdf 都视为台本
    if '台本' in file_path.parent.name:
        return True

    # 文件名以数字编号开头（如 01_xxx, 02_xxx）→ 很可能是台本音轨文件
    if re.match(r'^[０-９0-9]{2,3}[_\-．. ]', stem):
        return True

    return False


def is_scriptbook_file(file_path: Path) -> bool:
    """公开的台本文件检测接口"""
    return _is_scriptbook_file(file_path)


# ==================== 台本加载 ====================

def load_scriptbook_content(file_path: Path) -> Optional[str]:
    """加载台本文件内容

    支持 .txt 和 .pdf 格式。
    PDF 使用 PyMuPDF 或 PaddleOCR 提取。
    TXT 尝试多种编码（UTF-8, Shift-JIS, CP932, EUC-JP）。

    返回: 文本内容，失败返回 None
    """
    if file_path.suffix.lower() == '.pdf':
        return _load_pdf_scriptbook(file_path)
    else:
        # 尝试多种编码，日文 Windows 上常见 Shift-JIS
        for encoding in ('utf-8', 'shift-jis', 'cp932', 'euc-jp', 'iso-2022-jp'):
            try:
                text = file_path.read_text(encoding=encoding)
                if text.strip():
                    return text
            except Exception:
                continue
        return None


def _load_pdf_scriptbook(file_path: Path) -> Optional[str]:
    """从 PDF 加载台本内容

    优先使用 PyMuPDF 直接提取，失败后使用 PaddleOCR。
    """
    # 策略1：PyMuPDF 直接提取
    text = _extract_with_pymupdf(file_path)
    if text and len(text) > 100:
        import re
        japanese_chars = len(re.findall(r'[぀-ゟ゠-ヺ一-鿿]', text))
        total_chars = len(text.replace('\n', '').replace(' ', ''))
        if total_chars > 0 and japanese_chars / max(total_chars, 1) > 0.03:
            return text
        if total_chars > 500:
            return text

    # 策略2：PaddleOCR 回退
    try:
        from ocr_utils import extract_with_ocr
        ocr_text = extract_with_ocr(file_path, clean_for_translation=True)
        if ocr_text:
            return ocr_text
    except Exception:
        pass

    return None


def _extract_with_pymupdf(file_path: Path) -> Optional[str]:
    """使用 PyMuPDF 提取 PDF 文本"""
    import unicodedata

    try:
        import fitz
    except ImportError:
        return None

    try:
        text_blocks: list[str] = []
        garbled_pages = 0
        total_pages = 0

        with fitz.open(file_path) as doc:
            total_pages = len(doc)
            for page_num, page in enumerate(doc, 1):
                # 先用 plain text 获取正确阅读顺序的文本
                plain_text = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                if not plain_text or not plain_text.strip():
                    continue

                # 乱码检测：用 dict 模式取少量 spans 做采样
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
                sample_spans: list[str] = []
                for block in blocks:
                    if block.get('type') != 0:
                        continue
                    for line in block.get('lines', []):
                        for span in line.get('spans', []):
                            t = span.get('text', '')
                            if t.strip():
                                sample_spans.append(t)
                            if len(sample_spans) >= 50:
                                break
                        if len(sample_spans) >= 50:
                            break
                    if len(sample_spans) >= 50:
                        break

                if sample_spans and _is_garbled_text(''.join(sample_spans)):
                    garbled_pages += 1
                    continue

                text_blocks.append(plain_text.strip())

        if total_pages > 0 and garbled_pages / total_pages > 0.5:
            return None

        result = '\n\n'.join(text_blocks)
        # 检测并修复"逐字分行"的竖排PDF：每行只有一个有效字符时自动拼接
        result = _fix_single_char_lines(result)
        from utils.text_filter import _filter_page_numbers
        return _filter_page_numbers(result)

    except Exception as e:
        print(f"  PyMuPDF 提取失败: {e}")
        return None


def _sort_horizontal_spans(
    spans: list[tuple[float, float, str, float, float]],
    tolerance: float,
) -> str:
    """横排布局排序"""
    spans.sort(key=lambda s: (round(s[1] / tolerance), s[0]))
    lines: list[str] = []
    current_y_group = -10000
    current_line: list[tuple] = []

    for cx, cy, text, x0, y0 in spans:
        y_group = round(cy / tolerance)
        if y_group != current_y_group:
            if current_line:
                current_line.sort(key=lambda s: s[0])
                lines.append(''.join(s[2] for s in current_line))
            current_y_group = y_group
            current_line = [(cx, cy, text, x0, y0)]
        else:
            current_line.append((cx, cy, text, x0, y0))

    if current_line:
        current_line.sort(key=lambda s: s[0])
        lines.append(''.join(s[2] for s in current_line))

    return '\n'.join(lines)


def _sort_vertical_spans(
    spans: list[tuple[float, float, str, float, float]],
    tolerance: float,
) -> str:
    """竖排布局排序"""
    spans.sort(key=lambda s: (round(s[0] / tolerance), s[1]))
    columns: list[tuple[int, str]] = []
    current_x_group = -10000
    current_col: list[tuple] = []

    for cx, cy, text, x0, y0 in spans:
        x_group = round(cx / tolerance)
        if x_group != current_x_group:
            if current_col:
                current_col.sort(key=lambda s: s[1])
                columns.append((current_x_group, ''.join(s[2] for s in current_col)))
            current_x_group = x_group
            current_col = [(cx, cy, text, x0, y0)]
        else:
            current_col.append((cx, cy, text, x0, y0))

    if current_col:
        current_col.sort(key=lambda s: s[1])
        columns.append((current_x_group, ''.join(s[2] for s in current_col)))

    columns.sort(key=lambda c: -c[0])
    return '\n'.join(c[1] for c in columns)


def _fix_single_char_lines(text: str) -> str:
    """修复竖排 PDF 的逐字分行问题。

    PyMuPDF 提取竖排日文 PDF 时，每个字符可能独占一行。
    检测并拼接这种模式：如果大部分非空行只有一个 CJK 字符，
    则将所有单字符行按顺序拼接，用空行保留段落边界。
    """
    import re
    lines = text.split('\n')
    if len(lines) < 20:
        return text

    # 统计单 CJK 字符行的比例
    cjk_single = 0
    empty = 0
    multi = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            empty += 1
        elif re.fullmatch(r'[　-〿぀-ヿ一-鿿㐀-䶿豈-﫿＀-￯ -⁯ -/:-@[-`{-~　-〃〈-】〔-〟・！-／：-＠［-｀｛-～\w]', stripped):
            cjk_single += 1
        else:
            multi += 1

    total = cjk_single + multi
    if total == 0 or cjk_single / total < 0.6:
        return text  # 不是逐字分行模式

    # 拼接：连续的单字符行合并，空行保留为段落分隔
    result: list[str] = []
    buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                result.append(''.join(buf))
                buf = []
            if result and result[-1] != '':
                result.append('')
        elif re.fullmatch(r'[　-〿぀-ヿ一-鿿㐀-䶿豈-﫿＀-￯ -⁯ -/:-@[-`{-~　-〃〈-】〔-〟・！-／：-＠［-｀｛-～\w]', stripped):
            buf.append(stripped)
        else:
            if buf:
                result.append(''.join(buf))
                buf = []
            result.append(stripped)
    if buf:
        result.append(''.join(buf))

    return '\n'.join(result)


def _is_garbled_text(text: str) -> bool:
    """检测文本是否为乱码"""
    if not text or len(text.strip()) == 0:
        return False

    total_chars = len(text.replace('\n', '').replace(' ', ''))
    if total_chars == 0:
        return False

    # 替换字符占比
    replacement_chars = text.count('\ufffd')
    if replacement_chars / total_chars > 0.05:
        return True

    # 日文字符比例
    japanese_chars = len(re.findall(
        r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', text
    ))
    if total_chars > 100 and japanese_chars / total_chars < 0.05:
        return True

    return False


# ==================== 台本解析 ====================

def parse_scriptbook_content(content: str) -> list[dict]:
    """解析台本内容，提取结构化行

    返回: [{"line_num": int, "character": str, "text": str, "raw_line": str, "type": str}, ...]
    type: "dialogue" | "character" | "se" | "direction" | "track" | "chapter" | "empty" | "other"
    """
    from utils.scriptbook_patterns import (
        SE_PATTERNS, CHARACTER_PATTERN, TRACK_PATTERN,
        CHAPTER_TITLE_PATTERN, POSITION_PATTERN, POSITION_COMPLEX_PATTERN,
        MOVE_PATTERN, VOICE_STYLE_PATTERN, EJACULATION_PATTERN,
        TRACK_MARK_PATTERN, TRACK_DOT_PATTERN,
        STAR_TRACK_PATTERN, STAR_CHAPTER_PATTERN, CHAPTER_TRACK_PATTERN,
        TR_DOT_PATTERN, SUB_TRACK_PATTERN,
        DRAMA_EPISODE_PATTERN, DRAMA_OP_PATTERN,
        TRACK_TRIANGLE_PATTERN, TRACK_BRACKET_NUM_PATTERN,
    )

    lines = content.split('\n')
    parsed: list[dict] = []
    current_character = ""

    for i, line in enumerate(lines, 1):
        raw_line = line
        stripped = line.strip()
        stripped_no_pagenum = re.sub(r'^\d+\s+', '', stripped)

        if not stripped:
            parsed.append({
                "line_num": i, "character": "", "text": "",
                "raw_line": raw_line, "type": "empty"
            })
            continue

        # 章节标题
        if re.match(CHAPTER_TITLE_PATTERN, stripped):
            if re.search(r'トラック[０-９0-9]', stripped):
                parsed.append({
                    "line_num": i, "character": "", "text": stripped,
                    "raw_line": raw_line, "type": "track"
                })
            else:
                parsed.append({
                    "line_num": i, "character": "", "text": stripped,
                    "raw_line": raw_line, "type": "chapter"
                })
            continue

        # 音轨标记模式
        track_match = False
        for pattern in [
            TRACK_PATTERN, TRACK_MARK_PATTERN, TRACK_DOT_PATTERN,
            STAR_TRACK_PATTERN, STAR_CHAPTER_PATTERN, CHAPTER_TRACK_PATTERN,
            TR_DOT_PATTERN, TRACK_TRIANGLE_PATTERN, TRACK_BRACKET_NUM_PATTERN,
            SUB_TRACK_PATTERN, DRAMA_EPISODE_PATTERN, DRAMA_OP_PATTERN,
        ]:
            extra = re.IGNORECASE if pattern in (TR_DOT_PATTERN,) else 0
            if re.match(pattern, stripped_no_pagenum, extra if extra else 0):
                parsed.append({
                    "line_num": i, "character": "", "text": stripped_no_pagenum,
                    "raw_line": raw_line, "type": "track"
                })
                track_match = True
                break
        if track_match:
            continue

        # 角色名标记
        if re.match(CHARACTER_PATTERN, stripped):
            m = re.search(r'【([^】]+)】', stripped)
            current_character = m.group(1) if m else ""
            parsed.append({
                "line_num": i, "character": current_character, "text": "",
                "raw_line": raw_line, "type": "character"
            })
            continue

        # 位置/演技指示
        if re.match(POSITION_COMPLEX_PATTERN, stripped) or re.match(POSITION_PATTERN, stripped):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "direction"
            })
            continue

        # 移动指示
        if re.match(MOVE_PATTERN, stripped):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "direction"
            })
            continue

        # 声音方式指示
        if re.match(VOICE_STYLE_PATTERN, stripped):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "direction"
            })
            continue

        # SE/音效标记
        is_se = False
        for pattern in SE_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                is_se = True
                break
        if is_se or re.search(r'【効果音[:：]', stripped):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "se"
            })
            continue

        # 方向指示 (# 开头)
        if stripped.startswith('#'):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "direction"
            })
            continue

        # 时间标记
        if stripped.startswith('(') and stripped.endswith(')'):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "direction"
            })
            continue

        # 射精标记
        if re.match(EJACULATION_PATTERN, stripped):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "se"
            })
            continue

        # 章节标题
        if re.match(r'^【(?:プロローグ|第[0-9０-９]+章|エピローグ)[：:][^】]*】', stripped):
            parsed.append({
                "line_num": i, "character": "", "text": stripped,
                "raw_line": raw_line, "type": "chapter"
            })
            continue

        # 纯数字行
        if re.match(r'^[０-９0-9]+$', stripped):
            if re.match(r'^[０-９]+$', stripped):
                parsed.append({
                    "line_num": i, "character": "", "text": stripped,
                    "raw_line": raw_line, "type": "track"
                })
                continue
            elif re.match(r'^[0-9]+$', stripped) and len(stripped) >= 2:
                parsed.append({
                    "line_num": i, "character": "", "text": stripped,
                    "raw_line": raw_line, "type": "track"
                })
                continue

        # 默认为对话
        parsed.append({
            "line_num": i,
            "character": current_character,
            "text": stripped,
            "raw_line": raw_line,
            "type": "dialogue"
        })

    return parsed


# ==================== 台本地图构建 ====================

def build_track_scriptbook_map(
    scriptbook_files: list[Path],
) -> dict[int, list[str]]:
    """
    构建音轨→台本行映射

    从台本文件中提取每个音轨的台词内容。

    参数:
        scriptbook_files: 台本文件列表（已按音轨顺序排序）

    返回:
        {track_num: [line1, line2, ...]}
    """
    track_map: dict[int, list[str]] = {}

    for idx, sb_file in enumerate(scriptbook_files):
        track_num = idx + 1
        content = load_scriptbook_content(sb_file)
        if not content:
            continue

        parsed = parse_scriptbook_content(content)

        # 提取对话行
        dialogue_lines: list[str] = []
        for item in parsed:
            if item['type'] == 'dialogue':
                text = item['text'].strip()
                if text:
                    character = item['character']
                    if character:
                        dialogue_lines.append(f"【{character}】{text}")
                    else:
                        dialogue_lines.append(text)

        if dialogue_lines:
            track_map[track_num] = dialogue_lines

    return track_map


def build_raw_scriptbook_map(
    scriptbook_files: list[Path],
) -> dict[int, list[str]]:
    """
    构建音轨→原始台本行映射（不做结构化解析，保留全文）

    参数:
        scriptbook_files: 台本文件列表

    返回:
        {track_num: [raw_line1, raw_line2, ...]}
    """
    track_map: dict[int, list[str]] = {}

    for idx, sb_file in enumerate(scriptbook_files):
        track_num = idx + 1
        content = load_scriptbook_content(sb_file)
        if not content:
            continue

        lines = [line.strip() for line in content.split('\n') if line.strip()]
        if lines:
            track_map[track_num] = lines

    return track_map


# ==================== 脚本关键字过滤 ====================

def is_key_dialogue_line(line: str) -> bool:
    """判断是否为值得参考的关键台词

    关键台词特征：
    1. 包含角色名（格式为"角色名：台词"）
    2. 包含重要信息（地点、时间、人物关系）
    3. 包含情感表达
    4. 包含剧情转折
    5. 长度适中的句子（5-150字符）
    """
    import re

    if not line or not line.strip():
        return False

    stripped = line.strip()

    if len(stripped) < 5:
        return False
    if len(stripped) > 150:
        return True  # 长句保留

    # 包含角色名（格式为"角色名：台词"）
    if re.search(r'^[^：\n]{2,10}：', stripped):
        return True

    key_patterns = [
        r'(学校|教室|部室|家|部屋|寮|廊下|屋上|図書室|トイレ|ロッカー|教室)',
        r'(昨日|今日|明日|朝|昼|夜|放課後|授業中|休み時間)',
        r'(姉|妹|兄|弟|母|父|友達|クラスメイト|先輩|後輩|彼氏|彼女)',
        r'(さん|ちゃん|くん|様|先生|先輩)',
        r'(好き|嫌い|愛してる|大切|大事|嬉しい|悲しい|辛い|辛かった)',
        r'(でも|だって|だから|しかし|それに|実は|実はね|ねえ|あのね)',
        r'(？|\?)',
        r'(したい|してほしい|して|させて|させてください)',
    ]

    for pattern in key_patterns:
        if re.search(pattern, stripped):
            return True

    return False