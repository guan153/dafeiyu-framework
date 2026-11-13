# -*- coding: utf-8 -*-
"""语感 + 情绪规范：从词包「看」出来的规律，不是背句子。

读的是 data/voice_profile.json（voice_study.py 量出来的），
加上人读语料后写下的定性规则。

对外只有四个东西：
    spec(kid=False, mood="")   → 给 prompt 的一小段规范（规则，不是例句）
    focus()                    → 这一轮要特别修的毛病（voice_train 写的）
    score(text, kid=False)     → 拿一句话对表打分，返回毛病清单
    write_runtime_files()      → 给 QQ 那边落一份纯文本（她那边只读文件）

原则：不往里塞语料原句。句子会被背，规律不会。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PROFILE = DATA / "voice_profile.json"
FOCUS = DATA / "voice_focus.json"
SCORE = DATA / "voice_score.json"
QQ_SPEC = DATA / "voice_spec_daughter.txt"
MOM_SPEC = DATA / "voice_spec_mom.txt"

# ── 从语料里读出来的数字（兜底值，文件在就用文件）────────────────
DEFAULT_NUM = {
    "median_len": 12,
    "le15": 0.75,
    "le25": 0.99,
    "no_dot": 0.99,
    "two_clause": 0.78,
    "question": 0.11,
    "particle": 0.05,
}


def _numbers(kid: bool = True) -> dict:
    try:
        p = json.loads(PROFILE.read_text(encoding="utf-8"))
        a = p["kid_safe"] if kid else p["all"]
        end = a.get("end") or {}
        return {
            "median_len": a["len"]["median"],
            "le15": a["len"]["le15"],
            "le25": a["len"]["le25"],
            "no_dot": end.get("不加标点", 0.99),
            "two_clause": a["clause_ge2"],
            "question": a["question_rate"],
            "particle": a["particle_rate"],
        }
    except Exception:
        return DEFAULT_NUM


# ── 人读出来的定性规则 ─────────────────────────────────────────
STYLE = """【人怎么说话 · 学规律，别背句子】
- 一句 {median_len} 字上下，{le15:.0%} 的话 15 字内说完；到 25 字就拆成两条发。
- 聊天短句常不加句号（语料里 {no_dot:.0%} 不加）；长段、正事、多句就正常用标点。
- 多数话是两小截：先给个反应，再补一句具体的。{two_clause:.0%} 都这么走。
- 用「就/才/还/也/又/再」把话串起来，不用「因此/然而/综上/首先」。
- 十句里约一句反问或追问（{question:.0%}），别句句都问。
- 「呢/嘛/啊/吧」很少用（{particle:.0%}），一段话最多一个。
- 具体压过抽象：说手上的、眼前的、身上的，不说「我理解你的感受」。
- 数字别凭印象：条数、个数、金额、时间，没查过就别说具体数，宁可说「我去查一下」。
- 自然比「像人」重要：别为了显得口语就故意短、故意省标点、故意装熟；该说清楚就说清楚。
- 一次只说一件事；说一半也行，不用讲圆满，不用解释自己为什么这么说。"""

EMOTION = """【情绪怎么接 · 先接人，再谈事】
- 他难受：先接住，不解决。给一个具体动作，或者允许他不好、不用马上振作；别连珠炮问怎么了，别给方案。
- 他生气：站他这边，先认同再说话，不劝和、不讲道理。
- 他高兴：跟着抬一下，夸那个具体细节，别泛泛地说「真棒」。
- 他道歉或软下来：接住就完，不追责，不趁机教育。
- 你自己低落：说自己累到哪、松到哪，说一半，不演也不讨安慰。
- 你自己被顶：可以回顶一句，也可以服软，但不能是客服式道歉。
- 不想聊了：干脆断，留一句回头再找他，别解释一大段。
- 任何时候：不总结、不排比、不给建议清单、不说「希望对你有帮助」。"""

NSFW_HINT = """【荤的分寸 · 只在你和宝宝之间】
- 擦边靠语气和留白，不靠器官名词；一句里最多一个钩子。
- 他真难受的时候不接荤的，先接人。
- 说停就停，不追问、不硬撑。"""


def _numbers_text(kid: bool) -> str:
    n = _numbers(kid)
    n["no_dot"] = min(0.99, n["no_dot"])
    n["le25"] = min(0.99, n["le25"])
    return STYLE.format(**n)


def spec(kid: bool = False, mood: str = "") -> str:
    """给 prompt 的规范块。kid=True 就是女儿版（无荤）。"""
    parts = [_numbers_text(kid), EMOTION]
    if not kid:
        parts.append(NSFW_HINT)
    return "\n".join(parts)


# ── 这一轮要修的毛病 ──────────────────────────────────────────
def focus(kid: bool = False) -> str:
    try:
        d = json.loads(FOCUS.read_text(encoding="utf-8"))
    except Exception:
        return ""
    key = "kid" if kid else "mom"
    items = (d.get(key) or [])[:2]
    if not items:
        return ""
    return "这轮特别改这两条：\n" + "\n".join(f"- {x}" for x in items)


# ── 打分 ─────────────────────────────────────────────────────
AI_SMELL = ("总之", "综上", "总的来说", "总结一下", "首先", "其次", "最后",
            "需要注意的是", "值得注意的是", "希望能帮到你", "希望这对你有帮助",
            "如果你需要", "我可以帮你", "建议你", "应该考虑", "作为一个",
            "我理解你的感受", "我明白你的感受", "抱抱你", "加油哦")
BOOKISH = ("因此", "然而", "此外", "从而", "综上", "基于", "针对", "关于这一点",
           "总的来说", "一方面", "另一方面")
PARTICLES = "呀呢吧嘛啊哦诶嘿哈啦咯咧哇哟嗷唉"


def score(text: str, kid: bool = False) -> dict:
    """一句话有多像人。返回 {'ok': bool, 'bad': [毛病], 'm': {...}}"""
    t = (text or "").strip()
    bad: list[str] = []
    m: dict = {"len": len(t)}

    if not t:
        return {"ok": False, "bad": ["空的"], "m": m}

    # 1 长度
    if len(t) > 34:
        bad.append("太长，拆成两条")
    elif len(t) > 25:
        bad.append("偏长，砍到 20 字内")

    # 2 句号收尾（短句管；长段/正事允许正常标点）
    if t.rstrip().endswith("。") and len(t) <= 45:
        bad.append("句尾别加句号")

    # 3 AI 腔
    hit = [w for w in AI_SMELL if w in t]
    if hit:
        bad.append("AI 腔：" + "、".join(hit[:2]))
    bk = [w for w in BOOKISH if w in t]
    if bk:
        bad.append("书面连接词：" + "、".join(bk[:2]))

    # 4 语气词堆叠
    pc = sum(t.count(c) for c in PARTICLES)
    if pc >= 3:
        bad.append("语气词堆了")

    # 5 结构：排比 / 分号 / 序号
    if t.count("；") or re.search(r"[1-3][\.、）)]", t):
        bad.append("别列条列点")

    # 6 抽象安慰
    if kid is False and "我懂你" in t:
        pass  # 「我懂」是口语，不拦

    return {"ok": not bad, "bad": bad, "m": m}


def score_batch(texts: list[str], kid: bool = False) -> dict:
    """一批回复的整体像人度。"""
    rows = [score(t, kid) for t in texts]
    n = max(1, len(rows))
    dims = {
        "长度超标": sum(1 for r in rows if any("长" in b for b in r["bad"])),
        "句号收尾": sum(1 for r in rows if any("句号" in b for b in r["bad"])),
        "AI腔": sum(1 for r in rows if any("AI 腔" in b or "书面" in b for b in r["bad"])),
        "语气词堆叠": sum(1 for r in rows if any("语气词" in b for b in r["bad"])),
        "列条列点": sum(1 for r in rows if any("列条" in b for b in r["bad"])),
    }
    # 复读：相邻两条 7 字重合
    dup = 0
    for i in range(1, len(texts)):
        a, b = texts[i - 1], texts[i]
        if len(a) >= 7 and len(b) >= 7:
            if any(b[j:j + 7] in a for j in range(len(b) - 6)):
                dup += 1
    dims["复读"] = dup
    return {
        "n": len(rows),
        "pass": sum(1 for r in rows if r["ok"]) / n,
        "dims": dims,
        "avg_len": round(sum(len(t) for t in texts) / n, 1) if texts else 0,
    }


def write_runtime_files() -> None:
    """给各处落纯文本：谁只读文件谁就不用改代码。"""
    DATA.mkdir(parents=True, exist_ok=True)
    for path, kid in ((QQ_SPEC, True), (MOM_SPEC, False)):
        body = spec(kid=kid)
        f = focus(kid=kid)
        path.write_text(body + ("\n\n" + f if f else "") + "\n", encoding="utf-8")


def read_spec_file(path: Path, max_age: float = 300.0) -> str:
    """读落盘的规范（带 5 分钟缓存，改完不用重启）。"""
    global _FILE_CACHE
    try:
        st = path.stat()
    except Exception:
        return ""
    key = str(path)
    hit = _FILE_CACHE.get(key)
    if hit and hit[0] == st.st_mtime and time.time() - hit[1] < max_age:
        return hit[2]
    try:
        txt = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    _FILE_CACHE[key] = (st.st_mtime, time.time(), txt)
    return txt


_FILE_CACHE: dict[str, tuple[float, float, str]] = {}


if __name__ == "__main__":
    print(_numbers_text(True))
    print()
    print(EMOTION)
    print()
    for s in ("我今天有点累", "嗯，你先忙", "这个方案因此需要调整。"):
        print(s, "→", score(s, kid=True))
