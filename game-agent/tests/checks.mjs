import assert from 'node:assert/strict';

// 同一批场景全部执行后统一反馈，避免模型每轮只能看见第一个错误断言。
export function assertUiResults(results) {
  const failures = [...results.entries()].filter(([, result]) => result.error);
  assert.equal(failures.length, 0, 'UI acceptance failed:\n'+failures.slice(0, 8)
    .map(([check, result]) => check.slice(0, 500)+': '+result.error.message.slice(0, 300)).join('\n'));
}

// 晚期第一个敌人可能是 Boss，必须按类型验证普通敌人与侦察机。
export function checkDifficulty(core) {
  for (const [seed, kind, cap, earlySpeed] of [[.1, 'scout', 220, 73], [.5, 'normal', 180, 53]]) {
    for (const time of [0, 1000]) {
      const state = Object.assign(core.createState(), {phase:'playing', time, spawn:0, shot:999});
      core.step(state, .02, {}, () => seed);
      const mobs = state.enemies.filter(enemy => !enemy.isBoss && enemy.kind === kind);
      assert.ok(mobs.length > 0, `${kind} enemies must still spawn at time=${time}`);
      assert.ok(mobs.every(enemy => Number.isFinite(enemy.speed) && enemy.speed > 0 && enemy.speed <= cap), `${kind} speed must be capped at ${cap}`);
      if (time === 0) assert.ok(mobs.every(enemy => enemy.speed === earlySpeed), `${kind} early speed must remain ${earlySpeed}`);
      assert.ok(state.spawn >= .35, 'Late spawn interval must remain fair');
      // 清掉当前敌人后仍须能继续生成，不能通过停刷来满足限速断言。
      state.enemies = []; state.spawn = 0;
      core.step(state, .02, {}, () => seed);
      assert.ok(state.enemies.some(enemy => !enemy.isBoss && enemy.kind === kind), `${kind} spawning must continue after a boss`);
    }
  }
}

export function checkValue(actual, check) {
  const expected=check.value;
  let ok=false;
  switch(check.op){
    case 'eq':ok=actual===expected;break;
    case 'gt':ok=typeof actual==='number'&&actual>expected;break;
    case 'gte':ok=typeof actual==='number'&&actual>=expected;break;
    case 'lt':ok=typeof actual==='number'&&actual<expected;break;
    case 'lte':ok=typeof actual==='number'&&actual<=expected;break;
    case 'contains':ok=typeof actual==='string'&&actual.includes(String(expected));break;
    case 'exists':ok=actual!==undefined&&actual!==null;break;
  }
  assert.ok(ok,`Acceptance failed: ${check.path||check.selector} ${check.op} ${JSON.stringify(expected)}; got ${JSON.stringify(actual)}`);
}

export function coreValue(core, check) {
  const state=core.createState();Object.assign(state,structuredClone(check.initial||{}));state.phase='playing';
  for(let i=0;i<(check.steps||1);i++)core.step(state,1/60,check.input||{},()=>.3);
  let value=state;
  for(const key of check.path.split('.'))value=value?.[key];
  return value;
}

export function coreCheck(core, check) {
  checkValue(coreValue(core,check),check);
}
