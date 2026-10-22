#!/usr/bin/env python3
"""独立的绝对截止时间保险，避免工作循环故障延长任务。"""
import json
from pathlib import Path
import subprocess
import time

BASE = Path('/srv/game-agent')
if time.time() >= json.loads((BASE / 'config.json').read_text())['deadline']:
    subprocess.run(['systemctl', 'stop', 'starharbor-agent'], check=True, timeout=60)
    file = BASE / 'public/state.json'
    state = json.loads(file.read_text())
    if state.get('phase') != 'stopped':
        state['phase'] = 'completed'
    state['updated_at'] = time.time()
    temp = file.with_name('state.json.deadline-new')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temp.chmod(0o644)
    temp.replace(file)
