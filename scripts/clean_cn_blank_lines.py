# -*- coding: utf-8 -*-
"""删除中文 LRC 字幕里的空时间轴行（清屏行）。

背景:
    LRC 里 `[mm:ss.xx]` 后面没有文本的行是「清屏行」，在播放器里会显示成
    一条长时间的空字幕。中文译文不需要它；日文原文（`.ja.lrc`）需要保留。

规则:
    - 默认只处理 `.lrc`，且只处理 `X.lrc` 旁边存在 `X.ja.lrc` 的文件
      （证明它是本管道翻译产物，而不是游戏/其他来源的 LRC）。
    - 语言检测：必须含汉字；默认按「假名 ≤ 汉字 10%」判为中文
      （与 `io_adapter.lrc_handler.detect_lrc_language` 一致），
      `--strict-kana` 可收紧为「必须零假名」。
    - `.ja.*` 一律跳过。
    - 只删除「只有时间标签、没有文本」的行；`[ti:]`/`[ar:]` 等元数据保留。
    - 默认 dry-run；加 `--apply` 才写回，写回前默认备份为 `*.blankclean.bak`。

用法::

    python scripts/clean_cn_blank_lines.py "E:/奥术"
    python scripts/clean_cn_blank_lines.py "E:/奥术" --apply
    python scripts/clean_cn_blank_lines.py "E:/奥术/RJ01745725" --apply --no-backup
    python scripts/clean_cn_blank_lines.py "E:/奥术" --exts lrc,srt,vtt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TIME_TAG = re.compile(r'\[\d+:\d+(?:\.\d+)?\]')
_KANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]')
_HAN = re.compile(r'[\u4e00-\u9fff]')


def _is_ja_file(path: Path) -> bool:
    return path.suffix.lower() in ('.lrc', '.srt', '.vtt') and \
        path.stem.lower().endswith('.ja')


def _texts_for_lang(content: str, ext: str) -> str:
    if ext == '.lrc':
        parts = []
        for line in content.splitlines():
            tag = _TIME_TAG.search(line)
            if tag:
                parts.append(_TIME_TAG.sub('', line).strip())
        return '\n'.join(parts)
    # srt/vtt: 去掉序号/时间轴/格式行，只留文本
    parts = []
    for line in content.splitlines():
        s = line.strip()
        if not s or s.isdigit() or '-->' in s:
            continue
        if s == 'WEBVTT' or s.startswith(('NOTE', 'STYLE')):
            continue
        parts.append(s)
    return '\n'.join(parts)


def _is_chinese(content: str, ext: str, strict_kana: bool,
                ja_content: str | None = None) -> bool:
    text = _texts_for_lang(content, ext)
    han = len(_HAN.findall(text))
    kana = len(_KANA.findall(text))
    if han <= 0:
        return False
    if kana == 0:
        return True
    if strict_kana:
        return False
    if kana <= han * 0.1:
        return True
    # 短标题/宣传轨：中文里夹着日文 CV/社团名，假名占比会超过 10%。
    # 用同 stem 的日文留档做参照：译文假名数通常远低于原文。
    if ja_content:
        ja_text = _texts_for_lang(ja_content, ext)
        ja_kana = len(_KANA.findall(ja_text))
        if ja_kana > 0 and kana <= ja_kana * 0.5:
            return True
    return False


def _clean_lrc(content: str) -> tuple[str, int]:
    out: list[str] = []
    removed = 0
    for line in content.splitlines():
        s = line.strip()
        if not s:
            removed += 1
            continue
        if _TIME_TAG.search(s) and not _TIME_TAG.sub('', s).strip():
            removed += 1
            continue
        out.append(line)
    if not out:
        return '', removed
    return '\n'.join(out) + '\n', removed


def _clean_srt(content: str, ext: str) -> tuple[str, int]:
    """删除「有时间轴但没有文本」的空字幕块；保留块间空行。"""
    blocks = re.split(r'\n[ \t]*\n', content.replace('\r\n', '\n').strip('\n'))
    kept: list[str] = []
    removed = 0
    header: list[str] = []
    for i, block in enumerate(blocks):
        s = block.strip()
        if not s:
            continue
        if s.startswith('WEBVTT') or s.startswith(('NOTE', 'STYLE')):
            header.append(s)
            continue
        if '-->' not in s:
            kept.append(s)
            continue
        lines = s.splitlines()
        ti = next((j for j, ln in enumerate(lines) if '-->' in ln), -1)
        body = [ln for ln in lines[ti + 1:] if ln.strip()] if ti >= 0 else []
        if not body:
            removed += 1
            continue
        kept.append(s)
    all_blocks = header + kept
    if not all_blocks:
        return '', removed
    return '\n\n'.join(all_blocks) + '\n', removed


def _has_ja_sibling(path: Path) -> bool:
    ja = path.with_name(f'{path.stem}.ja{path.suffix}')
    return ja.is_file()


def _iter_targets(root: Path, exts: set[str]):
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        if _is_ja_file(p):
            continue
        yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='删除中文 LRC 的空时间轴行')
    ap.add_argument('root', help='作品根目录（会递归扫描）')
    ap.add_argument('--apply', action='store_true', help='实际写回（默认 dry-run）')
    ap.add_argument('--exts', default='lrc',
                    help='要处理的扩展名，逗号分隔（默认 lrc）')
    ap.add_argument('--no-backup', action='store_true', help='写回前不备份')
    ap.add_argument('--strict-kana', action='store_true',
                    help='只处理零假名文件（默认允许 ≤10%% 假名，与管道语言检测一致）')
    ap.add_argument('--no-ja-sibling', action='store_true',
                    help='不要求存在 X.ja.* 兄弟文件（默认要求）')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        print(f'路径不存在: {root}')
        return 2

    exts = {('.' + e.strip().lower().lstrip('.')) for e in args.exts.split(',') if e.strip()}
    exts &= {'.lrc', '.srt', '.vtt'}
    if not exts:
        print('没有可处理的扩展名')
        return 2

    scanned = skipped_lang = skipped_ja = skipped_noja = 0
    changed = 0
    removed_total = 0
    for p in _iter_targets(root, exts):
        scanned += 1
        if not args.no_ja_sibling and not _has_ja_sibling(p):
            skipped_noja += 1
            continue
        try:
            content = p.read_text(encoding='utf-8')
        except Exception:
            skipped_lang += 1
            continue
        ja_content = None
        if not args.no_ja_sibling:
            try:
                ja_content = p.with_name(
                    f'{p.stem}.ja{p.suffix}').read_text(encoding='utf-8')
            except Exception:
                ja_content = None
        if not _is_chinese(content, p.suffix.lower(), args.strict_kana,
                           ja_content):
            skipped_lang += 1
            continue
        if p.suffix.lower() == '.lrc':
            new, removed = _clean_lrc(content)
        else:
            new, removed = _clean_srt(content, p.suffix.lower())
        if not removed or new == content:
            continue
        changed += 1
        removed_total += removed
        if args.verbose or changed <= 10:
            print(f'  {removed:4d} 行  {p}')
        if args.apply:
            if not args.no_backup:
                bak = p.with_name(p.name + '.blankclean.bak')
                if not bak.exists():
                    bak.write_text(content, encoding='utf-8')
            p.write_text(new, encoding='utf-8')

    mode = '已写回' if args.apply else 'dry-run（未写回）'
    print()
    print(f'[{mode}] 扫描 {scanned} 个文件 | 命中 {changed} 个 | 删除空行 {removed_total}')
    print(f'  跳过: 无 .ja 兄弟 {skipped_noja} | 非中文/无法读取 {skipped_lang}')
    if not args.apply and changed:
        print('  确认无误后加 --apply 写回')
    return 0


if __name__ == '__main__':
    sys.exit(main())
