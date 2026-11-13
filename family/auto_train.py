# -*- coding: utf-8 -*-
"""自动训练一轮：学（找重复词）+ 验收（自检）+ 记日志。

计划任务每小时跑这个。她自己学、自己判、自己记。
不往对话里写任何东西。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LOG = DATA / "train_log.jsonl"

PY = sys.executable


def run(script: str, *args: str) -> tuple[int, str]:
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"   # 计划任务里没控制台，中文别按 GBK 走
        env["PYTHONUTF8"] = "1"
        r = subprocess.run([PY, str(HERE / script), *args],
                           cwd=str(HERE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900, env=env,
                           creationflags=(0x08000000 if sys.platform == "win32" else 0))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def main() -> int:
    t0 = time.time()
    entry = {"ts": t0, "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    # 0) 语料画像：词包换了才重算（每周检查一次）
    prof = DATA / "voice_profile.json"
    if (not prof.exists()) or (t0 - prof.stat().st_mtime > 7 * 86400):
        code, out = run("voice_study.py")
        entry["study_ok"] = code == 0
        entry["study_tail"] = out.strip().splitlines()[:2]
    else:
        entry["study_ok"] = True

    # 1) 语感 + 情绪训练：现场出题、对表打分、写这轮要改的毛病
    code, out = run("voice_train.py")
    entry["voice_ok"] = code == 0
    entry["voice_tail"] = out.strip().splitlines()[-4:]
    try:
        vs = json.loads((DATA / "voice_score.json").read_text(encoding="utf-8"))
        entry["voice_pass"] = {k: (v.get("probe") or {}).get("pass")
                               for k, v in (vs.get("who") or {}).items()}
        entry["voice_focus"] = vs.get("focus") or {}
    except Exception:
        entry["voice_pass"] = {}

    # 2) 学：最近 24 小时里有没有用滥的词（分人）
    code, out = run("learn_watch.py", "--hours", "24")
    entry["learn_ok"] = code == 0
    entry["learn_tail"] = out.strip().splitlines()[-4:]
    try:
        av = json.loads((DATA / "avoid_words.json").read_text(encoding="utf-8"))
        entry["avoid"] = av.get("avoid_mom") or av.get("avoid") or []
        entry["avoid_kid"] = av.get("avoid_kid") or []
        entry["samples"] = av.get("n")
    except Exception:
        entry["avoid"] = []

    # 3) 验收：18 项自检
    code, out = run("self_check.py", "--json")
    try:
        chk = json.loads(out.strip().splitlines()[-1])
    except Exception:
        chk = {"passed": 0, "total": 0, "ok": False, "failed": [], "parse_error": out[-300:]}
    entry["check_ok"] = bool(chk.get("ok"))
    entry["passed"] = chk.get("passed")
    entry["total"] = chk.get("total")
    entry["failed"] = [f.get("name") for f in (chk.get("failed") or [])]

    # 4) 如果验收没过，试着自愈一次（只拉妈妈，不碰 QQ 机器人）
    if not entry["check_ok"] and entry["failed"]:
        names = " ".join(entry["failed"])
        if "心跳" in names or "妈妈" in names:
            entry["heal"] = "妈妈心跳缺失，留给看板自愈（不杀进程）"

    entry["cost_s"] = round(time.time() - t0, 1)
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 只留最近 500 条
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > 500:
            LOG.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
    except Exception:
        pass

    print(f"[{entry['time']}] 练：{'OK' if entry['voice_ok'] else 'FAIL'} "
          f"{entry.get('voice_pass')} "
          f"学：{'OK' if entry['learn_ok'] else 'FAIL'} "
          f"验：{entry.get('passed')}/{entry.get('total')} "
          f"{'全过' if entry['check_ok'] else '待修:' + str(entry['failed'])} "
          f"禁词 {len(entry.get('avoid') or [])}+{len(entry.get('avoid_kid') or [])} "
          f"{entry['cost_s']}s")
    return 0 if entry["check_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
