# -*- coding: utf-8 -*-
"""
峰谷定价时间侦测

判断当前北京时间处于高峰还是空闲时段：
- 高峰时段: 09:00-12:00, 14:00-18:00（含起始，不含结束）
- 空闲时段: 其余时间（API 价格 = 高峰价的一半）

用途：转录完成后延迟翻译到空闲时段执行，降低 API 费用。
"""

from __future__ import annotations

import datetime as _dt

# 高峰时段（含起始不含结束）: 09:00 ≤ t < 12:00, 14:00 ≤ t < 18:00
PEAK_WINDOWS = [("09:00", "12:00"), ("14:00", "18:00")]

# 北京固定 UTC+8，无夏令时
_BEIJING_TZ = _dt.timezone(_dt.timedelta(hours=8))


def _beijing_tz():
    """返回北京时区对象；zoneinfo 可用时用之，否则回退固定 UTC+8。

    Windows 未内置 IANA 时区库时 zoneinfo 会抛 ZoneInfoNotFoundError，
    此时用固定 +8 偏移（北京无夏令时，结果完全一致）。
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return _BEIJING_TZ


def _normalize_now(now):
    """统一入参：None → 当前北京时间；naive 时间 → 附上北京时区"""
    if now is None:
        return now_beijing()
    if now.tzinfo is None:
        return now.replace(tzinfo=_beijing_tz())
    return now


def now_beijing() -> _dt.datetime:
    """当前北京时间（时区感知）"""
    return _dt.datetime.now(_beijing_tz())


def _window_bounds(now: _dt.datetime) -> list[tuple]:
    """把 PEAK_WINDOWS 解析为当天的 (start, end) 时区感知时刻列表"""
    out = []
    for start_s, end_s in PEAK_WINDOWS:
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        out.append((start, end))
    return out


def is_peak_time(now=None) -> bool:
    """是否处于高峰时段（默认按当前北京时间判断）"""
    now = _normalize_now(now)
    for start, end in _window_bounds(now):
        if start <= now < end:
            return True
    return False


def is_idle_time(now=None) -> bool:
    """是否处于空闲时段"""
    return not is_peak_time(now)


def seconds_until_idle(now=None) -> int:
    """距下一个空闲窗口开始的秒数（当前已空闲则为 0）"""
    now = _normalize_now(now)
    for start, end in _window_bounds(now):
        if start <= now < end:
            return max(0, int((end - now).total_seconds()))
    return 0


def next_idle_start(now=None) -> _dt.datetime:
    """下一个空闲窗口的开始时刻（当前已空闲则返回 now）"""
    now = _normalize_now(now)
    for start, end in _window_bounds(now):
        if start <= now < end:
            return end
    return now


def format_hms(seconds: int) -> str:
    """把秒数格式化为 X小时Y分Z秒"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"
