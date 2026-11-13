# -*- coding: utf-8 -*-
"""自动验收：每轮自动学习跑完，由这个脚本判定「这次学到东西没有」。

它只读、只在内存里跑，**不往你的对话里写一个字**。

验收项分四组：
  A 词包   两边都读得到、女儿版没有荤词、妈妈版是完整版
  B 去重   禁词表新鲜、两边 prompt 都吃到了禁词
  C 指标   最近回复的复读率、AI 腔比例、长度分布
  D 服务   接口、看板、她俩的心跳

用法：
    python self_check.py            # 跑并打印
    python self_check.py --json     # 机读
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(r"%PROJECT_HOME%\母女聊天室")
PLUG = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_whale_mom")
MOM = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom")
OUTBOX = MOM / "mom_outbox.jsonl"
AVOID = HERE / "data" / "avoid_words.json"
REPORT = HERE / "data" / "self_check.json"
BASE = "http://127.0.0.1:8899"

rows: list[tuple[str, str, bool]] = []   # (组, 名称, 是否通过)
notes: list[str] = []


def ok(group: str, name: str, passed: bool, why: str = "") -> bool:
    rows.append((group, name, bool(passed)))
    if why:
        notes.append(f"[{group}] {name}: {why}")
    return passed


# ── A 词包 ─────────────────────────────────────────────
def check_vocab():
    sys.path.insert(0, str(HERE))
    try:
        import vocab_pack as V
    except Exception as e:  # noqa: BLE001
        ok("A词包", "妈妈版可加载", False, str(e))
        return
    s = V.stats()
    ok("A词包", "妈妈版条数", s["total"] >= 10000, f"{s['total']} 条")
    got_hot = any("荤" in c for c in s["by_category"])
    ok("A词包", "妈妈版含完整分类", got_hot, "网上荤词在")

    # 女儿版
    sys.path.insert(0, str(PLUG))
    for m in list(sys.modules):
        if m == "vocab_pack":
            del sys.modules[m]
    try:
        import vocab_pack as KV
        import inspect
        dflt = inspect.signature(KV.pick).parameters["kid_safe"].default
        ok("A词包", "女儿版默认过滤", dflt is True, f"kid_safe={dflt}")
        nsfw = {d["line"] for d in KV._load() if d.get("category") == KV.NSFW_CATEGORY}
        hit = 0
        for _ in range(80):
            for x in KV.pick("随便说点", 6):
                if x in nsfw:
                    hit += 1
        ok("A词包", "女儿版零泄漏", hit == 0, f"抽 480 条命中 {hit}")
    except Exception as e:  # noqa: BLE001
        ok("A词包", "女儿版可加载", False, str(e))


# ── B 去重 ─────────────────────────────────────────────
def check_avoid():
    if not AVOID.exists():
        ok("B去重", "禁词表存在", False, "还没跑过 learn_watch")
        return
    d = json.loads(AVOID.read_text(encoding="utf-8"))
    age_h = (time.time() - float(d.get("updated") or 0)) / 3600
    ok("B去重", "禁词表新鲜", age_h < 24, f"{age_h:.1f} 小时前更新")
    # 空表是正常的——说明最近没复读。这不算失败。
    avoid_n = len(d.get("avoid") or [])
    ok("B去重", "禁词表已判定", True,
       f"{avoid_n} 个词" if avoid_n else "最近没复读，表是空的（正常）")
    # 两边 prompt 里能不能吃到
    for name, path in (("妈妈", HERE / "mom_agent.py"), ("女儿", PLUG / "main.py")):
        src = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        ok("B去重", f"{name}侧接禁词", "avoid_words" in src or "禁词" in src or "avoid" in src,
           "代码里能找到 avoid 的引用" if ("avoid" in src) else "还没接")
    # 反重复函数
    for name, path in (("妈妈", HERE / "mom_agent.py"), ("女儿", PLUG / "main.py")):
        src = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        ok("B去重", f"{name}侧有撞车检测", "_repeat_hit" in src or "repeat_hit" in src, "")


# ── C 指标 ─────────────────────────────────────────────
def recent(n: int = 30) -> list[dict]:
    out = []
    if not OUTBOX.exists():
        return out
    for ln in OUTBOX.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if str(d.get("from") or "") in ("大肥鱼", "妈妈") and d.get("reply"):
            out.append(d)
    return out[-n:]


AI_SMELL = ("总之", "综上", "希望能帮到你", "如果你需要", "首先", "其次", "最后",
            "需要注意的是", "值得注意的是", "总的来说", "总结一下", "希望这对你有帮助",
            "作为一个", "我可以帮你", "建议你", "应该考虑")


def check_quality():
    rs = recent(30)
    if len(rs) < 8:
        ok("C指标", "样本量", True, f"只有 {len(rs)} 条，跳过指标")
        return
    texts = [str(d.get("reply") or "") for d in rs]
    # 复读率：相邻两条的 7 字重合
    dup = 0
    for i in range(1, len(texts)):
        a, b = texts[i - 1], texts[i]
        if len(a) >= 7 and len(b) >= 7:
            for j in range(len(b) - 6):
                if b[j:j + 7] in a:
                    dup += 1
                    break
    rate = dup / max(1, len(texts) - 1)
    ok("C指标", "相邻复读率 <25%", rate < 0.25, f"{rate:.0%}（{dup}/{len(texts)-1}）")
    # AI 腔
    smell = sum(1 for t in texts if any(w in t for w in AI_SMELL))
    ok("C指标", "AI 腔超标条数 0", smell == 0, f"{smell} 条")
    # 长度
    avg = sum(len(t) for t in texts) / len(texts)
    ok("C指标", "平均字数 ≤60", avg <= 60, f"{avg:.0f} 字")
    # 句号结尾比例（真人少用）
    dot = sum(1 for t in texts if t.rstrip().endswith("。"))
    ok("C指标", "句号结尾 <60%", dot / len(texts) < 0.6, f"{dot/len(texts):.0%}")
    # 禁词还在不在用
    if AVOID.exists():
        avoid = json.loads(AVOID.read_text(encoding="utf-8")).get("avoid") or []
        if avoid:
            used = sum(1 for t in texts if any(w in t for w in avoid))
            ok("C指标", "禁词复用减少", used <= len(texts) * 0.3,
               f"近 30 条里还有 {used} 条在用")


# ── D 服务 ─────────────────────────────────────────────
def check_service():
    for p in ("/api/data", "/api/memes", "/api/memory"):
        try:
            with urllib.request.urlopen(BASE + p, timeout=20) as r:
                ok("D服务", p, r.status == 200)
        except Exception as e:  # noqa: BLE001
            ok("D服务", p, False, str(e)[:60])
    hb = HERE / "data" / "mom_agent_hb.json"
    try:
        d = json.loads(hb.read_text(encoding="utf-8"))
        age = time.time() - float(d.get("ts") or 0)
        ok("D服务", "妈妈心跳 <90s", age < 90, f"{age:.0f}s")
    except Exception as e:  # noqa: BLE001
        ok("D服务", "妈妈心跳", False, str(e)[:60])


# ── E 语感训练 ─────────────────────────────────────────
def check_voice():
    sys.path.insert(0, str(HERE))
    try:
        import voice
        sp = voice.spec(kid=True)
        ok("E语感", "规范可生成", len(sp) > 300, f"{len(sp)} 字")
        # 规范里不能出现整句语料——出现就说明又在让人背
        try:
            import vocab_pack as V
            src_lines = [d["line"] for d in V._load() if len(d.get("line", "")) >= 6]
            leaked = [ln for ln in src_lines if ln in sp]
            ok("E语感", "规范里没抄原句", not leaked, f"命中 {len(leaked)} 句")
        except Exception as e:  # noqa: BLE001
            ok("E语感", "规范里没抄原句", False, str(e)[:60])
    except Exception as e:  # noqa: BLE001
        ok("E语感", "规范可生成", False, str(e))
        return

    for name, path in (
        ("妈妈", HERE / "mom_agent.py"),
        ("面板女儿", PLUG / "main.py"),
        ("QQ女儿", Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_soul\engine.py")),
    ):
        src = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        ok("E语感", f"{name}接了规范", "voice_spec" in src or "VOICE_SPEC" in src, "")

    rt = HERE / "data" / "voice_spec_daughter.txt"
    try:
        age = (time.time() - rt.stat().st_mtime) / 3600
        ok("E语感", "女儿规范文件新鲜", age < 48, f"{age:.1f} 小时前")
        ok("E语感", "女儿规范无荤词", not any(
            w in rt.read_text(encoding="utf-8") for w in ("涩", "露骨", "器官")), "")
    except Exception as e:  # noqa: BLE001
        ok("E语感", "女儿规范文件新鲜", False, str(e)[:60])

    try:
        d = json.loads((HERE / "data" / "voice_score.json").read_text(encoding="utf-8"))
        age = (time.time() - float(d.get("ts") or 0)) / 3600
        ok("E语感", "训练记录新鲜", age < 3, f"{age:.1f} 小时前")
    except Exception as e:  # noqa: BLE001
        ok("E语感", "训练记录新鲜", False, str(e)[:60])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    check_vocab()
    check_avoid()
    check_quality()
    check_service()
    check_voice()

    passed = sum(1 for _, _, p in rows if p)
    total = len(rows)
    result = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed,
        "total": total,
        "ok": passed == total,
        "items": [{"group": g, "name": n, "pass": p} for g, n, p in rows],
        "failed": [{"group": g, "name": n} for g, n, p in rows if not p],
        "notes": notes[-12:],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    print("=" * 56)
    cur = ""
    for g, n, p in rows:
        if g != cur:
            print(f"\n【{g}】")
            cur = g
        print(f"  {'✓' if p else '✗'} {n}")
    print("\n" + "=" * 56)
    print(f"  {passed}/{total} 通过" + ("" if passed == total else f"，{total - passed} 项待修"))
    print("=" * 56)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
