"""每日健康巡检：状态/日志大小/语音服务/异常，自动滚动过大日志。

用法：python daily_health.py
输出：20_每日健康-YYYYMMDD.md（追加）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.request
import sys
try:
    # 计划任务无控制台，Windows 默认 GBK：显式改 utf-8，防 emoji 抛错
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PID = r"%ASTRBOT_HOME%\data\plugin_data"
BEAMS = os.path.join(PID, "astrbot_plugin_whale_beams")
OUT = os.path.join(r"%PROJECT_HOME%\框架重构", "20_每日健康-%s.md" % time.strftime("%Y%m%d"))
LOG = r"%ASTRBOT_HOME%\runtime.log"

#: 日志超过这个大小就归档（保留最近一份 .old）
ROLL_MB = 10.0


def _rj(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _health(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def _count(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _l2_status() -> str:
    """记忆 L2 依赖（embedding）是否可用 —— 汇总 02 §V #10。"""
    iris = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_iris_chat_memory"
    meta_p = os.path.join(iris, "faiss", "memory_default", "index_meta.json")
    idx_p = os.path.join(iris, "faiss", "memory_default", "index.faiss")
    meta = _rj(meta_p)
    model = meta.get("embedding_model") or "?"
    dims = meta.get("embedding_dimensions") or "?"
    if not os.path.isfile(idx_p):
        return "❌ 向量索引缺失（%s）" % idx_p
    size_kb = os.path.getsize(idx_p) / 1024.0
    entries, fail = None, ""
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-8000:]
        for ln in reversed(tail):
            m = re.search(r"当前条目数: (\d+)", ln)
            if m and entries is None:
                entries = int(m.group(1))
            if "嵌入模型加载失败" in ln or "L2 记忆库将不可用" in ln:
                fail = ln.strip()[-120:]
                break
            if entries is not None:
                break
    except OSError:
        pass
    if fail:
        return "⚠️ 降级：%s（模型 %s）" % (fail, model)
    return "✅ 可用（模型 %s / 维度 %s / 条目 %s / 索引 %.0fKB）" % (
        model, dims, entries if entries is not None else "?", size_kb)


def _plugin_failures() -> list:
    """今日插件加载/重载失败（失败可见，汇总 02 §V #6）。"""
    pats = ("Failed to load plugin", "did not terminate cleanly",
            "Unhandled exception in dashboard API", "Error in ASGI Framework")
    hits = []
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-8000:]
        for ln in tail:
            if any(p in ln for p in pats):
                hits.append(ln.strip()[:160])
    except OSError:
        pass
    return hits[-5:]


def main():
    lines = ["# 每日健康巡检 · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    st = _rj(os.path.join(BEAMS, "beams_state.json"))
    snap = _rj(os.path.join(BEAMS, "beams_snapshot.json"))
    now = time.time()
    age = now - float(st.get("ts") or 0)

    lines.append("## 内核")
    lines.append("- tick：%s（最后心跳 %.1fs 前）%s" % (
        st.get("tick_count"), age, "✅" if age < 30 else "⚠️ 失联"))
    lines.append("- 情绪：%s（%+.2f / 唤醒 %.2f）· 动机：%s" % (
        (st.get("fast") or {}).get("label"),
        float((st.get("fast") or {}).get("valence") or 0),
        float((st.get("fast") or {}).get("arousal") or 0),
        (st.get("arbitration") or {}).get("winner")))
    lines.append("- 闸门：open=%s route=%s（%s）" % (
        (st.get("gate") or {}).get("open"), (st.get("gate") or {}).get("route"),
        (st.get("gate") or {}).get("reason")))
    lines.append("")

    lines.append("## 语音四模型")
    ok_all = True
    for name, port in (("ASR", 8913), ("laya", 8901), ("IndexTTS", 8912)):
        h = _health(port)
        ok = bool(h.get("ok"))
        ok_all = ok_all and ok
        extra = ""
        if port == 8912 and ok:
            extra = "（缓存 %s / 显存余 %sMB）" % (h.get("cache_entries"), h.get("free_mb"))
        lines.append("- %s：%s%s" % (name, "✅" if ok else "❌", extra))
    lines.append("")

    lines.append("## 记忆 L2 依赖（embedding）")
    lines.append("- " + _l2_status())
    lines.append("")

    fails = _plugin_failures()
    lines.append("## 插件失败可见")
    if fails:
        for _f in fails:
            lines.append("- ⚠️ %s" % _f)
    else:
        lines.append("- ✅ 今日无插件加载/重载失败")
    lines.append("")

    lines.append("## 文件与日志")
    total = 0
    rolled = []
    for name in sorted(os.listdir(BEAMS)):
        p = os.path.join(BEAMS, name)
        if not os.path.isfile(p):
            continue
        size_mb = os.path.getsize(p) / 1024.0 / 1024.0
        total += size_mb
        if name.endswith(".jsonl") and size_mb > ROLL_MB:
            old = p + ".old"
            try:
                shutil.move(p, old)
                rolled.append(name)
            except OSError:
                pass
    lines.append("- 目录总大小：%.1f MB；流水条数：memory %d / obs %d / compare %d" % (
        total, _count(os.path.join(BEAMS, "beams_state_memory.jsonl")),
        _count(os.path.join(BEAMS, "beams_obs.jsonl")),
        _count(os.path.join(BEAMS, "beams_compare.jsonl"))))
    if rolled:
        lines.append("- ⚠️ 已滚动归档：%s" % "、".join(rolled))
    lines.append("")

    # 新旧情绪对比（接管必要性证据）
    try:
        import json as _j
        _new = _j.loads(open(os.path.join(BEAMS, "beams_snapshot.json"), encoding="utf-8").read())
        _oldp = os.path.join(PID, "astrbot_plugin_whale_affect", "affect_state.json")
        _old = _j.loads(open(_oldp, encoding="utf-8").read()) if os.path.isfile(_oldp) else {}
        _nv = float(_new.get("valence") or 0)
        _ov = float((_old.get("core") or {}).get("valence") or 0)
        lines.append("## 新旧情绪对比")
        lines.append("- 新内核：%s（%+.3f）· 旧 affect：%s（%+.3f）· 差值 %.3f" % (
            _new.get("label"), _nv, _old.get("label"), _ov, abs(_nv - _ov)))
        lines.append("")
    except Exception:
        pass

    lines.append("## 结论")
    issues = []
    if age >= 30:
        issues.append("内核心跳超时")
    if not ok_all:
        issues.append("有语音服务掉线")
    if fails:
        issues.append("有 %d 条插件/框架失败日志（详见上节）" % len(fails))
    lines.append("- %s" % ("**全部正常**" if not issues else "；".join(issues)))
    text = "\n".join(lines)
    print(text)
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(text + "\n\n")
    print("\n已写入：" + OUT)


if __name__ == "__main__":
    main()