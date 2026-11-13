# -*- coding: utf-8 -*-
"""情感分类服务：常驻进程，HTTP 接口（laya encoder + 分类头）

启动: python emotion_server.py --port 8901
接口: POST /classify {"text": "..."} → {"label": "...", "confidence": 0.9, "probs": {...}}
"""
import sys, json, argparse, threading, os
from pathlib import Path
_HERE = Path(__file__).resolve().parent
for _p in (_HERE / "laya_pkg", os.environ.get("LAYA_HOME")):
    if _p and Path(_p).is_dir():
        sys.path.insert(0, str(_p))
from http.server import BaseHTTPRequestHandler, HTTPServer

import torch
import torch.nn as nn
import laya

BASE_DIR = Path(__file__).resolve().parent
MODEL = Path(os.environ.get("EMOTION_CLS_MODEL", str(BASE_DIR / "emotion_cls.pt")))
LAYA_MODEL_DIR = Path(os.environ.get("LAYA_MODEL_DIR", str(BASE_DIR / "model" / "laya")))
# 默认 CPU；--device cuda 可切回 GPU
DEVICE = "cpu"
_state = {"agent": None, "head": None, "embed": None, "label2id": None, "id2label": None}


def load(device: str = "cpu"):
    if LAYA_MODEL_DIR.is_dir():
        agent = laya.load(str(LAYA_MODEL_DIR), subfolder="multilingual", device=device)
    else:
        agent = laya.load("convaiinnovations/laya", subfolder="multilingual", device=device)
    agent.model.eval()
    ckpt = torch.load(MODEL, map_location=DEVICE)
    label2id = ckpt["label2id"]
    id2label = ckpt["id2label"]
    head = nn.Linear(768, len(label2id)).to(DEVICE)
    head.load_state_dict(ckpt["head"])
    head.eval()
    _state.update({
        "agent": agent, "head": head, "label2id": label2id, "id2label": id2label,
        "device": device,
        "embed": laya.embed_fn_from_agent(agent, max_length=128, batch_size=8),
    })
    print(f"[情感服务] 就绪 device={DEVICE} 类别={list(label2id.keys())}")


def classify(text: str):
    embed = _state["embed"]
    head = _state["head"]
    with torch.no_grad():
        x = torch.tensor(embed([text]), dtype=torch.float32).to(DEVICE)
        logits = head(x)
        probs = torch.softmax(logits, dim=1)[0]
        idx = int(probs.argmax().item())
    id2label = _state["id2label"]
    return {
        "label": id2label[idx],
        "confidence": round(float(probs[idx].item()), 3),
        "probs": {id2label[i]: round(float(p), 3) for i, p in enumerate(probs.tolist())},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/classify":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            text = str(body.get("text") or "")[:500]
            if not text.strip():
                self._json({"error": "empty text"}, 400)
                return
            result = classify(text)
            self._json(result, 200)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "device": DEVICE, "classes": list(_state["label2id"].keys()) if _state["label2id"] else []}, 200)
        else:
            self.send_error(404)

    def _json(self, obj, code):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    args = ap.parse_args()
    load(args.device)
    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[情感服务] 监听 http://127.0.0.1:{args.port}")
    srv.serve_forever()
