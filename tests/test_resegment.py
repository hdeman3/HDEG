# -*- coding: utf-8 -*-
"""字幕重排（长句拆分）单元测试（合成数据）。"""

from __future__ import annotations

import json
import re

from engines.resegment import (
    _legal_boundaries, find_words_path, parse_lrc_items, resegment_lrc_file)

# 17 字 / 10.2s；两个「、」，合法切点为 6 和 12
LONG = 'あいうえお、かきくけこ、さしすせそ'
LONG_SPLITS = [6, 12]
SHORT = 'はい'


def _seg(start_ms, words, step=600):
    toks = []
    t = start_ms
    for w in words:
        toks.append({'start': t, 'end': t + step, 'word': w})
        t += step
    return {'start': start_ms, 'end': t, 'text': ''.join(words), 'words': toks}


class FakeEngine:
    def __init__(self, splits_by_text=None, raise_exc=False, fail_times=0):
        self.splits_by_text = splits_by_text or {}
        self.raise_exc = raise_exc
        self.fail_times = fail_times
        self.calls = 0

    def call_api(self, system, user, **kw):
        self.calls += 1
        if self.raise_exc or self.calls <= self.fail_times:
            raise RuntimeError('simulated api failure')
        stats = {'prompt_tokens': 10, 'completion_tokens': 5,
                 'hit_tokens': 0, 'miss_tokens': 10, 'reasoning_tokens': 0}
        results = {}
        for line in user.splitlines():
            m = re.match(r'^【([^】]+)】(.*)$', line.strip())
            if m:
                sid, text = m.group(1), m.group(2).strip()
                results[sid] = self.splits_by_text.get(text, [])
        return json.dumps({'results': results}, ensure_ascii=False), stats


def _ts(ms: int) -> str:
    total_cs = (ms + 5) // 10
    return f'[{total_cs // 6000:02d}:{(total_cs % 6000) // 100:02d}.{total_cs % 100:02d}]'


def _write_track(work, stem, word_lists, start=1000):
    (work / 'raw').mkdir(parents=True, exist_ok=True)

    segs = []
    t = start
    for words in word_lists:
        s = _seg(t, words)
        segs.append(s)
        t = s['end'] + 1000

    (work / 'raw' / f'{stem}.words.jsonl').write_text(
        '\n'.join(json.dumps(x, ensure_ascii=False) for x in segs) + '\n',
        encoding='utf-8')

    lines = []
    for i, s in enumerate(segs):
        lines.append(f"{_ts(s['start'])}{s['text']}")
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if nxt is None or nxt['start'] > s['end'] + 1:
            lines.append(_ts(s['end']))

    lrc = work / f'{stem}.lrc'
    lrc.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return lrc


def _setup_segments(tmp_path, word_lists):
    work = tmp_path / 'RJ99999999'
    lrc = _write_track(work, '05_test', word_lists)
    return work, lrc


def _setup(tmp_path, long_words, short_words):
    return _setup_segments(tmp_path, [long_words, short_words])


def _cue_durations(items):
    """按条目顺序，用「下一条目时间」作为当前 lyric 的结束。"""
    out = []
    for i, it in enumerate(items):
        if it['type'] != 'lyric':
            continue
        end = None
        for nxt in items[i + 1:]:
            if nxt['type'] in ('lyric', 'clear'):
                end = nxt['time_ms']
                break
        if end is not None:
            out.append((end - it['time_ms']) / 1000)
    return out


def _toks(text):
    return [{'word': c, 'start': i * 300, 'end': (i + 1) * 300}
            for i, c in enumerate(text)]


def test_legal_boundaries_block_midword():
    """停顿时落在词中间也不能当切点：ご|主人、主|人、の|… 全部禁止。"""
    s = 'ご主人様の性欲処理は、全部私がご対応させていただきますから'
    legal = _legal_boundaries(_toks(s))
    for k in (1, 2, 3, 4, 5, 6):          # ご|主人 / 主|人 / 主人|様 / 様|の / の|性 / 性|欲
        assert k not in legal, (k, s[:k], '|', s[k:])
    assert legal.get(11) == 'S'           # 「、」之后


def test_legal_boundaries_block_reduplication():
    """叠词不能在中间切：つよ|つよ。"""
    legal = _legal_boundaries(_toks('つよつよ'))
    assert 2 not in legal


def test_weak_boundary_not_exposed():
    """没有标点/停顿/小节结束时，弱边界不暴露（宁可不拆）。"""
    assert _legal_boundaries(_toks('ご主人様の性欲処理は')) == {}


def test_overlong_split_rejected(tmp_path):
    """模型切完仍超字数上限 → 该切法被拒，由本地兜底给出合规切法。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    engine = FakeEngine({LONG: [6]})              # 切出 6 / 11 字，11 > 8
    r = resegment_lrc_file(lrc, engine, max_duration=8.0, max_chars=8,
                           retries=0, retry_delay=0)
    assert r and r.get('failed') is not True
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert ''.join(it['text'] for it in lyrics) == LONG + SHORT
    for it in lyrics:
        assert len(it['text']) <= 8


def test_weak_boundaries_exposed_when_primary_insufficient():
    """字数超限且 S/P/C 无法满足时，追加 W 弱边界作为兜底候选。"""
    from engines.resegment import (
        _feasible, _legal_boundaries, _weak_boundaries)

    s = 'ご主人様の性欲処理は全部私がご対応させていただきます'
    toks = [{'word': c, 'start': i * 600, 'end': (i + 1) * 600}
            for i, c in enumerate(s)]
    primary = _legal_boundaries(toks)
    assert not _feasible(toks, primary, 8.0, 10)
    weak = _weak_boundaries(toks)
    assert weak
    assert _feasible(toks, {**primary, **weak}, 8.0, 10)


def test_long_cue_split_keeps_text_and_structure(tmp_path):
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    assert find_words_path(lrc).name.endswith('.words.jsonl')

    engine = FakeEngine({LONG: LONG_SPLITS})
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40)

    assert r and r['long'] == 1
    out = lrc.read_text(encoding='utf-8')
    assert lrc.with_suffix('.lrc.bak').exists()

    items = parse_lrc_items(out)
    lyrics = [it for it in items if it['type'] == 'lyric']
    clears = [it for it in items if it['type'] == 'clear']

    s1_end = 1000 + len(LONG) * 600
    s2_start = s1_end + 1000
    s2_end = s2_start + len(SHORT) * 600

    # 长句切成 3 条 + 短句 1 条
    assert len(lyrics) == 4
    # 清屏行保留（碎片间隙不再新增清屏）
    assert [c['time_ms'] for c in clears] == [s1_end, s2_end]
    # 文字无损
    assert ''.join(it['text'] for it in lyrics) == LONG + SHORT
    # 每条显示时长 ≤ 6s
    for d in _cue_durations(items):
        assert d <= 6.0 + 1e-6, d
    # 短句保持原样
    assert any(it['text'] == SHORT and it['time_ms'] == s2_start for it in lyrics)


def test_label_output_maps_to_token(tmp_path):
    """模型返回标签（A/B）而不是 token 编号 → 本地映射回切点。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    engine = FakeEngine({LONG: ['A', 'B']})
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40,
                           retries=0, retry_delay=0)
    assert r and r.get('long') == 1
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert len(lyrics) == 4


def test_llm_failure_keeps_original(tmp_path):
    """LLM 失败 → 不拆分，原文件不变，并标记 failed。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    before = lrc.read_text(encoding='utf-8')

    engine = FakeEngine(raise_exc=True)
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40,
                           retries=0, retry_delay=0)
    assert r and r.get('failed') is True
    assert engine.calls == 1
    assert lrc.read_text(encoding='utf-8') == before


def test_inline_retry_then_success(tmp_path):
    """内联重试：首次失败、第二次成功 → 仍完成拆分（与翻译 chunk 重试一致）。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))

    engine = FakeEngine({LONG: LONG_SPLITS}, fail_times=1)
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40,
                           retries=2, retry_delay=0)
    assert r and r.get('long') == 1
    assert engine.calls == 2                      # 1 次失败 + 1 次成功
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert len(lyrics) == 4


def test_retries_exhausted_returns_failed(tmp_path):
    """请求失败 → 重试 3 次后仍失败，标记 failed，文件不变。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    before = lrc.read_text(encoding='utf-8')

    engine = FakeEngine(raise_exc=True)
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40,
                           retries=3, retry_delay=0)
    assert r and r.get('failed') is True
    assert engine.calls == 4                      # 1 次首问 + 3 次重试
    assert lrc.read_text(encoding='utf-8') == before


def test_midword_split_rejected(tmp_path):
    """LLM 给出词内切点（ご|主人）→ 忽略；本地在「、」后兜底切分。"""
    mid = 'ご主人様の性欲処理は、全部私がご対応させていただきますから'
    _, lrc = _setup(tmp_path, list(mid), list(SHORT))

    engine = FakeEngine({mid: [1]})               # ご|主人
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40,
                           retries=0, retry_delay=0)
    assert r and r.get('long') == 1
    assert engine.calls == 1                      # 没有重问
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert ''.join(it['text'] for it in lyrics) == mid + SHORT
    assert not any(it['text'] == 'ご' for it in lyrics)
    assert any(it['text'].startswith('全部私が') for it in lyrics)


def test_redundant_cut_dropped(tmp_path):
    """能少切就少切：两刀里有一刀多余时应删掉。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    engine = FakeEngine({LONG: [6, 12]})
    r = resegment_lrc_file(lrc, engine, max_duration=9.0, max_chars=40)
    assert r and r.get('split') == 2              # [6,12] → [12]
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert len(lyrics) == 3                       # 2 段 + 短句


def test_mismatch_words_skipped(tmp_path):
    work, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    wp = work / 'raw' / (lrc.stem + '.words.jsonl')
    # 与 LRC 完全对不上的 words（时间/文本都不同）→ 跳过
    fake = {'start': 999999, 'end': 1000999, 'text': 'ちがう',
            'words': [{'start': 999999, 'end': 1000999, 'word': 'ちがう'}]}
    wp.write_text(json.dumps(fake, ensure_ascii=False) + '\n', encoding='utf-8')

    before = lrc.read_text(encoding='utf-8')
    r = resegment_lrc_file(lrc, FakeEngine(), max_duration=6.0, max_chars=40)
    assert r and r.get('skipped')
    assert lrc.read_text(encoding='utf-8') == before


def _cfg_file(tmp_path, **app_extra):
    app = {'translation_parallel': 2, 'api_preflight': False,
           'resegment': {'enabled': True, 'max_duration': 6.0, 'max_chars': 40,
                         'retry_delay': 0}}
    app.update(app_extra)
    cfg = {
        'api': {'key': 'mock', 'base_url': 'http://127.0.0.1:1/v1',
                'protocol': 'openai', 'model': 'mock', 'clear_proxy': True},
        'app': app,
        'network': {'clear_proxy_on_startup': True},
    }
    p = tmp_path / 'config.test.json'
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    return p


def test_pipeline_step_resegments(tmp_path):
    """验证 pipeline 步骤接线（并发执行、写回 .lrc、生成 .bak）。"""
    from pipeline import orchestrator as orch
    from pipeline.orchestrator import PipelineContext

    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))

    ctx = PipelineContext(_cfg_file(tmp_path))
    ctx._translate_engine = FakeEngine({LONG: LONG_SPLITS})
    orch._pr = ctx.pr

    orch._maybe_resegment_subtitles(ctx, [lrc])

    assert lrc.with_suffix('.lrc.bak').exists()
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert len(lyrics) == 4
    assert ''.join(it['text'] for it in lyrics) == LONG + SHORT


def test_pipeline_round_retries_failed_files(tmp_path):
    """pipeline 层：内联重试关闭时，失败文件进入下一轮队列重试并成功。"""
    from pipeline import orchestrator as orch
    from pipeline.orchestrator import PipelineContext

    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))

    ctx = PipelineContext(_cfg_file(
        tmp_path, resegment={'enabled': True, 'max_duration': 6.0,
                             'max_chars': 40, 'retries': 0,
                             'failed_rounds': 2}))
    engine = FakeEngine({LONG: LONG_SPLITS}, fail_times=1)
    ctx._translate_engine = engine
    orch._pr = ctx.pr

    orch._maybe_resegment_subtitles(ctx, [lrc])

    assert engine.calls == 2                      # 第 1 轮失败 → 第 2 轮成功
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert len(lyrics) == 4


def test_work_batch_one_request(tmp_path):
    """默认一音轨一次请求：同作品两个文件 → 2 次 LLM 调用。"""
    from engines.resegment import resegment_work

    a = 'あいうえお、かきくけこ'
    b = 'たちつてと、なにぬねの'
    work, lrc1 = _setup_segments(tmp_path, [list(a), list(SHORT)])
    lrc2 = _write_track(work, '06_second', [list(b), list(SHORT)])

    engine = FakeEngine({a: [6], b: [6]})
    r = resegment_work([lrc1, lrc2], engine,
                       max_duration=6.0, max_chars=40, retries=0)

    assert engine.calls == 2                      # 每音轨一次
    assert r['changed'] == 2
    for p in (lrc1, lrc2):
        lyrics = [it for it in parse_lrc_items(p.read_text(encoding='utf-8'))
                  if it['type'] == 'lyric']
        assert len(lyrics) == 3                   # 2 段 + 短句


def test_work_batch_by_five_tracks(tmp_path):
    """每 5 个音轨一次请求；剩余不足 5 个的合并发一次（7 条 → 2 次）。"""
    from engines.resegment import resegment_work

    text = 'あいうえお、かきくけこ'
    work, lrc0 = _setup_segments(tmp_path, [list(text), list(SHORT)])
    lrcs = [lrc0]
    for i in range(1, 7):
        lrcs.append(_write_track(work, f'{10 + i}_t', [list(text), list(SHORT)]))

    engine = FakeEngine({text: [6]})
    r = resegment_work(lrcs, engine, max_duration=6.0, max_chars=40,
                       retries=0, tracks_per_request=5)

    assert engine.calls == 2                      # 5 个 + 剩余 2 个
    assert r['changed'] == 7


def test_invalid_split_not_retried(tmp_path):
    """模型正常回复但只给一个切点 → 只发 1 次请求，不再重复提问。"""
    from pipeline import orchestrator as orch
    from pipeline.orchestrator import PipelineContext

    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))

    ctx = PipelineContext(_cfg_file(tmp_path, resegment={
        'enabled': True, 'max_duration': 4.0, 'max_chars': 40,
        'failed_rounds': 3, 'retry_delay': 0}))
    engine = FakeEngine({LONG: [6]})              # 切完仍 6.6s > 4s
    ctx._translate_engine = engine
    orch._pr = ctx.pr

    orch._maybe_resegment_subtitles(ctx, [lrc])
    assert engine.calls == 1                      # 不重复提问


def test_one_request_per_track(tmp_path):
    """同一音轨的多条过长句应合并为一次 LLM 请求。"""
    a = 'あいうえお、かきくけこ'                  # 11 字 / 6.6s
    b = 'たちつてと、なにぬねの'
    c = SHORT
    _, lrc = _setup_segments(tmp_path, [list(a), list(b), list(c)])

    engine = FakeEngine({a: [6], b: [6]})
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40)

    assert r and r['long'] == 2
    assert engine.calls == 1                      # 整轨一次
    items = parse_lrc_items(lrc.read_text(encoding='utf-8'))
    lyrics = [it for it in items if it['type'] == 'lyric']
    assert ''.join(it['text'] for it in lyrics) == a + b + c
    for d in _cue_durations(items):
        assert d <= 6.0 + 1e-6, d


# ==================== 回归：ASR 时间戳漂移不应切出碎片 ====================

def test_short_line_with_huge_gap_not_split(tmp_path):
    """2 字的「好き」跨 18s（ASR 漂移）→ 不拆、不发请求。"""
    work = tmp_path / 'RJ99999999'
    (work / 'raw').mkdir(parents=True)
    seg = {'start': 0, 'end': 18320, 'text': '好き', 'words': [
        {'start': 0, 'end': 400, 'word': '好'},
        {'start': 17560, 'end': 17920, 'word': 'き'}]}
    (work / 'raw' / '05_like.words.jsonl').write_text(
        json.dumps(seg, ensure_ascii=False) + '\n', encoding='utf-8')
    lrc = work / '05_like.lrc'
    lrc.write_text('[00:00.00]好き\n[00:18.32]\n', encoding='utf-8')
    before = lrc.read_text(encoding='utf-8')

    engine = FakeEngine({'好き': [1]})
    r = resegment_lrc_file(lrc, engine, max_duration=8.0, max_chars=40)
    assert r is None
    assert engine.calls == 0
    assert lrc.read_text(encoding='utf-8') == before


def test_duration_only_does_not_force_midword_split(tmp_path):
    """只是时长超标、句内没有安全切点 → 不拆（宁可显示长一点）。"""
    text = 'あ' * 20                             # 20 字 / 12s，无标点、无停顿
    _, lrc = _setup_segments(tmp_path, [list(text), list(SHORT)])
    before = lrc.read_text(encoding='utf-8')

    engine = FakeEngine({text: [1]})
    r = resegment_lrc_file(lrc, engine, max_duration=6.0, max_chars=40)
    assert r is None
    assert engine.calls == 0
    assert lrc.read_text(encoding='utf-8') == before


def test_gap_boundaries_never_inside_word():
    """长静音在词素内部（好|き）→ 不进入 G 候选。"""
    from engines.resegment import _gap_boundaries, _legal_boundaries

    toks = _toks('あああ好きいいい')
    k = 4                                        # 「好|き」
    for i in range(k, len(toks)):
        toks[i]['start'] += 5000
        toks[i]['end'] += 5000
    legal = _legal_boundaries(toks)
    gap = _gap_boundaries(toks, legal)
    assert k not in gap


def test_gap_boundary_requires_min_fragment():
    """G 两侧不足 3 字（お|姉）→ 不进入候选。"""
    from engines.resegment import _gap_boundaries, _legal_boundaries

    toks = _toks('お姉ちゃん……')
    for i in range(1, len(toks)):
        toks[i]['start'] += 5000
        toks[i]['end'] += 5000
    legal = _legal_boundaries(toks)
    gap = _gap_boundaries(toks, legal)
    assert 1 not in gap


def test_local_fallback_when_model_returns_empty(tmp_path):
    """模型偷懒返回 [] → 本地求解器兜底，仍然按字数上限拆分。"""
    _, lrc = _setup(tmp_path, list(LONG), list(SHORT))
    engine = FakeEngine({})                       # 所有条都返回 []
    r = resegment_lrc_file(lrc, engine, max_duration=8.0, max_chars=8)
    assert r and r.get('split')
    lyrics = [it for it in parse_lrc_items(lrc.read_text(encoding='utf-8'))
              if it['type'] == 'lyric']
    assert ''.join(it['text'] for it in lyrics) == LONG + SHORT
    for it in lyrics:
        assert len(it['text']) <= 8
