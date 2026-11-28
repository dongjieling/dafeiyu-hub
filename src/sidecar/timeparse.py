"""把「昨天晚上 / 前天 / 上周」这类相对时间解析成本地时间戳区间。"""

from __future__ import annotations

import datetime
import time


def _day_start(d: datetime.datetime) -> float:
    return datetime.datetime(d.year, d.month, d.day).timestamp()


def _at(d: datetime.datetime, hour: int, minute: int = 0) -> float:
    return datetime.datetime(d.year, d.month, d.day, hour, minute).timestamp()


_CN_NUM = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
           '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
           '十一': 11, '十二': 12}


def _cn_hour(text: str, context: bool = True) -> int | None:
    import re

    m = re.search(r'([零一二两三四五六七八九十]{1,2})\s*[点時时]', text)
    h = None
    if m:
        h = _CN_NUM.get(m.group(1))
    else:
        m = re.search(r'(\d{1,2})\s*[点時时]', text)
        if m:
            h = int(m.group(1))
    if h is None or not (0 <= h <= 23):
        return None
    if not context:
        return h
    prefix = text[max(0, m.start() - 3):m.start()]
    if any(k in prefix for k in ('晚', '夜', '下午', '傍晚')) and h < 12:
        h += 12
    if '凌晨' in prefix and h == 12:
        h = 0
    return h


def detect_range(text: str, now: float | None = None) -> tuple[float, float] | None:
    """识别查询里的相对/绝对时间，返回 (start, end)；识别不到返回 None。"""
    import re

    t = (text or '').strip()
    if not t:
        return None
    now = now or time.time()
    today = datetime.datetime.fromtimestamp(now)

    # 1) 绝对日期：2026-11-25 / 11月25号（可带时间）
    m = re.search(r'(20\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})', t)
    if m:
        try:
            d = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            h = _cn_hour(t)
            if h is None:
                return (_day_start(d), _at(d, 23, 59))
            start_ts = _at(d, h)
            return (start_ts, start_ts + 2 * 3600)
        except Exception:
            pass
    m = re.search(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', t)
    if m:
        try:
            d = datetime.datetime(today.year, int(m.group(1)), int(m.group(2)))
            h = _cn_hour(t)
            if h is None:
                return (_day_start(d), _at(d, 23, 59))
            start_ts = _at(d, h)
            return (start_ts, start_ts + 2 * 3600)
        except Exception:
            pass

    # 只写日期（如「25号」）
    m = re.search(r'(?<![\d月])(\d{1,2})\s*[日号]', t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            try:
                d = datetime.datetime(today.year, today.month, day)
                if (d.date() - today.date()).days > 1:
                    prev = datetime.datetime(today.year, today.month, 1) - datetime.timedelta(days=1)
                    d = datetime.datetime(prev.year, prev.month, day)
                h = _cn_hour(t)
                if h is None:
                    return (_day_start(d), _at(d, 23, 59))
                start_ts = _at(d, h)
                return (start_ts, start_ts + 2 * 3600)
            except Exception:
                pass

    # 2) 昨晚（可带具体小时，如「昨晚十点」）
    if any(k in t for k in ('昨天晚上', '昨晚', '昨晚上', '昨天夜里', '昨天半夜')):
        y = today - datetime.timedelta(days=1)
        h = _cn_hour(t, context=False)
        if h is not None:
            # 口语里的「昨晚十点」指 22 点；「昨晚一点」指次日 01 点
            if h == 12:
                start = _at(today, 0)
            elif 6 <= h < 12:
                start = _at(y, h + 12)
            elif h < 6:
                start = _at(today, h)
            else:
                start = _at(y, h)
            return (start, start + 2 * 3600)
        start = _at(y, 18)
        end = min(_at(today, 6), now)
        return (start, end)

    if '前天晚上' in t:
        d = today - datetime.timedelta(days=2)
        return (_at(d, 18), min(_at(today - datetime.timedelta(days=1), 6), now))

    if '前天' in t:
        d = today - datetime.timedelta(days=2)
        return (_day_start(d), _at(d, 23, 59))

    if '昨天' in t:
        d = today - datetime.timedelta(days=1)
        return (_day_start(d), _at(d, 23, 59))

    # 3) 周三 / 上周三
    m = re.search(r'(上|这|本)?\s*(?:周|星期)\s*([一二三四五六日天])', t)
    if m:
        idx = '一二三四五六日天'.index(m.group(2))
        target = idx if idx < 6 else 6
        monday = today - datetime.timedelta(days=today.weekday())
        if m.group(1) == '上':
            d = monday - datetime.timedelta(days=7) + datetime.timedelta(days=target)
        else:
            d = monday + datetime.timedelta(days=target)
            if d.date() > today.date():
                d -= datetime.timedelta(days=7)
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

    # 4) 「最近」只在明确指一段时间时触发，避免「最近好吗」被误判
    if re.search(r'这两天|这几天|最近\s*(几|这|一|两|段|天|周|月|怎么|如何|过得)', t):
        return (now - 7 * 86400, now)

    return None


def describe(start: float, end: float) -> str:
    a = datetime.datetime.fromtimestamp(start)
    b = datetime.datetime.fromtimestamp(end)
    if a.date() == b.date() and (b - a).total_seconds() <= 6 * 3600:
        return f'{a.strftime("%Y-%m-%d %H:%M")} ~ {b.strftime("%H:%M")}'
    if a.date() == b.date():
        return a.strftime('%Y-%m-%d')
    return f'{a.strftime("%Y-%m-%d %H:%M")} ~ {b.strftime("%Y-%m-%d %H:%M")}'
