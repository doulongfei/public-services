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
from acceptance import proposal, validate_check, UI_ATTRIBUTES
from contracts import validate_controls

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
 ('self-review', 'Review the included source, identify one real missing feature or defect yourself, implement a small observable improvement and explain it.'),
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
    'accessibility': (['main.js'], 'Make #arena keyboard-focusable (tabindex="0") with an informative Chinese aria-label. Focus it in launch() after hiding the overlay. Add Escape to pause a playing run without changing P toggle or button accessible names. Preserve mouse/touch controls.',
                      {'kind': 'ui', 'selector': '#arena', 'phase': 'playing', 'property': 'attribute', 'attribute': 'tabindex', 'op': 'eq', 'value': '0'}),
    'difficulty': (['core.js'], 'Cap normal enemy speed at 180 and scout speed at 220 while preserving their initial speed and variety. Set a minimum spawn interval of 0.35 seconds for late waves. Preserve damage, wave timing and weapon/pickup rules. This is a late-game balance cap, not a rewrite.',
                   {'kind': 'core', 'initial': {'time': 1000, 'spawn': 0}, 'path': 'spawn', 'op': 'gte', 'value': .35}),
    'performance': (['core.js'], 'Remove bullets once fully outside the 900x600 arena with a 32px margin (x<-32, x>932, y<-32 or y>632), in addition to existing lifetime cleanup. Preserve collisions, scoring, in-bounds live bullets, damage feedback and every other mechanic. Make one focused edit to the existing bullet filter.',
                    {'kind': 'core', 'initial': {'spawn': 999, 'shot': 999, 'bullets': [{'x': -100, 'y': -100, 'vx': -1, 'vy': -1, 'life': 2}]}, 'path': 'bullets.length', 'op': 'eq', 'value': 0}),
    'onboarding': (['index.html'], 'Add a visible Chinese instructions paragraph id="flight-guide" inside the ready overlay. Explain movement, automatic fire, P pause, health pickups, and weapon levels: 15 kills for Lv2 and 30 for Lv3. Preserve all existing element IDs and button text, especially 开始游戏 →. Keep instructions concise and mobile-friendly.',
                   {'kind': 'ui', 'selector': '#flight-guide', 'phase': 'ready', 'property': 'text', 'op': 'contains', 'value': '15'}),
    'visual-polish': (['render.js'], 'Improve the player ship silhouette with a readable cockpit or engine detail, using existing local Canvas drawing. Keep the ship position/radius, colors readable, and all mechanics unchanged. Only read state; do not add timers or update game fields in render().',
                      {'kind': 'visual', 'selector': '#arena'}),
}

VIEW_PLANS = {
    'enemy-types': 'Render existing enemies[].kind (normal/scout) with distinct colors or silhouettes. Read existing r and speed; do not change enemies or spawning.',
    'wave-feedback': 'Display existing read-only state.waveNotice.text and life. The core already maintains the timer in seconds. Do not decrement life, change state, or replace the wave HUD update.',
    'health-pickup': 'Draw existing pickups[] with kind health, x/y/r/life, with readable health colors. Core already handles collection, healing and expiry; never modify those fields.',
    'weapon-upgrade': 'Display existing state.level and upgraded bullets clearly. Core already handles levels and firing; rendering must not change game state.',
    'hit-feedback': 'Display existing state.damageFlash seconds with a small local effect. The core already expires it; do not decrement timers or change HP.',
    'boss': 'Display existing enemy isBoss and telegraph seconds with a readable warning. Core already handles spawning and timer expiry; never change those fields.',
    'shield': 'Display existing shield.active and shield.cooldown. In main.js add keyboard/touch input.shield and button aria-label 启动护盾; in render.js draw the existing state. Core owns activation, damage prevention and cooldown; do not mutate shield state in UI.',
}

WAVE_ANCHORS = ["const pauseMsg=document.createElement('div');", 'step(state,dt,input);render(ctx,state);']

# 自审的关注点与可写文件一起定义，不能重用首轮“添加既有功能”的指令。
REVIEW_LENSES = [
    (['core.js'], 'Review core rules for ONE real timer, lifecycle, collision or state-boundary defect. Prefer a small bug demonstrable by a deterministic core scenario. Inspect timers that cross zero, entity cleanup, and consistency of existing rules; do not change balance arbitrarily or add an unused field.'),
    (['render.js'], 'Review current Canvas rendering for ONE missing, useful visual cue or readability improvement visible in the ready or first playing frame. Choose a detail not already present. Rendering must remain read-only and inexpensive. Do not add decorative changes that hide useful information.'),
    (['main.js'], 'Review existing DOM/HUD accessibility and explanations for ONE missing useful improvement visible in ready/playing/paused. Preserve controls, existing accessible button names and all game rules. Use a real element ID and observable text or an allowed accessibility attribute for proof.'),
    (['index.html', 'style.css'], 'Review first-run guidance and mobile readability for ONE concrete missing explanation or accessibility improvement. Preserve existing element IDs and button names, especially 开始游戏 →. Prove a useful visible text or accessibility change on a real element ID; CSS-only changes cannot be proved by the Canvas check.'),
]
SOURCE_FILES = ['core.js', 'render.js', 'main.js', 'index.html', 'style.css']


def review_lens(cursor):
    return REVIEW_LENSES[max(0, cursor-(len(TOPICS)-1)) % len(REVIEW_LENSES)]


def checkpoint_files(cp):
    return review_lens(cp['cursor'])[0] if cp['task'] == 'self-review' else task_files(cp['task'])


def task_files(task):
    if task in PLANS:
        return PLANS[task][0]
    return {'pause-overlay': ['main.js'], 'best-score': ['main.js', 'index.html'],
            'touch-controls': ['main.js', 'style.css'], 'difficulty': ['core.js'],
            'accessibility': ['main.js', 'style.css'], 'boss': ['core.js', 'render.js'],
            'shield': ['core.js', 'main.js', 'render.js'], 'visual-polish': ['render.js', 'style.css'],
            'performance': ['core.js', 'render.js'], 'onboarding': ['main.js', 'index.html']}.get(task, ['core.js', 'render.js'])


def generation_stages(task, cp=None):
    # 固定功能先实现并验收核心规则，再让独立上下文实现页面，禁止跨层来回修补。
    return [[name] for name in task_files(task)] if task in PLANS else [checkpoint_files(cp) if cp else task_files(task), ['acceptance.json']]


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
    if 'Control contract:' in failure:
        return '候选破坏了开始/重开按钮或页面元素契约，已拦截并反馈具体修复方法。'
    if 'Renderer must not mutate core state' in failure:
        return '绘制时修改了游戏状态或计时器，候选未通过验收。'
    if 'repeated_tool_error' in failure:
        return '模型连续请求了被禁止的操作，已提前结束本阶段；保留通过验证的源码后重试。'
    if any(word in failure for word in ('acceptance.json', 'Core steps', 'UI property', 'UI phase', 'assertion operator', 'JSON', 'Expecting property')):
        return '验收声明格式不合规，源码保持待验收；将单独修复声明后重新验证。'
    if 'Acceptance failed:' in failure:
        detail = failure.split('Acceptance failed:', 1)[1].split('\n', 1)[0][:180]
        return '验收行为不符：'+detail
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
    if failure.startswith(('Round interrupted', 'Tests interrupted', 'Publication cancelled')):
        return '本轮因控制指令、截止时间或资源预算中止，未发布。'
    if 'Pi exited' in failure or 'Pi did not' in failure:
        return '模型客户端运行未正常完成，未发布；运行器将重试。'
    assertion = re.search(r'AssertionError(?:\s*\[[^\]]+\])?:\s*([^\n]+)', failure)
    if assertion:
        return '固定回归未通过：'+assertion.group(1)[:180]
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
    args = ['docker', 'run', '--rm', '--init', '--name', NAME,
            '--network', 'starharbor-model', '--user', '1000:1000', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--cpus', '0.5', '--memory', '768m', '--memory-swap', '768m', '--pids-limit', '96',
            '--tmpfs', '/tmp:rw,size=192m,mode=1777',
            '-e', 'PI_CODING_AGENT_DIR=/tmp/pi-profile',
            '-e', 'STARHARBOR_FILES=' + json.dumps(allowed),
            '-e', 'STARHARBOR_TASK=' + state['current_task'],
            '-e', 'STARHARBOR_READ_FILES=' + json.dumps(['game/'+f for f in SOURCE_FILES]),
            '-e', 'STARHARBOR_INSERT_ANCHORS=' + json.dumps(WAVE_ANCHORS if state['current_task']=='wave-feedback' and files==['main.js'] else []),
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
            '--system-prompt', 'You develop Starharbor, a Chinese browser survival game. This is ONE implementation stage. Current source is included: edit it immediately. Use a SINGLE edit call with edits:[{oldText,newText},...] for disjoint changes in the file. Copy small unique oldText anchors exactly, without inventing whitespace. Each oldText matches the ORIGINAL file before the call. Do not pad edits with unchanged functions. Only modify the listed file(s). Preserve exports, snapshot API, labels and controls. No dependencies, network calls or inline scripts. Tests run externally. For acceptance.json use ONE write call with valid JSON and the exact task ID. After 1-3 file changes, finish with a brief Chinese summary, without more tools. Hard limit 8 turns.',
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
    last_tool_error = ''
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
            if entry.get('isError'):
                result = entry.get('result', {})
                detail = ('\n'.join(str(item.get('text', '')) for item in result.get('content', []) if isinstance(item, dict))
                          if isinstance(result, dict) else str(result))[:1000]
                if detail.strip():
                    last_tool_error = detail
            if entry.get('toolName') in ('edit', 'write') and entry.get('isError') is False:
                successful_writes += 1
        if entry.get('type') == 'message_end' and entry.get('message', {}).get('role') == 'assistant':
            messages.append(entry['message'])
    if not ended or not messages:
        raise ModelIncomplete('Pi did not emit agent_end and a final assistant message')
    if limit:
        raise ModelIncomplete('Stage budget exhausted: ' + limit + ('; last tool error: '+last_tool_error if last_tool_error else ''))
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
        raise ModelIncomplete('No successful source edit/write tool call' + ('; last tool error: '+last_tool_error if last_tool_error else ''))
    summary = '\n'.join(item.get('text', '') for item in final.get('content', []) if item.get('type') == 'text')[-350:]
    return summary, sum(m.get('usage', {}).get('totalTokens', 0) for m in messages), tools


def budget_candidate(logfile, candidate, before):
    """只识别受信任 guard 的预算中止；返回的候选仍必须通过独立验收。"""
    reason = None; writes = tools = 0
    for line in logfile.read_text(errors='replace').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get('type') == 'guard_limit':
            reason = entry.get('reason')
        if entry.get('type') == 'tool_execution_end':
            tools += 1
            if entry.get('toolName') in ('edit', 'write') and entry.get('isError') is False:
                writes += 1
    changed = any((candidate/'game'/name).is_file() and (candidate/'game'/name).read_bytes() != original
                  for name, original in before.items())
    if reason not in ('model_turns', 'tool_calls', 'repeated_tool_error') or not writes or not changed:
        return None
    return '模型达到阶段预算；保留实际写入的候选，由独立回归和改进证明决定是否发布。', tools


def validate_runtime(cfg, candidate, baseline, outputs, suite, core_only=False):
    outputs.mkdir(parents=True, exist_ok=True)
    os.chown(outputs, 1000, 1000)
    plan = outputs.parent / 'contracts.json'
    atomic(plan, suite)
    syntax_files = '/workspace/game/core.js' if core_only else '/workspace/game/*.js'
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
            'sh', '-c', f'for f in {syntax_files}; do node --check "$f" || exit 1; done; node --test /harness/core.test.mjs && node /harness/features.mjs' + ('' if core_only else ' && node /harness/browser.mjs')]
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
    # 六次失败后的固定任务只在发布基线变化后重试；同一源码不能每小时死循环。
    deferred = next((item for item in state.get('deferred_tasks', [])
                     if item['task'] != 'self-review' and item['after'] <= time.time()
                     and item.get('base_version', version) != version), None) if cursor >= len(TOPICS) else None
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
    detail = review_lens(cursor)[1] if cp['task'] == 'self-review' else TOPICS[cursor % len(TOPICS)][1]
    files = files or checkpoint_files(cp)
    if files == ['acceptance.json']:
        return acceptance_prompt(cp, candidate)
    instruction = VIEW_PLANS[cp['task']] if cp['task'] in VIEW_PLANS and files != ['core.js'] else PLANS.get(cp['task'], (None, detail))[1]
    text = f"Task ID: {cp['task']}. {instruction}\n"
    text += 'Edit only ' + ', '.join('game/'+f for f in files) + '. Source is included below; do not survey other files. Make minimal edits and finish normally. Preserve all existing behavior.\n'
    text += 'Workspace root is /workspace. Supporting source is READ-ONLY: '+', '.join('game/'+f for f in SOURCE_FILES if f not in files)+'. At most FOUR permitted targeted reads if needed, default 120 lines (maximum 160); directory reads and NOTES.md are unavailable. The current small dependency files can be read completely. Use the supplied source first.\n'
    text += 'CONTROL CONTRACT: #start is ONE existing button. Its initial visible text is 开始游戏 →; main.js changes the SAME button to 再次出航 → on game over. Keep its published HTML markup, without aria-label or aria-labelledby; REMOVE those attributes if an earlier candidate introduced them. Do not add a separate restart button. Preserve every existing button name. Add explanations to a separate paragraph instead.\n'
    if cp['task'] in PLANS:
        text += 'The controller supplies acceptance.json and records your summary in NOTES.md. You only edit game source; do not write metadata.\n'
        if files == ['core.js']:
            text += 'CURRENT STAGE: implement ONLY the game rules and state in core.js. Store all new state in createState() and update it inside step(); do not introduce UI callbacks or extra parameters. Core feature tests must pass before proceeding.\n'
        elif cp['task'] == 'wave-feedback':
            text += '''CURRENT STAGE: core.js has ALREADY passed wave tests and exposes state.waveNotice={text,life}.
Make exactly TWO disjoint edits in ONE edit call, then finish with a short summary:
1. Use oldText exactly "const pauseMsg=document.createElement('div');". Preserve it and append creation of a hidden div with id="wave-notice" to canvas.parentNode. Use individual style properties like the pause message; pointerEvents='none'.
2. Use oldText exactly "step(state,dt,input);render(ctx,state);". Preserve it and append the notice update. Use the exact condition: state.phase==='playing' && state.waveNotice?.life>0 && state.waveNotice.text. The lifetime field is state.waveNotice.life. Display state.waveNotice.text when this condition is true, hide otherwise. Set BOTH hidden and style.display consistently.
Do not edit launch(), togglePause(), game-over or keyboard/touch handlers: the per-frame update already handles ready, pause, over and restart. No new timer, lastWave, callback, or core changes. The two short anchors above exist verbatim; do not include neighboring lines in oldText.\n'''
        elif cp['task'] in VIEW_PLANS:
            text += 'CURRENT STAGE: core.js has ALREADY passed its feature tests and exposes the fields specified above. Only implement display/controls in this file; do not duplicate game rules in the UI.\n'
    else:
        text += 'IMPLEMENTATION STAGE ONLY. Do not write acceptance.json or NOTES.md. A separate stage will produce the checks after this source passes regression. Choose one small observable improvement; preserve all existing behavior.\n'
        text += 'The controller measures real visible UI/Canvas differences and supplies proof automatically when possible. Prefer an actual visible instruction, useful accessibility attribute, or clear rendering improvement. A JS variable name is NOT an element ID. Adding only an ID, comments or unused state is not an improvement.\n'
        text += 'Choose a change provable by a core state scenario (initial time/score/entities plus steps), real UI text/visibility/accessibility attributes, or a Canvas difference visible in the ready/first playing frame. Late-only cosmetic changes cannot be proved by a default first-frame screenshot.\n'
        text += 'Supported UI proof attributes: '+', '.join(UI_ATTRIBUTES)+'. Do not choose an improvement requiring unsupported checks.\n'
        if 'main.js' in files:
            text += 'FIXED BUTTON CONTRACTS: #shield-btn aria-label MUST remain 启动护盾 in every phase. #pause aria-label must be 暂停游戏 while playing and 继续游戏 while paused. Never replace these names with status text. Convey extra status using aria-pressed, aria-describedby, title, or a separate visible element with an ID. Preserve the original accessible names exactly.\n'
    if cp['task']=='shield' and files==['core.js']:
        text += 'Existing hit-feedback tests trigger ordinary collision with no shield input. Preserve damageFlash for unshielded hits. Only input.shield activates the shield.\n'
    if cp['task']=='boss' and files==['core.js']:
        text += 'The next spawn at time=120 must be an elite. Decrement EACH elite enemy.telegraph in step() to zero; a separate global timer does not expire enemy.telegraph. During warning it cannot deal contact damage; after warning it must be able to deal damage. Bullet hits subtract HP and defeat it only at zero. Keep the ORIGINAL normal spawn condition and schedule: a boss must never disable normal/scout spawns. Store any new flags in createState(). Preserve time-based waves.\n'
    if cp['task']=='difficulty':
        text += 'Late scenes also contain a slow boss. Check ordinary enemies by kind, never assume enemies[0] is normal. The acceptance proof checks the actual spawn interval; retained feature tests separately verify normal <=180, scout <=220, early-game speeds and continued spawning.\n'
    for name in files:
        content = (candidate/'game'/name).read_text()
        limit = 24000 // len(files)
        if len(content) > limit:
            content = content[:limit//2]+'\n[...middle omitted; use a targeted read if needed...]\n'+content[-limit//2:]
        text += f'\nCURRENT /workspace/game/{name}:\n```\n{content}\n```\n'
    text += '\nOwner feedback (within fixed contracts):\n' + controls().get('feedback', '')[-800:]
    if cp['task'] == 'self-review':
        recent = load(BASE/'public/state.json', {}).get('history', [])
        text += '\nRecent accepted changes; do not repeat them:\n'+'\n'.join(e.get('summary', '')[:350] for e in recent if e.get('result') == 'accepted')[-1600:]
        feedback = load(BASE/'public/state.json', {}).get('review_feedback', {}).get(','.join(checkpoint_files(cp)), {})
        if feedback.get('hint') and feedback.get('baseline') == cp.get('base_version'):
            text += '\nThis focus failed previously on the SAME published source. Avoid repeating that approach: '+feedback['hint']+'\n'
    if cp.get('error'):
        text += '\nREPAIR ONLY: the included source is the CURRENT candidate. Preserve working parts; fix only the specific failed assertion with the smallest edit. Do not reimplement the whole feature.\nPrevious validation error:\n' + cp['error'][-1400:]
    return text


def acceptance_prompt(cp, candidate):
    # 实现与验收分离，验收阶段只有声明文件可写，不能为迎合测试改动源码。
    text = f"Task ID: {cp['task']}. ACCEPTANCE STAGE ONLY. The implementation already passed retained regression. Write ONLY game/acceptance.json with ONE write call, then finish. Do not edit source or NOTES.md.\n"
    text += 'Read the actual changed source below. Describe 1-4 checks, with at least ONE that passes on the candidate and fails on the old source. Do not invent selectors or state fields. Do not claim an unchanged field proves improvement.\n'
    text += 'Prefer ONE minimal, reliable check proving the change. Additional checks must be necessary and actually true in their specified scene, not speculative behavior. Attribute values are strings or null, not booleans.\n'
    text += 'JSON object keys: task (exact task ID above), checks (array length 1-4). No Markdown in the file, no comments, no executable test code.\n'
    text += 'Each check has kind and op (eq/gt/gte/lt/lte/contains/exists), plus scalar value if needed. Core: kind=core, path=actual state field path, optional initial object, steps integer 1-4000 (never 0), input object. Core runs in playing at 1/60 seconds. Allowed initial keys: time, score, hp, spawn, shot, player, enemies, pickups, shield, level, bullets. Allowed input keys: x,y,shield,reducedMotion.\n'
    text += 'UI: kind=ui, selector=ONE real element ID from the source, phase=ready/playing/paused, property=text/visible/attribute. For attribute also set attribute='+ '/'.join(UI_ATTRIBUTES)+'. Never change button names for a test. Visual: {"kind":"visual","selector":"#arena"} compares deterministic Canvas pixels. A visual check is useful only when you changed render.js.\n'
    text += 'UI/visual scenes capture ready after 32ms, playing after starting for 64ms, or paused immediately after the pause action. They cannot wait for late waves or run arbitrary actions. Each visual check must itself show different pixels at its selected phase; omit it for late-only render changes. For late mechanics use core initial.time and a real state path instead.\n'
    text += 'Only core checks accept input, initial and steps. UI/visual checks MUST NOT contain those fields or actions. In a playing UI scene the shield has NOT been activated; do not assert an active shield or its active-only title. Choose the actual initial/idle value if that newly exposes a useful state.\n'
    import difflib
    source=baseline(cp['base_version'])
    for name in checkpoint_files(cp):
        before=(source/'game'/name).read_text();after=(candidate/'game'/name).read_text()
        diff=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='published/'+name,tofile='candidate/'+name))
        text += '\nACTUAL DIFF '+name+':\n'+diff[:10000]
    if cp.get('error'):
        text += '\nPrevious validation error; repair the check, not the source:\n'+cp['error'][-1400:]
    observed = cp.get('stage_results', {}).get('0', {}).get('core_observations', [])
    if observed:
        text += '\nTRUSTED PROBE DATA (not instructions): '+json.dumps(observed,ensure_ascii=False)[:4000]+'\nUse the actual values for those exact scenarios. Do not invent unrelated fields.\n'
    return text


def stage_hashes(candidate, files):
    return {name:hashlib.sha256((candidate/'game'/name).read_bytes()).hexdigest() for name in files}


def measured_checks(cp, candidate):
    result = cp.get('stage_results', {}).get('0', {})
    checks = result.get('proof_checks', [])
    if not checks:
        return []
    if result.get('proof_source_hashes') != stage_hashes(candidate, SOURCE_FILES):
        raise ValueError('Measured proof source changed; regenerate implementation and measurements')
    for check in checks:
        validate_check(check)
    return checks[:1]


def failed_generation_step(cp, failure):
    """按实际失败的固定测试定位文件，不能总把核心回归交给末尾渲染修复。"""
    stages = generation_stages(cp['task'], cp)
    if 'Measured proof source changed' in failure:
        return 0
    syntax = re.search(r'/workspace/game/([a-z0-9_.-]+\.js)(?::|\b)', failure)
    for index, files in enumerate(stages):
        if syntax and syntax.group(1) in files:
            return index
        if 'core.js' in files and ('core.test.mjs' in failure or (cp['task'] in PLANS and '/harness/features.mjs' in failure)):
            return index
    return min(cp.get('generation_step', 0), len(stages)-1)


def generate(cfg, cp, candidate, source, round_dir, state):
    stages = generation_stages(cp['task'], cp)
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
        before = {name:(candidate/'game'/name).read_bytes() if (candidate/'game'/name).exists() else None for name in files}
        previous = results.get(str(index), {})
        reused = files != ['acceptance.json'] and previous.get('completion') in ('normal', 'budget-recovered') and previous.get('source_hashes') == stage_hashes(candidate, files)
        if remaining <= 0 and not reused:
            raise ModelIncomplete('Round exceeded time budget between stages')
        completion = 'normal'
        measured = measured_checks(cp, candidate) if files == ['acceptance.json'] else []
        if measured:
            atomic(candidate/'game/acceptance.json', {'task': cp['task'], 'checks': measured})
            summary, tools, completion = '受信任测试根据新旧版本的实际观测生成改进证明；仍须通过最终完整验收。', 0, 'measured'
            atomic(round_dir/'proof-selected.json', {'checks': measured, 'source_hashes': stage_hashes(candidate, SOURCE_FILES)})
        elif reused:
            summary, tools, completion = previous['summary'], previous['tools'], previous['completion']
            state['phase'] = 'testing'; save(state)
        else:
            try:
                try:
                    summary, _, tools = run_agent({**cfg, 'round_timeout': remaining}, candidate, make_prompt(cp, candidate, files), logfile, state, files=files)
                except ModelIncomplete:
                    recovered = budget_candidate(logfile, candidate, before) if logfile.exists() else None
                    if recovered is None:
                        raise
                    validate_tree(candidate, [Path(cfg['model_key']).read_text().strip(), (BASE/'control/api-key').read_text().strip()])
                    summary, tools = recovered
                    completion = 'budget-recovered'
            finally:
                tokens, output = agent_usage(logfile)
                state['tokens'] += tokens
                state['output_tokens'] = state.get('output_tokens', 0)+output
                atomic(round_dir/f'usage-{index}.json', {'tokens': tokens, 'output_tokens': output})
                save(state)
        if files == ['acceptance.json']:
            proposal(candidate, cp['task'])
        elif cp['task'] not in PLANS:
            validate_tree(candidate, [Path(cfg['model_key']).read_text().strip(), (BASE/'control/api-key').read_text().strip()])
            validate_controls(candidate)
            if fingerprint(candidate) == fingerprint(source):
                raise ValueError('No game source changes; do not proceed to acceptance without an implementation')
            approved = load(BASE/'contracts.json', {'checks': [], 'features': []})
            state['phase'] = 'testing'; save(state)
            validate_runtime(cfg, candidate, source, round_dir/f'stage-{index}',
                             {'checks': approved['checks'], 'new_checks': [], 'features': approved['features'],
                              'discover_proofs': True, 'proof_files': files})
        elif files == ['core.js']:
            approved = load(BASE/'contracts.json', {'checks': [], 'features': []})
            check = PLANS[cp['task']][2]
            state['phase'] = 'testing'; save(state)
            validate_runtime(cfg, candidate, source, round_dir/f'stage-{index}',
                             {'checks': approved['checks']+[check], 'new_checks': [check],
                              'features': sorted(set(approved['features']+[cp['task']]))}, core_only=True)
        elif index < len(stages)-1:
            # 三阶段任务的操作层也必须验收，不能把它的错误留给末尾渲染层重试。
            approved = load(BASE/'contracts.json', {'checks': [], 'features': []})
            check = PLANS[cp['task']][2]
            state['phase'] = 'testing'; save(state)
            validate_runtime(cfg, candidate, source, round_dir/f'stage-{index}',
                             {'checks': approved['checks']+[check], 'new_checks': [check],
                              'features': sorted(set(approved['features']+[cp['task']]))})
        results[str(index)] = {'summary': summary, 'tools': tools, 'completion': completion,
                               'source_hashes': stage_hashes(candidate, files), 'reused': bool(reused)}
        if cp['task'] not in PLANS and files != ['acceptance.json']:
            catalog_file = round_dir/f'stage-{index}/proof-catalog.json'
            catalog = load(catalog_file, {'checks': []})
            if catalog_file.exists() and not catalog['checks'] and 'core.js' not in files:
                raise ValueError('No measured UI/Canvas improvement in ready/playing/paused scenes. Choose a different observable change; do not invent acceptance checks for unchanged output.')
            results[str(index)]['proof_checks'] = catalog['checks'][:4]
            results[str(index)]['proof_source_hashes'] = stage_hashes(candidate, SOURCE_FILES)
            results[str(index)]['core_observations'] = catalog.get('core_observations', [])
        cp['generation_step'] = index+1
        cp.pop('error', None)
        checkpoint(cp)
    return '\n'.join(results[str(i)]['summary'] for i in range(len(stages))), sum(results[str(i)]['tools'] for i in range(len(stages)))


def delay(failures):
    return min(3600, 120 * 2**min(failures, 5))


def event(state, entry):
    state['history'] = (state['history'] + [entry])[-60:]


def remember_failure(state, cp, failure):
    if cp['task'] != 'self-review':
        return
    if 'Control contract:' in failure or 'getByRole' in failure:
        hint = 'Preserve existing start/pause/restart buttons and names. Never add a static label to #start. Add help in a separate element.'
    elif 'undefined' in failure or 'explicit value' in failure:
        hint = 'Comparisons require a real value with the correct type. A missing value or unrelated state path cannot prove improvement.'
    elif 'Acceptance failed:' in failure or 'UI acceptance failed:' in failure:
        hint = 'Use actual measured DOM IDs and state values. Do not confuse JS variable names with IDs or invent a scenario.'
    elif 'No new acceptance check' in failure or 'No game source changes' in failure or 'No measured UI/Canvas improvement' in failure:
        hint = 'The previous edit had no observable improvement. Choose a different missing feature visible in a supported scene.'
    else:
        hint = 'Use one small edit with short exact anchors copied from current source. Do not repeat broad rewrites or extra reads.'
    key = ','.join(checkpoint_files(cp))
    feedback = state.setdefault('review_feedback', {})
    previous = feedback.get(key, {})
    feedback[key] = {'baseline': cp['base_version'], 'hint': hint, 'count': previous.get('count', 0)+1}


def main():
    cfg = load(BASE / 'config.json')
    secrets = [Path(cfg['model_key']).read_text().strip(), (BASE / 'control/api-key').read_text().strip()]
    state = release.recover(BASE) or load(BASE / 'public/state.json', {'round': 0, 'accepted': 0, 'rejected': 0, 'history': [], 'tokens': 0})
    state.update({'started_at': cfg['started_at'], 'deadline': cfg['deadline'], 'model': 'qwen3.6-35b-a3b-q3_k_m',
                  'workflow_version': 8, 'limits': {'round_minutes': cfg['round_timeout']//60, 'model_cpus': 2, 'agent_cpus': .5, 'max_model_turns_per_stage': 8, 'max_stages': 3, 'max_reads_per_stage': 4, 'read_lines': 120}})
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
                budget_recovered = any(item.get('completion')=='budget-recovered' for item in cp.get('stage_results', {}).values())
                if budget_recovered:
                    summary = '预算恢复候选已通过完整验收并发布。'+summary
                version = release.prepare_commit(BASE, candidate, cp['base_version'], number, task)
                release_id = f'{number:05d}-{version[:12]}'
                next_state = json.loads(json.dumps(state))
                next_state.update({'accepted': state['accepted']+1, 'last_release': version[:12], 'release_id': release_id,
                                   'generation_file': None, 'generation_step': None,
                                   'pause_overlay': 'pause-overlay' in features, 'accepted_features': features,
                                   'topic_index': cp.get('next_cursor', cp['cursor']+1), 'phase': 'cooldown', 'failure_count': 0,
                                   'retry_at': time.time()+cfg['cooldown'], 'updated_at': time.time()})
                event(next_state, {'round': number, 'task': task, 'result': 'accepted', 'version': version[:12],
                                   'summary': summary[:350], 'tool_calls': cp['tools'],
                                   'reused_stages': [i for i,item in cp.get('stage_results', {}).items() if item.get('reused')],
                                   'budget_recovered': budget_recovered, 'at': time.time()})
                next_state['history'][-1]['acceptance_source'] = ('measured' if any(item.get('completion')=='measured' for item in cp.get('stage_results', {}).values()) else 'model-or-plan')
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
                        cp['generation_step'] = (min(cp.get('generation_step', 0), len(generation_stages(task, cp))-1)
                                                 if incomplete else failed_generation_step(cp, failure))
                        cp.get('stage_results', {}).pop(str(cp['generation_step']), None)
                        if task not in PLANS and ('No new acceptance check' in failure or 'No game source changes' in failure):
                            # 声明格式问题只重试声明；无法证明改进时则回到实现，不能反复捏造用例。
                            cp.pop('candidate', None)
                            reset_generation(cp)
                    # 所有失败共用回退阈值，不能因错误分类而无限复用坏候选。
                    if cp['attempt_failures'] % 3 == 0:
                        if cp.get('generation_step', 0) == 0:
                            cp.pop('candidate', None)
                            reset_generation(cp)
                        # 后置阶段的文件会在重试前单独恢复，已经验收的前置阶段保留。
                        cp['resets'] = cp.get('resets', 0)+1
                checkpoint(cp)
                if result != 'interrupted':
                    remember_failure(state, cp, failure)
                event(state, {'round': number, 'task': task, 'result': result,
                              'summary': public_failure(failure), 'failure_kind': type(error).__name__,
                              'baseline_reset': 'candidate' not in cp, 'at': time.time()})
                if result != 'interrupted' and cp.get('attempt_failures', 0) >= (3 if task == 'self-review' else 6):
                    # 不把失败记成成功；先处理后续任务，完成首轮任务表后再尝试此项。
                    if task != 'self-review':
                        state['deferred_tasks'].append({'cursor': cp['cursor'], 'task': task,
                                                        'base_version': cp['base_version'], 'after': time.time()+3600})
                    state['topic_index'] = cp.get('next_cursor', cp['cursor']+1)
                    state['history'][-1].update({'result': 'deferred', 'summary': ('本次自审连续三次未完成，保留失败记录并切换关注点。' if task == 'self-review' else '本任务连续六次未完成，保留记录，等待发布基线更新后再尝试；先继续其他任务。')})
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
