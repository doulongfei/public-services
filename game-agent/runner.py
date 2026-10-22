#!/usr/bin/env python3
"""受信任的发布控制器：模型只能写候选源码，测试成功才原子发布。"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time

BASE = Path(os.environ.get('STARHARBOR_ROOT', '/srv/game-agent'))
APP = Path(os.environ.get('STARHARBOR_APP', '/opt/starharbor-agent'))
NAME = 'starharbor-round'
TEST_NAME = 'starharbor-test'
HALT = False
TOPICS = [
 ('pause-overlay', 'Modify ONLY game/main.js to add a pause overlay inside .viewport, id="pause-message", with Chinese text 航线已暂停. Create it with DOM APIs and style properties; do not add inline script/style tags. It must be visible while paused and hidden via hidden and display while playing/ready/over. Keep HUD pause button accessible. Use one focused edit rather than rewriting the file. Keep start/restart working.'),
 ('best-score', 'Add persistent best score with safe localStorage handling and a clear Chinese HUD label. Invalid or unavailable storage must never break gameplay.'),
 ('enemy-types', 'Improve enemy variety: add one distinct enemy behavior or silhouette, with bounded spawn rate and clear visual readability.'),
 ('wave-feedback', 'Improve wave transitions: show a brief readable wave announcement without blocking controls or resetting the run.'),
 ('health-pickup', 'Add or improve a fair health pickup mechanic with visible pickup feedback and bounded entity lifetime.'),
 ('weapon-upgrade', 'Add or improve one gradual weapon upgrade tied to survival or kills. Preserve reasonable difficulty and entity limits.'),
 ('hit-feedback', 'Improve hit and damage feedback: readable short effects, respect reduced motion, avoid heavy screen flashing.'),
 ('touch-controls', 'Review and improve mobile controls, touch feedback, safe areas, readability and prevention of stuck input.'),
 ('difficulty', 'Review difficulty progression and enemy speed/spawn limits. Make one concrete balance improvement; preserve a playable early game.'),
 ('accessibility', 'Improve keyboard controls, focus visibility, pause explanations or accessible labels without changing tested button names.'),
 ('boss', 'Add or improve a rare elite enemy or boss with a telegraphed attack and a fair, observable counterplay.'),
 ('shield', 'Add or improve a short defensive ability with a visible cooldown and support for both keyboard and touch.'),
 ('visual-polish', 'Improve one meaningful visual element: ship, enemy, arena, particles or readable HUD. Keep frame cost and contrast under control.'),
 ('performance', 'Inspect update/render loops for a real performance or entity lifecycle bug and make a small measured improvement.'),
 ('onboarding', 'Improve the first-run instructions and in-game explanations so a new player understands survival and upgrades.'),
 ('self-review', 'Review existing source and NOTES.md, identify one real missing feature or defect yourself, implement a small improvement and explain it.'),
]


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.new')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.chmod(mode)
    tmp.replace(path)


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120, **kwargs).stdout


def controls():
    return load(BASE / 'control/commands.json', {'mode': 'running', 'feedback': ''})


def permitted(cfg):
    return not HALT and time.time() < cfg['deadline'] and controls()['mode'] == 'running'


def clean_container():
    subprocess.run(['docker', 'rm', '-f', NAME, TEST_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)


def validate_tree(candidate, secrets):
    total = 0
    for file in candidate.rglob('*'):
        if file.is_symlink():
            raise ValueError('Symlinks are not permitted')
        if not file.is_file():
            continue
        rel = file.relative_to(candidate).as_posix()
        if rel != 'NOTES.md' and not re.fullmatch(r'game/[a-z0-9][a-z0-9_.-]{0,48}\.(js|css|html|json)', rel):
            raise ValueError('Unsupported artifact path: ' + rel)
        data = file.read_bytes()
        total += len(data)
        if len(data) > 64 * 1024 or total > 512 * 1024:
            raise ValueError('Artifact size budget exceeded')
        text = data.decode('utf-8')
        if any(secret and secret in text for secret in secrets):
            raise ValueError('Artifact contains a private credential')
        if rel.startswith('game/'):
            urls = re.findall(r'https?://[^\s\"\'<>`]+', text)
            if any(url.rstrip('/') != 'https://lab.doufei.eu.org' for url in urls):
                raise ValueError('Game must remain offline and use only local assets')
            if re.search(r'<\s*(iframe|object|embed|form)\b|serviceWorker|\beval\s*\(|\bnew\s+Function\s*\(', text, re.I):
                raise ValueError('Unsupported active-content construct')
    for required in ['index.html', 'main.js', 'core.js', 'render.js', 'style.css']:
        if not (candidate / 'game' / required).is_file():
            raise ValueError('Missing required game file: ' + required)


def fingerprint(candidate):
    return {str(p.relative_to(candidate)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((candidate / 'game').glob('*')) if p.is_file()}


def owner_tree(root):
    for p in [root, *root.rglob('*')]:
        os.chown(p, 1000, 1000)
        p.chmod(0o755 if p.is_dir() else 0o644)


def publish(candidate, version, outputs):
    release = BASE / 'public/releases' / version
    shutil.copytree(candidate / 'game', release / 'game')
    for p in [release, *release.rglob('*')]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    link = BASE / 'public/current.new'
    link.unlink(missing_ok=True)
    link.symlink_to(Path('releases') / version, target_is_directory=True)
    link.replace(BASE / 'public/current')
    for name in ['desktop.png', 'mobile.png']:
        source = outputs / name
        if source.exists():
            tmp = BASE / 'public' / (name + '.new')
            shutil.copyfile(source, tmp)
            tmp.chmod(0o644)
            tmp.replace(BASE / 'public' / name)
    for stale in sorted((BASE / 'public/releases').iterdir(), key=lambda p: p.stat().st_mtime)[:-25]:
        if stale != release:
            shutil.rmtree(stale)


def save(state):
    state['updated_at'] = time.time()
    atomic(BASE / 'public/state.json', state)


def public_failure(failure):
    if 'Credential store read failed' in failure:
        return '模型客户端缓存初始化失败，试玩版本保持上一版。'
    if 'No game source changes' in failure:
        return '本轮没有生成游戏源码变更，未发布。'
    if 'interrupted' in failure or 'cancelled' in failure:
        return '本轮因控制指令、截止时间或资源预算中止，未发布。'
    if 'Pi exited' in failure or 'Pi did not' in failure:
        return '模型客户端运行未正常完成，未发布；运行器将重试。'
    return '候选版本未通过固定验证，继续保留上一版，并把具体错误交给下轮修复。'


def wait(cfg, seconds):
    end = min(time.time() + seconds, cfg['deadline'])
    while time.time() < end and not HALT:
        if controls()['mode'] != 'running':
            break
        time.sleep(min(5, max(0, end-time.time())))


def record_output(process, output):
    # Pi 增量事件重复携带部分消息；保留最终消息与工具事件，避免长任务日志平方增长。
    for line in process.stdout:
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if isinstance(entry, dict) and entry.get('type') == 'message_update':
            continue
        output.write(line)
        output.flush()


def run_agent(cfg, candidate, prompt, logfile, state):
    args = ['docker', 'run', '--rm', '--init', '--name', NAME,
            '--network', 'starharbor-model', '--user', '1000:1000', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--cpus', '0.5', '--memory', '768m', '--memory-swap', '768m', '--pids-limit', '96',
            '--tmpfs', '/tmp:rw,size=192m,mode=1777',
            '-e', 'PI_CODING_AGENT_DIR=/tmp/pi-profile',
            '--mount', f'type=bind,src={candidate},dst=/workspace',
            '--mount', f'type=bind,src={APP / "models.json"},dst=/run/config/models.json,readonly',
            '--mount', f'type=bind,src={APP / "settings.json"},dst=/run/config/settings.json,readonly',
            '--mount', f'type=bind,src={cfg["model_key"]},dst=/run/secrets/api-key,readonly',
            cfg['runtime_image'], 'sh', '-c',
            'mkdir -p /tmp/pi-profile && cp /run/config/models.json /tmp/pi-profile/models.json && cp /run/config/settings.json /tmp/pi-profile/settings.json && exec pi "$@"',
            'pi', '--provider', 'oci', '--model', 'qwen3.6-35b-a3b-q3_k_m',
            '--no-session', '--no-extensions', '--extension', '/opt/guard.mjs', '--no-mcp',
            '--no-skills', '--no-prompt-templates', '--no-themes', '--no-context-files', '--offline',
            '--thinking', 'off', '--tools', 'read,edit,write', '--mode', 'json',
            '--system-prompt', 'You are the developer of Starharbor, a Chinese browser survival game. Use read/edit/write tools, not prose-only plans. Make one small working improvement. Use edit for existing files. Read at most 70 lines at once. Preserve exported core API, window.starharbor.snapshot(), original button labels, keyboard and touch controls. No external assets, network calls, dependencies, inline scripts, or changes outside /workspace/game and NOTES.md. Tests run outside your workspace after you finish. Stop after at most 8 model turns. Summarize the actual change in Chinese.',
            '-p', prompt]
    with logfile.open('w') as output:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        reader = threading.Thread(target=record_output, args=(proc, output), daemon=True)
        reader.start()
        end = min(time.time() + cfg['round_timeout'], cfg['deadline'])
        try:
            while proc.poll() is None:
                if not permitted(cfg) or time.time() >= end or logfile.stat().st_size > 12*1024*1024:
                    clean_container()
                    proc.wait(timeout=20)
                    raise RuntimeError('Round interrupted by control, deadline or resource budget')
                state['heartbeat_at'] = time.time()
                save(state)
                time.sleep(5)
        finally:
            if proc.poll() is None:
                clean_container()
                proc.wait(timeout=20)
            reader.join(timeout=10)
            if reader.is_alive():
                raise RuntimeError('Worker output stream did not close')
    summary = ''
    usage = 0
    turns = 0
    tools = 0
    for line in logfile.read_text(errors='replace').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get('type') == 'tool_execution_end':
            tools += 1
        if entry.get('type') == 'message_end' and entry.get('message', {}).get('role') == 'assistant':
            turns += 1
            message = entry['message']
            usage += message.get('usage', {}).get('totalTokens', 0)
            for item in message.get('content', []):
                if item.get('type') == 'text':
                    summary = item.get('text', '')[-350:]
    if proc.returncode:
        raise RuntimeError('Pi exited with code ' + str(proc.returncode) + ': ' + logfile.read_text(errors='replace')[-1200:])
    if not turns or not tools:
        raise RuntimeError('Pi did not complete a model/tool turn')
    return summary, usage, tools


def validate_runtime(cfg, candidate, outputs, pause_overlay):
    outputs.mkdir(parents=True, exist_ok=True)
    os.chown(outputs, 1000, 1000)
    args = ['docker', 'run', '--rm', '--init', '--name', TEST_NAME, '--network', 'none', '--user', '1000:1000', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--cpus', '0.75',
            '--memory', '1g', '--memory-swap', '1g', '--pids-limit', '192',
            '--tmpfs', '/tmp:rw,size=256m,mode=1777', '--shm-size', '128m',
            '--mount', f'type=bind,src={candidate},dst=/workspace,readonly',
            '--mount', f'type=bind,src={APP / "tests"},dst=/harness,readonly',
            '--mount', f'type=bind,src={outputs},dst=/output',
            '-e', 'EXPECT_PAUSE_OVERLAY=' + str(int(pause_overlay)), cfg['runtime_image'],
            'sh', '-c', 'for f in /workspace/game/*.js; do node --check "$f" || exit 1; done; node --test /harness/core.test.mjs && node /harness/browser.mjs']
    logfile = outputs / 'tests.txt'
    with logfile.open('w') as output:
        result = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
        end = min(time.time()+180, cfg['deadline'])
        try:
            while result.poll() is None:
                if not permitted(cfg) or time.time() >= end:
                    subprocess.run(['docker', 'rm', '-f', TEST_NAME], capture_output=True, timeout=20)
                    result.wait(timeout=20)
                    raise RuntimeError('Tests interrupted by owner control or time budget')
                time.sleep(2)
        finally:
            if result.poll() is None:
                subprocess.run(['docker', 'rm', '-f', TEST_NAME], capture_output=True, timeout=20)
                result.wait(timeout=20)
    if result.returncode:
        raise RuntimeError(logfile.read_text()[-3500:])


def commit(candidate, number, task):
    repo = BASE / 'history'
    shutil.rmtree(repo / 'game', ignore_errors=True)
    shutil.copytree(candidate / 'game', repo / 'game')
    if (candidate / 'NOTES.md').exists():
        shutil.copyfile(candidate / 'NOTES.md', repo / 'NOTES.md')
    command(['git', '-C', str(repo), 'add', '--', 'game', 'NOTES.md'])
    command(['git', '-C', str(repo), 'commit', '-m', f'round {number}: {task}'])
    return command(['git', '-C', str(repo), 'rev-parse', '--short=12', 'HEAD']).strip()


def main():
    cfg = load(BASE / 'config.json')
    secrets = [Path(cfg['model_key']).read_text().strip(), (BASE / 'control/api-key').read_text().strip()]
    state = load(BASE / 'public/state.json', {'round': 0, 'accepted': 0, 'rejected': 0, 'history': [], 'tokens': 0})
    state.update({'started_at': cfg['started_at'], 'deadline': cfg['deadline'], 'model': 'qwen3.6-35b-a3b-q3_k_m', 'limits': {'round_minutes': cfg['round_timeout']//60, 'model_cpus': 2, 'agent_cpus': .5, 'max_model_turns': 8}})
    clean_container()
    failure = ''
    candidate = None
    failures = 0
    try:
        while not HALT and time.time() < cfg['deadline']:
            mode = controls()['mode']
            if mode == 'stopped':
                break
            if mode == 'paused':
                state['phase'] = 'paused'; save(state); time.sleep(5); continue
            if shutil.disk_usage(BASE).free < 12*1024**3:
                state['phase'] = 'waiting-disk'; save(state); wait(cfg, 300); continue
            state['round'] += 1
            number = state['round']
            cursor = state.get('topic_index', 0)
            topic_index = cursor % len(TOPICS)
            task, detail = TOPICS[topic_index]
            if cursor >= len(TOPICS):
                detail = ('Choose the highest-value next improvement yourself from current source, NOTES.md and owner feedback. '
                          'Do not add an already implemented feature again. Optional review lens: ' + detail)
                task = 'self-review'
            round_dir = BASE / 'rounds' / f'{number:05d}'
            round_dir.mkdir(parents=True, exist_ok=True)
            if not failure or candidate is None or not candidate.exists():
                candidate = round_dir / 'candidate'
                shutil.copytree(BASE / 'history/game', candidate / 'game')
                shutil.copyfile(BASE / 'history/NOTES.md', candidate / 'NOTES.md')
                owner_tree(candidate)
            state.update({'phase': 'generating', 'current_task': task, 'round_started_at': time.time()})
            save(state)
            print(f'Round {number} started: {task}', flush=True)
            notes = (candidate / 'NOTES.md').read_text()[-1800:]
            files = ', '.join(str(p.relative_to(candidate)) for p in sorted((candidate / 'game').glob('*')) if p.is_file())
            prompt = f'Round {number}. Task: {detail}\nAvailable files under /workspace: {files}, NOTES.md. Use read on a file path, not a directory.\nImprove one small unfinished part, do not repeat existing changes. Write the next useful step briefly into NOTES.md.\nCurrent notes:\n{notes}\nOwner feedback (higher priority unless incompatible with fixed contracts):\n{controls().get("feedback", "")[-1500:]}\n'
            if failure:
                prompt += 'The previous candidate failed trusted tests; first fix this error and preserve the intended feature:\n' + failure[-2500:]
            if task == 'pause-overlay':
                prompt += '\nThis is a single-file task: read ONLY game/main.js (plus NOTES.md if needed), then edit it. Do NOT read style.css/index.html/core.js/render.js; phase values are ready/playing/paused/over and .viewport already exists. Use DOM style properties for the self-contained overlay. Implement now rather than surveying the project.\n'
            before = fingerprint(BASE / 'history')
            try:
                summary, tokens, tools = run_agent(cfg, candidate, prompt, round_dir / 'pi.jsonl', state)
                state['tokens'] += tokens
                validate_tree(candidate, secrets)
                if fingerprint(candidate) == before:
                    raise RuntimeError('No game source changes; notes-only edits are not a release')
                if not permitted(cfg):
                    raise RuntimeError('Publication cancelled by control or deadline')
                state['phase'] = 'testing'; save(state)
                pause_overlay = task == 'pause-overlay' or state.get('pause_overlay', False)
                validate_runtime(cfg, candidate, round_dir / 'output', pause_overlay)
                if not permitted(cfg):
                    raise RuntimeError('Publication cancelled by control or deadline')
                if any(secret in summary for secret in secrets):
                    summary = 'Changed game source; private content omitted.'
                version = commit(candidate, number, task)
                publish(candidate, f'{number:05d}-{version}', round_dir / 'output')
                state.update({'accepted': state['accepted']+1, 'last_release': version, 'pause_overlay': pause_overlay, 'topic_index': cursor+1})
                event = {'round': number, 'task': task, 'result': 'accepted', 'version': version, 'summary': summary[:350], 'tool_calls': tools, 'at': time.time()}
                failure = ''; failures = 0
            except Exception as error:
                failure = str(error)
                for secret in secrets:
                    failure = failure.replace(secret, '[redacted]')
                (round_dir / 'failure.txt').write_text(failure)
                failures += 1
                state['rejected'] += 1
                event = {'round': number, 'task': task, 'result': 'rejected', 'summary': public_failure(failure), 'at': time.time()}
                if failures % 3 == 0:
                    failure = ''; candidate = None
                    state['topic_index'] = cursor+1
            state['history'] = (state['history'] + [event])[-60:]
            state['phase'] = 'cooldown'; save(state)
            print(f'Round {number} {event["result"]}; accepted={state["accepted"]}, rejected={state["rejected"]}', flush=True)
            protected = candidate.parent if candidate is not None else None
            for old in sorted((BASE / 'rounds').iterdir())[:-30]:
                if old != protected:
                    shutil.rmtree(old)
            wait(cfg, cfg['cooldown'] if not failure else min(3600, 120*2**min(failures, 5)))
        state['phase'] = 'stopped' if controls()['mode'] == 'stopped' else ('interrupted' if HALT else 'completed')
        save(state)
    finally:
        clean_container()


if __name__ == '__main__':
    def stop(_signum, _frame):
        global HALT
        HALT = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    main()
