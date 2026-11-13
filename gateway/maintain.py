# -*- coding: utf-8 -*-
"""长线维护：日志滚动 + 临时文件清理（让长线能跑一个月不爆）"""
import os, time, glob
from pathlib import Path

AB = Path(r"%ASTRBOT_HOME%")
AUD = Path(r"%PROJECT_HOME%\audition")
GW = AUD / "gateway"
LOG = GW / "maintain.log"


def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), m))
    except Exception:
        pass


def trim(path, max_mb=5.0, keep_ratio=0.2):
    """文件超过 max_mb：保留尾部 keep_ratio 内容，前面丢掉"""
    try:
        p = Path(path)
        if not p.exists():
            return 0
        size = p.stat().st_size
        if size < max_mb * 1024 * 1024:
            return 0
        keep = int(size * keep_ratio)
        with open(p, "rb") as f:
            f.seek(size - keep)
            tail = f.read()
        with open(p, "wb") as f:
            f.write(b"...[trimmed %d bytes]...\n" % (size - keep))
            f.write(tail)
        return size - keep
    except Exception as e:
        log("trim err %s: %s" % (path, e))
        return 0


def clean_astrbot_logs(keep=2):
    """只留最新 N 个重启日志（restart*/final*），旧的删掉"""
    try:
        files = sorted(AB.glob("runtime-*.out.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        removed = 0
        for f in files[keep:]:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
        files2 = sorted(AB.glob("runtime-*.err.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files2[keep:]:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
        return removed
    except Exception as e:
        log("clean_astrbot err: %s" % e)
        return 0


def trim_big_logs():
    total = 0
    # [2026-11-04 冲突修复] 不 trim AstrBot 的 runtime.log：它是 cmd ">>" 正在写的活跃文件，
    # 原地截断会让 Windows 句柄偏移留在旧位置，后续追加留下 NUL 空洞（日志变垃圾）。
    # 改为由 bot-supervisor.ps1 在 AstrBot 启动前轮转。
    targets = [AUD / "events.jsonl", GW / "gateway.log",
               GW / "watchdog.log", GW / "longline_status.jsonl",
               Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_voice\decisions.jsonl"),
               Path(r"G:\ai\feiyu_voice\tts_requests.jsonl")]
    for t in targets:
        total += trim(t, max_mb=20.0 if "runtime.log" in str(t) else 8.0)
    return total


def clean_segments(keep=200):
    """切出来的语音段只留最近 N 个"""
    try:
        files = sorted(AUD.glob("out/seg_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        n = 0
        for f in files[keep:]:
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


def clean_tmp(days=3):
    try:
        cut = time.time() - days * 86400
        n = 0
        for f in AUD.glob("tmp/*"):
            try:
                if f.is_file() and f.stat().st_mtime < cut:
                    f.unlink()
                    n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


if __name__ == "__main__":
    freed = 0
    r1 = clean_astrbot_logs(keep=2)
    freed += trim_big_logs()
    r2 = clean_segments(keep=200)
    r3 = clean_tmp(days=3)
    log("maintain: astrbot_logs=%d segs=%d tmp=%d trimmed_bytes=%d" % (r1, r2, r3, freed))
    print("清理完成: AstrBot旧日志 %d 个 / 语音段 %d 个 / tmp %d 个 / 截断 %d KB" % (
        r1, r2, r3, round(freed / 1024, 1)))