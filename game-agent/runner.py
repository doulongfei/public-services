#!/usr/bin/env python3
"""受信任的发布控制器：模型只能写候选源码，测试成功才原子发布。"""
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

import release
from acceptance import proposal

atomic = release.atomic


class Cancelled(RuntimeError):
    pass


class InfrastructureFailure(RuntimeError):
    pass


class ModelIncomplete(RuntimeError):
    pass

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
    return controls()['mode'] == 'running' and not HALT and time.time() < cfg['deadline']


def clean_container():
    subprocess.run(['docker', 'rm', '-f', NAME, TEST_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)


def validate_tree(candidate, secrets):
    total = 0
    for file in candidate.rglob('*'):
        if file.is_symlink():
            raise ValueError('Symlinks are not permitted')
        if file.is_dir():
            continue
        if not file.is_file():
            raise ValueError('Only regular files are permitted')
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
            if re.search(r'<\s*(base|iframe|object|embed|form)\b|serviceWorker|\beval\s*\(|\bnew\s+Function\s*\(', text, re.I):
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


def save(state):
    state['updated_at'] = time.time()
    atomic(BASE / 'public/state.json', state)


def public_failure(failure):
    if 'No new acceptance check' in failure:
        return '本轮没有证明相对已发布版本的有效改进，未发布。'
    if 'Model did not finish' in failure or 'agent_end' in failure or 'budget' in failure:
        return '模型轮次未正常完成，保留候选并退避重试。'
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
                    if not permitted(cfg):
                        raise Cancelled('Round interrupted by owner control or deadline')
                    raise ModelIncomplete('Round exceeded time or log budget')
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
    if proc.returncode:
        raise InfrastructureFailure('Pi exited with code ' + str(proc.returncode) + ': ' + logfile.read_text(errors='replace')[-1200:])
    return parse_agent_log(logfile)


def parse_agent_log(logfile):
    messages = []; tools = 0; successful_writes = 0; ended = False
    for line in logfile.read_text(errors='replace').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get('type') == 'agent_end':
            ended = True
        if entry.get('type') == 'tool_execution_end':
            tools += 1
            if entry.get('toolName') in ('edit', 'write') and entry.get('isError') is False:
                successful_writes += 1
        if entry.get('type') == 'message_end' and entry.get('message', {}).get('role') == 'assistant':
            messages.append(entry['message'])
    if not ended or not messages:
        raise ModelIncomplete('Pi did not emit agent_end and a final assistant message')
    final = messages[-1]
    if final.get('stopReason') != 'stop':
        error = 'Model did not finish normally: ' + str(final.get('stopReason'))
        if final.get('stopReason') == 'error':
            raise InfrastructureFailure(error + ': ' + str(final.get('errorMessage', ''))[-1000:])
        raise ModelIncomplete(error)
    if not successful_writes:
        raise ModelIncomplete('No successful source edit/write tool call')
    summary = '\n'.join(item.get('text', '') for item in final.get('content', []) if item.get('type') == 'text')[-350:]
    return summary, sum(m.get('usage', {}).get('totalTokens', 0) for m in messages), tools


def validate_runtime(cfg, candidate, baseline, outputs, suite):
    outputs.mkdir(parents=True, exist_ok=True)
    os.chown(outputs, 1000, 1000)
    plan = outputs.parent / 'contracts.json'
    atomic(plan, suite)
    args = ['docker', 'run', '--rm', '--init', '--name', TEST_NAME, '--network', 'none', '--user', '1000:1000', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--cpus', '0.75',
            '--memory', '1g', '--memory-swap', '1g', '--pids-limit', '192',
            '--tmpfs', '/tmp:rw,size=256m,mode=1777', '--shm-size', '128m',
            '--mount', f'type=bind,src={candidate},dst=/workspace,readonly',
            '--mount', f'type=bind,src={APP / "tests"},dst=/harness,readonly',
            '--mount', f'type=bind,src={baseline},dst=/baseline,readonly',
            '--mount', f'type=bind,src={plan},dst=/run/contracts.json,readonly',
            '--mount', f'type=bind,src={outputs},dst=/output',
            cfg['runtime_image'],
            'sh', '-c', 'for f in /workspace/game/*.js; do node --check "$f" || exit 1; done; node --test /harness/core.test.mjs && node /harness/features.mjs && node /harness/browser.mjs']
    logfile = outputs / 'tests.txt'
    with logfile.open('w') as output:
        result = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
        end = min(time.time()+180, cfg['deadline'])
        try:
            while result.poll() is None:
                if not permitted(cfg) or time.time() >= end:
                    subprocess.run(['docker', 'rm', '-f', TEST_NAME], capture_output=True, timeout=20)
                    result.wait(timeout=20)
                    if not permitted(cfg):
                        raise Cancelled('Tests interrupted by owner control or deadline')
                    raise InfrastructureFailure('Test container exceeded time budget')
                time.sleep(2)
        finally:
            if result.poll() is None:
                subprocess.run(['docker', 'rm', '-f', TEST_NAME], capture_output=True, timeout=20)
                result.wait(timeout=20)
    if result.returncode in (125, 126, 127, 137, 143):
        raise InfrastructureFailure('Test container failed: ' + logfile.read_text()[-2000:])
    if result.returncode:
        raise ValueError(logfile.read_text()[-3500:])


def checkpoint(data):
    atomic(BASE / 'checkpoint.json', data, 0o600)


def baseline(version):
    path = BASE / 'baselines' / version
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.new')
        shutil.rmtree(temporary, ignore_errors=True)
        release.export_source(BASE, version, temporary)
        temporary.rename(path)
    return path


def new_checkpoint(state, number):
    version = release.git(BASE, 'rev-parse', state['last_release']).strip()
    return {'base_version': version, 'cursor': state.get('topic_index', 0),
            'task': TOPICS[state.get('topic_index', 0)][0] if state.get('topic_index', 0) < len(TOPICS) else 'self-review',
            'code_failures': 0, 'infra_failures': 0, 'stage': 'generating'}


def candidate_path(cp):
    rel = cp.get('candidate')
    if not rel:
        return None
    if not re.fullmatch(r'rounds/[0-9]{5}/candidate', rel):
        raise ValueError('Invalid candidate checkpoint')
    path = BASE / rel
    if path.is_symlink() or (path.exists() and not path.resolve().is_relative_to((BASE / 'rounds').resolve())):
        raise ValueError('Invalid candidate checkpoint path')
    return path


def make_prompt(cp, candidate):
    cursor = cp['cursor']
    detail = TOPICS[cursor % len(TOPICS)][1]
    if cursor >= len(TOPICS):
        detail = 'Choose one real missing feature or defect from the source and feedback. Optional review lens: ' + detail
    text = f'''Task ID: {cp['task']}. {detail}
Use read on file paths, not directories. Implement one small observable improvement and update NOTES.md.
Also WRITE game/acceptance.json: {{"task":"{cp['task']}","checks":[...]}} with 1–4 declarative checks.
A core check example: {{"kind":"core","initial":{{"score":100}},"steps":1,"path":"level","op":"gt","value":1}}.
Core starts playing, merges initial, steps 1/60 seconds (steps 1–4000, optional input). Paths can inspect arrays via .length.
A UI check example: {{"kind":"ui","selector":"#best-score","phase":"playing","property":"text","op":"contains","value":"最高"}}.
UI selector must be a single #id; phase ready/playing/paused; property text/visible; op eq/gt/gte/lt/lte/contains/exists.
For a visual-only improvement use {{"kind":"visual","selector":"#arena"}}. Comments, unused files and notes are not an improvement.
All checks must pass in this candidate and at least one MUST fail on the last published source. Previously accepted checks remain mandatory.
Feature contracts: enemy-types uses enemies[].kind with two different speeds/sizes; health-pickup uses pickups[] {{kind:'health',x,y,r,life}}; weapon-upgrade uses level; wave-feedback uses waveNotice {{text,life}} and #wave-notice; hit-feedback uses damageFlash seconds; boss uses enemies[].isBoss and telegraph seconds at time>=120; shield uses shield {{active,cooldown}}, input.shield and button aria-label 启动护盾. Best score keeps sh_best localStorage and #best, accepts invalid/unavailable storage safely.
Current notes:\n{(candidate / 'NOTES.md').read_text()[-1800:]}
Owner feedback (higher priority within fixed contracts):\n{controls().get('feedback', '')[-1500:]}
'''
    if cp.get('error'):
        text += '\nPrevious attempt was incomplete or failed. Fix this first, preserve existing changes, and finish normally:\n' + cp['error'][-2400:]
    return text


def delay(failures):
    return min(3600, 120 * 2**min(failures, 5))


def event(state, entry):
    state['history'] = (state['history'] + [entry])[-60:]


def main():
    cfg = load(BASE / 'config.json')
    secrets = [Path(cfg['model_key']).read_text().strip(), (BASE / 'control/api-key').read_text().strip()]
    state = release.recover(BASE) or load(BASE / 'public/state.json', {'round': 0, 'accepted': 0, 'rejected': 0, 'history': [], 'tokens': 0})
    state.update({'started_at': cfg['started_at'], 'deadline': cfg['deadline'], 'model': 'qwen3.6-35b-a3b-q3_k_m',
                  'workflow_version': 2, 'limits': {'round_minutes': cfg['round_timeout']//60, 'model_cpus': 2, 'agent_cpus': .5, 'max_model_turns': 8}})
    cp = load(BASE / 'checkpoint.json', {})
    clean_container()
    try:
        while not HALT and time.time() < cfg['deadline']:
            mode = controls()['mode']
            if mode == 'stopped':
                break
            if mode == 'paused':
                state['phase'] = 'paused'; save(state); time.sleep(5); continue
            if shutil.disk_usage(BASE).free < 12*1024**3:
                state['phase'] = 'waiting-disk'; save(state); wait(cfg, 300); continue
            retry_at = max(state.get('retry_at', 0), cp.get('retry_at', 0))
            if retry_at > time.time():
                state['phase'] = 'cooldown'; save(state); wait(cfg, retry_at-time.time()); continue
            state['round'] += 1
            number = state['round']
            round_dir = BASE / 'rounds' / f'{number:05d}'
            round_dir.mkdir(parents=True, exist_ok=True)
            cp = cp if cp.get('task') else new_checkpoint(state, number)
            task = cp['task']
            state.update({'current_task': task, 'round_started_at': time.time(), 'phase': 'generating'})
            save(state)
            print(f'Round {number} started: {task}', flush=True)
            try:
                source = baseline(cp['base_version'])
                candidate = candidate_path(cp)
                if candidate is None or not candidate.exists():
                    candidate = round_dir / 'candidate'
                    shutil.copytree(source, candidate)
                    owner_tree(candidate)
                    cp['candidate'] = str(candidate.relative_to(BASE))
                    cp['stage'] = 'generating'
                checkpoint(cp)
                if cp.get('stage') != 'generated':
                    summary, tokens, tools = run_agent(cfg, candidate, make_prompt(cp, candidate), round_dir / 'pi.jsonl', state)
                    state['tokens'] += tokens
                    cp.update({'summary': summary, 'tools': tools, 'stage': 'generated'})
                    checkpoint(cp); save(state)
                validate_tree(candidate, secrets)
                if fingerprint(candidate) == fingerprint(source):
                    raise ValueError('No game source changes; notes-only edits are not a release')
                checks = proposal(candidate, task)
                approved = load(BASE / 'contracts.json', {'checks': [], 'features': []})
                features = sorted(set(approved['features'] + [task]))
                suite = {'checks': approved['checks'] + checks, 'new_checks': checks, 'features': features}
                if len(suite['checks']) > 4096:
                    raise ValueError('Acceptance budget reached; owner review required')
                if not permitted(cfg):
                    raise Cancelled('Publication cancelled by control or deadline')
                state['phase'] = 'testing'; save(state)
                validate_runtime(cfg, candidate, source, round_dir / 'output', suite)
                if not permitted(cfg):
                    raise Cancelled('Publication cancelled by control or deadline')
                summary = cp['summary']
                if any(secret in summary for secret in secrets):
                    summary = 'Changed game source; private content omitted.'
                version = release.prepare_commit(BASE, candidate, cp['base_version'], number, task)
                release_id = f'{number:05d}-{version[:12]}'
                next_state = json.loads(json.dumps(state))
                next_state.update({'accepted': state['accepted']+1, 'last_release': version[:12], 'release_id': release_id,
                                   'pause_overlay': 'pause-overlay' in features, 'accepted_features': features,
                                   'topic_index': cp['cursor']+1, 'phase': 'cooldown', 'failure_count': 0,
                                   'retry_at': time.time()+cfg['cooldown'], 'updated_at': time.time()})
                event(next_state, {'round': number, 'task': task, 'result': 'accepted', 'version': version[:12],
                                   'summary': summary[:350], 'tool_calls': cp['tools'], 'at': time.time()})
                release.stage(BASE, candidate, release_id, version, next_state,
                              {'checks': suite['checks'], 'features': features}, round_dir / 'output')
                state = release.activate(BASE, release_id, lambda: permitted(cfg))
                cp = {}
                print(f'Round {number} accepted; version={version[:12]}', flush=True)
            except Exception as error:
                # 指针切换后的异常属于待恢复成功事务，不得再次计为拒绝。
                if (BASE / 'pending.json').exists():
                    state = release.recover(BASE)
                    if state.get('round') == number and any(e.get('round') == number and e.get('result') == 'accepted' for e in state['history']):
                        cp = {}; continue
                failure = str(error)
                for secret in secrets:
                    failure = failure.replace(secret, '[redacted]')
                (round_dir / 'failure.txt').write_text(failure)
                cp['error'] = failure[-4000:]
                if isinstance(error, (Cancelled, InterruptedError)):
                    result = 'interrupted'
                    state['cancelled'] = state.get('cancelled', 0)+1
                    cp['retry_at'] = 0
                else:
                    infra = isinstance(error, (InfrastructureFailure, ModelIncomplete, OSError, subprocess.SubprocessError))
                    counter = 'infra_failures' if infra else 'code_failures'
                    cp[counter] += 1
                    cp['retry_at'] = time.time()+delay(cp[counter])
                    state['retry_at'] = cp['retry_at']
                    state['rejected'] += 1
                    state['failure_count'] = cp[counter]
                    result = 'retrying' if infra else 'rejected'
                    if not infra:
                        cp['stage'] = 'generating'
                        if cp['code_failures'] % 3 == 0:
                            # 保留失败目录供分析；回到已发布源码修复同一任务，不跳过目标。
                            cp.pop('candidate', None)
                    elif isinstance(error, ModelIncomplete):
                        cp['stage'] = 'generating'
                checkpoint(cp)
                event(state, {'round': number, 'task': task, 'result': result,
                              'summary': public_failure(failure), 'at': time.time()})
                state['phase'] = 'cooldown'; save(state)
                print(f'Round {number} {result}: {type(error).__name__}', flush=True)
            try:
                release.prune(BASE, candidate_path(cp))
            except OSError as error:
                print(f'Nonfatal cleanup error: {error}', flush=True)
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
