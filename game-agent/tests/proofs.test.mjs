import test from 'node:test';
import assert from 'node:assert/strict';
import {uiProofs,coreProofs} from './proofs.mjs';

const node=(id,text,attributes={},visible=true,tag='p')=>({id,text,attributes,visible,tag});
test('界面证明只来自真实可见差异，忽略无变化、隐藏元素和只加 ID',()=>{
  assert.deepEqual(uiProofs([node('guide','旧说明')],[node('guide','旧说明')],'ready'),[]);
  assert.deepEqual(uiProofs([node('','旧说明')],[node('guide','旧说明')],'ready'),[]);
  assert.deepEqual(uiProofs([],[node('guide','隐藏说明',{},false)],'ready'),[]);
  const proof=uiProofs([node('guide','旧说明')],[node('guide','新的有效说明')],'ready');
  assert.deepEqual(proof,[{kind:'ui',selector:'#guide',phase:'ready',op:'contains',property:'text',value:'新的有效说明'}]);
  assert.deepEqual(uiProofs([],[],'playing'),[]);
  const revealed=uiProofs([node('guide','操作说明',{},false)],[node('guide','操作说明')],'ready');
  assert.deepEqual(revealed,[{kind:'ui',selector:'#guide',phase:'ready',op:'eq',property:'visible',value:true}]);
  const oldText='很长的已有说明。'.repeat(8)+'补给恢复20点生命。'+'其他说明。'.repeat(8);
  const newText=oldText.replace('20点','25点');
  const focused=uiProofs([node('guide',oldText)],[node('guide',newText)],'ready')[0];
  assert.ok(focused.value.length<60);assert.ok(newText.includes(focused.value));assert.ok(!oldText.includes(focused.value));
});

test('保护按钮名称、隐式角色和无效描述引用不能制造自动证明',()=>{
  assert.deepEqual(uiProofs([node('start','开始游戏 →')],[node('start','改名',{'aria-label':'改名'},true,'button')],'ready'),[]);
  assert.deepEqual(uiProofs([node('x','确定',{},true,'button')],[node('x','确定',{role:'button'},true,'button')],'ready'),[]);
  assert.deepEqual(uiProofs([node('guide','说明')],[node('guide','说明',{'aria-describedby':'ghost'})],'ready'),[]);
  const proof=uiProofs([node('guide','说明')],[node('guide','说明',{title:'新的帮助'})],'playing');
  assert.equal(proof[0].attribute,'title');assert.equal(proof[0].value,'新的帮助');
});

test('核心证明只接受既有玩法不变量的修复，不接受添加无用字段',()=>{
  const fixture=(clamp,expose=true)=>({
    createState:()=>({hp:100,shield:{active:false,cooldown:0},...(expose?{waveNotice:{life:2}}:{})}),
    step(state,dt){if(state.waveNotice)state.waveNotice.life=clamp?Math.max(0,state.waveNotice.life-dt):state.waveNotice.life-dt;},
  });
  const result=coreProofs(fixture(false),fixture(true));
  assert.equal(result.checks.length,1);assert.equal(result.checks[0].path,'waveNotice.life');
  assert.deepEqual(coreProofs(fixture(true),fixture(true)).checks,[]);
  assert.deepEqual(coreProofs(fixture(false,false),fixture(true)).checks,[]);
});
