# -*- coding: utf-8 -*-
"""端到端集成测试：用 5 个真实作品素材驱动整条 pipeline（LLM 走本地 mock）。

断言策略（**只断言不变量，绝不比对具体译文**，因为模型表述可变）：
- 管道不抛异常、正常返回 stats；
- 每条 `.ja.lrc` 都产出对应 `.lrc`，且被判定为中文；
- 有录制的作品：mock 无「未命中」阶段，且阶段覆盖符合该作品形态。

无录制时退化为合成响应（bootstrap），仅验证管道连通性；有录制时严格校验。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.conftest import RECORDINGS_DIR, copy_work, list_works, write_config

WORKS = list_works()

_CHINESE_RE = re.compile(r'[\u4e00-\u9fff]')
# 短轨可能保留日文专有名词，语言检测会判成 japanese；因此不要求 100% 判为中文，
# 只要求「整体绝大多数是中文」+「每轨确实被翻译过（含中文且与原文不同）」。
_MIN_CHINESE_RATIO = 0.8

# 各作品按台本形态应出现的 LLM 阶段（仅在「有录制」时严格断言）
EXPECTED_STAGES = {
    'RJ01615872': {'identify', 'pdf_layout', 'split', 'worldview', 'translate'},  # PDF 未分割
    'RJ01702644': {'identify', 'worldview', 'translate'},            # 无台本
    'RJ01653978': {'identify', 'worldview', 'translate'},            # TXT 预分割
    'RJ01671823': {'identify', 'split', 'worldview', 'translate'},   # TXT 未分割
    'RJ01654985': {'identify', 'pdf_layout', 'worldview', 'translate'},  # PDF 预分割
}


def _has_recording(work: str) -> bool:
    return (RECORDINGS_DIR / f'{work}.json').exists()


@pytest.mark.skipif(not WORKS, reason='未准备测试素材：先运行 scripts/prepare_fixtures.py')
@pytest.mark.parametrize('work', WORKS)
def test_work_pipeline(work, tmp_path, mock_llm, monkeypatch):
    from pipeline import orchestrator
    from io_adapter.lrc_handler import detect_lrc_language

    # 余额查询会强制 https 访问本地 mock（无意义），测试中直接跳过
    monkeypatch.setattr(orchestrator, '_fetch_balance', lambda ctx: None)

    work_dir = copy_work(work, tmp_path)
    srv = mock_llm(work)
    cfg = write_config(tmp_path, srv.base_url)

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        stats = orchestrator.run_pipeline(work_dir, config_path=cfg)
    finally:
        os.chdir(cwd)

    assert isinstance(stats, dict), 'run_pipeline 应返回 stats 字典'

    ja_files = list(work_dir.rglob('*.ja.lrc'))
    assert ja_files, f'{work}: fixture 应包含 .ja.lrc'

    lrc_files = [p for p in work_dir.rglob('*.lrc') if not p.name.endswith('.ja.lrc')]
    assert len(lrc_files) == len(ja_files), (
        f'{work}: 产出 .lrc 数 {len(lrc_files)} != .ja.lrc 数 {len(ja_files)}')

    chinese_count = 0
    for p in lrc_files:
        text = p.read_text(encoding='utf-8', errors='replace')
        assert _CHINESE_RE.search(text), f'{work}: {p.name} 无任何中文字符'
        ja_path = p.parent / f'{p.stem}.ja{p.suffix}'
        if ja_path.exists():
            assert text != ja_path.read_text(encoding='utf-8', errors='replace'), (
                f'{work}: {p.name} 与 .ja.lrc 完全相同（未翻译）')
        if detect_lrc_language(p) == 'chinese':
            chinese_count += 1
    ratio = chinese_count / len(lrc_files)
    assert ratio >= _MIN_CHINESE_RATIO, (
        f'{work}: 中文占比过低 {ratio:.0%}（{chinese_count}/{len(lrc_files)}）')

    # 有录制时：严格校验 mock 命中与阶段覆盖
    if _has_recording(work):
        assert srv.missing == [], f'{work}: mock 未命中的调用 {srv.missing}'
        stages = set(srv.stages())
        expected = EXPECTED_STAGES.get(work, set())
        missing = {s for s in expected if s not in stages}
        assert not missing, f'{work}: 缺少阶段 {missing}（实际 {stages}）'
