"""
竖排文本重排脚本
从 PDF 提取每个字符的 (text, x, y, w, h, page)，按 X 聚类成列，
列从右到左排序，列内从 Y 升序（上到下）。
"""
import pypdfium2 as pdfium
import json, os, sys
from collections import defaultdict
from pathlib import Path

PDF_FILE = "test.pdf"
OUT_JSON = "chars.json"
OUT_MD = "output.md"

# ── 1. 提取所有字符及位置 ──────────────────────────────────────
def extract_chars(pdf_path: str) -> list[dict]:
    """从 PDF 逐页提取每个字符的文本和 bbox"""
    pdf = pdfium.PdfDocument(pdf_path)
    total_pages = len(pdf)
    all_chars = []

    for pg_idx in range(total_pages):
        page = pdf[pg_idx]
        tp = page.get_textpage()
        n = tp.count_chars()
        for ci in range(n):
            try:
                box = tp.get_charbox(ci)   # (x1, y1, x2, y2) — bottom-left origin
                c = tp.get_text_range(index=ci, count=1)
                x, y1, w_box, y2 = box[0], box[1], box[2] - box[0], box[3] - box[1]
                h = abs(y2)
                # Flip Y to top-left origin
                page_h = page.get_height()
                y = page_h - max(y1, y2)
                all_chars.append({
                    "text": c,
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "w": round(w_box, 1),
                    "h": round(h, 1),
                    "page": pg_idx + 1,
                    "page_w": round(page.get_width(), 1),
                    "page_h": round(page_h, 1),
                })
            except Exception:
                pass
    pdf.close()
    return all_chars


# ── 2. 竖排重排 ──────────────────────────────────────────────────
def reorder_vertical(chars: list[dict], col_gap: float = 15.0) -> str:
    """
    按 X 聚类成列（右→左），列内按 Y 排序（上→下）。
    使用 round(x / col_gap) 进行聚类。
    """
    if not chars:
        return ""

    # 按页分组
    pages = defaultdict(list)
    for ch in chars:
        pages[ch["page"]].append(ch)

    output_lines = []

    for pg in sorted(pages.keys()):
        page_chars = pages[pg]
        if not page_chars:
            continue

        page_w = page_chars[0]["page_w"]

        # X 聚类成列（取整到最近的 col_gap）
        columns = defaultdict(list)
        for ch in page_chars:
            col_key = round(ch["x"] / col_gap) * col_gap
            columns[col_key].append(ch)

        # 列排序：右→左（X 降序）
        sorted_cols = sorted(columns.items(), key=lambda kv: -kv[0])

        page_lines = []
        for col_x, col_chars in sorted_cols:
            # 列内：上→下（Y 升序），跳过纯数字字符
            col_chars.sort(key=lambda ch: ch["y"])
            line = "".join(ch["text"] for ch in col_chars
                          if not ch["text"].strip().isdigit())
            stripped = line.strip()
            # 跳过空行和纯数字行
            if stripped and not stripped.isdigit():
                page_lines.append(stripped)

        output_lines.append(f"<!-- page {pg} -->")
        output_lines.extend(page_lines)
        output_lines.append("")

    return "\n".join(output_lines)


# ── 3. 横排重排（对横排页面降级处理）─────────────────────────────
def is_vertical_page(page_chars: list[dict]) -> bool:
    """检测页面是否为竖排：X 方差小、Y 方差大"""
    if len(page_chars) < 10:
        return False
    xs = [ch["x"] for ch in page_chars]
    ys = [ch["y"] for ch in page_chars]
    hs = [ch["h"] for ch in page_chars]
    ws = [ch["w"] for ch in page_chars]

    # 标准差
    import statistics
    x_std = statistics.stdev(xs) if len(xs) > 1 else 0
    y_std = statistics.stdev(ys) if len(ys) > 1 else 0
    avg_h = sum(hs) / len(hs)
    avg_w = sum(ws) / len(ws)

    # 竖排特征：字符高度 > 宽度，Y 跨越大
    return (avg_h > avg_w * 1.5) and (y_std > x_std * 2)


# ── Main ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 提取
    print("提取字符位置...")
    chars = extract_chars(PDF_FILE)
    print(f"  共 {len(chars)} 个字符")

    # 保存中间 JSON（可查）
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(chars, f, ensure_ascii=False)
    print(f"  已保存: {OUT_JSON}")

    # 重排
    print("竖排重排...")
    markdown = reorder_vertical(chars, col_gap=12.0)

    # 后处理：去除 CJK 字符间空格（pypdfium2 也可能插入空格）
    import re as re_mod
    markdown = re_mod.sub(
        r"(?<=[⺀-⻿　-〿぀-ゟ゠-ヿ"
        r"㈀-㋿㐀-䶿一-鿿豈-﫿"
        r"＀-￯])\s+(?=[⺀-⻿　-〿"
        r"぀-ゟ゠-ヿ㈀-㋿"
        r"㐀-䶿一-鿿豈-﫿＀-￯])",
        "",
        markdown,
    )

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(markdown)

    # 最终数字清理：去除残留的独立数字/编号行
    clean_lines = []
    for ln in markdown.splitlines():
        s = ln.strip()
        # 跳过空行、页面标记、纯数字行
        if not s or s.startswith("<!--") or s.isdigit():
            clean_lines.append(ln)
            continue
        # 移除行首的数字标记（如 "109も" → "も"）
        import re
        s2 = re.sub(r'^\d{1,3}(?=[^\d\s])', '', s)
        if s2 and not s2.isdigit():
            clean_lines.append(s2)
        else:
            clean_lines.append("")
    markdown = "\n".join(clean_lines)

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"  已保存: {OUT_MD}")
    print(f"  行数: {len(markdown.splitlines())}")
