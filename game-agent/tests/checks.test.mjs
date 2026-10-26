import test from 'node:test';
import assert from 'node:assert/strict';
import {checkDifficulty, coreCheck, assertUiResults} from './checks.mjs';

test('一次报告多个 UI 断言错误，保留选择器、属性和实际值', () => {
  assertUiResults(new Map([['good', {passed:true}]]));
  const results=new Map([
    ['{"selector":"#shield-btn","attribute":"aria-pressed"}', {error:new Error('expected true, got false')}],
    ['{"selector":"#shield-btn","attribute":"title"}', {error:new Error('missing title text')}],
  ]);
  assert.throws(()=>assertUiResults(results), error=>/aria-pressed/.test(error.message)&&/title/.test(error.message)&&/got false/.test(error.message));
});

// Boss 排在普通敌人前面的回归场景，独立变更各项边界以检验验收器。
function fixture({normal=180, scout=220, interval=.35, early=0, stop=false}={}) {
  return {
    createState:()=>({enemies:[]}),
    step(state, _dt, _input, random) {
      if (stop && state.spawned) return;
      state.spawned = true;
      const kind = random() < .25 ? 'scout' : 'normal';
      if (state.time >= 120) state.enemies.push({isBoss:true, speed:42});
      state.enemies.push({kind, speed:state.time >= 120 ? (kind === 'scout' ? scout : normal) : (kind === 'scout' ? 73 : 53)+early});
      state.spawn = interval;
    },
  };
}

test('Boss 速度不能掩盖普通敌人超速，各类型与早期玩法独立验证', () => {
  checkDifficulty(fixture());
  for (const options of [{normal:200}, {scout:221}, {interval:.22}, {early:10}, {stop:true}]) {
    assert.throws(()=>checkDifficulty(fixture(options)), assert.AssertionError);
  }
});

test('旧版第一个敌人是低速 Boss 时，新证明仍能区分实际生成间隔', () => {
  const old=fixture({normal:303, scout:323, interval:.22});
  coreCheck(old, {initial:{time:1000, spawn:0}, path:'enemies.0.speed', op:'lte', value:220});
  const proof={initial:{time:1000, spawn:0}, path:'spawn', op:'gte', value:.35};
  assert.throws(()=>coreCheck(old, proof), assert.AssertionError);
  coreCheck(fixture(), proof);
});
