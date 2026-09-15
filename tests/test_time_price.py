# -*- coding: utf-8 -*-
"""峰谷定价时间侦测单元测试（pytest 版）。"""

from __future__ import annotations

import datetime as dt

from utils.time_price import (
    PEAK_WINDOWS,
    format_hms,
    is_idle_time,
    is_peak_time,
    next_idle_start,
    seconds_until_idle,
)

TZ8 = dt.timezone(dt.timedelta(hours=8))


def bj(hour: int, minute: int = 0, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 8, 14, hour, minute, second, tzinfo=TZ8)


CASES = [
    (0, 0, False), (8, 59, False), (9, 0, True), (10, 0, True),
    (11, 59, True), (12, 0, False), (13, 59, False), (14, 0, True),
    (15, 0, True), (17, 59, True), (18, 0, False), (23, 59, False),
]


def test_boundaries():
    for hour, minute, expect_peak in CASES:
        assert is_peak_time(bj(hour, minute)) == expect_peak, f'{hour}:{minute}'


def test_idle_complement():
    for hour in range(24):
        assert is_idle_time(bj(hour, 30)) == (not is_peak_time(bj(hour, 30)))


def test_seconds_until_idle():
    assert seconds_until_idle(bj(10, 0)) == 2 * 3600
    assert seconds_until_idle(bj(11, 59, 30)) == 30
    assert seconds_until_idle(bj(15, 0)) == 3 * 3600
    assert seconds_until_idle(bj(17, 59, 59)) == 1
    assert seconds_until_idle(bj(13, 0)) == 0
    assert seconds_until_idle(bj(19, 0)) == 0
    assert seconds_until_idle(bj(8, 0)) == 0
    assert seconds_until_idle(bj(9, 0)) == 3 * 3600
    assert seconds_until_idle(bj(12, 0)) == 0


def test_next_idle_start():
    assert next_idle_start(bj(10, 0)) == bj(12, 0)
    assert next_idle_start(bj(9, 0)) == bj(12, 0)
    assert next_idle_start(bj(15, 0)) == bj(18, 0)
    assert next_idle_start(bj(17, 59, 59)) == bj(18, 0)
    assert next_idle_start(bj(13, 0)) == bj(13, 0)
    assert next_idle_start(bj(19, 0)) == bj(19, 0)
    assert next_idle_start(bj(8, 0)) == bj(8, 0)
    assert next_idle_start(bj(12, 0)) == bj(12, 0)


def test_format_hms():
    assert format_hms(0) == '0秒'
    assert format_hms(30) == '30秒'
    assert format_hms(60) == '1分0秒'
    assert format_hms(90) == '1分30秒'
    assert format_hms(3600) == '1小时0分0秒'
    assert format_hms(2 * 3600 + 30 * 60 + 5) == '2小时30分5秒'
    assert format_hms(-10) == '0秒'


def test_peak_windows_constant():
    assert PEAK_WINDOWS == [('09:00', '12:00'), ('14:00', '18:00')]
