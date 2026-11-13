# -*- coding: utf-8 -*-
"""只读检查：看两边的 prompt 里到底注入了什么，不发任何消息。"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(r"%PROJECT_HOME%\母女聊天室")
sys.path.insert(0, str(HERE))

print("=" * 56)
print("【妈妈版】prompt 长什么样（不发消息，只构建）")
print("=" * 56)
spec = importlib.util.spec_from_file_location("ma", HERE / "mom_agent.py")
ma = importlib.util.module_from_spec(spec)
sys.modules["ma"] = ma
spec.loader.exec_module(ma)

p = ma.build_prompt("宝宝", "我今天真的很累，被领导骂了", "刚才屋里说的：宝宝：在吗\n我：在的")
for i, part in enumerate(p.split("\n\n")):
    tag = " ← 语料" if "真人平时是这么说的" in part else ""
    print(f"--- 段 {i+1}{tag} ---")
    print(part[:220])

print()
print("=" * 56)
print("【女儿版】只抽语料，确认没有荤词类")
print("=" * 56)
sys.path.insert(0, r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_whale_mom")
from vocab_pack import pick, stats, KID_SAFE_CATEGORIES, NSFW_CATEGORY

s = stats()
print(f"库共 {s['total']} 条；女儿可用分类 {len(KID_SAFE_CATEGORIES)} 个")
print(f"荤词类「{NSFW_CATEGORY}」{s['by_category'].get(NSFW_CATEGORY,0)} 条 —— 女儿这边整类不读")
for q in ("我今天很难受", "你在干嘛", "笑死我了", "困了想睡"):
    got = pick(q, 4, kid_safe=True)
    print(f"\n  「{q}」→")
    for x in got:
        print("    ", x)
