# -*- coding: utf-8 -*-
"""她的记忆备份。

为什么是这几样：她身上真正**不可再生**的东西只有这些——
记忆库、灵魂状态、欲望/关系状态、时间线、对话历史。
图片、TTS 缓存、浏览器配置都能重来，不占备份空间。

跑法：
    python tools/backup_state.py              # 备份一份
    python tools/backup_state.py --list       # 看已有备份
    python tools/backup_state.py --restore 2026-11-09_2237   # 解到临时目录（不覆盖线上）

产物：%PROJECT_HOME%\\backups\\state-<时间>.zip，保留最近 14 份。
每份里带 manifest.json（文件数、总字节、关键库的 sha256 前 12 位）。
"""

from __future__ import annotations

import sys
import pathlib as _pl
_ROOT = _pl.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from whale_core.paths import DATA  # noqa: E402
PD = DATA / "plugin_data"
from whale_core.paths import BACKUPS as OUT  # noqa: E402
KEEP = 14

#: 必带：不可再生的
MUST = [
    ("soul.sqlite3", PD / "astrbot_plugin_dafeiyu_soul" / "soul.sqlite3"),
    ("iris_memory", PD / "astrbot_plugin_iris_chat_memory"),
    ("whale_memory", PD / "astrbot_plugin_whale_memory"),
    ("whale_memoryflow", PD / "astrbot_plugin_whale_memoryflow"),
    ("whale_state", PD / "astrbot_plugin_whale_state"),
    ("whale_core_state", PD / "whale_core"),
    ("whale_face", PD / "astrbot_plugin_whale_face"),
    ("whale_will", PD / "astrbot_plugin_whale_will"),
    ("dere", PD / "astrbot_plugin_dere_lover"),
    ("conversations", DATA / "data_v4.db"),
]

#: 不带：能重来，或者太大
SKIP_DIRS = {"faiss", "images", "images_meta", "qzone_images", "tts_cache", "browser_profile"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".wav", ".mp3", ".webp", ".pyc"}


def sha12(p: Path) -> str:
    try:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return "?"


def collect() -> list[Path]:
    files: list[Path] = []
    for _, p in MUST:
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix not in SKIP_SUFFIX:
                files.append(p)
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            if f.suffix.lower() in SKIP_SUFFIX:
                continue
            files.append(f)
    return files


def backup() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    zpath = OUT / f"state-{stamp}.zip"
    files = collect()
    manifest = {"at": stamp, "files": [], "bytes": 0}
    for f in files:
        try:
            rel = f.relative_to(DATA).as_posix()
        except ValueError:
            rel = f.name
        manifest["files"].append({"path": rel, "size": f.stat().st_size,
                                  "sha12": sha12(f) if f.suffix in (".db", ".sqlite3") else ""})
        manifest["bytes"] += f.stat().st_size
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in files:
            try:
                rel = f.relative_to(DATA).as_posix()
            except ValueError:
                rel = f.name
            z.write(f, rel)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    # 自检：能打开、文件数对得上
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        n = len(z.namelist())
    if bad:
        raise RuntimeError(f"备份损坏：{bad}")
    print(f"备份完成：{zpath.name}  {zpath.stat().st_size/1024/1024:.1f} MB  "
          f"{n} 个文件（{manifest['bytes']/1024/1024:.1f} MB 原始）")
    prune()
    return zpath


def prune() -> None:
    zips = sorted(OUT.glob("state-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in zips[KEEP:]:
        try:
            old.unlink()
            print(f"  清理旧备份：{old.name}")
        except Exception:
            pass


def list_backups() -> None:
    zips = sorted(OUT.glob("state-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        print("还没有备份")
        return
    print(f"共 {len(zips)} 份（保留最近 {KEEP} 份）：")
    for z in zips:
        print(f"  {z.name}  {z.stat().st_size/1024/1024:>6.1f} MB  "
              f"{datetime.fromtimestamp(z.stat().st_mtime):%m-%d %H:%M}")


def restore(stamp: str) -> None:
    cand = list(OUT.glob(f"state-{stamp}*.zip"))
    if not cand:
        print(f"没找到 {stamp} 的备份")
        return
    dest = Path(r"%PROJECT_HOME%\backups\restore") / stamp
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cand[0]) as z:
        z.extractall(dest)
    print(f"已解到 {dest}（没有覆盖线上，确认无误再手动拷回去）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", default="")
    args = ap.parse_args()
    if args.list:
        list_backups()
        return 0
    if args.restore:
        restore(args.restore)
        return 0
    backup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
