# -*- coding: utf-8 -*-
"""块级重试测试。

模拟单块调用抛网络异常（如 ChunkedEncodingError），验证：
- 失败块被**原地重试**，成功块不重发；
- 失败块重试耗尽后**不中断后续块**；
- 只有所有块都失败才让整轨判失败（入音轨级重试队列）。
"""

from __future__ import annotations

import json

import pytest

from pipeline import orchestrator
from pipeline.orchestrator import PipelineContext, translate_one_lrc

N_LINES = 12
CHUNK = 5  # 12 行 → 块 [5,5,2] 共 3 块


def _write_lrc(work_dir):
    d = work_dir / 'RJ99999999'
    d.mkdir(parents=True, exist_ok=True)
    lrc = d / 'track1.lrc'
    lines = [f'[{i:02d}:00.00]日本語のテスト行その{i}' for i in range(1, N_LINES + 1)]
    lrc.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return lrc


def _write_cfg(tmp_path, retries=2):
    cfg = {
        'api': {
            'key': 'mock', 'base_url': 'http://127.0.0.1:1/v1',
            'protocol': 'openai', 'model': 'mock', 'clear_proxy': True,
            'generation_params': {'temperature': 1.0, 'max_tokens': 4096, 'max_retries': 0},
        },
        'app': {
            'lrc_max_lines_per_request': CHUNK,
            'translate_chunk_retries': retries,
            'translate_retry_delay': 0,       # 测试不等待
            'polish_after_translate': False,   # 润色不参与本测试
            'export_ja_lrc': False,
            'api_preflight': False,
        },
        'network': {'clear_proxy_on_startup': True},
    }
    p = tmp_path / 'config.test.json'
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    return p


class FakeEngine:
    """记录每次调用；指定序号的调用抛异常（模拟连接中断）。"""

    def __init__(self, fail_calls=()):
        self.calls: list = []
        self.fail_calls = set(fail_calls)

    def translate_batch(self, numbered, **_kwargs):
        self.calls.append(list(numbered))
        if len(self.calls) in self.fail_calls:
            raise RuntimeError(
                'ChunkedEncodingError: ("Connection broken: InvalidChunkLength")')
        return {
            'translated_lines': [f'译文{i}' for i in range(len(numbered))],
            'parsed_count': len(numbered),
            'finish_reason': 'stop',
            'hit_tokens': 0, 'miss_tokens': 0, 'prompt_tokens': 0,
            'completion_tokens': 0, 'reasoning_tokens': 0,
            'cost': 0.0, 'elapsed': 0.0,
        }


def _make_ctx(tmp_path, retries=2):
    cfg = _write_cfg(tmp_path, retries=retries)
    ctx = PipelineContext(cfg)
    ctx._enqueued: list = []

    def _fake_enqueue(_ctx, task, reason):
        ctx._enqueued.append((task.get('label'), reason))

    orchestrator._pr = ctx.pr  # `_log` 依赖模块级 printer
    return ctx


def test_block_retry_recovers_transient_error(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    lrc = _write_lrc(work)
    ctx = _make_ctx(tmp_path)
    engine = FakeEngine(fail_calls=[2])  # 第 2 次调用 = 第 2 块，失败一次
    ctx._translate_engine = engine
    monkeypatch.setattr(orchestrator, '_enqueue_failed_track',
                        lambda c, t, r: c._enqueued.append((t.get('label'), r)))

    ok = translate_one_lrc(lrc, ctx, terms={}, alias_list=[], attempts=0,
                           label='t', wkey='')
    assert ok is True
    # 3 块 + 第 2 块重试 1 次 = 4 次调用；且失败块与重试块内容一致
    assert len(engine.calls) == 4
    assert engine.calls[1] == engine.calls[2]
    # 成功块未重发：块1、块3 各只调用一次
    assert engine.calls[0][0] == '0001: 日本語のテスト行その1'
    assert engine.calls[3][0] == '0011: 日本語のテスト行その11'
    # 未入重试队列
    assert ctx._enqueued == []
    # 产出中文、行数完整
    out = lrc.read_text(encoding='utf-8')
    assert out.count('译文') == N_LINES


def test_block_retry_exhausted_continues_other_blocks(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    lrc = _write_lrc(work)
    ctx = _make_ctx(tmp_path, retries=2)
    # 第 2 块连续失败 3 次（2 次重试耗尽）；之后应继续第 3 块
    engine = FakeEngine(fail_calls=[2, 3, 4])
    ctx._translate_engine = engine
    monkeypatch.setattr(orchestrator, '_enqueue_failed_track',
                        lambda c, t, r: c._enqueued.append((t.get('label'), r)))

    ok = translate_one_lrc(lrc, ctx, terms={}, alias_list=[], attempts=0,
                           label='t', wkey='')
    assert ok is False
    # 块1(1次) + 块2(3次) + 块3(1次) = 5 次：证明失败块耗尽后仍继续后续块
    assert len(engine.calls) == 5
    assert engine.calls[1] == engine.calls[2] == engine.calls[3]
    # 整轨入音轨级重试队列
    assert len(ctx._enqueued) == 1
