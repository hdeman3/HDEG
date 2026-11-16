# -*- coding: utf-8 -*-
"""音轨名翻译（只写 JSON，不改文件名）。

两种使用场景：
- **pipeline 自动执行**（第 5.5 步）：作品名留空，**只翻译音轨名**。
- **接口调用**（调用方提供作品名 + 音轨名）：**作品名与音轨名都翻译并写出**。

输出 JSON 结构::

    {
      "work_title": "中文作品名（自动执行时为空）",
      "work_title_original": "原作品名（自动执行时为空）",
      "tracks": {
        "01. 元ギャル人妻…": "01. 原辣妹人妻…",
        "Tr2_…": "Tr2_…"
      }
    }

- ``tracks``：{原始音轨名(不带扩展名): 中文名}
- 编号/前缀（01.、Tr1_ 等）由模型保留；失败时回退原文。

对外接口（由调用方提供「作品名 + 音轨名」）
------------------------------------------------
Python::

    from engines.track_titles import translate_titles
    data = translate_titles(
        'RJ01681589 元ギャル人妻 …',      # 作品名
        ['01. 凌晨3点…', 'Tr2_…'],        # 音轨名列表
        config_path='config.json',        # 可选
    )
    # data = {'work_title': '中文作品名',
    #         'work_title_original': '原作品名',
    #         'tracks': {'原音轨名': '中文音轨名', ...}}

CLI（stdin/stdout JSON，语言无关）::

    echo {"work_title":"RJxxx 原题","tracks":["01. …","Tr2_…"]} | python -m engines.track_titles --stdin

CLI（参数形式）::

    python -m engines.track_titles --work-title "RJxxx 原题" --track "01. …" --track "Tr2_…"

CLI（按目录自动收集音轨名）::

    python -m engines.track_titles "D:/works/RJ01681589" [--out-dir DIR]

结果始终以 JSON 打印到 stdout；日志走 stderr（`--stdin` 模式 stdout 保持纯 JSON）。
可选 `--json-out 文件` / `--out-dir 目录`（后者写 `<dir>/_track_names.json`）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

NAMES_SYSTEM_PROMPT = (
    '你是音声作品标题翻译助手。把日文作品名与音轨名翻译成自然、简洁的中文标题。'
    '保留原有编号/前缀（如 RJ01681589、01.、Tr1_、#1、【…】）与专有名词设定；'
    '编号/前缀不翻译，不要增删编号，不要输出任何解释。只输出 JSON。'
)

DEFAULT_AUDIO_EXTS = {
    '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma',
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv',
}
DEFAULT_SUB_EXTS = {'.lrc', '.srt', '.vtt'}
_EXCLUDE_DIRS = {'raw', '_split_tracks', 'translate_logs', 'Hde_G_bug_lrc_collection'}


# ==================== 音轨名收集 ====================

def collect_track_stems(work_dir, audio_exts=None, subtitle_exts=None) -> list[str]:
    """收集作品目录下的音轨名（音频或字幕文件 stem，去重排序）。"""
    audio = {str(e).lower() for e in (audio_exts or DEFAULT_AUDIO_EXTS)}
    subs = {str(e).lower() for e in (subtitle_exts or DEFAULT_SUB_EXTS)}
    seen: dict[str, None] = {}
    for p in sorted(Path(work_dir).rglob('*')):
        if not p.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        ext = p.suffix.lower()
        is_audio = ext in audio
        is_sub = ext in subs and not p.name.endswith('.ja' + ext)
        if is_audio or is_sub:
            seen.setdefault(p.stem, None)
    return sorted(seen.keys())


# ==================== LLM 翻译 ====================

def _build_user_prompt(work_title: str, stems: list[str],
                       translate_work_title: bool = False) -> str:
    lines = []
    if translate_work_title and work_title:
        lines += [f'【作品名】{work_title}', '']
    lines.append('【音轨名列表】')
    for i, s in enumerate(stems):
        lines.append(f'  {i}: {s}')
    lines.append('')
    if translate_work_title and work_title:
        lines += [
            '【输出】仅 JSON：{"work_title": "中文作品名", "names": {"0": "中文标题", "1": "..."}}',
            '  作品名保留开头的 RJ 编号（如 RJ01681589），只翻译其后的标题部分；',
            '  每个音轨编号都必须出现；无法翻译或不需翻译时填原样。',
        ]
    else:
        # 默认：只翻译音轨名（作品名由调用方提供，原样返回）
        lines += [
            '【输出】仅 JSON：{"names": {"0": "中文标题", "1": "..."}}',
            '  每个编号都必须出现；无法翻译或不需翻译时填原样。',
        ]
    return '\n'.join(lines)


def _parse(content: str, n: int) -> tuple[str | None, dict[int, str]]:
    m = re.search(r'\{.*\}', content or '', re.S)
    if not m:
        return None, {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    work_zh = data.get('work_title')
    work_zh = work_zh.strip() if isinstance(work_zh, str) and work_zh.strip() else None
    names = data.get('names') if isinstance(data.get('names'), dict) else {}
    out: dict[int, str] = {}
    for k, v in names.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n and isinstance(v, str) and v.strip():
            out[idx] = v.strip()
    return work_zh, out


def translate_names(engine, work_title: str, stems: list[str],
                    translate_work_title: bool = False, log=None):
    """一次请求翻译「（可选）作品名 + 全部音轨名」。

    默认只翻译音轨名；作品名原样返回（由调用方提供）。
    返回: ({'work_title','work_title_original','tracks'}, stats)
    与现有流程一致：不覆盖任何生成参数，沿用全局模型/温度/token 上限。
    """
    if not stems and not (translate_work_title and work_title):
        return {}, None
    user = _build_user_prompt(work_title, stems, translate_work_title)
    t0 = time.time()
    content, stats = engine.call_api(NAMES_SYSTEM_PROMPT, user, log_io=False)
    elapsed = time.time() - t0
    if isinstance(stats, dict):
        stats['elapsed'] = elapsed
    work_zh, parsed = _parse(content, len(stems))
    tracks = {s: parsed.get(i, s) for i, s in enumerate(stems)}
    if translate_work_title:
        wt, wto = (work_zh or work_title or ''), (work_title or '')
    else:
        # 默认：作品名相关字段留空（只产出音轨名）
        wt, wto = '', ''
    result = {
        'work_title': wt,
        'work_title_original': wto,
        'tracks': tracks,
    }
    if log:
        if translate_work_title:
            log(f"作品名 {'已译' if work_zh else '未译'}，音轨 {len(parsed)}/{len(stems)} 条，{elapsed:.1f}s")
        else:
            log(f"音轨 {len(parsed)}/{len(stems)} 条，{elapsed:.1f}s")
    return result, stats


# ==================== 写 JSON ====================

def write_track_names(out_dir: Path, data: dict) -> Path:
    """写入 <out_dir>/_track_names.json（UTF-8 无 BOM）。"""
    path = Path(out_dir) / '_track_names.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return path


# ==================== 对外接口 ====================

def _build_engine(config_path=None):
    from io_adapter.config_loader import load_config, get_api_config
    from engines.translate_engine import create_translate_engine
    cfg = load_config(Path(config_path) if config_path else None)
    return create_translate_engine(
        get_api_config(cfg), verbose=False, pricing=cfg.get('pricing'))


def translate_titles(
    work_title='',
    track_names: list[str] = None,
    *,
    works: list = None,
    translate_work_title: bool = True,
    config_path=None,
    engine=None,
    parallel: int = 0,
    log=None,
):
    """核心对外接口：支持「单个作品」与「多个作品（并发）」。

    单个作品::

        translate_titles('RJxxx 原题', ['01. …', 'Tr2_…'])
        # → {'work_title': '中文作品名', 'work_title_original': '原作品名',
        #    'tracks': {'原音轨名': '中文音轨名', ...}}

    多个作品（一次传入，按 ``app.translation_parallel`` 并发）::

        translate_titles(works=[{'work_title': 'W1 …', 'tracks': ['a', 'b']},
                                {'work_title': 'W2 …', 'tracks': ['c']}])
        # 或 translate_titles([{'work_title': ..., 'tracks': [...]}, ...])
        # → [ {work_title, work_title_original, tracks}, ... ]   # 与输入同序

    参数:
        work_title: 单个作品的作品名；或直接传作品列表（等价于 works=）
        track_names: 单个作品的音轨名列表
        works: 作品列表 [{work_title, tracks}, ...]（多作品模式）
        translate_work_title: 是否连作品名一起翻译（默认 True；作品名为空则无效果）
        config_path: 配置文件路径（engine 为 None 时用于构建引擎）
        engine: 复用的翻译引擎
        parallel: 批量并发数（0 = 取 config.app.translation_parallel）
        log: 可选日志回调 log(msg)
    """
    if works is None and track_names is None and isinstance(work_title, (list, tuple)):
        works = list(work_title)
    if works is not None:
        return translate_titles_batch(
            works, translate_work_title=translate_work_title,
            config_path=config_path, engine=engine, parallel=parallel, log=log)

    stems = [str(s) for s in (track_names or [])]
    if engine is None:
        engine = _build_engine(config_path)
    data, _stats = translate_names(
        engine, work_title or '', stems,
        translate_work_title=translate_work_title, log=log)
    return data


def translate_titles_batch(
    works,
    *,
    translate_work_title: bool = True,
    config_path=None,
    engine=None,
    parallel: int = 0,
    log=None,
) -> list[dict]:
    """批量：``works`` = ``[{"work_title":..., "tracks":[...]}, ...]``。

    每个作品一次请求；``parallel<=0`` 时取 config ``app.translation_parallel``。
    返回结果列表，**与输入同序**。
    """
    items: list[tuple[str, list]] = []
    for w in (works or []):
        if isinstance(w, dict):
            items.append((
                str(w.get('work_title') or w.get('title') or ''),
                [str(s) for s in (w.get('tracks') or w.get('track_names') or [])],
            ))
        elif isinstance(w, (list, tuple)):
            wt = str(w[0]) if len(w) > 0 and w[0] is not None else ''
            ts = [str(s) for s in (w[1] if len(w) > 1 and w[1] else [])]
            items.append((wt, ts))
    if not items:
        return []

    cfg = None
    if engine is None:
        from io_adapter.config_loader import load_config, get_api_config
        from engines.translate_engine import create_translate_engine
        cfg = load_config(Path(config_path) if config_path else None)
        engine = create_translate_engine(
            get_api_config(cfg), verbose=False, pricing=cfg.get('pricing'))

    par = int(parallel) if parallel else 0
    if par <= 0:
        try:
            par = int(((cfg or {}).get('app', {}) or {}).get('translation_parallel', 1) or 1)
        except Exception:
            par = 1
    par = max(1, par)

    results: list = [None] * len(items)

    def _one(idx, wt, ts):
        data, _ = translate_names(
            engine, wt, ts, translate_work_title=translate_work_title, log=log)
        return idx, data

    if par <= 1 or len(items) == 1:
        for i, (wt, ts) in enumerate(items):
            results[i] = _one(i, wt, ts)[1]
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=par) as pool:
            futs = [pool.submit(_one, i, wt, ts) for i, (wt, ts) in enumerate(items)]
            for fut in as_completed(futs):
                i, data = fut.result()
                results[i] = data
    return results


def translate_work_names(
    work_dir,
    *,
    work_title: str = None,
    translate_work_title: bool = False,
    config_path=None,
    engine=None,
    write: bool = True,
    out_dir=None,
    audio_exts=None,
    subtitle_exts=None,
    log=None,
) -> dict:
    """按作品目录：自动收集音轨名 + 目录名作为作品名，翻译并（可选）写 JSON。

    若调用方已有作品名/音轨名，请直接用 translate_titles()。
    """
    wd = Path(work_dir)
    title = work_title if work_title is not None else wd.name
    stems = collect_track_stems(wd, audio_exts, subtitle_exts)
    if not stems and not title:
        if log:
            log('未找到作品名/音轨')
        return {}
    data = translate_titles(
        title, stems, translate_work_title=translate_work_title,
        config_path=config_path, engine=engine, log=log)
    if write and data:
        write_track_names(Path(out_dir) if out_dir else wd, data)
    return data


# 兼容旧名
translate_work_track_names = translate_work_names


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description='翻译作品名 + 音轨名（默认 config.json），输出 JSON（stdout）')
    parser.add_argument('work_dir', nargs='?', help='作品目录（自动收集音轨名；可选）')
    parser.add_argument('--work-title', default=None, help='作品名（调用方提供）')
    parser.add_argument('--track', action='append', default=None,
                        help='音轨名，可重复（调用方提供）')
    parser.add_argument('--stdin', action='store_true',
                        help='从 stdin 读 {"work_title": "...", "tracks": [...]}，'
                             '或批量 {"works": [{...}, ...]} / [{...}, ...]')
    parser.add_argument('--config', default=None, help='配置文件（默认 config.json）')
    parser.add_argument('--parallel', type=int, default=0,
                        help='批量并发数（默认 0 = 取 config.app.translation_parallel）')
    parser.add_argument('--no-translate-work-title', dest='translate_work_title',
                        action='store_false',
                        help='接口模式下不翻译作品名（默认：提供了作品名就一起翻译）')
    parser.add_argument('--out-dir', default=None, help='写 _track_names.json 的目录（可选）')
    parser.add_argument('--json-out', default=None, help='结果 JSON 输出文件（可选）')
    parser.add_argument('--audio-exts', default=None,
                        help='逗号分隔的音频扩展名（仅 work_dir 模式用）')
    args = parser.parse_args(argv)

    def _log(msg):
        print(f'[标题] {msg}', file=sys.stderr, flush=True)

    audio = None
    if args.audio_exts:
        audio = [e.strip() for e in args.audio_exts.split(',') if e.strip()]

    if args.stdin:
        payload = json.load(sys.stdin)
        works = None
        if isinstance(payload, list):
            works = payload
        elif isinstance(payload, dict) and isinstance(payload.get('works'), list):
            works = payload['works']
        if works is not None:
            results = translate_titles_batch(
                works, translate_work_title=args.translate_work_title,
                config_path=args.config, parallel=args.parallel, log=_log)
            data = {'results': results}
        else:
            work_title = payload.get('work_title') or payload.get('title') or ''
            tracks = payload.get('tracks') or payload.get('track_names') or []
            data = translate_titles(work_title, tracks,
                                    translate_work_title=args.translate_work_title,
                                    config_path=args.config, log=_log)
    elif args.work_title is not None or args.track:
        data = translate_titles(args.work_title or '', args.track or [],
                                translate_work_title=args.translate_work_title,
                                config_path=args.config, log=_log)
    elif args.work_dir:
        # 目录模式 = 自动执行：作品名留空，只翻译音轨名
        data = translate_work_names(args.work_dir, config_path=args.config,
                                    translate_work_title=False,
                                    out_dir=args.out_dir, audio_exts=audio, log=_log)
    else:
        parser.error('需要提供 work_dir，或 --stdin，或 --work-title/--track')

    if args.out_dir and (args.stdin or args.work_title is not None or args.track):
        write_track_names(Path(args.out_dir), data)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
