# -*- coding: utf-8 -*-
"""补齐缺的表情包：按 (包, 文件名) 逐张比，缺哪张补哪张。"""
from __future__ import annotations
import json
import shutil
import sqlite3
import time
from pathlib import Path

import import_memes as IM

DSH = IM.DSH
PLUG = IM.PLUG
INDEX = IM.INDEX
EMB = IM.EMB
MEMES = IM.MEMES


def pack_captions(pack: str) -> dict[str, tuple[str, str, str]]:
    """{文件名: (tag, caption, keywords)}"""
    db = DSH / pack / "index.db"
    out: dict[str, tuple[str, str, str]] = {}
    if not db.exists():
        return out
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for _path, tag, fname, caption, keywords in con.execute(
            "SELECT path, tag, file_name, caption, keywords FROM memes"):
        out[fname] = (tag or "daily", (caption or "").strip(), (keywords or "").strip())
    con.close()
    return out


def main() -> int:
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    emb = json.loads(EMB.read_text(encoding="utf-8"))
    items = idx["items"]

    have = set()
    for it in items:
        f = Path(it["file"]).name
        pack = it.get("pack") or ""
        name = f[len(pack + "__"):] if f.startswith(pack + "__") else f
        have.add((pack, name))

    todo = []
    for pack in sorted(DSH.iterdir()):
        memes = pack / "memes"
        if not memes.exists():
            continue
        caps = pack_captions(pack.name)
        for f in sorted(memes.rglob("*")):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                continue
            if (pack.name, f.name) in have:
                continue
            tag, caption, keywords = caps.get(f.name, ("daily", "", ""))
            if not caption:
                caption = f"{f.stem} {tag} 表情"
            if not keywords:
                keywords = f"{tag} 表情"
            todo.append({"pack": pack.name, "tag": tag, "src": f, "file_name": f.name,
                         "caption": caption, "keywords": keywords})

    print(f"要补 {len(todo)} 张")
    for t in todo[:6]:
        print(f"  {t['pack']}/{t['file_name']}  [{t['tag']}] {t['caption'][:36]}")
    if not todo:
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(INDEX, INDEX.with_suffix(f".json.bak-fill-{stamp}"))
    shutil.copy2(EMB, EMB.with_suffix(f".json.bak-fill-{stamp}"))

    new_items = []
    for r in todo:
        tag_dir = MEMES / r["tag"]
        tag_dir.mkdir(parents=True, exist_ok=True)
        safe = r["file_name"].replace("/", "_").replace("\\", "_")
        dst = tag_dir / f"{r['pack']}__{safe}"
        if not dst.exists():
            shutil.copy2(r["src"], dst)
        rel = f"memes/{r['tag']}/{dst.name}"
        new_items.append({"file": rel, "tag": r["tag"],
                          "caption": r["caption"][:80], "keywords": r["keywords"][:60],
                          "pack": r["pack"]})

    texts = [f"{i['caption']} {i['keywords']}" for i in new_items]
    vecs = IM.embed_texts(texts)
    for it, v in zip(new_items, vecs):
        emb["items"][it["file"]] = v

    items.extend(new_items)
    idx["items"] = items
    idx["count"] = len(items)
    emb["count"] = len(emb["items"])
    emb["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    INDEX.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    EMB.write_text(json.dumps(emb, ensure_ascii=False), encoding="utf-8")
    print(f"补齐完成：库内 {len(items)} 条，向量 {len(emb['items'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
