import json, sys, time, urllib.request
sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
import beams_voice

STATE = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\beams_state.json"
st = json.loads(open(STATE, encoding="utf-8").read())
vec = beams_voice.emo_vector(st)
preset = beams_voice.preset_of(st)
print("内核状态: label=%s v=%.2f a=%.2f mood=%.2f" % (
    st["fast"]["label"], st["fast"]["valence"], st["fast"]["arousal"], st["slow"]["mood_valence"]))
print("8维向量:", vec, "sum=%.3f" % sum(vec), "preset:", preset)

# 先测档位模式（更快，缓存可能命中）
body = {"text": "主人，本鱼在呢。", "voice": "clever", "emotion": preset,
        "max_chars": 18, "steps": 12, "deai": True,
        "out": r"%PROJECT_HOME%\框架重构\sim\_voice_check.wav"}
req = urllib.request.Request("http://127.0.0.1:8912/speak",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode())
    print("合成结果: %.1fs" % (time.time()-t0), {k: res.get(k) for k in ("out","audio_s","gen_s","cached","deai")})
except Exception as e:
    print("合成失败:", e)

# 再测向量模式（真正由 BEAM 情绪驱动）
body2 = dict(body, emo_vector=vec)
body2.pop("emotion", None)
body2["out"] = r"%PROJECT_HOME%\框架重构\sim\_voice_check_vec.wav"
req2 = urllib.request.Request("http://127.0.0.1:8912/speak",
                              data=json.dumps(body2).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
t0 = time.time()
try:
    with urllib.request.urlopen(req2, timeout=120) as r:
        res2 = json.loads(r.read().decode())
    print("向量合成: %.1fs" % (time.time()-t0), {k: res2.get(k) for k in ("out","audio_s","gen_s","cached","deai")})
except Exception as e:
    print("向量合成失败:", e)