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
