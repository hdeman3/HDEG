# -*- coding: utf-8 -*-
"""真实 API 冒烟（live smoke，L2 层）：只在 nightly / 手动 / 发布前跑。

与 mock 集成的区别：
- 这里**真的调用厂商 API**，用于验证「真机可用 / 协议发得对 / 没被限流」。
- 只断言**不变量**：不崩、产出中文、token 用量在预算内；**绝不比对具体译文**
  （模型表述可变）。
- 无凭证时自动 skip，不影响常规 CI。

素材：取「无台本」作品的最短一轨，截到前 20 句，压到最小 token。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tests.conftest import WORKS_DIR, write_config

pytestmark = pytest.mark.live

_CHINESE_RE = re.compile(r'[\u4e00-\u9fff]')

LIVE_WORK = 'RJ01702644'
MAX_LRC_LINES = 20
# token 预算（prompt+completion 合计）：1 轨 20 句 + 识别 + 世界观 + 术语，留足余量
TOKEN_BUDGET = 80000


def _live_api_cfg() -> dict:
    """从环境变量 / config.json 组装真实 API 配置。"""
    from io_adapter.config_loader import load_config

    try:
        base = load_config().get('api', {})
    except Exception:
        base = {}

    key = os.environ.get('HDEG_API_KEY') or base.get('key') or base.get('api_key') or ''
    if not key:
        pytest.skip('未配置真实 API Key（HDEG_API_KEY 或 config.json）')

    cfg = dict(base)
    cfg['key'] = key
    if os.environ.get('HDEG_BASE_URL'):
        cfg['base_url'] = os.environ['HDEG_BASE_URL']
    if os.environ.get('HDEG_MODEL'):
        cfg['model'] = os.environ['HDEG_MODEL']
    return cfg


def _build_live_work(dst: Path) -> Path:
    """复制无台本作品，只保留最短一轨并截断到前 N 句。"""
    import shutil

    src = WORKS_DIR / LIVE_WORK
    if not src.exists():
        pytest.skip(f'缺少素材 {src}，先运行 scripts/prepare_fixtures.py')

    work_dir = dst / LIVE_WORK
    shutil.copytree(src, work_dir)

    ja_files = sorted(work_dir.rglob('*.ja.lrc'), key=lambda p: p.stat().st_size)
    if not ja_files:
        pytest.skip(f'{LIVE_WORK} 无 .ja.lrc')

    keep = ja_files[0]
    for p in ja_files[1:]:
        p.unlink()

    lines = keep.read_text(encoding='utf-8').splitlines()
    kept = [ln for ln in lines if ln.strip()][:MAX_LRC_LINES]
    keep.write_text('\n'.join(kept) + '\n', encoding='utf-8')
    return work_dir


def test_live_smoke(tmp_path):
    from pipeline import orchestrator

    api_cfg = _live_api_cfg()

    work_dir = _build_live_work(tmp_path)

    # 真实配置：关润色 / 关预检 / 串行；其余沿用用户配置
    cfg_path = write_config(tmp_path, api_cfg.get('base_url', 'https://api.deepseek.com'))
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['api'] = dict(api_cfg)
    cfg['api'].setdefault('clear_proxy', True)
    cfg['app']['polish_after_translate'] = False
    cfg['app']['api_preflight'] = False
    cfg['app']['translation_parallel'] = 1
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        stats = orchestrator.run_pipeline(work_dir, config_path=cfg_path)
    finally:
        os.chdir(cwd)

    lrc_files = [p for p in work_dir.rglob('*.lrc') if not p.name.endswith('.ja.lrc')]
    assert lrc_files, '未产出 .lrc'
    for p in lrc_files:
        text = p.read_text(encoding='utf-8', errors='replace')
        assert _CHINESE_RE.search(text), f'{p.name} 无任何中文字符'

    total_tokens = (
        int(stats.get('total_hit_tokens', 0))
        + int(stats.get('total_miss_tokens', 0))
        + int(stats.get('total_completion_tokens', 0))
    )
    assert total_tokens <= TOKEN_BUDGET, (
        f'冒烟 token 超预算: {total_tokens} > {TOKEN_BUDGET}')
