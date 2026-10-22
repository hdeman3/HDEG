# -*- coding: utf-8 -*-
"""stdin/stdout 包装：读 {"work_title","tracks"} → 输出翻译结果 JSON。

供外部程序以子进程方式调用（语言无关）：

    echo {"work_title":"RJxxx 原题","tracks":["01. …","Tr2_…"]} | python -m engines.track_titles --stdin

    打包版： main.exe --run-script scripts/track_titles_stdin.py   （stdin 同样传 JSON）

输入（stdin，JSON）:
    {"work_title": "原作品名", "tracks": ["原音轨名1", "原音轨名2"], "config_path": "可选"}

输出（stdout，JSON）:
    {"work_title": "中文作品名", "work_title_original": "原作品名",
     "tracks": {"原音轨名": "中文音轨名", ...}}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 兼容直接运行 / PyInstaller --run-script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.track_titles import translate_titles  # noqa: E402


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'输入 JSON 解析失败: {e}'}, ensure_ascii=False))
        return 2

    work_title = payload.get('work_title') or payload.get('title') or ''
    tracks = payload.get('tracks') or payload.get('track_names') or []
    config_path = payload.get('config_path')

    data = translate_titles(
        work_title, tracks,
        config_path=config_path,
        log=lambda m: print(f'[标题] {m}', file=sys.stderr, flush=True),
    )
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
