import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import guard, {checkFile, alignIndentation} from '../guard.mjs';

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
    assert.equal(checkFile(event('state.waveNotice.life-=1;'),root,['game/main.js'],anchors).block,true);
    const third=event('showNotice();');third.input.edits.push({oldText:'updateWaveHud();',newText:'state.waveNotice.life=0;'});
    assert.equal(checkFile(third,root,['game/main.js'],anchors).block,true);
    const removed=event('showNotice();');removed.input.edits[0].newText='deleted';
    assert.equal(checkFile(removed,root,['game/main.js'],anchors).block,true);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
