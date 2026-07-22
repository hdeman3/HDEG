# -*- coding: utf-8 -*-
"""
翻译一致性检查器

扫描已翻译的字幕文件，检测：
1. 同一日文词在不同文件/位置译法不一致
2. 术语表定义了但翻译中未使用
3. 术语表定义与翻译实际用词偏离

纯本地实现，不依赖 LLM。
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from collections import defaultdict
from typing import TypedDict

# ── 类型定义 ──

class ConflictItem(TypedDict):
    """一个术语的翻译冲突"""
    source: str              # 日文原文
    translations: list[str]  # 所有不同译文
    files: list[str]         # 出现文件
    count: int               # 出现次数
    severity: str            # "high" | "medium" | "low"

class TermCoverageItem(TypedDict):
    """术语覆盖情况"""
    source: str              # 日文词
    expected: str            # 术语表定义的译文
    actual: str | None       # 实际使用的译文 (None = 未使用)
    found: bool              # 是否在译文中找到
    match: bool              # 译文是否与术语表一致

class ConsistencyReport(TypedDict):
    """一致性检查报告"""
    work_dir: str
    total_files: int
    total_lines: int
    conflicts: list[ConflictItem]
    uncovered_terms: list[TermCoverageItem]
    summary: dict


# ── 辅助：从字幕文件提取纯文本 ──

def _extract_subtitle_texts(file_path: Path) -> list[str]:
    """提取字幕中的纯文本行 (LRC/SRT/VTT)"""
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception:
        return []

    ext = file_path.suffix.lower()
    lines = content.split('\n')
    texts: list[str] = []

    if ext == '.lrc':
        tag_re = re.compile(r'^(\[.*?\])\s*(.*)')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = tag_re.match(line)
            if m and re.search(r'\[\d+:\d{2}\.\d{2,3}\]', m.group(1)):
                texts.append(m.group(2))
    elif ext == '.srt':
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.isdigit():
                i += 1
                if i < len(lines) and '-->' in lines[i]:
                    i += 1
                    while i < len(lines) and lines[i].strip():
                        texts.append(lines[i].strip())
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
                    nxt = lines[i].strip()
                    if not nxt or '-->' in nxt:
                        break
                    texts.append(nxt)
                    i += 1
            else:
                i += 1

    return texts


# ── 日语分词 ──

def _tokenize_japanese(text: str) -> list[str]:
    """日语分词——优先 MeCab/fugashi，回退到简单规则"""
    try:
        import fugashi
        tagger = fugashi.Tagger()
        words = []
        for word in tagger(text):
            # 只保留名词、动词、形容词、副词
            feats = str(word.feature)
            if feats:
                pos = feats.split(',')[0]
                if pos in ('名詞', '動詞', '形容詞', '副詞', '形状詞'):
                    surf = word.surface
                    if len(surf) >= 2 and not re.match(r'^[\d\s\d０-９]+$', surf):
                        words.append(surf)
        return words
    except ImportError:
        pass

    # 回退：基于字符类型的简单切分
    words = []
    # 用标点/空格切分
    for token in re.split(r'[、。！？\s　\!\?\.\,　]+', text):
        token = token.strip()
        if len(token) >= 2:
            # 去掉纯数字/符号
            if not re.match(r'^[\d\s\d０-９\W_]+$', token):
                words.append(token)
    return words


# ── 术语加载 ──

def _load_terms(work_dir: Path) -> dict[str, str]:
    """加载作品术语表"""
    for name in ('.terms.json', 'terms.json'):
        path = work_dir / name
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                pass
    return {}


# ── 主检查逻辑 ──

def check_consistency(work_dir: Path) -> ConsistencyReport:
    """扫描目录下所有已翻译文件，检测术语一致性

    参数:
        work_dir: 作品根目录（如 RJ01552944/）

    返回:
        ConsistencyReport 字典
    """
    work_dir = work_dir.resolve()
    term_map: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    file_stats: list[tuple[str, int]] = []

    # ─── 1. 收集所有日-中对应 ───
    for lrc_path in sorted(work_dir.rglob('*.lrc')):
        if lrc_path.stem.endswith('.ja'):
            continue
        ja_path = lrc_path.parent / f'{lrc_path.stem}.ja{lrc_path.suffix}'
        if not ja_path.exists():
            continue

        ja_texts = _extract_subtitle_texts(ja_path)
        cn_texts = _extract_subtitle_texts(lrc_path)
        if not ja_texts or not cn_texts:
            continue

        min_len = min(len(ja_texts), len(cn_texts))
        file_stats.append((lrc_path.name, min_len))

        rel_name = str(lrc_path.relative_to(work_dir))
        for i in range(min_len):
            ja_line = ja_texts[i].strip()
            cn_line = cn_texts[i].strip()
            if not ja_line or not cn_line:
                continue

            # 对日文分词
            words = _tokenize_japanese(ja_line)
            for w in words:
                term_map[w][cn_line].add(rel_name)

    # ─── 2. 检测冲突 ───
    conflicts: list[ConflictItem] = []
    for source, trans_map in term_map.items():
        unique_trans = list(trans_map.keys())
        if len(unique_trans) < 2:
            continue

        all_files: set[str] = set()
        for f_set in trans_map.values():
            all_files.update(f_set)
        total_count = sum(len(v) for v in trans_map.values())

        # 严重程度：译文差异大 → high；差异小 → medium
        severity = "medium"
        if len(unique_trans) >= 3:
            severity = "high"
        elif len(unique_trans) == 2:
            # 检查两个译文的相似度
            t1, t2 = unique_trans[0], unique_trans[1]
            common = set(t1) & set(t2)
            if len(common) < max(len(set(t1)), len(set(t2))) * 0.3:
                severity = "high"

        conflicts.append({
            'source': source,
            'translations': unique_trans[:5],
            'files': sorted(all_files)[:10],
            'count': total_count,
            'severity': severity,
        })

    # 按严重程度和出现次数排序
    conflicts.sort(key=lambda c: (
        0 if c['severity'] == 'high' else 1 if c['severity'] == 'medium' else 2,
        -c['count'],
    ))

    # ─── 3. 术语表覆盖检查 ───
    terms = _load_terms(work_dir)
    uncovered: list[TermCoverageItem] = []
    if terms:
        all_ja_text = ' '.join(
            t for f in work_dir.rglob('*.ja.lrc')
            for t in _extract_subtitle_texts(f)
        )
        for src, expected in terms.items():
            found = src in all_ja_text
            actual = None
            match = False
            if found and src in term_map:
                actual_trans = list(term_map[src].keys())
                actual = ' / '.join(actual_trans[:3])
                match = expected in actual_trans
            uncovered.append({
                'source': src,
                'expected': expected,
                'actual': actual,
                'found': found,
                'match': match,
            })

    # ─── 4. 汇总 ───
    total_lines = sum(n for _, n in file_stats)
    high_count = sum(1 for c in conflicts if c['severity'] == 'high')
    medium_count = sum(1 for c in conflicts if c['severity'] == 'medium')
    unmatched_terms = sum(1 for u in uncovered if u['found'] and not u['match'])
    unused_terms = sum(1 for u in uncovered if not u['found'])

    return {
        'work_dir': str(work_dir),
        'total_files': len(file_stats),
        'total_lines': total_lines,
        'conflicts': conflicts,
        'uncovered_terms': uncovered,
        'summary': {
            'conflict_count': len(conflicts),
            'high_conflicts': high_count,
            'medium_conflicts': medium_count,
            'unmatched_terms': unmatched_terms,
            'unused_terms': unused_terms,
            'total_terms_defined': len(terms),
        },
    }


# ── CLI 入口 ──

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python consistency_checker.py <作品目录>")
        sys.exit(1)

    report = check_consistency(Path(sys.argv[1]))
    print(json.dumps(report, ensure_ascii=False, indent=2))
