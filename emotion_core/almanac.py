# -*- coding: utf-8 -*-
"""历法：让她知道今天几号、星期几、什么节气、什么季节。

不联网、不查表：直接按太阳视黄经算二十四节气（Meeus 低精度公式，误差 < 1 分钟），
所以任何年份都能算，也不会因为数据没更新就失灵。

季节按**节气**分（传统分法）：
    立春→立夏 春 ｜ 立夏→立秋 夏 ｜ 立秋→立冬 秋 ｜ 立冬→立春 冬
"""

from __future__ import annotations

import math
from datetime import datetime, date, timedelta

__all__ = ["SOLAR_TERMS", "solar_term_of", "current_term", "season_of", "now_info", "render", "render_block"]

# 二十四节气，按黄经每 15° 一个（从春分 0° 起算）
SOLAR_TERMS = [
    ("春分", 0), ("清明", 15), ("谷雨", 30), ("立夏", 45), ("小满", 60), ("芒种", 75),
    ("夏至", 90), ("小暑", 105), ("大暑", 120), ("立秋", 135), ("处暑", 150), ("白露", 165),
    ("秋分", 180), ("寒露", 195), ("霜降", 210), ("立冬", 225), ("小雪", 240), ("大雪", 255),
    ("冬至", 270), ("小寒", 285), ("大寒", 300), ("立春", 315), ("雨水", 330), ("惊蛰", 345),
]

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

SEASON_START = {"立春": "春", "立夏": "夏", "立秋": "秋", "立冬": "冬"}


def _jd(dt: datetime) -> float:
    """公历 → 儒略日（UTC 近似，够算节气）。"""
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _sun_longitude(jd: float) -> float:
    """太阳视黄经（度，0-360）。Meeus 低精度。"""
    t = (jd - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    mr = math.radians(m % 360)
    c = (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(mr) \
        + (0.019993 - 0.000101 * t) * math.sin(2 * mr) \
        + 0.000289 * math.sin(3 * mr)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    lam = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    return lam % 360.0


def _term_time(year: int, target_deg: float) -> datetime:
    """求某年太阳黄经到达 target_deg 的时刻（取该年内的那一次）。"""
    # 先粗定位：黄经每过 15° 约 15.2 天
    base = datetime(year, 1, 1)
    # 用二分在一年里找
    lo = _jd(base)
    hi = _jd(datetime(year + 1, 1, 1))
    def diff(jd: float) -> float:
        d = (_sun_longitude(jd) - target_deg) % 360.0
        return d
    # 采样找跨越点（从 360 跳到 0 的地方）
    step = 1.0
    prev_jd, prev_d = lo, diff(lo)
    cross = None
    j = lo + step
    while j <= hi:
        cur_d = diff(j)
        if cur_d < prev_d - 180:      # 跨过 0°
            cross = (prev_jd, j)
            break
        prev_jd, prev_d = j, cur_d
        j += step
    if not cross:
        return base
    a, b = cross
    for _ in range(40):
        mid = (a + b) / 2
        if diff(mid) > 180:   # 还在 0° 之前
            a = mid
        else:
            b = mid
    jd = (a + b) / 2
    # JD → 本地时间：JD 基准是 UTC，这里统一换到 +08:00，否则节气日会差一天
    d0 = datetime(2000, 1, 1, 12) + timedelta(hours=8)
    return d0 + timedelta(days=jd - 2451545.0)


def _term_dates(year: int) -> list[tuple[str, datetime]]:
    out = []
    for name, deg in SOLAR_TERMS:
        dt = _term_time(year, deg)
        if dt.year != year:      # 边界年，取上年/下年那一次
            dt = _term_time(year + (1 if dt.year < year else -1), deg)
        out.append((name, dt))
    out.sort(key=lambda x: x[1])
    return out


def current_term(now: datetime | None = None) -> tuple[str, datetime, str]:
    """此刻所处节气（最近一个已经到来的）+ 它的时刻 + 下一个节气名。"""
    now = now or datetime.now()
    terms = _term_dates(now.year - 1) + _term_dates(now.year) + _term_dates(now.year + 1)
    terms.sort(key=lambda x: x[1])
    prev = None
    for name, dt in terms:
        if dt <= now:
            prev = (name, dt)
        else:
            return (prev[0] if prev else name), (prev[1] if prev else dt), name
    return (prev[0] if prev else ""), (prev[1] if prev else now), ""


def solar_term_of(day: date) -> str | None:
    """当天是不是节气日（是则返回节气名）。"""
    for name, dt in _term_dates(day.year):
        if dt.date() == day:
            return name
    return None


def season_of(now: datetime | None = None) -> str:
    """按节气分季：立春=春始，立夏=夏始，立秋=秋始，立冬=冬始。"""
    now = now or datetime.now()
    starts = {}
    for y in (now.year - 1, now.year, now.year + 1):
        for name, dt in _term_dates(y):
            if name in SEASON_START:
                starts[(y, name)] = dt
    ordered = sorted(((n, d) for (_, n), d in starts.items()), key=lambda x: x[1])
    cur = None
    for name, dt in ordered:
        if dt <= now:
            cur = name
    return SEASON_START.get(cur or "立春", "春")


def now_info(ts: float | None = None) -> dict:
    now = datetime.fromtimestamp(ts) if ts else datetime.now()
    term, term_at, nxt = current_term(now)
    days_in = (now.date() - term_at.date()).days
    season = season_of(now)
    hour = now.hour
    if hour < 5:
        part = "凌晨"
    elif hour < 8:
        part = "早上"
    elif hour < 11:
        part = "上午"
    elif hour < 13:
        part = "中午"
    elif hour < 17:
        part = "下午"
    elif hour < 19:
        part = "傍晚"
    else:
        part = "晚上"
    today_term = solar_term_of(now.date())
    return {
        "date": now.strftime("%Y年%m月%d日"),
        "time": now.strftime("%H:%M"),
        "weekday": WEEKDAYS[now.weekday()],
        "term": term,
        "term_days": days_in,
        "term_today": today_term,
        "next_term": nxt,
        "season": season,
        "part": part,
    }


def render(ts: float | None = None) -> str:
    i = now_info(ts)
    bits = [f"{i['date']} {i['weekday']} {i['time']}（{i['part']}）", f"{i['season']}季"]
    if i["term_today"]:
        bits.append(f"今天{i['term_today']}")
    else:
        bits.append(f"{i['term']}后第{i['term_days']}天")
    if i["next_term"]:
        bits.append(f"下个节气{i['next_term']}")
    return "，".join(bits)


def render_block(ts: float | None = None, budget: int = 90) -> str:
    text = render(ts)
    if len(text) > budget:
        text = text[: budget - 1] + "…"
    return f"<almanac>\n现在：{text}\n</almanac>"
