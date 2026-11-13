# -*- coding: utf-8 -*-
"""意志链路 10 分钟验证：只读采样。

看四件事：
1. 欲望水位 V 有没有在涨、离门槛 θ 还有多远
2. 一旦 can_talk=true，意志有没有按权限门做出决定（would_speak/preview/sent）
3. 生成出来的句子长什么样（preview 只写日志不发）
4. 有没有报错
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(r"%ASTRBOT_HOME%\runtime.log")
URGE = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_state\urge.json")
WILL_LOG = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_will\will_log.jsonl")
OUT = ROOT / "docs" / "will_soak.jsonl"


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--interval", type=float, default=15)
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    end = start + args.minutes * 60
    seen_will = 0
    print(f"[will] 开始 {datetime.now():%H:%M:%S}，{args.minutes} 分钟，每 {args.interval}s")

    while time.time() < end:
        time.sleep(args.interval)
        u = read_json(URGE)
        will_lines: list[dict] = []
        try:
            raw = WILL_LOG.read_text(encoding="utf-8").splitlines() if WILL_LOG.exists() else []
            for line in raw[seen_will:]:
                try:
                    will_lines.append(json.loads(line))
                except Exception:
                    pass
            seen_will = len(raw)
        except Exception:
            pass

        sample = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "v": u.get("v"),
            "theta": u.get("theta"),
            "tier": u.get("tier"),
            "can_talk": u.get("can_talk"),
            "block": u.get("block"),
            "reasons": u.get("reasons"),
            "will": will_lines,
        }
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
        print(f"[{sample['ts']}] V={u.get('v')} θ={u.get('theta')} {u.get('tier')} can={u.get('can_talk')} "
              f"block={u.get('block')} will+{len(will_lines)}")
        for w in will_lines:
            print("   →", w.get("result"), "|", (w.get("text") or "")[:40], "| gate:", w.get("gate"))

    print("\n===== 结论 =====")
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines()[-60:]]
    vs = [r["v"] for r in rows if isinstance(r.get("v"), (int, float))]
    fired = [w for r in rows for w in r.get("will") or []]
    print(f"采样 {len(rows)} 次 · V {vs[0]:.3f} → {vs[-1]:.3f}" if vs else "无采样")
    print(f"意志决策 {len(fired)} 条")
    for w in fired[:6]:
        print("  -", w.get("result"), "|", (w.get("text") or "")[:50])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
