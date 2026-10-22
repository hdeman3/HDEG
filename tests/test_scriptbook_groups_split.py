# -*- coding: utf-8 -*-
"""一文件多音轨（file_track_groups）应「内部再分割」，而不是把整份台本共享给每个音轨。"""

from __future__ import annotations

import json

import pytest

import core.scriptbook_parser as sbp
import engines.scriptbook_cleaner as sbc
from pipeline import orchestrator
from pipeline.orchestrator import PipelineContext


def _cfg(tmp_path):
    cfg = {
        'api': {'key': 'mock', 'base_url': 'http://127.0.0.1:1/v1',
                'protocol': 'openai', 'model': 'mock', 'clear_proxy': True},
        'app': {'export_scriptbook_content': False, 'api_preflight': False},
        'network': {'clear_proxy_on_startup': True},
    }
    p = tmp_path / 'config.test.json'
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    return p


def _setup(tmp_path, monkeypatch, *, splitter_result, splitting_raises=False):
    work = tmp_path / 'work'
    work.mkdir()
    sb = work / '台本.txt'
    sb.write_text('ダミー台本本文\n行2\n', encoding='utf-8')

    monkeypatch.setattr(orchestrator, '_try_load_cached_scriptbook', lambda *a, **k: None)
    monkeypatch.setattr(sbp, 'collect_all_scriptbook_candidates', lambda wd: [sb])
    monkeypatch.setattr(sbp, 'load_scriptbook_content',
                        lambda f, api_config=None, layout=None: 'ダミー台本本文\n行2\n')
    monkeypatch.setattr(
        orchestrator, '_llm_identify_scriptbook_files',
        lambda candidates, work_dir, ctx, track_names=None: (
            [sb], False, {}, {'台本.txt': ['t1', 't2']}))

    class _Splitter:
        def __init__(self, config, verbose=False):
            pass

        def split_by_llm_line_range(self, track_names, raw, track_samples=None, **kw):
            if splitting_raises:
                raise RuntimeError('simulated split failure')
            return splitter_result, {}

    monkeypatch.setattr(sbc, 'ScriptbookSplitter', _Splitter)

    ctx = PipelineContext(_cfg(tmp_path))
    orchestrator._pr = ctx.pr
    return work, ctx


def test_groups_are_split_per_track(tmp_path, monkeypatch):
    work, ctx = _setup(
        tmp_path, monkeypatch,
        splitter_result={'t1': ['a1', 'a2'], 't2': ['b1']})

    result = orchestrator._load_scriptbook(work, ctx, ['t1', 't2'], {'t1': 's1', 't2': 's2'})
    # 每个音轨拿到**自己的片段**，而不是整份台本
    assert result == {'t1': ['a1', 'a2'], 't2': ['b1']}


def test_groups_fallback_to_regex_on_failure(tmp_path, monkeypatch):
    work, ctx = _setup(tmp_path, monkeypatch, splitter_result={}, splitting_raises=True)
    monkeypatch.setattr(sbc, 'split_scriptbook_regex',
                        lambda text, names: {'t1': ['x1'], 't2': ['x2']})

    result = orchestrator._load_scriptbook(work, ctx, ['t1', 't2'], {'t1': 's1', 't2': 's2'})
    assert result == {'t1': ['x1'], 't2': ['x2']}
