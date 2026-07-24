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
    excluded_stems = {'_cleaned', '_processed', '_export', '_scriptbook_export',
                      '.cleaned_scriptbook'}  # 程序输出的清洗后台本
    excluded_dirs = {'_split_tracks'}  # 程序输出的分割音轨目录
    candidates: list[Path] = []

    for ext in ('.txt', '.pdf'):
        for f in sorted(work_dir.rglob(f'*{ext}')):
            stem = f.stem
            # 排除程序输出文件
            if any(ex in stem for ex in excluded_stems):
                continue
            # 排除程序输出目录中的文件
            if any(d in f.parts for d in excluded_dirs):
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
    PDF 使用 pypdfium2 逐字符提取 + 竖排列重排。
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

    使用 pypdfium2 逐字符提取 + 竖排列重排。
    OCR 回退已禁用。
    """
    text = _extract_pdf_text(file_path)
    if text and len(text) > 100:
        import re
        japanese_chars = len(re.findall(r'[぀-ゟ゠-ヺ一-鿿]', text))
        total_chars = len(text.replace('\n', '').replace(' ', ''))
        if total_chars > 0 and japanese_chars / max(total_chars, 1) > 0.03:
            return text
        if total_chars > 500:
            return text

    # OCR 回退已禁用
    return None


# ==================== PDF 提取（参照 vertical_sort.py） ====================

def _extract_pdf_text(file_path: Path) -> Optional[str]:
    """从 PDF 逐字符提取 + 竖排列重排

    与 vertical_sort.py 完全一致的实现：
    1. pypdfium2 逐字符获取 (text, x, y, w, h)
    2. 按页分组，每页 X 聚类成列（右→左），列内 Y 升序
    3. 后处理：去除 CJK 字符间空格、清理残留数字行
    失败时回退到 PyMuPDF 简单文本提取。
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return _extract_pdf_text_fitz_fallback(file_path)

    from collections import defaultdict

    try:
        pdf = pdfium.PdfDocument(str(file_path))
        all_chars: list[dict] = []

        for pg_idx in range(len(pdf)):
            page = pdf[pg_idx]
            tp = page.get_textpage()
            n = tp.count_chars()
            page_h = page.get_height()
            page_w = page.get_width()

            for ci in range(n):
                try:
                    box = tp.get_charbox(ci)  # (x1, y1, x2, y2) bottom-left origin
                    c = tp.get_text_range(index=ci, count=1)
                    x, y1, w_box, y2 = box[0], box[1], box[2] - box[0], box[3] - box[1]
                    h = abs(y2)
                    y = page_h - max(y1, y2)  # flip to top-left
                    all_chars.append({
                        "text": c,
                        "x": round(x, 1),
                        "y": round(y, 1),
                        "w": round(w_box, 1),
                        "h": round(h, 1),
                        "page": pg_idx + 1,
                        "page_w": round(page_w, 1),
                        "page_h": round(page_h, 1),
                    })
                except Exception:
                    pass

        pdf.close()

        if not all_chars:
            return None

        result = _reorder_vertical_pages(all_chars, col_gap=12.0)
        result = _remove_cjk_spaces(result)
        result = _clean_number_lines(result)

        from utils.text_filter import _filter_page_numbers
        return _filter_page_numbers(result)

    except Exception as e:
        print(f"  pypdfium2 提取失败: {e}，尝试 PyMuPDF 回退...")
        return _extract_pdf_text_fitz_fallback(file_path)


def _extract_pdf_text_fitz_fallback(file_path: Path) -> Optional[str]:
    """PyMuPDF 简单文本提取（pypdfium2 不可用时的回退）"""
    try:
        import fitz
    except ImportError:
        return None

    try:
        text_blocks: list[str] = []
        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                if text and text.strip():
                    text_blocks.append(text.strip())

        if not text_blocks:
            return None

        result = "\n\n".join(text_blocks)
        from utils.text_filter import _filter_page_numbers
        return _filter_page_numbers(result)

    except Exception as e:
        print(f"  PyMuPDF 回退提取失败: {e}")
        return None


# ── 竖排列重排（与 vertical_sort.py 完全一致）──

def _reorder_vertical_pages(chars: list[dict], col_gap: float = 12.0) -> str:
    """按页分组，每页 X 聚类成列（右→左），列内 Y 升序（上→下）"""
    from collections import defaultdict

    if not chars:
        return ""

    pages = defaultdict(list)
    for ch in chars:
        pages[ch["page"]].append(ch)

    output_lines: list[str] = []

    for pg in sorted(pages.keys()):
        page_chars = pages[pg]
        if not page_chars:
            continue

        columns = defaultdict(list)
        for ch in page_chars:
            col_key = round(ch["x"] / col_gap) * col_gap
            columns[col_key].append(ch)

        sorted_cols = sorted(columns.items(), key=lambda kv: -kv[0])

        page_lines: list[str] = []
        for _col_x, col_chars in sorted_cols:
            col_chars.sort(key=lambda ch: ch["y"])
            line = "".join(ch["text"] for ch in col_chars
                          if not ch["text"].strip().isdigit())
            stripped = line.strip()
            if stripped and not stripped.isdigit():
                page_lines.append(stripped)

        output_lines.append(f"<!-- page {pg} -->")
        output_lines.extend(page_lines)
        output_lines.append("")

    return "\n".join(output_lines)


# ── 后处理 ──

def _remove_cjk_spaces(text: str) -> str:
    """去除 CJK 字符间的空格"""
    import re
    return re.sub(
        r"(?<=[⺀-⻿　-〿぀-ゟ゠-ヿ"
        r"㈀-㋿㐀-䶿一-鿿豈-﫿"
        r"＀-￯])\s+(?=[⺀-⻿　-〿"
        r"぀-ゟ゠-ヿ㈀-㋿"
        r"㐀-䶿一-鿿豈-﫿＀-￯])",
        "",
        text,
    )


def _clean_number_lines(text: str) -> str:
    """清理残留的独立数字行和行首数字标记"""
    import re
    clean_lines: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("<!--") or s.isdigit():
            clean_lines.append(ln)
            continue
        s2 = re.sub(r'^\d{1,3}(?=[^\d\s])', '', s)
        if s2 and not s2.isdigit():
            clean_lines.append(s2)
        else:
            clean_lines.append("")
    return "\n".join(clean_lines)


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
