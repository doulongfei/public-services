import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {coreCheck} from './checks.mjs';
const suite=JSON.parse(await fs.readFile(process.env.CONTRACT_FILE||'/run/contracts.json','utf8'));
const root=process.env.GAME_ROOT||'/workspace/game',oldRoot=process.env.BASELINE_ROOT||'/baseline/game';
const core=await import(root+'/core.js'),old=await import(oldRoot+'/core.js');
const {createState,step}=core;
const playing=()=>Object.assign(createState(),{phase:'playing',spawn:999,shot:999});
for(const feature of suite.features){
 if(feature==='enemy-types'){
  const s=playing(),types=new Map();
  for(let i=0;i<40;i++){
   s.spawn=0;s.hp=100;s.phase='playing';step(s,.02,{},()=>((i*7)%19)/19);
   for(const e of s.enemies)if(typeof e.kind==='string')types.set(e.kind,`${e.speed}:${e.r}`);
  }
  assert.ok(types.size>=2,'Enemy variety needs at least two enemies[].kind');
  assert.ok(new Set(types.values()).size>=2,'Enemy types must differ in speed or size');
 }
 if(feature==='health-pickup'){
  const s=playing();assert.ok(Array.isArray(s.pickups),'Expose live pickups array');s.hp=50;
  s.pickups.push({kind:'health',x:s.player.x,y:s.player.y,r:8,life:3});
  step(s,.02,{});assert.ok(s.hp>50&&s.hp<=100,'Health pickup must heal and cap HP');
  assert.equal(s.pickups.length,0,'Collected pickup must be removed');
  s.pickups.push({kind:'health',x:20,y:20,r:8,life:.01});step(s,.05,{});
  assert.equal(s.pickups.length,0,'Expired pickup must be removed');
 }
 if(feature==='weapon-upgrade'){
  const shot=(score)=>{const s=playing();s.score=score;s.shot=0;s.enemies=[{x:100,y:100,hp:100,r:11,speed:0}];step(s,.02,{});return s;};
  const a=shot(0),b=shot(100);
  assert.ok(Number.isFinite(a.level)&&b.level>a.level,'Kills must increase weapon level');
  assert.ok(b.shot<a.shot||b.bullets.length>a.bullets.length||(b.bullets[0]?.damage||1)>(a.bullets[0]?.damage||1),'Upgrade must affect firing');
 }
 if(feature==='wave-feedback'){
  const s=playing();s.time=19.99;step(s,.05,{});
  assert.ok(s.waveNotice?.text&&s.waveNotice.life>0,'Wave transition must create a readable notice');
  for(let i=0;i<240;i++)step(s,.05,{});
  assert.ok(s.waveNotice.life<=0,'Wave notice must expire');
 }
 if(feature==='hit-feedback'){
  const s=playing();s.enemies=[{...s.player,hp:1,speed:0}];step(s,.02,{});
  assert.ok(s.hp<100&&s.damageFlash>0&&s.damageFlash<=.5,'Damage must cause a short bounded flash');
  for(let i=0;i<40;i++)step(s,.05,{});
  assert.ok(s.damageFlash<=0,'Damage feedback must expire');
 }
 if(feature==='boss'){
  const s=playing();s.time=120;s.spawn=0;step(s,.02,{},()=>.5);
  const boss=s.enemies.find(e=>e.isBoss);
  assert.ok(boss&&boss.hp>1&&boss.r>11&&Number.isFinite(boss.telegraph),'Late wave must spawn a telegraphed elite');
 }
 if(feature==='shield'){
  const s=playing();assert.ok(s.shield,'Expose shield active/cooldown state');
  s.enemies=[{...s.player,hp:1,speed:0}];step(s,.02,{shield:true});
  assert.equal(s.hp,100,'Active shield must prevent collision damage');
  assert.ok(s.shield.active&&s.shield.cooldown>0,'Shield must activate and start cooldown');
  for(let i=0;i<400;i++)step(s,.05,{});
  assert.ok(!s.shield.active,'Shield must expire');
 }
}
const changed=[];
for(const check of suite.checks.filter(c=>c.kind==='core'))coreCheck(core,check);
for(const check of suite.new_checks.filter(c=>c.kind==='core')){
 try{coreCheck(old,check);}catch{changed.push(check);}
}
await fs.writeFile((process.env.OUTPUT_ROOT||'/output')+'/core-evidence.json',JSON.stringify(changed));
console.log('PASS: feature contracts and retained core checks');
