# -*- coding: utf-8 -*-
"""把她表情包仓库里还没进 AstrBot 的包，按现有标准导入。

现有标准（插件 index.json 的条目）：
    {"file": "memes/<tag>/<文件名>", "tag": "<分类>",
     "caption": "<中文描述>", "keywords": "…", "pack": "<包名>"}
另外每张要在 embeddings.json 里有一条 1024 维向量。

数据来源：.dsh/meme-packs/<pack>/index.db 的 memes 表（有 caption / keywords / tag）。

用法：
    python import_memes.py --dry-run     # 只看会导多少
    python import_memes.py               # 真导
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
import urllib.request
from pathlib import Path

PLUG = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme")
INDEX = PLUG / "index.json"
EMB = PLUG / "embeddings.json"
MEMES = PLUG / "memes"
DSH = Path(r"%USERPROFILE%\.dsh\meme-packs")
CFG = Path(r"%ASTRBOT_HOME%\data\cmd_config.json")

# 需要导入的包（现有 index 里没有的）
TARGETS = ["dafeiyu-emoji", "paopao-eyi", "paopao-short", "paopao-whale-girl"]


def emb_conf() -> tuple[str, str, str, int]:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    for p in cfg.get("provider") or []:
        if p.get("id") == "bailian_embedding":
            return (p["embedding_api_key"], p["embedding_api_base"].rstrip("/"),
                    p["embedding_model"], int(p["embedding_dimensions"]))
    raise RuntimeError("没找到 bailian_embedding")


def embed_texts(texts: list[str], batch: int = 10) -> list[list[float]]:
    key, base, model, dim = emb_conf()
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        # DashScope 原生格式：input 是 {"texts": [...]}，不是数组
        body = {"model": model, "input": {"texts": chunk}, "parameters": {"dimension": dim}}
        req = urllib.request.Request(
            base + "/services/embeddings/text-embedding/text-embedding",
            data=json.dumps(body).encode("utf-8"), method="POST")
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
        got = resp["output"]["embeddings"]
        out.extend([e["embedding"] for e in got])
        print(f"  嵌入 {min(i + batch, len(texts))}/{len(texts)}", flush=True)
        time.sleep(0.2)
    return out


def read_pack(pack: str) -> list[dict]:
    db = DSH / pack / "index.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT path, tag, file_name, caption, keywords FROM memes").fetchall()
    con.close()
    out = []
    for path, tag, fname, caption, keywords in rows:
        src = DSH / pack / path.replace("\\", "/")
        if not src.exists():
            # path 可能带前缀差异，退一步按文件名找
            cand = list((DSH / pack / "memes").rglob(fname))
            src = cand[0] if cand else None
        if not src or not src.exists():
            continue
        out.append({
            "pack": pack, "tag": tag or "daily", "src": src,
            "caption": (caption or "").strip() or f"{tag} 表情",
            "keywords": (keywords or "").strip() or (tag or "daily"),
            "file_name": fname,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--packs", default=",".join(TARGETS))
    args = ap.parse_args()
    packs = [p.strip() for p in args.packs.split(",") if p.strip()]

    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    emb = json.loads(EMB.read_text(encoding="utf-8"))
    items = idx["items"]
    have_files = {i["file"] for i in items}
    have_caps = {(i.get("caption") or "")[:24] for i in items}
    have_packs = {i.get("pack") for i in items}
    print(f"现有 {len(items)} 条，来自包：{sorted(x for x in have_packs if x)}")

    todo = []
    for pack in packs:
        if pack in have_packs:
            print(f"[跳过] {pack} 已经在库里")
            continue
        rows = read_pack(pack)
        print(f"[{pack}] index.db 可用 {len(rows)} 条")
        for r in rows:
            if (r["caption"] or "")[:24] in have_caps:
                continue
            todo.append(r)

    print(f"\n待导入 {len(todo)} 张")
    if args.dry_run:
        for r in todo[:8]:
            print(f"  {r['tag']:<12} {r['caption'][:40]}")
        return 0
    if not todo:
        return 0

    # 备份
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(INDEX, INDEX.with_suffix(f".json.bak-{stamp}"))
    shutil.copy2(EMB, EMB.with_suffix(f".json.bak-{stamp}"))
    print("已备份 index.json / embeddings.json")

    new_items = []
    for n, r in enumerate(todo, 1):
        tag_dir = MEMES / r["tag"]
        tag_dir.mkdir(parents=True, exist_ok=True)
        # 文件名加包前缀，避免不同包重名
        safe = r["file_name"].replace("/", "_").replace("\\", "_")
        dst = tag_dir / f"{r['pack']}__{safe}"
        if not dst.exists():
            shutil.copy2(r["src"], dst)
        rel = f"memes/{r['tag']}/{dst.name}"
        if rel in have_files:
            continue
        new_items.append({
            "file": rel,
            "tag": r["tag"],
            "caption": r["caption"][:80],
            "keywords": r["keywords"][:60],
            "pack": r["pack"],
        })
        if n % 25 == 0:
            print(f"  复制 {n}/{len(todo)}", flush=True)

    print(f"新增条目 {len(new_items)}")

    # 生成向量
    texts = [f"{it['caption']} {it['keywords']}" for it in new_items]
    vecs = embed_texts(texts)
    if len(vecs) != len(new_items):
        raise RuntimeError(f"向量数量不匹配 {len(vecs)} != {len(new_items)}")
    for it, v in zip(new_items, vecs):
        emb["items"][it["file"]] = v

    items.extend(new_items)
    idx["items"] = items
    idx["count"] = len(items)
    emb["count"] = len(emb["items"])
    emb["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    INDEX.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    EMB.write_text(json.dumps(emb, ensure_ascii=False), encoding="utf-8")
    print(f"完成：库内 {len(items)} 条，向量 {len(emb['items'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
