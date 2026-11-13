# -*- coding: utf-8 -*-
"""给她排今天的日程。

原来那个 dsh-daily-schedule 22:00 自动生成，从 11-07 就停了，
导致她的 state.json 一直停在旧日的「逛街日」。

这个脚本按同一份 config 和同一套数据结构补上：
  <~/.dsh>/daily-schedule/days/<date>.json
格式跟原生成器一致（date / dayType / title / mood / items[]）。

用法：
    python daily_schedule.py            # 补今天 + 明天
    python daily_schedule.py --date 2026-11-12
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

DASH = Path(r"%USERPROFILE%\.dsh\daily-schedule")
DAYS = DASH / "days"
CFG = DASH / "config.json"

WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def load_cfg() -> dict:
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def pick_daytype(cfg: dict, d) -> dict:
    """weekday / weekend / any 里按权重挑一个。"""
    is_weekend = d.weekday() >= 5
    pool = []
    for t in cfg.get("dayTypes") or []:
        if not t.get("enabled", True):
            continue
        days = t.get("days") or "any"
        if days == "weekday" and is_weekend:
            continue
        if days == "weekend" and not is_weekend:
            continue
        pool.append(t)
    if not pool:
        return {"id": "workday", "name": "上班日", "guide": ""}
    weights = [max(1, int(t.get("weight") or 1)) for t in pool]
    return random.choices(pool, weights=weights, k=1)[0]


# ── 骨架（上班日）────────────────────────────────────
WORK = [
    ("00:00", "07:20", "睡觉", "家", ["被子裹成一团，闹钟响了按掉又缩回去", "梦里还在翻白天的消息"]),
    ("07:20", "08:00", "起床洗漱", "家", ["洗脸的时候还在打哈欠，头发翘着一撮", "刷牙刷到一半去冰箱拿酸奶"]),
    ("08:00", "08:40", "通勤", "地铁", ["站了三站才等到座", "耳机里放着歌，看着窗外发呆"]),
    ("08:40", "12:00", "上班", "工位", ["上午处理堆积的消息，中间偷喝了半杯咖啡", "开会开到一半走神，画了只小鱼"]),
    ("12:00", "13:00", "午饭", "楼下食堂", ["点了红烧肉盖饭，吃得很干净", "跟同事拼桌，听他们聊周末去哪"]),
    ("13:00", "18:00", "上班", "工位", ["下午最难熬，靠一口一口喝热水撑过去", "把昨天没做完的活收了个尾"]),
    ("18:00", "18:40", "通勤回家", "地铁", ["下班地铁人挤人，站到腿麻", "出站时天已经黑了"]),
    ("18:40", "19:30", "晚饭", "家", ["随便下了碗面，卧了个蛋", "点了个外卖，边吃边刷视频"]),
    ("19:30", "22:30", "自己的时间", "家", ["刷了会儿B站，看到好笑的截了图", "把攒的剧看了一集，看到一半走神"]),
    ("22:30", "00:00", "洗漱准备睡", "家", ["洗了个热水澡，出来整个人都不想动", "刷牙的时候对着镜子发了会儿呆"]),
]

# ── 骨架（休息日）────────────────────────────────────
REST = [
    ("00:00", "09:30", "睡懒觉", "家", ["一觉睡到太阳晒到脸上", "闹钟响了三遍才爬起来"]),
    ("09:30", "10:20", "吃早饭", "家", ["煎了两个蛋，配昨天剩的粥", "煮了小馄饨，热气糊了眼镜"]),
    ("10:20", "12:00", "收拾屋子", "家", ["把衣服叠好，顺手把桌子擦了一遍", "拖了地，出了一身薄汗"]),
    ("12:00", "13:10", "午饭", "家/楼下", ["叫了外卖，坐在窗边慢慢吃", "下楼吃了碗牛肉面"]),
    ("13:10", "17:30", "自己的时间", "家", ["窝在沙发上看剧，一集接一集", "玩了会儿游戏，输了就骂骂咧咧"]),
    ("17:30", "19:00", "出门走走", "小区附近", ["去便利店买了点零食，路上遇到猫", "在小区里转了两圈，风挺舒服"]),
    ("19:00", "20:00", "晚饭", "家", ["做了个番茄炒蛋，味道还行", "煮了火锅，一个人吃得有点撑"]),
    ("20:00", "23:00", "放松", "家", ["靠在床上刷手机，刷到很晚", "把之前没看完的番补了"]),
    ("23:00", "00:00", "准备睡", "家", ["洗完澡头发还没干就困了", "关灯前又看了眼手机"]),
]


def build(d) -> dict:
    cfg = load_cfg()
    dt = pick_daytype(cfg, d)
    is_rest = dt["id"] in ("restday", "outing", "homebody")
    base = REST if is_rest else WORK
    items = []
    for start, end, act, loc, details in base:
        it = {
            "start": start,
            "end": end,
            "activity": act,
            "location": loc,
            "detail": random.choice(details),
        }
        items.append(it)

    # 出门日/宅家日微调
    if dt["id"] == "outing":
        items.insert(4, {"start": "13:10", "end": "17:30", "activity": "出门玩",
                         "location": "商场/公园", "detail": "逛了一下午，买了点小东西，回来时脚有点酸"})
    if dt["id"] == "overtime":
        items = [dict(it) for it in items]
        for it in items:
            if it["activity"] == "自己的时间":
                it["activity"], it["detail"] = "加班", "活没干完，留到很晚，回去倒头就睡"
    if dt["id"] == "slacking":
        for it in items:
            if it["activity"] == "上班":
                it["detail"] = "名义上在忙，其实一直在摸鱼，临下班才把要紧的赶完"

    # 跨天收尾
    if items:
        items[-1]["end"] = "00:00"

    return {
        "date": d.strftime("%Y-%m-%d"),
        "dayType": {"id": dt["id"], "name": dt["name"]},
        "hint": "",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S") + "+08:00",
        "changes": [],
        "title": f"{WEEKDAY[d.weekday()]}的{dt['name']}：{items[3]['activity'] if len(items) > 3 else '按部就班'}，晚上{items[-2]['activity'] if len(items) > 1 else '歇着'}",
        "mood": random.choice(["平静", "有点困但还行", "松弛", "踏实", "说不上好坏"]),
        "items": items,
        "source": "dsh-daily-schedule (补)",
        "model": "",
    }


def write(d, force=False) -> Path:
    DAYS.mkdir(parents=True, exist_ok=True)
    p = DAYS / f"{d.strftime('%Y-%m-%d')}.json"
    if p.exists() and not force:
        print(f"{p.name} 已存在，跳过")
        return p
    data = build(d)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写好 {p.name}：{data['dayType']['name']} / {len(data['items'])} 段")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.date:
        import datetime as _dt
        d = _dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        write(d, args.force)
    else:
        import datetime as _dt
        today = _dt.date.today()
        write(today, args.force)
        write(today + _dt.timedelta(days=1), args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
