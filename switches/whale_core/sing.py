# -*- coding: utf-8 -*-
"""唱歌：用她自己的 RVC 音色，把一段人声换成她唱。

这不是「文字→作曲」，是 2026-11-07 已在本机跑通的链路：

    分离好的原曲人声 → 截取片段 → RVC 变声(feiyu3) → 去电音 → 与伴奏混合 → mp3

真实边界（别吹）：
- 需要已用 htdemucs 分离好的歌（当前只有《雨爱》），不是随便点一首就能唱；
- 音色是「她现在的 TTS 音色」，不是真人歌声；想更像真人得重训 RVC；
- CPU 推理，8 秒音频约 19 秒，24 秒约 35 秒。

设计原则：全部走子进程 + 超时，绝不把 30 多秒的推理放主线程。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

ASTRBOT_DATA = Path(os.environ.get("ASTRBOT_ROOT") or r"%ASTRBOT_HOME%") / "data"
OUT_DIR = ASTRBOT_DATA / "plugin_data" / "astrbot_plugin_whale_sing"

RVC_ROOT = Path(r"G:\ai\RVC")
RVC_PY = RVC_ROOT / ".venv" / "Scripts" / "python.exe"
RVC_INDEX = RVC_ROOT / "logs" / "feiyu3" / "added_IVF1495_Flat_nprobe_1_feiyu3_v2.index"
RVC_MODEL = "feiyu3.pth"
FFMPEG = Path(r"G:\ai\GPT-SoVITS\ffmpeg.exe")
STEMS = Path(r"%PROJECT_HOME%\audition\music\_hd\htdemucs")

DEFAULT_SONG = "yuai_orig"
DEFAULT_START = 148.0
DEFAULT_DURATION = 24.0
MAX_DURATION = 40.0

SONG_LABELS = {
    "yuai_orig": "雨爱（副歌）",
}


def assets_ok() -> tuple[bool, str]:
    """资产自检：缺什么就直说。"""
    missing = []
    if not RVC_PY.exists():
        missing.append(f"RVC 解释器 {RVC_PY}")
    if not (RVC_ROOT / "assets" / "weights" / RVC_MODEL).exists():
        missing.append(f"模型 {RVC_MODEL}")
    if not RVC_INDEX.exists():
        missing.append("RVC index")
    if not FFMPEG.exists():
        missing.append(f"ffmpeg {FFMPEG}")
    if not STEMS.exists():
        missing.append(f"分离目录 {STEMS}")
    return (not missing, "OK" if not missing else "缺：" + "、".join(missing))


def list_songs() -> list[dict[str, Any]]:
    songs = []
    if not STEMS.exists():
        return songs
    for path in sorted(STEMS.iterdir()):
        if not path.is_dir():
            continue
        if (path / "vocals.wav").exists() and (path / "no_vocals.wav").exists():
            songs.append({
                "id": path.name,
                "label": SONG_LABELS.get(path.name, path.name),
                "duration": _wav_duration(path / "vocals.wav"),
            })
    return songs


def _wav_duration(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path), "rb") as fh:
            frames = fh.getnframes()
            rate = fh.getframerate() or 1
            return round(frames / rate, 1)
    except Exception:  # noqa: BLE001
        return 0.0


_MIX_TEMPLATE = r'''# -*- coding: utf-8 -*-
import sys
import numpy as np
import soundfile as sf
from scipy import signal

src, dst, hi_keep = sys.argv[1], sys.argv[2], float(sys.argv[3])
y, sr = sf.read(src, dtype="float32")
if y.ndim > 1:
    y = y.mean(axis=1)
lo = signal.butter(2, 4000, btype="low", fs=sr, output="sos")
hi = signal.butter(2, 4000, btype="high", fs=sr, output="sos")
y = signal.sosfilt(lo, y).astype(np.float32) + signal.sosfilt(hi, y).astype(np.float32) * hi_keep
y = np.clip(y / (np.max(np.abs(y)) + 1e-9) * 0.95, -1, 1)
sf.write(dst, y, sr)
print("CLEAN:", dst)
'''


def _run(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "-1"          # 不抢 TTS 的显存
    env["PYTHONPATH"] = str(RVC_ROOT)
    env["PATH"] = r"G:\ai\GPT-SoVITS;" + env.get("PATH", "")
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, env=env,
            capture_output=True, timeout=timeout,
            text=True, encoding="utf-8", errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"超时（{timeout}s）"


def sing(
    song: str = DEFAULT_SONG,
    *,
    start: float = DEFAULT_START,
    duration: float = DEFAULT_DURATION,
    pitch: int = -3,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """跑一遍完整链路，返回 {ok, path, detail, elapsed}。"""
    ok, why = assets_ok()
    if not ok:
        return {"ok": False, "path": "", "detail": why}

    stem_dir = STEMS / song
    if not (stem_dir / "vocals.wav").exists():
        return {"ok": False, "path": "", "detail": f"没有分离好的歌：{song}"}

    duration = max(4.0, min(MAX_DURATION, float(duration)))
    work = OUT_DIR / "work" / time.strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()

    # 1) 切片段
    code, log = _run([str(FFMPEG), "-y", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
                      "-i", str(stem_dir / "vocals.wav"), "-ar", "44100", "-ac", "1",
                      "-c:a", "pcm_s16le", str(work / "src.wav")], timeout=60)
    if code != 0:
        return {"ok": False, "path": "", "detail": f"切音频失败：{log[-300:]}"}

    code, log = _run([str(FFMPEG), "-y", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
                      "-i", str(stem_dir / "no_vocals.wav"), "-ar", "44100", "-ac", "1",
                      "-c:a", "pcm_s16le", str(work / "accomp.wav")], timeout=60)
    if code != 0:
        return {"ok": False, "path": "", "detail": f"切伴奏失败：{log[-300:]}"}

    # 2) RVC 变声（CPU，慢）
    code, log = _run(
        [str(RVC_PY), "-m", "infer.cli", "--model", RVC_MODEL,
         "--input", str(work / "src.wav"), "--output", str(work / "rvc.wav"),
         "--pitch", str(pitch), "--f0-method", "rmvpe", "--index", str(RVC_INDEX)],
        timeout=timeout, cwd=RVC_ROOT,
    )
    if code != 0 or not (work / "rvc.wav").exists():
        return {"ok": False, "path": "", "detail": f"RVC 失败：{log[-400:]}"}

    # 3) 去电音
    mix_py = work / "_mix.py"
    mix_py.write_text(_MIX_TEMPLATE, encoding="utf-8")
    code, log = _run([str(RVC_PY), str(mix_py), str(work / "rvc.wav"), str(work / "clean.wav"), "0.62"],
                     timeout=120)
    if code != 0:
        return {"ok": False, "path": "", "detail": f"去电音失败：{log[-300:]}"}

    # 4) 混音 + 转 mp3
    mp3 = OUT_DIR / f"sing-{time.strftime('%Y%m%d-%H%M%S')}.mp3"
    code, log = _run(
        [str(FFMPEG), "-y", "-loglevel", "error",
         "-i", str(work / "clean.wav"), "-i", str(work / "accomp.wav"),
         "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:weights=1.0 0.75[out]",
         "-map", "[out]", "-ar", "44100", "-ac", "1", "-b:a", "192k", str(mp3)],
        timeout=120,
    )
    if code != 0 or not mp3.exists():
        return {"ok": False, "path": "", "detail": f"混音失败：{log[-300:]}"}

    elapsed = round(time.time() - started, 1)
    try:
        shutil.rmtree(work, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "path": str(mp3), "duration": duration, "elapsed": elapsed, "detail": "OK"}


def cleanup(days: int = 7) -> int:
    """清理旧的唱歌产物，避免无限增长。"""
    removed = 0
    cutoff = time.time() - days * 86400
    if not OUT_DIR.exists():
        return 0
    for path in OUT_DIR.glob("sing-*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed
