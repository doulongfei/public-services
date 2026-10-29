import {createState, step, WIDTH, HEIGHT} from './core.js';
import {render} from './render.js';

const canvas = document.querySelector('#arena');
const ctx = canvas.getContext('2d');
const overlay = document.querySelector('#overlay');
const start = document.querySelector('#start');
const pause = document.querySelector('#pause');
const shieldButton = document.querySelector('#shield-btn');
const shieldStatus = document.querySelector('#shield-status');
const pauseMessage = document.querySelector('#pause-message');
const waveNotice = document.querySelector('#wave-notice');
const timeLabel = document.querySelector('#time');
const scoreLabel = document.querySelector('#score');
const healthLabel = document.querySelector('#health');
const bestLabel = document.querySelector('#best');
const waveLabel = document.querySelector('#wave');
const statusBar = document.querySelector('#status-bar');
const gameStatus = document.querySelector('#game-status');
const keys = new Set();
let state = createState();
let last = performance.now();
let pointer = null;
let shieldRequested = false;

canvas.setAttribute('aria-label', '星港守卫游戏区域，使用 WASD 或方向键移动，手机拖动移动，武器自动射击。空格或 Shift 启动护盾，P 暂停或继续，Escape 暂停。');
const coarsePointer = matchMedia('(pointer: coarse)').matches;
document.querySelector('#first-run-hint').textContent = coarsePointer
 ? '单指拖动战区移动，点击「护盾」短暂免伤。'
 : '武器自动开火，空格 / Shift 启动短时护盾。';

const bestScore = {
 get() {
  try {
   const value = Number(localStorage.getItem('sh_best'));
   return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  } catch { return 0; }
 },
 set(value) { try { localStorage.setItem('sh_best', String(value)); } catch { /* 存储不可用时仍可正常游戏。 */ } }
};
bestLabel.textContent = String(bestScore.get());

// 仅在内容改变时更新文本，避免每帧重复触发 live region 播报。
function setText(element, text) {
 const value = String(text);
 if (element.textContent !== value) element.textContent = value;
}

function clearInput() {
 keys.clear();
 pointer = null;
 shieldRequested = false;
}

function launch() {
 clearInput();
 state = createState();
 state.phase = 'playing';
 last = performance.now();
 overlay.hidden = true;
 pauseMessage.hidden = true;
 canvas.focus({preventScroll: true});
 updateHud();
}

function togglePause() {
 if (state.phase === 'playing') state.phase = 'paused';
 else if (state.phase === 'paused') state.phase = 'playing';
 else return;
 clearInput();
 last = performance.now();
 updateHud();
}

function requestShield() {
 if (state.phase === 'playing' && !state.shield.active && state.shield.cooldown <= 0) shieldRequested = true;
}

start.addEventListener('click', launch);
pause.addEventListener('click', togglePause);
shieldButton.addEventListener('click', () => { requestShield(); canvas.focus({preventScroll: true}); });
window.addEventListener('keydown', event => {
 const key = event.key.toLowerCase();
 if (event.target.closest?.('input, textarea, select, [contenteditable="true"]')) return;
 if (key === 'p' && !event.repeat) { event.preventDefault(); togglePause(); return; }
 if (key === 'escape' && state.phase === 'playing') { event.preventDefault(); togglePause(); return; }
 // 按钮和链接保留原生的空格/回车操作，避免全局处理器重复触发。
 if (event.target.closest?.('button, a') || state.phase !== 'playing') return;
 if (event.code === 'Space' || key === 'shift') {
  event.preventDefault();
  if (!event.repeat) requestShield();
  return;
 }
 if (['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'].includes(key)) {
  event.preventDefault();
  keys.add(key);
 }
});
window.addEventListener('keyup', event => keys.delete(event.key.toLowerCase()));
function pauseOnLeave() { clearInput(); if (state.phase === 'playing') togglePause(); }
window.addEventListener('blur', pauseOnLeave);
document.addEventListener('visibilitychange', () => { if (document.hidden) pauseOnLeave(); });

canvas.addEventListener('pointerdown', event => {
 if (state.phase !== 'playing' || pointer || (event.pointerType === 'mouse' && event.button !== 0)) return;
 event.preventDefault();
 canvas.setPointerCapture(event.pointerId);
 pointer = {id: event.pointerId, x: event.clientX, y: event.clientY};
});
canvas.addEventListener('pointermove', event => {
 if (pointer?.id === event.pointerId) pointer = {id: event.pointerId, x: event.clientX, y: event.clientY};
});
for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
 canvas.addEventListener(name, event => { if (pointer?.id === event.pointerId) pointer = null; });
}

function readInput() {
 let input = {
  x: Number(keys.has('d') || keys.has('arrowright')) - Number(keys.has('a') || keys.has('arrowleft')),
  y: Number(keys.has('s') || keys.has('arrowdown')) - Number(keys.has('w') || keys.has('arrowup'))
 };
 if (pointer) {
  const rect = canvas.getBoundingClientRect();
  const dx = (pointer.x - rect.left) / rect.width * WIDTH - state.player.x;
  const dy = (pointer.y - rect.top) / rect.height * HEIGHT - state.player.y;
  const distance = Math.hypot(dx, dy);
  input = distance > 8 ? {x: dx / Math.max(40, distance), y: dy / Math.max(40, distance)} : {x: 0, y: 0};
 }
 // 键盘与触控共用 core.step 的持续时间和冷却校验，不直接改写 shield.active。
 input.shield = shieldRequested;
 shieldRequested = false;
 return input;
}

function updateHud() {
 const seconds = Math.floor(state.time);
 setText(timeLabel, String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0'));
 timeLabel.setAttribute('aria-label', '存活时间 ' + timeLabel.textContent);
 setText(scoreLabel, state.score);
 scoreLabel.setAttribute('aria-label', (state.phase === 'over' ? '游戏结束 得分 ' : '得分 ') + state.score);
 setText(healthLabel, state.hp);
 healthLabel.setAttribute('aria-label', '生命值 ' + state.hp);
 setText(waveLabel, `第 ${state.wave} 波`);
 waveLabel.setAttribute('aria-label', waveLabel.textContent);
 const playing = state.phase === 'playing';
 const paused = state.phase === 'paused';
 pause.disabled = !playing && !paused;
 setText(pause, paused ? '▶ 继续' : 'Ⅱ 暂停');
 pause.setAttribute('aria-label', paused ? '继续游戏' : '暂停游戏');
 pause.setAttribute('aria-pressed', String(paused));
 pauseMessage.hidden = !paused;
 const shield = state.shield;
 const shieldText = shield.active ? '护盾:生效中' : shield.cooldown > 0 ? `护盾:冷却 ${Math.ceil(shield.cooldown)}s` : '护盾:就绪';
 setText(shieldStatus, shieldText);
 shieldButton.disabled = !playing || shield.active || shield.cooldown > 0;
 shieldButton.setAttribute('aria-pressed', String(Boolean(shield.active)));
 shieldButton.title = shieldText + ' · 空格 / Shift / 点击';
 const showWave = playing && state.waveNotice.life > 0 && state.waveNotice.text;
 waveNotice.hidden = !showWave;
 if (showWave) setText(waveNotice, state.waveNotice.text + ' (剩余 ' + Math.ceil(state.waveNotice.life) + 's)');
 const status = playing ? '战斗进行中' : paused ? '航线已暂停' : state.phase === 'over' ? '等待再次出航' : '等待信号…';
 // 保留圆点节点，状态变更时才更新整条提示。
 if (statusBar.dataset.phase !== state.phase) {
  statusBar.replaceChildren(Object.assign(document.createElement('b'), {textContent: '●'}), ' 航道已连接 · ' + status);
  statusBar.dataset.phase = state.phase;
  setText(gameStatus, playing ? '游戏进行中' : paused ? '游戏已暂停' : state.phase === 'over' ? '游戏结束' : '等待开始');
 }
}

function finishGame() {
 clearInput();
 overlay.hidden = false;
 document.querySelector('#flight-guide').hidden = true;
 document.querySelector('#first-run-hint').hidden = true;
 setText(document.querySelector('#result-title'), '航道暂时失守');
 setText(document.querySelector('#result-detail'), `坚持 ${Math.floor(state.time)} 秒，获得 ${state.score} 分。再来一次？`);
 setText(start, '再次出航 →');
 if (state.score > bestScore.get()) bestScore.set(state.score);
 setText(bestLabel, bestScore.get());
 start.focus({preventScroll: true});
}

function frame(now) {
 const previous = state.phase;
 const dt = (now - last) / 1000;
 last = now;
 step(state, dt, readInput());
 render(ctx, state);
 if (previous !== 'over' && state.phase === 'over') finishGame();
 updateHud();
 requestAnimationFrame(frame);
}

// 固定只读快照接口供回归测试检查状态；游戏规则仍在 core.js 中运行。
window.starharbor = {snapshot: () => JSON.parse(JSON.stringify(state))};
updateHud();
requestAnimationFrame(frame);
