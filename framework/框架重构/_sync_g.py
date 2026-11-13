# -*- coding: utf-8 -*-
import shutil
from pathlib import Path
src=Path(r'%USERPROFILE%\agent-tools\qq-audit-sync')
dst=Path(r'%PROJECT_HOME%\框架重构')
assert dst.is_dir(), 'dest missing'
pending=[]
copied=[]
for p in src.iterdir():
    if p.is_file():
        try:
            shutil.copy2(p, dst/p.name)
            copied.append(p.name)
        except Exception as e:
            pending.append(f'{p.name}:{e}')
print('copied', copied)
print('pending', pending)
