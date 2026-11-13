"""语音输出闭环实测：BEAM 状态 → 8 维情绪向量 → IndexTTS 合成（不占 QQ）。"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
import beams_voice  # noqa: E402

STATE = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\beams_state.json"
OUT_DIR = r"%PROJECT_HOME%\框架重构\sim"
TTS = "http://127.0.0.1:8912/speak"


def speak(text, vec=None, preset=None, voice="clever", steps=12, out_name=None):
    body = {"text": text, "voice": voice, "max_chars": 18, "steps": steps, "deai": True,
            "out": os.path.join(OUT_DIR, out_name or "_voice_loop.wav")}
    if vec:
        body["emo_vector"] = vec
    elif preset:
        body["emotion"] = preset
    req = urllib.request.Request(TTS, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        res = json.loads(r.read().decode("utf-8"))
    res["wall_s"] = round(time.time() - t0, 2)
    return res


def main():
    st = json.loads(open(STATE, encoding="utf-8").read())
    vec = beams_voice.emo_vector(st)
    preset = beams_voice.preset_of(st)
    print("内核：label=%s v=%.2f a=%.2f mood=%.2f stress=%.2f" % (
        st["fast"]["label"], st["fast"]["valence"], st["fast"]["arousal"],
        st["slow"]["mood_valence"], st["slow"]["stress"]))
    print("8 维向量:", vec, "sum=%.3f" % sum(vec), "preset:", preset)

    reply = "累就累着吧，不用先跟自己解释一遍。"
    # 1) 档位模式
    r1 = speak(reply, preset=preset, out_name="_voice_preset.wav")
    print("[1] 档位合成:", {k: r1.get(k) for k in ("audio_s", "gen_s", "cached", "wall_s")})
    # 2) 向量模式（真正由内核情绪驱动）
    r2 = speak(reply, vec=vec, out_name="_voice_vec.wav")
    print("[2] 向量合成:", {k: r2.get(k) for k in ("audio_s", "gen_s", "cached", "wall_s")})
    # 3) 缓存命中（同文本同参数）
    r3 = speak(reply, vec=vec, out_name="_voice_vec.wav")
    print("[3] 缓存命中:", {k: r3.get(k) for k in ("audio_s", "gen_s", "cached", "wall_s")})
    for p in ("_voice_preset.wav", "_voice_vec.wav"):
        fp = os.path.join(OUT_DIR, p)
        print("   ", p, "存在" if os.path.isfile(fp) else "缺失", os.path.getsize(fp) if os.path.isfile(fp) else 0, "bytes")

    # 4) 情绪对比：开心 vs 难过 的向量差异是否明显
    happy = {"fast": {"valence": 0.6, "arousal": 0.7, "dominance": 0.6, "label": "开心"},
             "slow": {"mood_valence": 0.4, "stress": 0.1, "fatigue": 0.1},
             "intero": {"prediction_error": 0.1}}
    sad = {"fast": {"valence": -0.5, "arousal": 0.35, "dominance": 0.4, "label": "低落"},
           "slow": {"mood_valence": -0.35, "stress": 0.5, "fatigue": 0.5},
           "intero": {"prediction_error": 0.2}}
    vh, vs = beams_voice.emo_vector(happy), beams_voice.emo_vector(sad)
    print("[4] 情绪对比 happy:", vh, "sad:", vs)
    assert vh[0] > vs[0], "开心向量的 happy 维度应更高"
    assert vs[2] > vh[2] or vs[5] > vh[5], "难过向量 sad/melancholic 应更高"
    print("\nOK：语音输出闭环（内核→向量→TTS）实测通过")


if __name__ == "__main__":
    main()