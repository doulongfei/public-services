import assert from 'node:assert/strict';

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

export function coreCheck(core, check) {
  const state=core.createState();Object.assign(state,structuredClone(check.initial||{}));state.phase='playing';
  for(let i=0;i<(check.steps||1);i++)core.step(state,1/60,check.input||{},()=>.3);
  let value=state;
  for(const key of check.path.split('.'))value=value?.[key];
  checkValue(value,check);
}
