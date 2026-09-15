# -*- coding: utf-8 -*-
"""record 模式：用真实 API 跑一遍真实作品，把 LLM 响应录制成 fixture。

录制产物供 CI 的 mock 服务回放（0 token、确定性），真实模型只在本地/手动冒烟时用。

用法::

    # 用 config.json 里的真实 API 配置
    python scripts/record_fixtures.py --work RJ01653978

    # 或走环境变量注入凭证
    set HDEG_API_KEY=sk-xxx && python scripts/record_fixtures.py --all

产物::

    tests/fixtures/recordings/<work>.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Windows 控制台默认 GBK，管道输出含 ¥ 等字符会崩；统一 UTF-8
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
except Exception:
    pass

from io_adapter.config_loader import load_config, save_config  # noqa: E402
from tests.conftest import WORK_NAMES, copy_work  # noqa: E402
from tests.mock_llm_server import compute_key, detect_stage  # noqa: E402


def _build_record_config(tmp_path: Path, config_path: Path, clear_proxy: bool = True,
                         parallel: int = 1) -> Path:
    """基于真实配置，关掉润色/预检，产物写入临时配置。

    并行录制：mock 按 <asr> 内容哈希路由、与调用顺序无关，因此提高
    translation_parallel 只提速、不影响 fixture 正确性。推理强度不改。
    """
    cfg = load_config(config_path)
    app = cfg.setdefault('app', {})
    app['polish_after_translate'] = False
    app['api_preflight'] = False
    app['translation_parallel'] = max(1, int(parallel))
    app['delay_translate_to_offpeak'] = False
    app['export_scriptbook_content'] = True
    app['debug'] = False
    app['print_worker_detail'] = False
    if clear_proxy:
        api = cfg.setdefault('api', {})
        api['clear_proxy'] = True
        cfg.setdefault('network', {})['clear_proxy_on_startup'] = True
    out = tmp_path / 'config.record.json'
    save_config(cfg, out)
    return out


def record_work(work: str, config_path: Path, out_path: Path, clear_proxy: bool = True,
                parallel: int = 1) -> dict:
    from engines.api_client import APIClient
    from pipeline import orchestrator

    plan = {'work': work, 'calls': []}
    seen: set = set()
    lock = threading.Lock()
    orig_chat = APIClient.chat

    def patched(self, **kwargs):
        resp = orig_chat(self, **kwargs)
        messages = kwargs.get('messages') or []
        max_tokens = kwargs.get('max_tokens') or 0
        stage = detect_stage(messages, max_tokens)
        key = compute_key(stage, messages, max_tokens)
        try:
            content = APIClient.extract_content(resp.choices[0].message)
        except Exception:
            content = ''
        with lock:
            if key not in seen:
                seen.add(key)
                plan['calls'].append({'stage': stage, 'key': key, 'content': content})
        return resp

    tmp = Path(tempfile.mkdtemp(prefix=f'hdeg_record_{work}_'))
    try:
        work_dir = copy_work(work, tmp)
        cfg = _build_record_config(tmp, config_path, clear_proxy=clear_proxy, parallel=parallel)
        monkey_orig = getattr(orchestrator, '_fetch_balance')
        orchestrator._fetch_balance = lambda ctx: None  # 录制不查余额
        APIClient.chat = patched
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            orchestrator.run_pipeline(work_dir, config_path=cfg)
        finally:
            os.chdir(cwd)
            APIClient.chat = orig_chat
            orchestrator._fetch_balance = monkey_orig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    return plan


def main():
    parser = argparse.ArgumentParser(description='录制真实 LLM 响应为 CI fixture')
    parser.add_argument('--work', help='只录制该作品（如 RJ01653978）')
    parser.add_argument('--all', action='store_true', help='录制全部作品')
    parser.add_argument('--config', default=None, help='真实 API 配置（默认 config.json）')
    parser.add_argument('--out', default=None, help='录制输出目录（默认 tests/fixtures/recordings）')
    parser.add_argument('--keep-proxy', action='store_true',
                        help='保留系统代理（默认强制直连 clear_proxy=true）')
    parser.add_argument('--fast', action='store_true',
                        help='兼容旧参数，等价于 --parallel 4')
    parser.add_argument('--parallel', type=int, default=1,
                        help='录制并发数（默认 1；只提速，不改推理强度/不影响 fixture 正确性）')
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else (_REPO_ROOT / 'config.json')
    out_dir = Path(args.out) if args.out else (_REPO_ROOT / 'tests' / 'fixtures' / 'recordings')

    if args.all:
        works = WORK_NAMES
    elif args.work:
        works = [args.work]
    else:
        parser.error('需指定 --work <作品> 或 --all')

    parallel = 4 if args.fast else max(1, args.parallel)

    for work in works:
        print(f'\n===== 录制 {work} =====')
        out = out_dir / f'{work}.json'
        plan = record_work(work, config_path, out,
                           clear_proxy=not args.keep_proxy, parallel=parallel)
        print(f'[OK] {work}: 录制 {len(plan["calls"])} 个调用 → {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
