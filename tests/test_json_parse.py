# -*- coding: utf-8 -*-
"""翻译 JSON 解析回归测试（pytest 版）。

覆盖 LLM 返回的各种格式：JSONL 对象序列 / 标准 translations / zh 数组 /
纯数组 / 截断 JSON 恢复 / 空响应。
"""

from __future__ import annotations

from engines.translate_engine import OpenAICompatEngine as E


def test_jsonl_sequence():
    resp = '\n'.join([
        '{"index": 1, "text": "啊，是你啊。"},',
        '{"index": 2, "text": ""},',
        '{"index": 3, "text": "按照吩咐忍住了没手淫吗？"},',
        '{"index": 4, "text": ""}',
    ])
    assert E._extract_json_array(resp) == ['啊，是你啊。', '', '按照吩咐忍住了没手淫吗？', '']
    # 乱序 index → 按 index 排序
    assert E._extract_json_array(
        '{"index": 2, "text": "B"},\n{"index": 1, "text": "A"}'
    ) == ['A', 'B']


def test_standard_formats():
    assert E._extract_json_array(
        '{"translations": [{"index": 1, "text": "A"}, {"index": 2, "text": "B"}]}'
    ) == ['A', 'B']
    assert E._extract_json_array('{"zh": ["A", "B", "C"]}') == ['A', 'B', 'C']
    assert E._extract_json_array('["A", "B", "C"]') == ['A', 'B', 'C']
    assert E._extract_json_array('{"index": 1, "text": "啊，是你啊。"}') == ['啊，是你啊。']


def test_wrapped_and_truncated():
    assert E._extract_json_array(
        '以下是翻译结果:\n{"index":1,"text":"A"},\n{"index":2,"text":"B"}'
    ) == ['A', 'B']
    # 截断 JSON：恢复已闭合部分
    assert E._extract_json_array(
        '{"translations": [{"index": 1, "text": "A"}, {"index": 2, "text": "B"'
    ) == ['A']


def test_empty():
    assert E._extract_json_array('') is None
    assert E._extract_json_array('    ') is None
