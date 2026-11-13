# -*- coding: utf-8 -*-
import json, sqlite3
from pathlib import Path

PLUG = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme")
Dsh = Path(r"%USERPROFILE%\.dsh\meme-packs")

idx = json.loads((PLUG / "index.json").read_text(encoding="utf-8"))
items = idx["items"]
print("插件现有:", len(items))
from collections import Counter
print("按包:", Counter(i.get("pack", "?") for i in items).most_common())
print("按分类:", Counter(i.get("tag", "?") for i in items).most_common())

# embeddings 结构
emb = json.loads((PLUG / "embeddings.json").read_text(encoding="utf-8"))
its = emb.get("items")
print("\nembeddings.count:", emb.get("count"), "items 类型:", type(its).__name__, "长度:", len(its) if hasattr(its, "__len__") else "?")
if isinstance(its, dict):
    k = next(iter(its))
    print("  键样例:", k, "向量长度:", len(its[k]) if isinstance(its[k], list) else type(its[k]))
elif isinstance(its, list):
    print("  样例:", str(its[0])[:200])

# 各包的可导入量
print("\n=== .dsh 各包 ===")
total = 0
for pack in sorted(Dsh.iterdir()):
    db = pack / "index.db"
    if not (pack / "memes").exists():
        continue
    n_files = sum(1 for _ in (pack / "memes").rglob("*") if _.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"))
    rows = 0
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
        con.close()
    total += n_files
    print(f"  {pack.name}: 文件 {n_files}, index.db {rows}")
print("合计文件:", total)
