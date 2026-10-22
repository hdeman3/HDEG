# -*- coding: utf-8 -*-
"""作品名 + 音轨名翻译（写 JSON，不改文件名）测试。"""

from __future__ import annotations

import json
import re

from engines.track_titles import (
    collect_track_stems,
    translate_names,
    translate_titles,
    translate_titles_batch,
    translate_work_names,
)


class FakeEngine:
    def __init__(self, raise_exc=False, drop_indices=()):
        self.raise_exc = raise_exc
        self.drop_indices = set(drop_indices)
        self.calls = 0

    def call_api(self, system, user, **kw):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError('boom')
        data = {}
        m = re.search(r'^【作品名】(.*)$', user, re.M)
        wt = m.group(1).strip() if m else ''
        if wt and wt != '(无)':
            data['work_title'] = '中_' + wt
        names = {}
        for line in user.splitlines():
            mm = re.match(r'^\s*(\d+): (.*)$', line)
            if mm and int(mm.group(1)) not in self.drop_indices:
                names[mm.group(1)] = '中_' + mm.group(2)
        data['names'] = names
        return json.dumps(data, ensure_ascii=False), {
            'prompt_tokens': 20, 'completion_tokens': 10,
            'hit_tokens': 0, 'miss_tokens': 20, 'reasoning_tokens': 0,
        }


def _mk_work(tmp_path):
    work = tmp_path / 'RJ99999999 あいうえお'
    work.mkdir(parents=True)
    (work / 'raw').mkdir()
    (work / '_split_tracks').mkdir()
    (work / '01. あ.lrc').write_text('[00:01.00]テスト\n', encoding='utf-8')
    (work / '01. あ.ja.lrc').write_text('[00:01.00]テスト\n', encoding='utf-8')
    (work / '01. あ.mp3').write_bytes(b'x')
    (work / '02. い.lrc').write_text('[00:01.00]テスト\n', encoding='utf-8')
    (work / 'cover.jpg').write_bytes(b'x')
    (work / 'raw' / '99. き.lrc').write_text('[00:01.00]x\n', encoding='utf-8')
    (work / '_split_tracks' / '99. き.txt').write_text('x\n', encoding='utf-8')
    return work


def test_collect_track_stems(tmp_path):
    work = _mk_work(tmp_path)
    assert collect_track_stems(work) == ['01. あ', '02. い']


def test_translate_work_writes_json(tmp_path):
    work = _mk_work(tmp_path)
    engine = FakeEngine()
    data = translate_work_names(work, engine=engine)

    # 默认只译音轨名，作品名字段留空
    assert data['work_title_original'] == ''
    assert data['work_title'] == ''
    assert data['tracks'] == {'01. あ': '中_01. あ', '02. い': '中_02. い'}

    on_disk = json.loads((work / '_track_names.json').read_text(encoding='utf-8'))
    assert on_disk == data
    # 文件名未被修改
    assert (work / '01. あ.mp3').exists()
    assert (work / '01. あ.lrc').exists()


def test_missing_index_falls_back_to_original(tmp_path):
    work = _mk_work(tmp_path)
    data = translate_work_names(work, engine=FakeEngine(drop_indices=[1]))
    assert data['tracks']['01. あ'] == '中_01. あ'
    assert data['tracks']['02. い'] == '02. い'      # 回退原文


def test_translate_names_single_call(tmp_path):
    engine = FakeEngine()
    data, stats = translate_names(engine, 'RJ123 テスト', ['a', 'b', 'c'])
    assert engine.calls == 1
    assert stats and stats.get('elapsed') is not None
    assert data['work_title'] == ''                     # 默认留空
    assert data['tracks'] == {'a': '中_a', 'b': '中_b', 'c': '中_c'}


def test_translate_work_title_flag(tmp_path):
    engine = FakeEngine()
    data, _ = translate_names(engine, 'RJ123 テスト', ['a'], translate_work_title=True)
    assert data['work_title'] == '中_RJ123 テスト'


def test_translate_titles_explicit_inputs():
    """对外接口：调用方提供作品名 + 音轨名 → 两者都翻译。"""
    engine = FakeEngine()
    data = translate_titles('RJ123 テスト', ['01. あ', 'Tr2_い'], engine=engine)
    assert data['work_title_original'] == 'RJ123 テスト'
    assert data['work_title'] == '中_RJ123 テスト'
    assert data['tracks'] == {'01. あ': '中_01. あ', 'Tr2_い': '中_Tr2_い'}


def test_translate_titles_translate_work_title():
    engine = FakeEngine()
    data = translate_titles('RJ123 テスト', ['01. あ'],
                            translate_work_title=True, engine=engine)
    assert data['work_title'] == '中_RJ123 テスト'


def test_translate_titles_without_work_title():
    """调用方拿不到作品名 → 只翻译音轨名，作品名留空。"""
    engine = FakeEngine()
    data = translate_titles(track_names=['01. あ', 'Tr2_い'], engine=engine)
    assert data['work_title'] == ''
    assert data['work_title_original'] == ''
    assert data['tracks'] == {'01. あ': '中_01. あ', 'Tr2_い': '中_Tr2_い'}


def test_translate_titles_batch_sequential():
    engine = FakeEngine()
    works = [
        {'work_title': 'W1 あ', 'tracks': ['a', 'b']},
        {'work_title': 'W2 い', 'tracks': ['c']},
    ]
    res = translate_titles_batch(works, engine=engine, parallel=1)
    assert len(res) == 2
    assert res[0]['work_title'] == '中_W1 あ'
    assert res[0]['tracks'] == {'a': '中_a', 'b': '中_b'}
    assert res[1]['work_title'] == '中_W2 い'
    assert res[1]['tracks'] == {'c': '中_c'}
    assert engine.calls == 2


def test_translate_titles_batch_parallel():
    engine = FakeEngine()
    works = [{'work_title': f'W{i} あ', 'tracks': [f't{i}']} for i in range(5)]
    res = translate_titles_batch(works, engine=engine, parallel=4)
    assert len(res) == 5
    for i in range(5):
        assert res[i]['tracks'][f't{i}'] == f'中_t{i}'
    assert engine.calls == 5


def test_translate_titles_batch_via_main_interface():
    """同一接口：传作品列表即并发处理。"""
    engine = FakeEngine()
    res = translate_titles(
        [{'work_title': 'W1 あ', 'tracks': ['a']},
         {'work_title': 'W2 い', 'tracks': ['b']}],
        engine=engine, parallel=2)
    assert isinstance(res, list) and len(res) == 2
    assert res[0]['work_title'] == '中_W1 あ'
    assert res[0]['tracks'] == {'a': '中_a'}
    assert res[1]['tracks'] == {'b': '中_b'}
    assert engine.calls == 2


def test_pipeline_step_writes_json(tmp_path):
    from pipeline import orchestrator as orch
    from pipeline.orchestrator import PipelineContext

    work = _mk_work(tmp_path)
    cfg = {
        'api': {'key': 'mock', 'base_url': 'http://127.0.0.1:1/v1',
                'protocol': 'openai', 'model': 'mock', 'clear_proxy': True},
        'app': {'translation_parallel': 2, 'translate_track_names': True},
        'network': {'clear_proxy_on_startup': True},
    }
    cp = tmp_path / 'config.test.json'
    cp.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')

    ctx = PipelineContext(cp)
    ctx._translate_engine = FakeEngine()
    orch._pr = ctx.pr

    orch._maybe_translate_track_names(ctx, work)
    out = json.loads((work / '_track_names.json').read_text(encoding='utf-8'))
    assert out['work_title'] == ''                        # 默认留空
    assert out['tracks'] == {'01. あ': '中_01. あ', '02. い': '中_02. い'}
