# -*- coding: utf-8 -*-
"""从本地真实作品生成 CI 测试素材（只复制「输入」，不复制缓存/音频）。

用法::

    python scripts/prepare_fixtures.py --source-root "E:/奥术"

产物::

    tests/fixtures/works/<RJ号>/
        <台本文件(.pdf/.txt)>
        <每轨 .ja.lrc>

说明:
- 不复制音频（转录已移出 CI 范围）、不复制 .lrc（让管道从 .ja.lrc 冷启动）。
- 不复制缓存/产物: _scriptbook_clean.json / _split_tracks/ / .terms.json /
  .alias.json / .worldview.json / _scriptbook_export.txt。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# fixture key → (源目录, 说明)
WORK_SOURCES = {
    'RJ01615872': ('RJ01615872', 'PDF 未分割台本'),
    'RJ01702644': ('RJ01702644', '无台本'),
    'RJ01653978': ('RJ01653978', 'TXT 预分割台本'),
    'RJ01671823': ('RJ01671823', 'TXT 未分割台本'),
    'RJ01654985': ('RJ01654985 (2)', 'PDF 预分割台本'),
}

_EXCLUDE_DIRS = {'_split_tracks', '__pycache__'}
_EXCLUDE_NAMES = {
    '_scriptbook_clean.json', '_scriptbook_export.txt',
    '.terms.json', '.alias.json', '.worldview.json',
    'desktop.ini',
}


def _is_input_file(path: Path) -> bool:
    name = path.name
    if name in _EXCLUDE_NAMES:
        return False
    if name.startswith('_'):
        return False
    if name.endswith('.ja.lrc'):
        return True
    return path.suffix.lower() in ('.pdf', '.txt')


def _copy_work(src_dir: Path, dst_dir: Path) -> int:
    count = 0
    for src in src_dir.rglob('*'):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        if not _is_input_file(src):
            continue
        dst = dst_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description='生成 CI 测试素材')
    parser.add_argument('--source-root', required=True, help='本地音声作品根目录，如 E:/奥术')
    parser.add_argument('--out', default=None, help='输出目录（默认 tests/fixtures/works）')
    args = parser.parse_args()

    source_root = Path(args.source_root)
    out_dir = Path(args.out) if args.out else (Path(__file__).resolve().parent.parent / 'tests' / 'fixtures' / 'works')
    out_dir.mkdir(parents=True, exist_ok=True)

    if not source_root.exists():
        print(f'[错误] 源目录不存在: {source_root}', file=sys.stderr)
        return 1

    rc = 0
    for key, (src_name, note) in WORK_SOURCES.items():
        src_dir = source_root / src_name
        if not src_dir.exists():
            print(f'[跳过] {key}: 源目录不存在 {src_dir}')
            rc = 1
            continue
        dst_dir = out_dir / key
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        n = _copy_work(src_dir, dst_dir)
        print(f'[OK] {key} ({note}): 复制 {n} 个输入文件 → {dst_dir}')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
