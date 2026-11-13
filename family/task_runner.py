# -*- coding: utf-8 -*-
"""任务看护器：盯着一家三口的 tasks.json，把分给 DSH 的任务真的跑掉。

- 只接 assignee 是 dsh / DSH / agent 的任务
- 一次一个，跑完把状态写回（doing → done），结果插进 extra.jsonl，看板里能看见
- 失败最多重试 2 次，之后留在待办里不再自动跑，避免死循环
- 心跳写 data/task_runner_hb.json，方便体检

启动：
    pythonw task_runner.py
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
TASKS = DATA / "tasks.json"
EXTRA = DATA / "extra.jsonl"
LOG = DATA / "task_runner.log"
HB = DATA / "task_runner_hb.json"
LOCK = DATA / "task_runner.lock"
BRIDGE = HERE / "agent_bridge.py"
CANCEL = DATA / "cancel.flag"

TARGETS = {"dsh", "DSH", "agent", "deepseek"}
POLL = 3.0
TIMEOUT = 950
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def hb() -> None:
    try:
        HB.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}),
                      encoding="utf-8")
    except Exception:
        pass


def load_tasks() -> list[dict]:
    try:
        d = json.loads(TASKS.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_tasks(tasks: list[dict]) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        TASKS.write_text(json.dumps(tasks[-200:], ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log(f"写 tasks.json 失败：{exc}")


def extra(text: str) -> None:
    try:
        row = {"id": f"dsh-{int(time.time() * 1000)}", "ts": time.time(),
               "role": "dsh", "who": "DSH", "channel": "看板",
               "kind": "text", "text": text[:1500]}
        with EXTRA.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        log(f"写 extra 失败：{exc}")


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=20,
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    # 被强杀时 bridge 的 finally 来不及跑，锁要手动收掉
    try:
        (Path(r"%USERPROFILE%\.dsh\bridge\agent.lock")).unlink(missing_ok=True)
    except Exception:
        pass


def run_one(task: dict) -> None:
    tid = str(task.get("id") or "")
    title = str(task.get("title") or "")
    detail = str(task.get("detail") or "")
    text = title + ("\n\n" + detail if detail else "")
    log(f"开工 {tid}: {title[:40]}")
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    res: dict = {}
    cancelled = False
    timed_out = False
    try:
        proc = subprocess.Popen(
            [sys.executable, str(BRIDGE), "--text", text, "--json"],
            cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=CREATE_NO_WINDOW)
        while True:
            if CANCEL.exists():
                try:
                    CANCEL.unlink()
                except Exception:
                    pass
                cancelled = True
                _kill_tree(proc.pid)
                break
            if proc.poll() is not None:
                break
            if time.time() - t0 > TIMEOUT:
                timed_out = True
                _kill_tree(proc.pid)
                break
            time.sleep(2)
        try:
            out, _ = proc.communicate(timeout=20)
        except Exception:
            out = ""
        for ln in reversed((out or "").strip().splitlines()):
            try:
                res = json.loads(ln)
                break
            except Exception:
                continue
        if not res:
            res = {"ok": False, "error": (out or "")[-300:]}
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": str(exc)[:300]}

    ok = bool(res.get("ok")) and not cancelled and not timed_out
    elapsed = round(time.time() - t0, 1)
    result = str(res.get("result") or res.get("error") or "")[:1200]

    tasks = load_tasks()
    for t in tasks:
        if str(t.get("id")) != tid:
            continue
        t["elapsed"] = elapsed
        if cancelled:
            t["status"] = "cancelled"
            t["result"] = "主人叫停了"
        elif timed_out:
            t["attempts"] = int(t.get("attempts") or 0) + 1
            t["result"] = f"超时 {TIMEOUT}s"
            t["status"] = "todo" if t["attempts"] < 2 else "blocked"
        else:
            t["attempts"] = int(t.get("attempts") or 0) + 1
            if ok:
                t["status"] = "done"
                t["result"] = result
            else:
                t["result"] = result
                t["status"] = "todo" if t["attempts"] < 2 else "blocked"
        break
    save_tasks(tasks)

    if cancelled:
        extra(f"任务「{title}」被叫停了（跑了 {elapsed}s）。")
        log(f"叫停 {tid} {elapsed}s")
    elif timed_out:
        extra(f"任务「{title}」超时了（{TIMEOUT}s），先停下。")
        log(f"超时 {tid} {elapsed}s")
    elif ok:
        extra(f"任务「{title}」办完了（{elapsed}s）：\n{result}")
        log(f"完成 {tid} {elapsed}s")
    else:
        extra(f"任务「{title}」没办成（第 {int(task.get('attempts') or 0) + 1} 次）：\n{result}")
        log(f"失败 {tid} {elapsed}s")


def main() -> int:
    # 单实例：锁文件 30 秒内新鲜就认为已有看护器
    try:
        if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < 30:
            log("已有看护器在跑，退出")
            return 0
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass

    log(f"任务看护器启动 pid={os.getpid()}")
    try:
        (Path(r"%USERPROFILE%\.dsh\bridge\agent.lock")).unlink(missing_ok=True)
    except Exception:
        pass
    try:
        if CANCEL.exists():
            CANCEL.unlink()      # 上次留下的叫停标记，别误伤新任务
    except Exception:
        pass
    # 上次崩溃留下的 doing：放回待办
    tasks = load_tasks()
    changed = False
    for t in tasks:
        if t.get("status") == "doing":
            t["status"] = "todo"
            changed = True
    if changed:
        save_tasks(tasks)

    while True:
        try:
            hb()
            LOCK.write_text(str(os.getpid()), encoding="utf-8")
            tasks = load_tasks()
            pick = None
            for t in tasks:
                if t.get("status") == "todo" and str(t.get("assignee")) in TARGETS \
                        and int(t.get("attempts") or 0) < 2:
                    pick = t
                    break
            if pick:
                for t in tasks:
                    if t is pick:
                        t["status"] = "doing"
                save_tasks(tasks)
                run_one(pick)
        except Exception as exc:  # noqa: BLE001
            log(f"主循环出错（继续）：{exc}")
        time.sleep(POLL)


if __name__ == "__main__":
    raise SystemExit(main())
