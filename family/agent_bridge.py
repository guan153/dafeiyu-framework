# -*- coding: utf-8 -*-
"""DSH 单任务执行器：把一段任务交给 dsh headless 去干，拿回结果。

只依赖标准库。QQ / 看板 / 定时任务都能调它。

用法：
    python agent_bridge.py --text "把今天的消息数统计一下"
    python agent_bridge.py --text "按这个方案改" --context-file ctx.md
    python agent_bridge.py --text "..." --json      # 只输出一行 JSON

输出 JSON：
    {"ok": true, "result": "...", "elapsed": 42.1, "task_file": "...", "code": 0}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

# 被 pythonw / 计划任务拉起时没有控制台，默认走 GBK，中文和 emoji 会炸
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DSH_HOME = Path(r"%USERPROFILE%\.dsh")
DSH_EXE = Path(r"%USERPROFILE%\AppData\Local\Programs\DeepSeek Harness\DeepSeek Harness.exe")
DSH_CLI = Path(r"%USERPROFILE%\AppData\Local\Programs\DeepSeek Harness\resources\app.asar"
               r"\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js")
CRED = DSH_HOME / ".credentials.yaml"
BRIDGE = DSH_HOME / "bridge"
LOCK = BRIDGE / "agent.lock"
DEFAULT_WORK = Path(r"%PROJECT_HOME%")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

HEADER = """# 来自主人的任务

你在 %PROJECT_HOME% 工作。先看清楚再动手；破坏性操作（删除/覆盖/改路径/发布）先说明再等确认。
完成后用三句话汇报：做了什么、结果、还需要什么。

## 任务
"""


def _acquire_lock(wait_s: int = 600, stale_s: int = 1800) -> bool:
    """同一个时刻只允许一个 headless agent 跑，避免两边改同一个文件。"""
    BRIDGE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime > stale_s:
                    LOCK.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            if time.time() - t0 > wait_s:
                return False
            time.sleep(2)
        except Exception:
            return True   # 锁坏了也别把任务卡死


def _release_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except Exception:
        pass


def api_key() -> str:
    try:
        m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", CRED.read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except Exception:
        return ""


def run(text: str, context: str = "", timeout: int = 900,
        workdir: Path = DEFAULT_WORK) -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty task", "result": "", "elapsed": 0.0}
    if not _acquire_lock():
        return {"ok": False, "error": "另一个 agent 还在跑，等超时了",
                "result": "", "elapsed": 0.0}
    try:
        BRIDGE.mkdir(parents=True, exist_ok=True)
        tid = uuid.uuid4().hex[:10]
        task_file = BRIDGE / f"task_{tid}.md"
        body = HEADER + text + "\n"
        if context.strip():
            body += "\n## 上下文（刚才屋里/QQ里说的话）\n" + context.strip()[:4000] + "\n"
        task_file.write_text(body, encoding="utf-8")

        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["DSH_HOME"] = str(DSH_HOME)
        key = api_key()
        if key:
            env["DEEPSEEK_API_KEY"] = key

        prompt = f"Read the task file {task_file} and do it. Reply in Chinese, 3 sentences max."
        cmd = [str(DSH_EXE), "--expose-internals", str(DSH_CLI), "headless", prompt]

        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(workdir), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout {timeout}s", "result": "",
                    "elapsed": round(time.time() - t0, 1), "task_file": str(task_file)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:300], "result": "",
                    "elapsed": round(time.time() - t0, 1), "task_file": str(task_file)}

        lines = [x for x in out.splitlines()
                 if x.strip() and not x.startswith("[llm-deepseek")]
        # CLI 会把思考过程打在答案后面，截掉，别把 reasoning 当结果发出去
        for i, x in enumerate(lines):
            if x.startswith("dsh: reasoning:"):
                lines = lines[:i]
                break
        result = "\n".join(lines[-15:])[:2000]
        return {
            "ok": code == 0,
            "code": code,
            "result": result,
            "elapsed": round(time.time() - t0, 1),
            "task_file": str(task_file),
        }
    finally:
        _release_lock()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--context-file", default="")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--workdir", default=str(DEFAULT_WORK))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ctx = ""
    if args.context_file:
        try:
            ctx = Path(args.context_file).read_text(encoding="utf-8", errors="replace")
        except Exception:
            ctx = ""

    res = run(args.text, ctx, args.timeout, Path(args.workdir))
    print(json.dumps(res, ensure_ascii=False))
    if not args.json:
        print("\n--- 结果 ---")
        print(res.get("result") or res.get("error") or "")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
