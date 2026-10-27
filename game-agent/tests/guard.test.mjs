import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import guard, {checkFile, alignIndentation, acceptanceError} from '../guard.mjs';

test('第 22/24 轮的缩进差异只在唯一匹配时对齐，不猜测代码内容', () => {
  const source=" const secs=1;document.querySelector('#wave').textContent=`第 ${state.wave} 波`;\n if(state.phase==='over' && overlay.hidden){overlay.hidden=false;}";
  const old="document.querySelector('#wave').textContent=`第 ${state.wave} 波`;\nif(state.phase==='over'";
  const exact="document.querySelector('#wave').textContent=`第 ${state.wave} 波`;\n if(state.phase==='over'";
  assert.equal(alignIndentation(source,old),exact);
  assert.equal(alignIndentation(source+'\n'+source,old),old);
  assert.equal(alignIndentation(source,old.replace('state.wave','state.other')),old.replace('state.wave','state.other'));
  assert.equal(alignIndentation(source,exact),exact);
  const repeated='const longEnoughUniqueLookingVariable = 1234567890;';
  const overlapping=` ${repeated}\n ${repeated}\n ${repeated}`, pair=`${repeated}\n${repeated}`;
  assert.equal(alignIndentation(overlapping,pair),pair);
});

test('保护已有入口、任务范围及符号链接，允许小范围编辑', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'starharbor-guard-'));
  try {
    fs.mkdirSync(path.join(root,'game'));
    fs.writeFileSync(path.join(root,'game/core.js'),'export function step() {}');
    fs.symlinkSync('/etc/passwd',path.join(root,'game/render.js'));
    const files=['game/core.js','game/render.js','game/acceptance.json'];
    const event=(toolName,name,extra={})=>({toolName,input:{path:name,...extra}});
    assert.equal(checkFile(event('write','game/core.js',{content:'placeholder'}),root,files).block,true);
    assert.equal(checkFile(event('write','game/index.html'),root,files).block,true);
    assert.equal(checkFile(event('read','../outside'),root,files).block,true);
    assert.equal(checkFile(event('edit','game/render.js'),root,files).block,true);
    assert.equal(checkFile(event('edit','game/core.js',{oldText:'x'.repeat(500),newText:'placeholder'}),root,files).block,true);
    assert.equal(checkFile(event('edit','game/core.js',{edits:[{oldText:'step()',newText:'step(s)'},{oldText:'x'.repeat(500),newText:'placeholder'}]}),root,files).block,true);
    assert.equal(checkFile(event('edit','game/core.js',{edits:[{oldText:'step()',newText:'step(s)'}]}),root,files),undefined);
    assert.equal(checkFile(event('edit','game/core.js',{oldText:'step()',newText:'step(s)'}),root,files),undefined);
    assert.equal(checkFile(event('write','game/acceptance.json',{content:'{}'}),root,files),undefined);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('Pi 内部重试重置 turnIndex 后仍累计回合预算', async () => {
  const handlers={};let aborted=0;
  guard({on:(event,callback)=>{handlers[event]=callback;}});
  for(let i=0;i<8;i++)await handlers.turn_start({turnIndex:0},{abort:()=>aborted++});
  assert.equal(aborted,0);
  await handlers.turn_start({turnIndex:0},{abort:()=>aborted++});
  assert.equal(aborted,1);
});

test('波次页面只允许两个插入点，禁止重复递减核心计时和删除 HUD', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'starharbor-wave-'));
  const anchors=["const pauseMsg=document.createElement('div');",'step(state,dt,input);render(ctx,state);'];
  try {
    fs.mkdirSync(root+'/game');fs.writeFileSync(root+'/game/main.js',anchors.join('\n')+'\nupdateWaveHud();');
    const event=added=>({toolName:'edit',input:{path:'game/main.js',edits:anchors.map((oldText,index)=>({oldText,newText:oldText+'\n'+(index?added:'const waveNotice={};')}))}});
    const good=event("if(state.phase==='playing'&&state.waveNotice.life>0){waveNotice.text=state.waveNotice.text;}");
    assert.equal(checkFile(good,root,['game/main.js'],anchors),undefined);
    const indented=event('showNotice();');indented.input.edits[1].oldText=' '+indented.input.edits[1].oldText;indented.input.edits[1].newText=' '+indented.input.edits[1].newText;
    assert.equal(checkFile(indented,root,['game/main.js'],anchors),undefined);
    assert.equal(indented.input.edits[1].oldText,anchors[1]);
    assert.equal(checkFile(event('state.waveNotice.life-=1;'),root,['game/main.js'],anchors).block,true);
    const third=event('showNotice();');third.input.edits.push({oldText:'updateWaveHud();',newText:'state.waveNotice.life=0;'});
    assert.equal(checkFile(third,root,['game/main.js'],anchors).block,true);
    const removed=event('showNotice();');removed.input.edits[0].newText='deleted';
    assert.equal(checkFile(removed,root,['game/main.js'],anchors).block,true);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('提前拒绝最近失败的步骤数组、过量用例、虚构选择器及错误 UI 字段', () => {
  const check={kind:'core',path:'hp',steps:1,op:'gt',value:0};
  const encode=checks=>JSON.stringify({task:'self-review',checks});
  assert.equal(acceptanceError(encode([check]),'self-review'),null);
  assert.match(acceptanceError(encode([{...check,steps:[{action:'click'}]}]),'self-review'),/INTEGER/);
  assert.match(acceptanceError(encode(Array(6).fill(check)),'self-review'),/1-4/);
  assert.match(acceptanceError(encode([{kind:'ui',selector:'#id',property:'text',op:'exists'}]),'self-review'),/placeholder/);
  assert.match(acceptanceError(encode([{kind:'ui',selector:'#arena',property:'style',op:'exists'}]),'self-review'),/property/);
  assert.equal(acceptanceError(encode([{kind:'ui',selector:'#shield-btn',property:'attribute',attribute:'aria-pressed',op:'eq',value:'false'}]),'self-review'),null);
  assert.match(acceptanceError(encode([{kind:'ui',selector:'#shield-btn',property:'attribute',attribute:'onclick',op:'exists'}]),'self-review'),/attribute/);
  assert.match(acceptanceError('{oops','self-review'),/valid JSON/);
  assert.match(acceptanceError(encode([check]),'onboarding'),/task/);
});

test('验收阶段源码只读，非法声明不能落盘', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'starharbor-metadata-'));
  try{
    fs.mkdirSync(root+'/game');fs.writeFileSync(root+'/game/core.js','const a=1;');
    const options={task:'self-review',readFiles:['game/core.js']},files=['game/acceptance.json'];
    assert.equal(checkFile({toolName:'read',input:{path:'game/core.js'}},root,files,[],options),undefined);
    assert.equal(checkFile({toolName:'edit',input:{path:'game/core.js'}},root,files,[],options).block,true);
    const event={toolName:'write',input:{path:'game/acceptance.json',content:'{"task":"self-review","checks":[]}'}};
    assert.equal(checkFile(event,root,files,[],options).block,true);
    assert.equal(fs.existsSync(root+'/game/acceptance.json'),false);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('自审可定向读取辅助源码，但只能编辑本阶段文件', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'starharbor-context-'));
  try {
    fs.mkdirSync(root+'/game');
    fs.writeFileSync(root+'/game/core.js','const a=1;');
    fs.writeFileSync(root+'/game/main.js','const b=2;');
    const options={readFiles:['game/core.js','game/main.js']},files=['game/core.js'];
    assert.equal(checkFile({toolName:'read',input:{path:root+'/game/main.js'}},root,files,[],options),undefined);
    assert.equal(checkFile({toolName:'edit',input:{path:root+'/game/main.js'}},root,files,[],options).block,true);
    const failure=checkFile({toolName:'read',input:{path:root+'/game'}},root,files,[],options);
    assert.match(failure.reason,/Stage writable paths: game\/core.js/);
    assert.match(failure.reason,/Read-only source paths: game\/core.js, game\/main.js/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('UI 声明不能静默忽略核心输入或自行发明场景动作', () => {
  const check={kind:'ui',selector:'#shield-btn',phase:'playing',property:'attribute',attribute:'aria-pressed',op:'eq',value:'false'};
  const validate=c=>acceptanceError(JSON.stringify({task:'self-review',checks:[c]}),'self-review');
  assert.equal(validate(check),null);
  for (const extra of [{input:{shield:true}},{initial:{shield:{active:true}}},{steps:30},{actions:['click']}]) {
    assert.match(validate({...check,...extra}),/do not simulate custom inputs/);
  }
  assert.match(validate({kind:'visual',selector:'#arena',input:{shield:true}}),/Unsupported visual check fields/);
});

test('缺少比较值、数值类型错误和布尔属性值在写文件前拒绝',()=>{
  const check={kind:'core',path:'wave',op:'gte'};
  const validate=c=>acceptanceError(JSON.stringify({task:'self-review',checks:[c]}),'self-review');
  assert.match(validate(check),/explicit value/);
  for(const value of [null,true,'1'])assert.match(validate({...check,value}),/numeric value/);
  assert.equal(validate({...check,value:1}),null);
  assert.match(validate({kind:'ui',selector:'#arena',property:'attribute',attribute:'aria-pressed',op:'eq',value:false}),/string value/);
});

test('连续三次无效工具请求提前终止，避免重复读文件耗尽半小时', async () => {
  const handlers={};let aborted=0;
  guard({on:(event,callback)=>{handlers[event]=callback;}});
  for(let i=0;i<3;i++)await handlers.tool_call({toolName:'read',input:{path:'/workspace/forbidden'}},{abort:()=>aborted++});
  assert.equal(aborted,1);
});

test('依赖文件可完整读取，拒绝路径不占有效读取额度且第五次有效读取被限制',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'starharbor-read-budget-'));
  const previous=process.env.STARHARBOR_FILES;
  try{
    fs.mkdirSync(root+'/game');fs.writeFileSync(root+'/game/core.js',Array.from({length:130},(_,i)=>`// line ${i}`).join('\n'));
    process.env.STARHARBOR_FILES=JSON.stringify(['game/core.js']);
    const handlers={};guard({on:(name,callback)=>{handlers[name]=callback;}},{root});
    const ctx={abort:()=>assert.fail('A single refused read must not abort')};
    assert.equal((await handlers.tool_call({toolName:'read',input:{path:'outside'}},ctx)).block,true);
    for(let i=0;i<4;i++){
      const event={toolName:'read',input:{path:'game/core.js'}};
      assert.equal(await handlers.tool_call(event,ctx),undefined);
      assert.equal(event.input.limit,120);
    }
    assert.match((await handlers.tool_call({toolName:'read',input:{path:'game/core.js'}},ctx)).reason,/Four permitted reads/);
    const event={toolName:'read',input:{path:'game/core.js',limit:10000}};
    checkFile(event,root,['game/core.js']);assert.equal(event.input.limit,160);
  }finally{
    if(previous===undefined)delete process.env.STARHARBOR_FILES;else process.env.STARHARBOR_FILES=previous;
    fs.rmSync(root,{recursive:true,force:true});
  }
});
