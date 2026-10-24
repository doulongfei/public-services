import {test} from 'node:test';
import assert from 'node:assert/strict';
const core=await import((process.env.GAME_ROOT||'/workspace/game')+'/core.js');
const {createState,WIDTH,HEIGHT}=core;
// 固定基础场景的随机输入，极端掉落情况单独显式验证。
const step=(state,dt,input={},random=()=>.5)=>core.step(state,dt,input,random);

test('ready and paused states do not advance simulation',()=>{
 const s=createState();step(s,.05,{x:1});assert.equal(s.time,0);
 s.phase='paused';step(s,.05,{x:1});assert.equal(s.time,0);
});
test('movement is bounded and independent from frame stalls',()=>{
 const s=createState();s.phase='playing';s.spawn=999;
 step(s,10,{x:1,y:1});assert.ok(s.time<=.051);assert.ok(s.player.x>450);
 for(let i=0;i<1000;i++)step(s,.05,{x:1,y:1});
 assert.ok(s.player.x<=WIDTH && s.player.y<=HEIGHT);assert.ok(Number.isFinite(s.player.x));
});
test('bullets damage enemies and earn score',()=>{
 const s=createState();s.phase='playing';s.spawn=999;s.shot=999;
 s.enemies=[{x:100,y:100,r:11,hp:1,speed:0}];s.bullets=[{x:100,y:100,vx:0,vy:0,life:1}];
 step(s,.01,{});assert.ok(s.score>=1);assert.equal(s.enemies.length,0);
});
test('collision and game over remain valid',()=>{
 const s=createState();s.phase='playing';s.spawn=999;s.shot=999;s.hp=1;
 s.enemies=[{...s.player,hp:1,speed:0}];step(s,.01,{});
 assert.equal(s.phase,'over');assert.equal(s.hp,0);
});
test('fatal collision cannot be undone by same-frame health pickups',()=>{
 for(const roll of [0,.1,.5,.99]){
  const s=createState();Object.assign(s,{phase:'playing',spawn:999,shot:999,hp:1});
  s.enemies=[{...s.player,hp:1,speed:0}];
  if(Array.isArray(s.pickups))s.pickups.push({kind:'health',x:s.player.x,y:s.player.y,r:8,life:3});
  step(s,.01,{},()=>roll);
  assert.equal(s.hp,0,'A lethal hit must remain lethal even when a health pickup overlaps');
  assert.equal(s.phase,'over','Health collection must not resurrect a defeated player');
 }
});
test('long simulation covers active play with bounded entity counts',()=>{
 let s=createState();s.phase='playing';let elapsed=0;
 for(let i=0;i<12000;i++){
  if(s.phase==='over'){s=createState();s.phase='playing';}
  const before=s.time;step(s,1/60,{x:Math.sin(i/100),y:Math.cos(i/100)},()=>.3);elapsed+=s.time-before;
  if(i%120===0){
   assert.ok(Number.isFinite(s.time));assert.ok(s.hp>=0&&s.hp<=200);
   assert.ok(s.enemies.length<=150&&s.bullets.length<300&&s.particles.length<1500);
  }
 }
 assert.ok(elapsed>199,'Simulation must exercise 200 seconds of actual gameplay');
});
