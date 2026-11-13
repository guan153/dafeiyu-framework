# -*- coding: utf-8 -*-
"""生成 12 张「表情差分」——复用现有 anima 工作流（dafeiyu_v3b LoRA + FaceDetailer + ReLight）。

为什么不用 Live2D 渲染：见 face.py 的说明，本机没有 headless 渲染器（live2d-py 未装）。
先用同一角色 LoRA 出 12 张固定角度、只换表情的图，做成「表情卡」，零依赖、当天能用。

跑法（要求 ComfyUI 在 127.0.0.1:8188）：
    python tools/face_gen.py                 # 只补缺的
    python tools/face_gen.py --force         # 全部重出
    python tools/face_gen.py --only 委屈,吃醋
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core.face import MOOD_FACE  # noqa: E402

COMFY = "http://127.0.0.1:8188"
WORKFLOW = Path(r"G:\ai\dere-sd\workflows\dafeiyu_anima_v3b_fashion_bot_api.json")
OUT_DIR = ROOT / "docs" / "face"
MANIFEST = OUT_DIR / "manifest.json"

# 固定人物描述：与工作流默认 prompt 完全一致，只换表情那一段，保证 12 张是「同一张脸」
BASE = (
    "dafeiyu_v2, 1girl, solo, blue hair, long hair, blue eyes, ahoge, fin ears, "
    "white frilled maid headdress, blue hair bows, navy and white maid dress, "
    "white frilled apron with blue whale emblem, blue gemstone brooch, gold trim, "
    "upper body, looking at viewer"
)

NEG = (
    "low quality, worst quality, blurry, multiple girls, extra arms, extra fingers, "
    "bad hands, text, watermark, logo"
)

# 每种心情的表情 tag（Danbooru 风格，与 BASE 拼接）
EXPR_PROMPT = {
    "开心": "happy, open mouth, sparkling eyes, blush, fang, smile, warm light",
    "黏人": "clinging, leaning forward, blush, half-closed eyes, soft smile, close-up",
    "吃醋": "pout, jealous, narrowed eyes, annoyed, looking away, blush",
    "委屈": "crying, tears, >_<, pout, wet eyes, trembling",
    "困": "sleepy, half-closed eyes, yawning, sweatdrop, tired",
    "兴奋": "excited, open mouth, sparkling eyes, fang, blush, dynamic angle",
    "胡闹": "mischievous, smug, one eye closed, tongue out, grin, fang",
    "好奇": "curious, head tilt, wide eyes, leaning forward, mouth slightly open",
    "安心": "gentle smile, closed eyes, calm, soft blush, relaxed",
    "认真": "serious, black-framed glasses, focused, closed mouth, straight face",
    "平静": "neutral expression, calm, slight smile, looking at viewer",
    "低落": "sad, crying, tears streaming, looking down, drooping eyes",
}


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        COMFY + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path: str) -> bytes:
    with urllib.request.urlopen(COMFY + path, timeout=60) as r:
        return r.read()


def wait_ready(timeout: float = 180.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            _get("/system_stats")
            return True
        except Exception:
            time.sleep(3)
    return False


def build_workflow(mood: str, idx: int, seed: int) -> dict:
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    wf = copy.deepcopy(wf)
    expr = EXPR_PROMPT.get(mood, EXPR_PROMPT["平静"])
    wf["4"]["inputs"]["text"] = f"{BASE}, {expr}"
    wf["5"]["inputs"]["text"] = NEG
    wf["8"]["inputs"]["seed"] = seed + idx
    wf["21"]["inputs"]["seed"] = seed + idx  # FaceDetailer 同 seed，脸不飘
    return wf


def queue_and_wait(wf: dict, timeout: float = 420.0) -> Path | None:
    cid = str(uuid.uuid4())
    resp = _post("/prompt", {"prompt": wf, "client_id": cid})
    pid = resp.get("prompt_id")
    if not pid:
        raise RuntimeError(f"提交被拒：{resp}")

    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(3)
        try:
            hist = json.loads(_get(f"/history/{pid}").decode("utf-8"))
        except Exception:
            continue
        if pid not in hist:
            continue
        item = hist[pid]
        status = (item.get("status") or {}).get("status_str")
        if status == "error":
            raise RuntimeError(f"执行失败：{json.dumps(item.get('status'), ensure_ascii=False)[:400]}")
        images = []
        for node in (item.get("outputs") or {}).values():
            images.extend(node.get("images") or [])
        if images:
            info = images[-1]
            q = urllib.parse.urlencode(
                {"filename": info["filename"], "subfolder": info.get("subfolder", ""), "type": info.get("type", "output")}
            )
            return _get("/view?" + q), info["filename"]  # type: ignore[return-value]
    raise TimeoutError(f"{timeout}s 内没出图")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已有图也重出")
    ap.add_argument("--only", default="", help="只出这些心情，逗号分隔")
    ap.add_argument("--seed", type=int, default=20261108)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    moods = list(MOOD_FACE)
    if args.only:
        wanted = {m.strip() for m in args.only.split(",") if m.strip()}
        moods = [m for m in moods if m in wanted]
        missing = wanted - set(MOOD_FACE)
        if missing:
            print(f"[warn] 不认识的心情：{missing}")

    if not wait_ready():
        print("[fail] ComfyUI 没起来（127.0.0.1:8188）")
        return 2

    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    ok, fail = 0, 0
    for i, mood in enumerate(MOOD_FACE):
        if mood not in moods:
            continue
        target = OUT_DIR / f"{i:02d}_{mood}.png"
        if target.exists() and not args.force:
            print(f"[skip] {mood} 已有")
            ok += 1
            continue
        try:
            data, name = queue_and_wait(build_workflow(mood, i, args.seed))  # type: ignore[misc]
            target.write_bytes(data)  # type: ignore[union-attr]
            manifest[mood] = {
                "file": target.name,
                "expr": EXPR_PROMPT[mood],
                "exp3": MOOD_FACE[mood].exp3,
                "seed": args.seed + i,
                "comfy_file": name,
                "ts": int(time.time()),
            }
            MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[ok] {mood} → {target.name} ({len(data) // 1024} KB)")  # type: ignore[arg-type]
            ok += 1
        except Exception as e:  # 单张失败不拖垮整批
            print(f"[fail] {mood}: {type(e).__name__}: {e}")
            fail += 1
        time.sleep(1)

    print(f"\n完成：{ok} 成功 / {fail} 失败 → {OUT_DIR}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
