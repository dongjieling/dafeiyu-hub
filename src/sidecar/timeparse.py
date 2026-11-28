"""把「昨天晚上 / 前天 / 上周」这类相对时间解析成本地时间戳区间。"""

from __future__ import annotations

import datetime
import time


def _day_start(d: datetime.datetime) -> float:
    return datetime.datetime(d.year, d.month, d.day).timestamp()


def _at(d: datetime.datetime, hour: int, minute: int = 0) -> float:
    return datetime.datetime(d.year, d.month, d.day, hour, minute).timestamp()


def detect_range(text: str, now: float | None = None) -> tuple[float, float] | None:
    """识别查询里的相对时间，返回 (start, end)；识别不到返回 None。"""
    t = (text or '').strip()
    if not t:
        return None
    now = now or time.time()
    today = datetime.datetime.fromtimestamp(now)

    if any(k in t for k in ('刚才', '刚刚')):
        return (now - 2 * 3600, now)

    if any(k in t for k in ('昨天晚上', '昨晚', '昨晚上', '昨天夜里', '昨天半夜')):
        y = today - datetime.timedelta(days=1)
        start = _at(y, 18)
        # 凌晨也算「昨晚」：区间固定到第二天早上 6 点
        end = min(_at(today, 6), now)
        return (start, end)

    if '前天晚上' in t:
        d = today - datetime.timedelta(days=2)
        return (_at(d, 18), _at(d, 23, 59))

    if '前天' in t:
        d = today - datetime.timedelta(days=2)
        return (_day_start(d), _at(d, 23, 59))

    if '昨天' in t:
        d = today - datetime.timedelta(days=1)
        return (_day_start(d), _at(d, 23, 59))

    if any(k in t for k in ('今天上午', '今天下午', '今天晚上', '今天', '今早')):
        return (_day_start(today), now)

    if '上周' in t or '上个星期' in t:
        monday = today - datetime.timedelta(days=today.weekday())
        last_monday = monday - datetime.timedelta(days=7)
        return (_day_start(last_monday), _day_start(monday) - 1)

    if '上个月' in t:
        first = datetime.datetime(today.year, today.month, 1)
        prev_end = first - datetime.timedelta(seconds=1)
        prev_start = datetime.datetime(prev_end.year, prev_end.month, 1)
        return (_day_start(prev_start), _day_start(first) - 1)

    if any(k in t for k in ('最近', '这几天', '这两天')):
        return (now - 7 * 86400, now)

    return None


def describe(start: float, end: float) -> str:
    a = datetime.datetime.fromtimestamp(start)
    b = datetime.datetime.fromtimestamp(end)
    if a.date() == b.date():
        return a.strftime('%Y-%m-%d')
    return f'{a.strftime("%Y-%m-%d %H:%M")} ~ {b.strftime("%Y-%m-%d %H:%M")}'
