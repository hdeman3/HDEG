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

    # 排除程序自身输出目录（防止上次运行的清洗结果被当做台本）
    _EXCLUDED_DIRS = {'_split_tracks', '_cleaned', '_processed', '_export', '_scriptbook_export'}
    _EXCLUDED_STEMS = {'_cleaned', '_processed', '_export', '_scriptbook_export',
                       '.cleaned_scriptbook'}

    def _in_excluded_dir(p: Path) -> bool:
        return any(d in p.parts for d in _EXCLUDED_DIRS)

    def _has_excluded_stem(p: Path) -> bool:
        return any(ex in p.stem for ex in _EXCLUDED_STEMS)

    candidates: list[Path] = []

    # 优先检查「台本」子文件夹（精确匹配 + 包含匹配，如 05.台本）
    for scriptbook_dir_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
        sb_dir = work_dir / scriptbook_dir_name
        if sb_dir.is_dir() and not _in_excluded_dir(sb_dir):
            for ext in SCRIPTBOOK_EXTS:
                for f in sorted(sb_dir.glob(f'*{ext}')):
                    if _has_excluded_stem(f) or _in_excluded_dir(f):
                        continue
                    if _is_scriptbook_file(f):
                        candidates.append(f)
            if candidates:
                return _finalize_scriptbook_files(candidates)

    # 回退：检查名称中包含「台本」的子目录（如 05.台本）
    if not candidates:
        try:
            for entry in sorted(work_dir.iterdir()):
                if entry.is_dir() and '台本' in entry.name and not _in_excluded_dir(entry):
                    for ext in SCRIPTBOOK_EXTS:
                        for f in sorted(entry.glob(f'*{ext}')):
                            if _has_excluded_stem(f) or _in_excluded_dir(f):
                                continue
                            if _is_scriptbook_file(f):
                                candidates.append(f)
                    if candidates:
                        return _finalize_scriptbook_files(candidates)
        except Exception:
            pass

    # 回退：递归扫描根目录
    for ext in SCRIPTBOOK_EXTS:
        for f in sorted(work_dir.rglob(f'*{ext}')):
            if _has_excluded_stem(f) or _in_excluded_dir(f):
                continue
            if _is_scriptbook_file(f):
                candidates.append(f)

    return _finalize_scriptbook_files(candidates)


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


def prefer_txt_over_pdf(files: list[Path]) -> list[Path]:
    """同一目录下同名 txt+pdf 同时被识别为台本时，优先保留 txt、丢弃 pdf。

    仅用于正则回退路径（find_scriptbooks_in_dir）：该路径不走 LLM，无法用提示词约束，
    若 txt+pdf 都收集进来，非预分割时会重复拼接同一份台本。
    """
    if not files:
        return files

    # 按 (父目录, 不含扩展名的文件名) 分组，找出同名 txt/pdf 对
    groups: dict[tuple[str, str], list[Path]] = {}
    for f in files:
        groups.setdefault((str(f.parent), f.stem), []).append(f)

    pdf_twin: set[Path] = set()
    for paths in groups.values():
        pdfs = [p for p in paths if p.suffix.lower() == '.pdf']
        txts = [p for p in paths if p.suffix.lower() == '.txt']
        if pdfs and txts:
            pdf_twin.update(pdfs)

    if not pdf_twin:
        return files
    return [f for f in files if f not in pdf_twin]


def _finalize_scriptbook_files(files: list[Path]) -> list[Path]:
    """识别结果收尾：txt 优先于 pdf，再按编号排序"""
    kept = prefer_txt_over_pdf(files)
    return _sort_scriptbook_files(kept)


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

def load_scriptbook_content(file_path: Path, api_config: dict = None) -> Optional[str]:
    """加载台本文件内容

    支持 .txt 和 .pdf 格式。
    PDF 使用 fitz (PyMuPDF) 提取，若提供 api_config 则先过 LLM 分析排版参数。
    TXT 尝试多种编码（UTF-8, Shift-JIS, CP932, EUC-JP）。

    返回: 文本内容，失败返回 None
    """
    if file_path.suffix.lower() == '.pdf':
        return _load_pdf_scriptbook(file_path, api_config=api_config)
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


def _load_pdf_scriptbook(file_path: Path, api_config: dict = None) -> Optional[str]:
    """从 PDF 加载台本内容

    全链路 fitz (PyMuPDF)：采样 → LLM 分析 → 文本提取 → 逐字重排回退。
    若提供 api_config，LLM 分析排版参数提升精度。
    """
    text = _extract_pdf_text(file_path, api_config=api_config)
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


# ==================== LLM 排版参数分析 ====================

def _analyze_pdf_layout(all_chars: list[dict], api_config: dict) -> dict:
    """调用 LLM 分析 PDF 排版参数（排向、col_gap/row_gap、阅读方向）

    从字符坐标中取样，发送给 Flash LLM 做一次性判断。
    失败时返回默认竖排参数。
    """
    from collections import defaultdict

    if not api_config or not (api_config.get('key') or api_config.get('api_key')):
        return {"orientation": "vertical", "col_gap": 24.0, "reading_order": "right-to-left"}

    # 选字符最多的 3 页做样本
    page_chars = defaultdict(list)
    for ch in all_chars:
        page_chars[ch['page']].append(ch)
    sorted_pages = sorted(page_chars.items(), key=lambda kv: -len(kv[1]))
    sample_pages = [chars for _pg, chars in sorted_pages[:3] if len(chars) > 50]

    if not sample_pages:
        return {"orientation": "vertical", "col_gap": 24.0, "reading_order": "right-to-left"}

    # 格式化：X 分布 + Y 分布 + 抽样字符
    parts = []
    for i, chars in enumerate(sample_pages):
        pw = chars[0].get('page_w', 842)
        ph = chars[0].get('page_h', 595)
        # X 分布
        x_buckets = defaultdict(int)
        for c in chars:
            x_buckets[int(c['x']) // 20 * 20] += 1
        x_dist = '  '.join('x≈{}:{}字'.format(k, x_buckets[k])
                          for k in sorted(x_buckets.keys(), reverse=True))
        # Y 分布
        y_buckets = defaultdict(int)
        for c in chars:
            y_buckets[int(c['y']) // 30 * 30] += 1
        y_dist = '  '.join('y≈{}:{}字'.format(k, y_buckets[k])
                          for k in sorted(y_buckets.keys()))
        # 抽样（带 X,Y 坐标）
        step = max(1, len(chars) // 150)
        sampled = chars[::step][:150]
        segs = ['[{:.0f},{:.0f}]{}'.format(c['x'], c['y'], c['text']) for c in sampled]
        parts.append('=== 样本页{} ({:.0f}x{:.0f}, {}字) ===\nX分布:\n{}\nY分布:\n{}\n抽样({}个,步长{}):\n{}'.format(
            i + 1, pw, ph, len(chars), x_dist, y_dist, len(sampled), step, ' '.join(segs)))

    prompt = '\n\n'.join(parts) + '''

基于以上坐标数据和抽样字符内容，输出JSON（不要其他内容）：
{"orientation":"horizontal或vertical","reading_order":"right-to-left或left-to-right或top-to-bottom","col_gap":0,"body_y_min":0,"body_y_max":0}

各参数由你根据坐标数据和抽样字符内容独立判断，填入实际数值：
- orientation: 横排(horizontal)或竖排(vertical)
- reading_order: 竖排时填列阅读方向（right-to-left=右列→左列，left-to-right=左列→右列），横排时填top-to-bottom
- col_gap: 一个完整视觉列的宽度(px)，不是字符间距。
  同一视觉列内字符的X坐标有一定散布，不同视觉列之间有明显的X间隔。
  col_gap应大于列内散布、小于列间间隔。
- body_y_min: 正文顶部Y坐标。过滤此坐标以上的内容（页眉、页码、行号等非正文元素）。
  观察Y分布和抽样字符：顶部Y值最小的少量字符通常就是页码/行号，正文从Y分布开始密集的地方开始。
  必须填入实际值，不要填0。
- body_y_max: 正文底部Y坐标。过滤此坐标以下的内容（底部页码等）。
  观察Y分布尾部：底部Y值最大的少量字符通常是页码，正文到Y分布密集区结束为止。
  如果底部无明显页码，填入页面高度。

判断横排/竖排的关键：
- 竖排：X分布有多个密集峰值（多列），Y范围覆盖页面大部分高度
- 横排：Y分布只有少量峰值（少数行），字符Y接近但X跨度大，抽样中同一行的字符Y坐标几乎相同'''

    try:
        from openai import OpenAI
        import os as _os
        for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            _os.environ.pop(k, None)
        _os.environ['NO_PROXY'] = '*'

        raw_timeout = api_config.get('timeout', 60)
        # config.json 的 timeout 单位是秒（如 2000 = 2000 秒），直接按秒用，
        # 不做毫秒猜测，避免长文本解析被 30 秒钳住导致超时
        timeout = float(raw_timeout) if raw_timeout >= 30 else 120.0
        api_key = api_config.get('key') or api_config.get('api_key', '')
        base_url = api_config.get('base_url', 'https://api.deepseek.com')

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        response = client.chat.completions.create(
            model='deepseek-v4-pro',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1, max_tokens=262140, timeout=300,
        )
        content = response.choices[0].message.content or ''
        if not content:
            content = getattr(response.choices[0].message, 'reasoning_content', '') or ''
    except Exception as e:
        print(f'  [PDF排版] LLM 分析失败: {e}，用默认参数')
        return {"orientation": "vertical", "col_gap": 24.0, "reading_order": "right-to-left"}

    import re, json
    m = re.search(r'\{[^{}]*\}', content, re.DOTALL)
    if m:
        try:
            params = json.loads(m.group())
            col_gap = float(params.get('col_gap', 24))
            reading = params.get('reading_order', 'right-to-left')
            orient = params.get('orientation', 'vertical')
            body_y_min = float(params.get('body_y_min', 0))
            body_y_max = float(params.get('body_y_max', 99999))
            print(f'  [PDF排版] LLM 分析: orientation={orient}, col_gap={col_gap:.0f}, reading={reading}, body_y=[{body_y_min:.0f},{body_y_max:.0f}]')
            return {"orientation": orient, "col_gap": col_gap, "reading_order": reading,
                    "body_y_min": body_y_min, "body_y_max": body_y_max}
        except (json.JSONDecodeError, ValueError):
            pass

    print(f'  [PDF排版] LLM 返回非JSON，用默认参数')
    return {"orientation": "vertical", "col_gap": 24.0, "reading_order": "right-to-left"}


# ── 孤儿列/行合并阈值 ──
_ORPHAN_THRESHOLD = 5


# ==================== PDF 提取（参照 vertical_sort.py） ====================

# ==================== fitz 字符采样（替代 pypdfium2） ====================

def _sample_chars_fitz(file_path: Path, max_pages: int = 5,
                       start_page: int = 0) -> list[dict]:
    """用 fitz rawdict 逐字符提取坐标，用于 LLM 排版分析

    start_page: 起始页码(0-based)，max_pages: 最多提取页数
    """
    try:
        import fitz
    except ImportError:
        return []

    _ctrl = {'\r', '\n', '\t', '\x00', '\x0c', '\x0b'}
    chars = []

    try:
        doc = fitz.open(str(file_path))
        end_page = min(len(doc), start_page + max_pages)
        for pg_idx in range(start_page, end_page):
            page = doc[pg_idx]
            page_w = page.rect.width
            page_h = page.rect.height
            raw = page.get_text("rawdict")
            for block in raw.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        for ch_data in span.get("chars", []):
                            c = ch_data.get("c", "")
                            if c in _ctrl:
                                continue
                            if c.isspace() or c == '':
                                continue
                            bbox = ch_data.get("bbox")
                            if not bbox:
                                bbox = (0.0, 0.0, 0.0, 0.0)
                            chars.append({
                                "text": c,
                                "x": round(bbox[0], 1),
                                "y": round(bbox[1], 1),
                                "w": round(bbox[2] - bbox[0], 1),
                                "h": round(bbox[3] - bbox[1], 1),
                                "page": pg_idx + 1,
                                "page_w": round(page_w, 1),
                                "page_h": round(page_h, 1),
                            })
        doc.close()
    except Exception as e:
        print(f"  fitz 采样失败: {e}")
        return []

    return chars


# ==================== fitz 逐字重排（替代 pypdfium2 回退） ====================

def _reorder_fitz_vertical(chars: list[dict], col_gap: float = 24.0) -> str:
    """fitz 字符 + LLM 参数：竖排列重排（右→左，列内 Y 升序）"""
    from collections import defaultdict

    pages = defaultdict(list)
    for ch in chars:
        pages[ch["page"]].append(ch)

    output_lines = []
    for pg in sorted(pages.keys()):
        page_chars = pages[pg]
        columns = defaultdict(list)
        for ch in page_chars:
            col_key = round(ch["x"] / col_gap) * col_gap
            columns[col_key].append(ch)

        columns = _merge_orphan_columns(columns, axis="x")

        sorted_cols = sorted(columns.items(), key=lambda kv: -kv[0])
        page_lines = []
        for _col_x, col_chars in sorted_cols:
            col_chars.sort(key=lambda ch: ch["y"])
            line = "".join(ch["text"] for ch in col_chars).strip()
            if line and not line.isdigit():
                page_lines.append(line)

        if page_lines:
            output_lines.append(f"<!-- page {pg} -->")
            output_lines.extend(page_lines)
            output_lines.append("")

    return "\n".join(output_lines)


def _reorder_fitz_horizontal(chars: list[dict], row_gap: float = 8.0) -> str:
    """fitz 字符 + LLM 参数：横排行重排（上→下，行内 X 升序）"""
    from collections import defaultdict

    pages = defaultdict(list)
    for ch in chars:
        pages[ch["page"]].append(ch)

    output_lines = []
    for pg in sorted(pages.keys()):
        page_chars = pages[pg]
        rows = defaultdict(list)
        for ch in page_chars:
            row_key = round(ch["y"] / row_gap) * row_gap
            rows[row_key].append(ch)

        rows = _merge_orphan_columns(rows, axis="y")

        sorted_rows = sorted(rows.items(), key=lambda kv: kv[0])
        page_lines = []
        for _row_y, row_chars in sorted_rows:
            row_chars.sort(key=lambda ch: ch["x"])
            line = "".join(ch["text"] for ch in row_chars).strip()
            if line and not line.isdigit():
                page_lines.append(line)

        if page_lines:
            output_lines.append(f"<!-- page {pg} -->")
            output_lines.extend(page_lines)
            output_lines.append("")

    return "\n".join(output_lines)


def _merge_orphan_columns(groups: dict, *, axis: str = "x",
                          threshold: int = _ORPHAN_THRESHOLD) -> dict:
    """将孤儿组（字符数 < threshold）合并到最近的大组。

    固定宽度分桶（如 round(x/col_gap)*col_gap）会产生 1~2 字符的
    "碎片"列/行——段首字符因微小 X/Y 偏移落入了不同的桶。
    此函数将碎片合并到距离最近的大组，调用方随后按正交轴排序
    即可恢复正确顺序。

    Args:
        groups: {bucket_key: [char_dicts]}
        axis: "x"（列合并，基于 key 的 X 距离）或
              "y"（行合并，基于 key 的 Y 距离）
        threshold: 少于此字符数的组视为孤儿

    Returns:
        合并后的 dict（孤儿组的 key 被移除，字符合并到大组）
    """
    if len(groups) <= 1:
        return dict(groups)

    large = {k: v for k, v in groups.items() if len(v) >= threshold}
    orphans = {k: v for k, v in groups.items() if len(v) < threshold}

    if not orphans or not large:
        return dict(groups)

    sorted_large_keys = sorted(large.keys())
    for orphan_key, orphan_chars in orphans.items():
        best_key = min(
            sorted_large_keys,
            key=lambda lk: (abs(orphan_key - lk), -len(large[lk])),
        )
        large[best_key].extend(orphan_chars)

    return large


def _merge_same_flow_columns(columns: dict, max_gap: float) -> dict:
    """合并同一阅读流的相邻列。

    相邻列的X中心间距 ≤ max_gap → 视为同一阅读流 → 合并。
    合并后列内按Y重排即得正确阅读顺序（竖排右→左列，列内上→下）。

    例如：一句话跨两列时，右列x=510和左列x=480间距30px ≤ max_gap，
    合并为一个逻辑列后Y排序，恢复正确阅读顺序。
    """
    if len(columns) <= 1 or max_gap <= 0:
        return dict(columns)

    sorted_keys = sorted(columns.keys(), reverse=True)
    groups = []
    current_group = [sorted_keys[0]]
    for i in range(1, len(sorted_keys)):
        gap = sorted_keys[i-1] - sorted_keys[i]
        if gap <= max_gap:
            current_group.append(sorted_keys[i])
        else:
            groups.append(current_group)
            current_group = [sorted_keys[i]]
    groups.append(current_group)

    if all(len(g) == 1 for g in groups):
        return dict(columns)

    merged = {}
    for group in groups:
        target_key = group[0]
        merged[target_key] = []
        for key in group:
            merged[target_key].extend(columns[key])

    return merged


# ==================== 主提取函数 ====================

def _extract_pdf_text(file_path: Path, api_config: dict = None) -> Optional[str]:
    """从 PDF 提取文本（全链路 fitz）

    1. fitz rawdict 逐字符采样 → LLM 分析 {orientation, col_gap, body_y, reading_order}
    2. fitz 文本提取 + body_y 过滤 → 优先返回
    3. fitz 质量不够 → fitz 逐字重排 + LLM 参数回退
    """
    import re as _re_q

    # ── 第一步：LLM 排版分析（有 api_config 时必过，用 fitz 采样）──
    layout = None
    if api_config:
        sample_chars = _sample_chars_fitz(file_path, max_pages=6, start_page=4)
        if sample_chars:
            layout = _analyze_pdf_layout(sample_chars, api_config)

    # ── 第二步：按 LLM 排向选择路径 ──
    if layout and layout.get('orientation') == 'vertical':
        # 竖排：fitz 文本不可靠（单字行或乱序碎片），走逐字重排
        all_chars = _sample_chars_fitz(file_path, max_pages=999)
        if all_chars:
            col_gap = float(layout.get('col_gap', 24.0))
            if layout.get('body_y_min', 0) > 0:
                ymin = layout['body_y_min']; ymax = layout.get('body_y_max', 99999)
                all_chars = [ch for ch in all_chars if ymin <= ch['y'] <= ymax]
            result = _reorder_fitz_vertical(all_chars, col_gap=col_gap)
            result = _remove_cjk_spaces(result)
            result = _clean_number_lines(result)
            from utils.text_filter import _filter_page_numbers
            return _filter_page_numbers(result)

    # 横排或无 LLM 参数：fitz 文本提取
    fitz_result = _extract_pdf_text_fitz(file_path, layout)
    if fitz_result and len(fitz_result) > 100:
        jp = len(_re_q.findall(r'[぀-ゟ゠-ヺ一-鿿]', fitz_result))
        total = len(fitz_result.replace('\n', '').replace(' ', ''))
        if total > 0 and jp / total > 0.05:
            return fitz_result
        print(f"  fitz 日文占比低 ({jp}/{total}={jp/total:.1%})，回退逐字重排...")

    # ── 第三步：逐字重排回退，复用 LLM 参数 ──
    all_chars = _sample_chars_fitz(file_path, max_pages=999)
    if not all_chars:
        return fitz_result

    # body_y 过滤
    if layout and layout.get('body_y_min', 0) > 0:
        ymin = layout['body_y_min']; ymax = layout.get('body_y_max', 99999)
        all_chars = [ch for ch in all_chars if ymin <= ch['y'] <= ymax]

    # 排向：优先 LLM，其次本地检测
    if layout and layout.get('orientation') == 'horizontal':
        row_gap = float(layout.get('row_gap', 8.0))
        result = _reorder_fitz_horizontal(all_chars, row_gap=row_gap)
    elif layout and layout.get('orientation') == 'vertical':
        col_gap = float(layout.get('col_gap', 24.0))
        result = _reorder_fitz_vertical(all_chars, col_gap=col_gap)
    else:
        is_h = _detect_is_horizontal(all_chars)
        if is_h:
            result = _reorder_fitz_horizontal(all_chars, row_gap=8.0)
        else:
            result = _reorder_fitz_vertical(all_chars, col_gap=24.0)

    result = _remove_cjk_spaces(result)
    result = _clean_number_lines(result)

    from utils.text_filter import _filter_page_numbers
    return _filter_page_numbers(result)


def _extract_pdf_text_fitz(file_path: Path, layout: dict = None) -> Optional[str]:
    """PyMuPDF 提取，支持 LLM 排版参数（body_y 过滤页眉页码）"""
    try:
        import fitz
    except ImportError:
        return None

    body_ymin = layout.get('body_y_min', 0) if layout else 0
    body_ymax = layout.get('body_y_max', 99999) if layout else 99999
    has_body_filter = body_ymin > 0

    try:
        text_blocks: list[str] = []
        with fitz.open(str(file_path)) as doc:
            for page in doc:
                page_h = page.rect.height
                if has_body_filter:
                    # 用 block 级别坐标过滤
                    blocks = page.get_text("blocks")
                    page_lines = []
                    for b in blocks:
                        # b = (x0, y0, x1, y1, text, block_no, block_type)
                        y0, y1 = b[1], b[3]
                        text = b[4] if len(b) > 4 else ''
                        if not text or not text.strip():
                            continue
                        # block 的 Y 范围与 body_y 有交集则保留
                        if y1 >= body_ymin and y0 <= body_ymax:
                            page_lines.append(text.strip())
                    if page_lines:
                        text_blocks.append('\n'.join(page_lines))
                else:
                    text = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                    if text and text.strip():
                        text_blocks.append(text.strip())

        return '\n\n'.join(text_blocks) if text_blocks else None

    except Exception as e:
        print(f"  PyMuPDF 提取失败: {e}")
        return None


def _detect_is_horizontal(chars: list[dict], max_sample_pages: int = 3) -> bool:
    """检测 PDF 是否为横排：对前几页同时尝试竖排/横排，比较输出文本质量

    同一作品所有页面排向一致。若无法判定，默认竖排（保持原有行为）。

    返回: True = 横排, False = 竖排
    """
    from collections import defaultdict

    if not chars:
        return False

    pages = defaultdict(list)
    for ch in chars:
        pages[ch["page"]].append(ch)

    sample_pages = sorted(pages.keys())[:max_sample_pages]
    votes_horizontal = 0
    votes_vertical = 0

    for pg in sample_pages:
        page_chars = pages[pg]
        if len(page_chars) < 30:
            continue  # 跳过封面等字符太少的页面

        # 尝试竖排
        vert_lines = _reorder_vertical_page(page_chars)
        # 尝试横排
        horz_lines = _reorder_horizontal_page(page_chars)

        vert_score = _score_text_quality(vert_lines)
        horz_score = _score_text_quality(horz_lines)

        if horz_score > vert_score:
            votes_horizontal += 1
        elif vert_score > horz_score:
            votes_vertical += 1

    # 无法判定时默认竖排（保持原有行为）
    if votes_horizontal == 0 and votes_vertical == 0:
        return False

    return votes_horizontal > votes_vertical


def _score_text_quality(lines: list[str]) -> float:
    """评分文本质量：CJK 字符连续度越高、孤立符号越少，分数越高"""
    if not lines:
        return 0.0

    total_score = 0.0
    total_len = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.isdigit():
            continue

        length = len(stripped)
        total_len += length

        cjk = len(re.findall(r'[぀-ゟ゠-ヺ一-鿿⺀-⻿㈀-㋿㐀-䶿豈-﫿]', stripped))
        symbols = len(re.findall(r'[♡♪/＝・…※✧⩌⩊]', stripped))

        cjk_ratio = cjk / max(length, 1)
        symbol_penalty = symbols / max(length, 1) * 2.0

        line_score = cjk_ratio - symbol_penalty
        total_score += line_score * length

    return total_score / max(total_len, 1)


def _reorder_vertical_page(page_chars: list[dict], col_gap: float = 24.0) -> list[str]:
    """竖排单页（供 _detect_is_horizontal 内部使用）"""
    from collections import defaultdict

    # 过滤控制字符
    _ctrl = {'\r', '\n', '\t', '\x00', '\x0c', '\x0b'}
    page_chars = [ch for ch in page_chars
                  if ch["text"] not in _ctrl and ch["text"].strip()]

    if not page_chars:
        return []

    columns = defaultdict(list)
    for ch in page_chars:
        col_key = round(ch["x"] / col_gap) * col_gap
        columns[col_key].append(ch)

    columns = _merge_orphan_columns(columns, axis="x")

    sorted_cols = sorted(columns.items(), key=lambda kv: -kv[0])

    page_lines: list[str] = []
    for _col_x, col_chars in sorted_cols:
        col_chars.sort(key=lambda ch: ch["y"])
        line = "".join(ch["text"] for ch in col_chars)
        stripped = line.strip()
        if stripped and not stripped.isdigit():
            page_lines.append(stripped)

    return page_lines


def _reorder_horizontal_page(page_chars: list[dict], row_gap: float = 8.0) -> list[str]:
    """横排单页（供 _detect_is_horizontal 内部使用）"""
    from collections import defaultdict

    rows = defaultdict(list)
    for ch in page_chars:
        row_key = round(ch["y"] / row_gap) * row_gap
        rows[row_key].append(ch)

    rows = _merge_orphan_columns(rows, axis="y")

    sorted_rows = sorted(rows.items(), key=lambda kv: kv[0])

    page_lines: list[str] = []
    for _row_y, row_chars in sorted_rows:
        row_chars.sort(key=lambda ch: ch["x"])
        line = "".join(ch["text"] for ch in row_chars
                      if not ch["text"].strip().isdigit())
        stripped = line.strip()
        if stripped and not stripped.isdigit():
            page_lines.append(stripped)

    return page_lines


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
    api_config: dict = None,
) -> dict[int, list[str]]:
    """
    构建音轨→原始台本行映射（不做结构化解析，保留全文）

    参数:
        scriptbook_files: 台本文件列表
        api_config: API 配置，用于 LLM 分析 PDF 排版参数

    返回:
        {track_num: [raw_line1, raw_line2, ...]}
    """
    track_map: dict[int, list[str]] = {}

    for idx, sb_file in enumerate(scriptbook_files):
        track_num = idx + 1
        content = load_scriptbook_content(sb_file, api_config=api_config)
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
