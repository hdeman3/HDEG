# -*- coding: utf-8 -*-
"""写回结构测试：只发送非空行，但写回 .lrc 保留日文原结构。

覆盖：
- 空时间轴行（仅时间戳）原样保留，不再被删除；
- 非空行填入译文，输出与原文逐行/时间轴对齐；
- 元数据标签行保留；
- 续行（无时间轴、被解析器并入上一条）行为不变（不单独成行）。
"""

from __future__ import annotations

import json

from pipeline import orchestrator
from pipeline.orchestrator import PipelineContext, translate_one_lrc


def _cfg(tmp_path, lines_per_chunk=100):
    cfg = {
        'api': {
            'key': 'mock', 'base_url': 'http://127.0.0.1:1/v1',
            'protocol': 'openai', 'model': 'mock', 'clear_proxy': True,
            'generation_params': {'temperature': 1.0, 'max_tokens': 4096, 'max_retries': 0},
        },
        'app': {
            'lrc_max_lines_per_request': lines_per_chunk,
            'translate_chunk_retries': 0,
            'translate_retry_delay': 0,
            'polish_after_translate': False,
            'export_ja_lrc': False,
            'api_preflight': False,
        },
        'network': {'clear_proxy_on_startup': True},
    }
    p = tmp_path / 'config.test.json'
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    return p


class FakeEngine:
    def __init__(self):
        self.sent: list = []

    def translate_batch(self, numbered, **_kwargs):
        self.sent.append(list(numbered))
        return {
            'translated_lines': [f'译文{i}' for i in range(len(numbered))],
            'parsed_count': len(numbered),
            'finish_reason': 'stop',
            'hit_tokens': 0, 'miss_tokens': 0, 'prompt_tokens': 0,
            'completion_tokens': 0, 'reasoning_tokens': 0,
            'cost': 0.0, 'elapsed': 0.0,
        }


def _run(tmp_path, lrc_text):
    work = tmp_path / 'work' / 'RJ99999999'
    work.mkdir(parents=True)
    lrc = work / 'track1.lrc'
    lrc.write_text(lrc_text, encoding='utf-8')

    ctx = PipelineContext(_cfg(tmp_path))
    engine = FakeEngine()
    ctx._translate_engine = engine
    orchestrator._pr = ctx.pr
    ok = translate_one_lrc(lrc, ctx, terms={}, alias_list=[], attempts=0,
                           label='t', wkey='')
    return ok, engine, lrc.read_text(encoding='utf-8')


def test_empty_timeline_lines_preserved(tmp_path):
    src = (
        '[ti:サンプル]\n'
        '[00:01.00]こんにちは\n'
        '[00:03.00]\n'
        '[00:05.00]元気ですか\n'
        '[00:07.50]\n'
        '[00:09.00]さようなら\n'
    )
    ok, engine, out = _run(tmp_path, src)
    assert ok is True
    # 只发送非空行：3 条
    assert len(engine.sent) == 1 and len(engine.sent[0]) == 3

    out_lines = out.splitlines()
    # 结构保留：行数与原文一致（含元数据行、空时间轴行）
    assert len(out_lines) == len(src.splitlines())
    # 元数据行保留
    assert out_lines[0] == '[ti:サンプル]'
    # 空时间轴行原样保留（时间戳在、无文本）
    assert out_lines[2] == '[00:03.00]'
    assert out_lines[4] == '[00:07.50]'
    # 非空行填入译文
    assert out_lines[1] == '[00:01.00]译文0'
    assert out_lines[3] == '[00:05.00]译文1'
    assert out_lines[5] == '[00:09.00]译文2'


def test_continuation_line_merged_unchanged(tmp_path):
    """续行（无时间轴）仍被并入上一条、不单独成行（保持原行为）。"""
    src = (
        '[00:01.00]長い台詞\n'
        'の続きです\n'
        '[00:05.00]次の台詞\n'
    )
    ok, engine, out = _run(tmp_path, src)
    assert ok is True
    # 续行被并入上一条 → 只有 2 条非空歌词发给 LLM
    assert len(engine.sent[0]) == 2
    out_lines = out.splitlines()
    # 物理续行不再单独成行：输出 2 行（原文 3 行减去被合并的 1 行）
    assert len(out_lines) == 2
    assert not any('の続き' in l for l in out_lines)
    assert out_lines[0] == '[00:01.00]译文0'
    assert out_lines[1] == '[00:05.00]译文1'
