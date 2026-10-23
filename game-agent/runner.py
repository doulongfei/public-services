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

# 已有固定功能契约的任务由控制器提供最小验收声明，模型只实现对应源码。
PLANS = {
    'enemy-types': (['core.js', 'render.js'], 'Add enemies[].kind for normal and scout enemies. Scouts occur with probability about 0.25, move faster and have a smaller radius. Render scouts in a different color/silhouette. Keep the normal enemy and existing collision/scoring rules.',
                    {'kind': 'core', 'initial': {'spawn': 0}, 'steps': 1, 'path': 'enemies.0.kind', 'op': 'exists'}),
    'wave-feedback': (['core.js', 'main.js'], 'On a wave change set waveNotice={text,life:2}; decrement life to zero. Show it in #wave-notice without blocking controls.',
                      {'kind': 'core', 'initial': {'time': 19.99, 'spawn': 999}, 'path': 'waveNotice.life', 'op': 'gt', 'value': 0}),
    'health-pickup': (['core.js', 'render.js'], 'Expose pickups[] of {kind:"health",x,y,r,life}. Occasionally drop health after a kill. Collect nearby pickups to heal, cap HP at 100, and remove collected/expired pickups; render them.',
                      {'kind': 'core', 'path': 'pickups', 'op': 'exists'}),
    'weapon-upgrade': (['core.js', 'render.js'], 'Expose level derived from score, bounded to 3. Higher level must improve actual fire interval or bullet count/damage. Keep normal early-game difficulty.',
                       {'kind': 'core', 'initial': {'score': 100}, 'path': 'level', 'op': 'gt', 'value': 1}),
    'hit-feedback': (['core.js', 'render.js'], 'Expose damageFlash seconds, set at most 0.3 on collision damage and decrement to zero. Render a small local damage indication, avoid screen flashing.',
                     {'kind': 'core', 'initial': {'spawn': 999, 'shot': 999, 'enemies': [{'x': 450, 'y': 300, 'hp': 1, 'speed': 0, 'r': 11}]}, 'path': 'damageFlash', 'op': 'gt', 'value': 0}),
    'boss': (['core.js', 'render.js'], 'At time>=120 the next spawn should be a rare elite with isBoss=true, hp>1, r>11 and finite telegraph seconds. Telegraph before damaging the player. Render its warning and preserve normal enemy types and bounded spawn/entity counts.',
             {'kind': 'core', 'initial': {'time': 120, 'spawn': 0}, 'path': 'enemies.0.isBoss', 'op': 'eq', 'value': True}),
    'shield': (['core.js', 'main.js', 'render.js'], 'Expose shield={active,cooldown}. input.shield activates a short shield that prevents collision damage, starts a cooldown and expires. Add keyboard and touch controls, a button with aria-label 启动护盾 and visible cooldown. Preserve start/pause/restart.',
               {'kind': 'core', 'input': {'shield': True}, 'path': 'shield.cooldown', 'op': 'gt', 'value': 0}),
}


def task_files(task):
    if task in PLANS:
        return PLANS[task][0]
    return {'pause-overlay': ['main.js'], 'best-score': ['main.js', 'index.html'],
            'touch-controls': ['main.js', 'style.css'], 'difficulty': ['core.js'],
            'accessibility': ['main.js', 'style.css'], 'boss': ['core.js', 'render.js'],
            'shield': ['core.js', 'main.js', 'render.js'], 'visual-polish': ['render.js', 'style.css'],
            'performance': ['core.js', 'render.js'], 'onboarding': ['main.js', 'index.html']}.get(task, ['core.js', 'render.js'])


def generation_stages(task):
    # 固定功能先实现并验收核心规则，再让独立上下文实现页面，禁止跨层来回修补。
    return [[name] for name in task_files(task)] if task in PLANS else [task_files(task)]


def reset_generation(cp):
    cp['stage'] = 'generating'
    cp['generation_step'] = 0
    cp['stage_results'] = {}


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
    html = (candidate / 'game/index.html').read_text()
    if not re.search(r'<html\b', html, re.I) or not re.search(r'<canvas\b', html, re.I) or 'main.js' not in html:
        raise ValueError('Invalid game entry: HTML, canvas and main.js are required')


def fingerprint(candidate):
    return {str(p.relative_to(candidate)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((candidate / 'game').glob('*')) if p.is_file() and p.name != 'acceptance.json'}


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
    if 'model_turns' in failure:
        return '模型耗尽单阶段 8 次回合，候选未发布；下次继续当前阶段，连续失败会恢复基线。'
    if 'tool_calls' in failure:
        return '模型耗尽单阶段工具调用预算，候选未发布；下次继续当前阶段。'
    if 'Model did not finish' in failure or 'agent_end' in failure or 'budget' in failure:
        return '模型轮次未正常完成，将缩小改动后重试；连续失败会恢复已发布基线。'
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


def run_agent(cfg, candidate, prompt, logfile, state, files=None):
    files = files or task_files(state['current_task'])
    allowed = ['game/'+f for f in files]
    if state['current_task'] not in PLANS:
        allowed += ['game/acceptance.json', 'NOTES.md']
    args = ['docker', 'run', '--rm', '--init', '--name', NAME,
            '--network', 'starharbor-model', '--user', '1000:1000', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--cpus', '0.5', '--memory', '768m', '--memory-swap', '768m', '--pids-limit', '96',
            '--tmpfs', '/tmp:rw,size=192m,mode=1777',
            '-e', 'PI_CODING_AGENT_DIR=/tmp/pi-profile',
            '-e', 'STARHARBOR_FILES=' + json.dumps(allowed),
            '--mount', f'type=bind,src={candidate},dst=/workspace',
            '--mount', f'type=bind,src={APP / "models.json"},dst=/run/config/models.json,readonly',
            '--mount', f'type=bind,src={APP / "settings.json"},dst=/run/config/settings.json,readonly',
            '--mount', f'type=bind,src={APP / "guard.mjs"},dst=/run/config/guard.mjs,readonly',
            '--mount', f'type=bind,src={cfg["model_key"]},dst=/run/secrets/api-key,readonly',
            cfg['runtime_image'], 'sh', '-c',
            'mkdir -p /tmp/pi-profile && cp /run/config/models.json /tmp/pi-profile/models.json && cp /run/config/settings.json /tmp/pi-profile/settings.json && exec pi "$@"',
            'pi', '--provider', 'oci', '--model', 'qwen3.6-35b-a3b-q3_k_m',
            '--no-session', '--no-extensions', '--extension', '/run/config/guard.mjs', '--no-mcp',
            '--no-skills', '--no-prompt-templates', '--no-themes', '--no-context-files', '--offline',
            '--thinking', 'off', '--tools', 'read,edit,write', '--mode', 'json',
            '--system-prompt', 'You develop Starharbor, a Chinese browser survival game. This is ONE implementation stage. Current source is included: edit it immediately. Use a SINGLE edit call with edits:[{oldText,newText},...] for disjoint changes in the file. Copy small unique oldText anchors exactly, without inventing whitespace. Each oldText matches the ORIGINAL file before the call. Do not pad edits with unchanged functions. Only modify the listed file(s). Preserve exports, snapshot API, labels and controls. No dependencies, network calls or inline scripts. Tests run externally. After 1-3 edit calls, finish with a brief Chinese summary, without more tools. Hard limit 8 turns.',
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
    # 容器可能在循环再次检查控制指令前退出，暂停/停机优先于退出码分类。
    if not permitted(cfg):
        raise Cancelled('Round interrupted by owner control or deadline')
    if proc.returncode:
        raise InfrastructureFailure('Pi exited with code ' + str(proc.returncode) + ': ' + logfile.read_text(errors='replace')[-1200:])
    return parse_agent_log(logfile)


def agent_usage(logfile):
    """失败和中止时也统计已落盘的用量；不把增量事件重复计入。"""
    total = output = 0
    if not logfile.exists():
        return total, output
    for line in logfile.read_text(errors='replace').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('type') == 'message_end' and entry.get('message', {}).get('role') == 'assistant':
            usage = entry['message'].get('usage', {})
            total += usage.get('totalTokens', 0)
            output += usage.get('output', 0)
    return total, output


def parse_agent_log(logfile):
    messages = []; tools = 0; successful_writes = 0; ended = False; limit = None
    for line in logfile.read_text(errors='replace').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get('type') == 'agent_end':
            ended = True
        if entry.get('type') == 'guard_limit':
            limit = entry.get('reason')
        if entry.get('type') == 'tool_execution_end':
            tools += 1
            if entry.get('toolName') in ('edit', 'write') and entry.get('isError') is False:
                successful_writes += 1
        if entry.get('type') == 'message_end' and entry.get('message', {}).get('role') == 'assistant':
            messages.append(entry['message'])
    if not ended or not messages:
        raise ModelIncomplete('Pi did not emit agent_end and a final assistant message')
    if limit:
        raise ModelIncomplete('Stage budget exhausted: ' + limit)
    final = messages[-1]
    if final.get('stopReason') != 'stop':
        error = 'Model did not finish normally: ' + str(final.get('stopReason'))
        if final.get('stopReason') == 'error':
            detail = str(final.get('errorMessage', ''))[-1000:]
            if 'abort' in detail.lower():
                raise ModelIncomplete(error + ': ' + detail)
            raise InfrastructureFailure(error + ': ' + detail)
        raise ModelIncomplete(error)
    if not successful_writes:
        raise ModelIncomplete('No successful source edit/write tool call')
    summary = '\n'.join(item.get('text', '') for item in final.get('content', []) if item.get('type') == 'text')[-350:]
    return summary, sum(m.get('usage', {}).get('totalTokens', 0) for m in messages), tools


def validate_runtime(cfg, candidate, baseline, outputs, suite, core_only=False):
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
            'sh', '-c', 'for f in /workspace/game/*.js; do node --check "$f" || exit 1; done; node --test /harness/core.test.mjs && node /harness/features.mjs' + ('' if core_only else ' && node /harness/browser.mjs')]
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
    if not permitted(cfg):
        raise Cancelled('Tests interrupted by owner control or deadline')
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
    cursor = state.get('topic_index', 0)
    deferred = next((item for item in state.get('deferred_tasks', []) if item['after'] <= time.time()), None) if cursor >= len(TOPICS) else None
    if deferred:
        cursor = deferred['cursor']
    return {'base_version': version, 'cursor': cursor,
            'next_cursor': state.get('topic_index', 0) if deferred else cursor+1,
            'task': TOPICS[cursor][0] if cursor < len(TOPICS) else 'self-review',
            'code_failures': 0, 'infra_failures': 0, 'model_failures': 0, 'attempt_failures': 0, 'stage': 'generating'}


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


def make_prompt(cp, candidate, files=None):
    cursor = cp['cursor']
    detail = TOPICS[cursor % len(TOPICS)][1]
    if cursor >= len(TOPICS):
        detail = 'Choose one real missing feature or defect from the source and feedback. Optional review lens: ' + detail
    files = files or task_files(cp['task'])
    text = f"Task ID: {cp['task']}. {PLANS.get(cp['task'], (None, detail))[1]}\n"
    text += 'Edit only ' + ', '.join('game/'+f for f in files) + '. Source is included below; do not survey other files. Make minimal edits and finish normally. Preserve all existing behavior.\n'
    if cp['task'] in PLANS:
        text += 'The controller supplies acceptance.json and records your summary in NOTES.md. You only edit game source; do not write metadata.\n'
        if files == ['core.js']:
            text += 'CURRENT STAGE: implement ONLY the game rules and state in core.js. DOM/UI will be implemented in a later stage. Store all new state in createState() and update it inside step(); do not introduce UI callbacks or extra parameters. Core feature tests must pass before proceeding.\n'
        elif cp['task'] == 'wave-feedback':
            text += '''CURRENT STAGE: core.js has ALREADY passed wave tests and exposes state.waveNotice={text,life}.
Make exactly TWO disjoint edits in ONE edit call, then finish with a short summary:
1. Use oldText exactly "const pauseMsg=document.createElement('div');". Preserve it and append creation of a hidden div with id="wave-notice" to canvas.parentNode. Use individual style properties like the pause message; pointerEvents='none'.
2. Use oldText exactly "step(state,dt,input);render(ctx,state);". Preserve it and append the notice update: show state.waveNotice.text ONLY when phase is playing, life>0 AND text is nonempty; hide otherwise. Set BOTH hidden and style.display consistently.
Do not edit launch(), togglePause(), game-over or keyboard/touch handlers: the per-frame update already handles ready, pause, over and restart. No new timer, lastWave, callback, or core changes. The two short anchors above exist verbatim; do not include neighboring lines in oldText.\n'''
        else:
            text += 'CURRENT STAGE: core.js has ALREADY passed its feature tests and exposes the fields specified above. Only implement display/controls in this file; do not duplicate game rules in the UI.\n'
    else:
        text += f'''Also write game/acceptance.json with {{"task":"{cp['task']}","checks":[...]}}. One check must fail on the old version.
Checks: core {{kind:"core",initial:{{}},steps:1,path:"hp",op:"gt",value:0}}; ui {{kind:"ui",selector:"#id",phase:"playing",property:"text",op:"contains",value:"text"}}; visual {{kind:"visual",selector:"#arena"}}. Only operators eq/gt/gte/lt/lte/contains/exists. No executable tests. Choose a check specific to your change, not the hp example. Existing sh_best storage, #best and pause overlay must keep working.\n'''
    for name in files:
        content = (candidate/'game'/name).read_text()
        limit = 14000 // len(files)
        if len(content) > limit:
            content = content[:limit//2]+'\n[...middle omitted; use a targeted read if needed...]\n'+content[-limit//2:]
        text += f'\nCURRENT /workspace/game/{name}:\n```\n{content}\n```\n'
    text += '\nOwner feedback (within fixed contracts):\n' + controls().get('feedback', '')[-800:]
    if cp.get('error'):
        text += '\nPrevious error (candidate may have been reset to the published baseline). Make a smaller complete edit:\n' + cp['error'][-1400:]
    return text


def generate(cfg, cp, candidate, source, round_dir, state):
    stages = generation_stages(cp['task'])
    end = min(time.time()+cfg['round_timeout'], cfg['deadline'])
    results = cp.setdefault('stage_results', {})
    for index in range(cp.get('generation_step', 0), len(stages)):
        files = stages[index]
        if index > 0 and cp['task'] in PLANS and cp.get('error'):
            # 重试未通过的页面文件时丢弃其半成品，保留已验收的核心与前置阶段。
            for name in files:
                shutil.copyfile(source/'game'/name, candidate/'game'/name)
        state['phase'] = 'generating'
        state['generation_file'] = ', '.join(files)
        state['generation_step'] = f'{index+1}/{len(stages)}'
        save(state)
        logfile = round_dir/f'pi-{index}.jsonl'
        remaining = end-time.time()
        if remaining <= 0:
            raise ModelIncomplete('Round exceeded time budget between stages')
        try:
            summary, _, tools = run_agent({**cfg, 'round_timeout': remaining}, candidate, make_prompt(cp, candidate, files), logfile, state, files=files)
        finally:
            tokens, output = agent_usage(logfile)
            state['tokens'] += tokens
            state['output_tokens'] = state.get('output_tokens', 0)+output
            atomic(round_dir/f'usage-{index}.json', {'tokens': tokens, 'output_tokens': output})
            save(state)
        if files == ['core.js'] and cp['task'] in PLANS:
            approved = load(BASE/'contracts.json', {'checks': [], 'features': []})
            check = PLANS[cp['task']][2]
            state['phase'] = 'testing'; save(state)
            validate_runtime(cfg, candidate, source, round_dir/f'stage-{index}',
                             {'checks': approved['checks']+[check], 'new_checks': [check],
                              'features': sorted(set(approved['features']+[cp['task']]))}, core_only=True)
        results[str(index)] = {'summary': summary, 'tools': tools}
        cp['generation_step'] = index+1
        cp.pop('error', None)
        checkpoint(cp)
    return '\n'.join(results[str(i)]['summary'] for i in range(len(stages))), sum(results[str(i)]['tools'] for i in range(len(stages)))


def delay(failures):
    return min(3600, 120 * 2**min(failures, 5))


def event(state, entry):
    state['history'] = (state['history'] + [entry])[-60:]


def main():
    cfg = load(BASE / 'config.json')
    secrets = [Path(cfg['model_key']).read_text().strip(), (BASE / 'control/api-key').read_text().strip()]
    state = release.recover(BASE) or load(BASE / 'public/state.json', {'round': 0, 'accepted': 0, 'rejected': 0, 'history': [], 'tokens': 0})
    state.update({'started_at': cfg['started_at'], 'deadline': cfg['deadline'], 'model': 'qwen3.6-35b-a3b-q3_k_m',
                  'workflow_version': 4, 'limits': {'round_minutes': cfg['round_timeout']//60, 'model_cpus': 2, 'agent_cpus': .5, 'max_model_turns_per_stage': 8, 'max_stages': 3}})
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
            state['deferred_tasks'] = [item for item in state.get('deferred_tasks', []) if item['task'] != task]
            state.update({'current_task': task, 'round_started_at': time.time(), 'phase': 'generating'})
            save(state)
            print(f'Round {number} started: {task}', flush=True)
            try:
                source = baseline(cp['base_version'])
                candidate = candidate_path(cp)
                if candidate is not None and candidate.exists():
                    try:
                        validate_tree(candidate, secrets)
                    except (ValueError, UnicodeError):
                        cp.pop('candidate', None)
                        reset_generation(cp)
                        cp['resets'] = cp.get('resets', 0)+1
                        cp['error'] = 'Damaged candidate discarded; restored published source. Make only the requested small edit.'
                        candidate = None
                if candidate is None or not candidate.exists():
                    candidate = round_dir / 'candidate'
                    shutil.copytree(source, candidate)
                    owner_tree(candidate)
                    cp['candidate'] = str(candidate.relative_to(BASE))
                    reset_generation(cp)
                checkpoint(cp)
                if cp.get('stage') != 'generated':
                    summary, tools = generate(cfg, cp, candidate, source, round_dir, state)
                    cp.update({'summary': summary, 'tools': tools, 'stage': 'generated'})
                    if task in PLANS:
                        atomic(candidate/'game/acceptance.json', {'task': task, 'checks': [PLANS[task][2]]})
                        notes = candidate/'NOTES.md'
                        notes.write_text(notes.read_text()[-12000:]+'\n\n'+task+': '+summary+'\n')
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
                                   'generation_file': None, 'generation_step': None,
                                   'pause_overlay': 'pause-overlay' in features, 'accepted_features': features,
                                   'topic_index': cp.get('next_cursor', cp['cursor']+1), 'phase': 'cooldown', 'failure_count': 0,
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
                    infra = isinstance(error, (InfrastructureFailure, OSError, subprocess.SubprocessError))
                    incomplete = isinstance(error, ModelIncomplete)
                    counter = 'infra_failures' if infra else ('model_failures' if incomplete else 'code_failures')
                    cp[counter] = cp.get(counter, 0)+1
                    cp['attempt_failures'] = cp.get('attempt_failures', 0)+1
                    cp['retry_at'] = time.time()+min(delay(cp[counter]), 3600 if infra else 900)
                    state['retry_at'] = cp['retry_at']
                    state['rejected'] += 1
                    state['failure_count'] = cp['attempt_failures']
                    result = 'retrying' if infra or incomplete else 'rejected'
                    if not infra:
                        cp['stage'] = 'generating'
                        cp['generation_step'] = min(cp.get('generation_step', 0), len(generation_stages(task))-1)
                    # 所有失败共用回退阈值，不能因错误分类而无限复用坏候选。
                    if cp['attempt_failures'] % 3 == 0:
                        cp.pop('candidate', None)
                        reset_generation(cp)
                        cp['resets'] = cp.get('resets', 0)+1
                checkpoint(cp)
                event(state, {'round': number, 'task': task, 'result': result,
                              'summary': public_failure(failure), 'failure_kind': type(error).__name__,
                              'baseline_reset': 'candidate' not in cp, 'at': time.time()})
                if result != 'interrupted' and cp.get('attempt_failures', 0) >= 6:
                    # 不把失败记成成功；先处理后续任务，完成首轮任务表后再尝试此项。
                    state['deferred_tasks'].append({'cursor': cp['cursor'], 'task': task, 'after': time.time()+3600})
                    state['topic_index'] = cp.get('next_cursor', cp['cursor']+1)
                    state['history'][-1].update({'result': 'deferred', 'summary': '本任务连续六次未完成，已保留记录并重新排队，先继续后续任务。'})
                    state['retry_at'] = time.time()+cfg['cooldown']
                    cp = {}; checkpoint(cp)
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
