# -*- coding: utf-8 -*-
"""对比 .dsh 原仓和插件库，找出没进去的图。"""
import json
from pathlib import Path

DSH = Path(r"%USERPROFILE%\.dsh\meme-packs")
PLUG = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme")

idx = json.loads((PLUG / "index.json").read_text(encoding="utf-8"))
# 库里每张图的 (pack, 原文件名)
have = set()
for it in idx["items"]:
    f = Path(it["file"]).name
    pack = it.get("pack") or ""
    name = f
    if f.startswith(pack + "__"):
        name = f[len(pack + "__"):]
    have.add((pack, name))

print(f"库内 {len(have)} 个 (pack, file)")
missing = []
for pack in sorted(DSH.iterdir()):
    memes = pack / "memes"
    if not memes.exists():
        continue
    for f in memes.rglob("*"):
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            continue
        if (pack.name, f.name) not in have:
            missing.append((pack.name, f.name, f))
print(f"\n对不上的 {len(missing)} 张：")
for p, n, f in missing[:20]:
    print(f"  {p}/{n}  ({f.stat().st_size} B)")

# 插件里被隔离的
q = PLUG / "_quarantine"
if q.exists():
    qs = [x for x in q.rglob("*") if x.is_file()]
    print(f"\n_quarantine 里有 {len(qs)} 个文件")
    for x in qs[:8]:
        print("  ", x.name)
