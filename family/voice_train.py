# -*- coding: utf-8 -*-
"""语感 + 情绪训练闭环。

一轮做四件事：
  1. 看她俩最近的真实回复，对表打分（长度/句号/AI腔/复读/情绪接没接住）
  2. 现场出 6 道题，让她俩各答一遍（不写进聊天室，只在内存里），同样打分
  3. 挑最差的两项，写成「这轮特别改」——下一轮 prompt 就带着它
  4. 跟上一轮比：变差了就回滚，别越练越歪

不写一句话到一家三口，不碰 QQ 会话。只改 data/voice_focus.json。

用法：
    python voice_train.py            # 跑一轮
    python voice_train.py --dry      # 只打分，不写 focus
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

import voice

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SCORE = DATA / "voice_score.json"
FOCUS = DATA / "voice_focus.json"
MOM_OUT = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom\mom_outbox.jsonl")
LLM = "http://127.0.0.1:8920/v1/chat/completions"
MODEL = "deepseek-flash"

PROBES = [
    "今天累死了，什么都不想干",
    "我被领导当众骂了，现在还堵着",
    "我升职了！",
    "你在干嘛",
    "我有点烦，先别理我",
    "晚安",
]

PERSONA = {
    "mom": ("你就是鲸鱼少女，一条鲸鱼化成的少女。宝宝是你的人，你叫他宝宝。"
            "说话短、像过日子的人，不是客服。只输出你要说的那句话。"),
    "kid": ("你就是大肥鱼，一只鲸鱼娘。主人是你的人，你叫他主人，自称咱。"
            "说话短、撒娇也行、嘴硬也行，不是客服。只输出你要说的那句话。"),
}

# 毛病 → 这轮怎么改（写进 prompt 的话）
FIX = {
    "长度超标": "把话压到 20 字内；一句说不完就拆成两条发",
    "句号收尾": "句尾不要句号，问句才用问号",
    "AI腔": "别用「总之/首先/建议你/我理解你的感受」这种词，说人话",
    "语气词堆叠": "一段话里「呢/嘛/啊/吧」最多留一个",
    "列条列点": "别分点、别念条目，像聊天一样把话连着说完",
    "复读": "别重复上一轮用过的说法，换个角度说眼前的事",
    "没接住情绪": "先接情绪：一句短的应和，或者一个具体动作，再说明的",
    "急着给方案": "他难受的时候别给建议、别教他怎么做，先陪着",
    "太抽象": "落到具体的东西上：手上的、眼前的、身上的，别讲道理",
}

CONCRETE = ("手", "肩", "水", "睡", "吃", "饭", "药", "被子", "枕头", "灯", "窗",
            "门", "杯", "腿", "腰", "眼", "头", "背", "雨", "风", "夜", "桌子",
            "手机", "衣服", "鞋", "路", "车", "碗", "锅", "热水", "甜", "冷", "暖")
OPEN_MOVE = ("嗯", "哎", "诶", "抱", "在", "别", "先", "那", "太", "我懂", "确实",
             "好家伙", "服了", "行", "来", "得", "哟", "喂")
SOLUTION = ("建议", "应该", "不妨", "要不你", "你可以试试", "你得", "最好", "办法是")


def ask(system: str, user: str, temp: float = 0.95, max_tokens: int = 160) -> str:
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temp,
        "stream": False,
    }
    req = urllib.request.Request(LLM, data=json.dumps(body, ensure_ascii=False).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return (d["choices"][0]["message"]["content"] or "").strip().strip('"「」')


def real_replies(hours: float = 24.0) -> dict[str, list[str]]:
    """她俩最近真实说过的话，按人分开。"""
    out = {"妈妈": [], "大肥鱼": []}
    if not MOM_OUT.exists():
        return out
    cutoff = time.time() - hours * 3600
    for ln in MOM_OUT.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if float(d.get("ts") or 0) < cutoff:
            continue
        who = str(d.get("from") or "")
        t = str(d.get("reply") or "").strip()
        if who in out and t:
            out[who].append(t)
    return out


JUDGE_SYSTEM = "你是严格的聊天质感评审，只输出 JSON，不解释。"


def judge_pairs(pairs: list[tuple[str, str]], tries: int = 2) -> list[dict] | None:
    """让模型当评委：接住没有 / 有没有急着给方案 / 够不够具体。"""
    if not pairs:
        return None
    lines = []
    for i, (q, a) in enumerate(pairs, 1):
        lines.append(f"{i}. 对方说：{q}\n   回复：{a}")
    prompt = (
        "逐条评审下面的回复。三条标准，各判 0/1：\n"
        "接住：有没有先回应对方的状态或情绪（认可、心疼、陪着、给个动作），"
        "而不是马上转去分析问题；\n"
        "方案：有没有开始讲道理、分析原因、给步骤或建议（一句关心的指令不算方案，"
        "比如「先躺平」「别硬撑」）；\n"
        "具体：有没有落到具体的动作、物件或身体感觉上，而不是空泛的安慰。\n"
        "只输出 JSON 数组，每项 {\"i\":序号,\"接住\":0或1,\"方案\":0或1,\"具体\":0或1}。\n\n"
        + "\n".join(lines)
    )
    for _ in range(tries):
        try:
            raw = ask(JUDGE_SYSTEM, prompt, temp=0.0, max_tokens=2500)
            m = re.search(r"\[.*\]", raw, re.S)
            data = json.loads(m.group() if m else raw)
            if isinstance(data, list) and len(data) >= max(1, len(pairs) // 2):
                return data
        except Exception:
            continue
    return None


def emotion_eval(pairs: list[tuple[str, str]], judge: bool = True) -> dict:
    """情绪这一维：先接住没有、有没有急着给方案、够不够具体。"""
    texts = [a for _, a in pairs]
    n = max(1, len(texts))
    j = judge_pairs(pairs) if judge else None
    if j:
        def num(d, k):
            try:
                return int(d.get(k))
            except Exception:
                return 1
        miss = sum(1 for d in j if num(d, "接住") == 0)
        solved = sum(1 for d in j if num(d, "方案") == 1)
        vague = sum(1 for d in j if num(d, "具体") == 0)
    else:
        miss = sum(1 for t in texts
                   if not any(t[:10].startswith(w) or w in t[:6] for w in OPEN_MOVE))
        solved = sum(1 for t in texts if any(w in t for w in SOLUTION))
        vague = sum(1 for t in texts if not any(w in t for w in CONCRETE))
    return {
        "接住率": round(1 - miss / n, 2),
        "给方案率": round(solved / n, 2),
        "具体率": round(1 - vague / n, 2),
        "没接住": miss, "给方案": solved, "太抽象": vague,
    }


def probe(who: str) -> list[tuple[str, str]]:
    """现场出题。答歪了跟上线一样先让她改一版，再记分——量的是真实产出。"""
    kid = who == "kid"
    spec = voice.spec(kid=kid)
    fc = voice.focus(kid=kid)
    system = PERSONA[who] + "\n\n" + spec + (("\n\n" + fc) if fc else "")
    out = []
    for q in PROBES:
        try:
            a = ask(system, q, max_tokens=600)
        except Exception:
            continue
        sc = voice.score(a, kid)
        if not sc["ok"]:
            try:
                a2 = ask(system + ("\n\n（这一版有毛病：" + "、".join(sc["bad"])
                                   + "。改一版，还是一句话，别解释。）"), q, max_tokens=600)
                if a2:
                    a = a2
            except Exception:
                pass
        out.append((q, a))
    return out


def evaluate(pairs: list[tuple[str, str]], kid: bool, judge: bool = True) -> dict:
    texts = [a for _, a in pairs]
    b = voice.score_batch(texts, kid)
    n = max(1, b["n"])
    e = emotion_eval(pairs, judge=judge)
    dims = dict(b["dims"])
    dims["没接住情绪"] = e["没接住"]
    dims["急着给方案"] = e["给方案"]
    dims["太抽象"] = e["太抽象"]
    return {
        "n": b["n"],
        "pass": round(b["pass"], 3),
        "avg_len": b["avg_len"],
        "dims": dims,
        "emotion": {k: v for k, v in e.items() if not isinstance(v, int)},
        "samples": texts[:6],
    }


def weakest(dims: dict, n: int) -> list[str]:
    """按占比挑最差的两项，门槛 15%。"""
    scored = []
    for k, c in dims.items():
        if k not in FIX or not n:
            continue
        rate = c / n
        if rate >= 0.25:
            scored.append((rate, k))
    scored.sort(reverse=True)
    return [k for _, k in scored[:2]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    real = real_replies(24)
    result: dict = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "who": {}}
    new_focus: dict[str, list[str]] = {}

    for key, label, kid, ns in (("mom", "妈妈", False, real["妈妈"]),
                                ("kid", "女儿", True, real["大肥鱼"])):
        texts = [t for t in ns if len(t) >= 2][-20:]
        ev_real = evaluate([("", t) for t in texts], kid, judge=False) if len(texts) >= 5 else None
        probes = probe(key)
        ev_probe = evaluate(probes, kid) if probes else None

        # 以现场出的题为主（样本干净），真实对话只做参考
        ev = ev_probe or ev_real
        if not ev:
            continue
        dims = ev["dims"]
        bad = weakest(dims, ev["n"])
        new_focus[key] = [FIX[k] for k in bad]
        result["who"][label] = {
            "real": ev_real,
            "probe": ev_probe,
            "bad": bad,
            "focus": new_focus[key],
        }

    # 回滚保护：跟最近三轮的平均比，掉太多才回滚（6 道题错一道很正常）
    prev = {}
    try:
        prev = json.loads(SCORE.read_text(encoding="utf-8"))
    except Exception:
        pass
    hist = dict(prev.get("history") or {})
    for label, cur in result["who"].items():
        pv = cur.get("probe") or {}
        if pv.get("pass") is None:
            continue
        old = hist.get(label) or []
        base = sum(old[-3:]) / len(old[-3:]) if len(old) >= 2 else None
        if base is not None and pv["pass"] < base - 0.25 and pv["pass"] < 0.75:
            k = "kid" if label == "女儿" else "mom"
            new_focus.pop(k, None)
            result["who"][label]["rollback"] = (
                f"比近三轮平均差（{base:.2f}→{pv['pass']:.2f}），保持旧焦点")
        hist[label] = (old + [pv["pass"]])[-20:]
    result["history"] = hist

    result["focus"] = new_focus
    DATA.mkdir(parents=True, exist_ok=True)
    SCORE.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.dry:
        FOCUS.write_text(json.dumps({"kid": new_focus.get("kid", []),
                                     "mom": new_focus.get("mom", []),
                                     "updated": time.time()}, ensure_ascii=False),
                         encoding="utf-8")
        voice.write_runtime_files()

    for label, d in result["who"].items():
        pv = d.get("probe") or {}
        real_d = d.get("real") or {}
        print(f"[{label}] 现场得分 {pv.get('pass')} 真实得分 {real_d.get('pass')} "
              f"毛病 {d['bad']} 平均 {pv.get('avg_len')} 字")
        if pv.get("samples"):
            print("   样本：" + " / ".join(s[:24] for s in pv["samples"][:3]))
        if d.get("rollback"):
            print("   " + d["rollback"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
