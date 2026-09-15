# -*- coding: utf-8 -*-
"""解析契约测试：用「多份真实变体响应」验证解析器能容忍模型的不同表述。

这里不依赖任何录制，只喂给解析函数一批形态各异的合法/半合法响应，
覆盖「正确但措辞/包装不同」的情况——这是 mock 回放无法覆盖的部分。
"""

from __future__ import annotations

import pytest

from engines.scriptbook_cleaner import ScriptbookSplitter
from engines.translate_engine import OpenAICompatEngine as E

# ==================== 翻译 JSON 解析：变体语料 ====================

TRANSLATE_VARIANTS = [
    # (名称, 原始响应, 期望数组)
    ('标准 translations', '{"translations": [{"index": 1, "text": "甲"}, {"index": 2, "text": "乙"}]}', ['甲', '乙']),
    ('zh 数组', '{"zh": ["甲", "乙", "丙"]}', ['甲', '乙', '丙']),
    ('纯数组', '["甲", "乙"]', ['甲', '乙']),
    ('markdown 围栏', '```json\n{"zh": ["甲", "乙"]}\n```', ['甲', '乙']),
    ('无语言标识围栏', '```\n["甲", "乙"]\n```', ['甲', '乙']),
    ('文字包裹', '好的，以下是翻译：\n{"zh": ["甲", "乙"]}\n希望有帮助。', ['甲', '乙']),
    ('JSONL 对象序列', '{"index":1,"text":"甲"},\n{"index":2,"text":"乙"}', ['甲', '乙']),
    ('乱序 index', '{"index":2,"text":"乙"},\n{"index":1,"text":"甲"}', ['甲', '乙']),
    ('含空行占位', '{"zh": ["甲", "", "丙"]}', ['甲', '', '丙']),
    ('单对象', '{"index": 1, "text": "甲"}', ['甲']),
]

EMPTY_VARIANTS = ['', '   ', 'no json here', '{}']


@pytest.mark.parametrize('name,resp,expect', TRANSLATE_VARIANTS, ids=[v[0] for v in TRANSLATE_VARIANTS])
def test_translate_parse_variants(name, resp, expect):
    assert E._extract_json_array(resp) == expect


@pytest.mark.parametrize('resp', EMPTY_VARIANTS)
def test_translate_parse_returns_none_or_empty(resp):
    got = E._extract_json_array(resp)
    assert got is None or got == []


def test_translate_truncated_recovers_prefix():
    # 截断 JSON：至少恢复已闭合的前缀，而不是整段丢弃
    got = E._extract_json_array('{"translations": [{"index": 1, "text": "甲"}, {"index": 2, "text": "乙"')
    assert got == ['甲']


# ==================== 台本分割解析：变体语料 ====================

TRACKS = ['トラック1', 'トラック2']
ORIGINAL_LINES = [
    '一行目のはじめ',          # 1 (index 0)
    '二行目のつづき',          # 2
    '三行目のなかみ',          # 3
    '四行目のしめくくり',      # 4
    '五行目のよみ',            # 5
    '六行目のまとめ',          # 6
]


@pytest.fixture
def splitter() -> ScriptbookSplitter:
    return ScriptbookSplitter({'key': 'x', 'base_url': 'http://127.0.0.1:9/v1', 'model': 'm', 'protocol': 'openai'})


def test_parse_range_format(splitter):
    content = '{"tracks": {"トラック1": [1, 3], "トラック2": [4, 6]}}'
    result = splitter._parse_response(content, TRACKS, original_lines=ORIGINAL_LINES)
    assert result is not None
    assert result['トラック1'] == ORIGINAL_LINES[0:3]
    assert result['トラック2'] == ORIGINAL_LINES[3:6]


def test_parse_range_fenced(splitter):
    content = '```json\n{"tracks": {"トラック1": [1, 2], "トラック2": [3, 6]}}\n```'
    result = splitter._parse_response(content, TRACKS, original_lines=ORIGINAL_LINES)
    assert result['トラック1'] == ORIGINAL_LINES[0:2]
    assert result['トラック2'] == ORIGINAL_LINES[2:6]


def test_parse_text_format(splitter):
    content = '{"tracks": {"トラック1": ["台詞A", "台詞B"], "トラック2": []}}'
    result = splitter._parse_response(content, TRACKS, original_lines=ORIGINAL_LINES)
    assert result['トラック1'] == ['台詞A', '台詞B']
    assert result['トラック2'] == []


def test_parse_out_of_range_gives_empty(splitter):
    content = '{"tracks": {"トラック1": [99, 120], "トラック2": [1, 3]}}'
    result = splitter._parse_response(content, TRACKS, original_lines=ORIGINAL_LINES)
    assert result['トラック1'] == []
    assert result['トラック2'] == ORIGINAL_LINES[0:3]


def test_parse_missing_track_filled_empty(splitter):
    content = '{"tracks": {"トラック1": [1, 6]}}'
    result = splitter._parse_response(content, TRACKS, original_lines=ORIGINAL_LINES)
    assert result['トラック1'] == ORIGINAL_LINES
    assert result['トラック2'] == []


@pytest.mark.parametrize('content', [
    '',
    'not json at all',
    '{"foo": "bar"}',
    '{"tracks": "not a dict"}',
    '{"tracks": {"トラック1": "not a list"}}',
])
def test_parse_malformed_returns_none_or_empty(splitter, content):
    result = splitter._parse_response(content, TRACKS, original_lines=ORIGINAL_LINES)
    assert result is None or set(result.keys()) == set(TRACKS)
