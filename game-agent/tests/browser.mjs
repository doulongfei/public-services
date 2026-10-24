import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium} from '/opt/runtime/node_modules/playwright/index.mjs';
import {checkValue} from './checks.mjs';

const root=process.env.GAME_ROOT||'/workspace/game',out=process.env.OUTPUT_ROOT||'/output';
const baseline=process.env.BASELINE_ROOT||'/baseline/game';
const suite=JSON.parse(await fs.readFile(process.env.CONTRACT_FILE||'/run/contracts.json','utf8'));
const prefix='/releases/00000-acceptance/game/';
const server=http.createServer(async(req,res)=>{
 try{
  const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  const old=pathname.startsWith('/old/');
  const gameRoot=old?baseline:root;
  const rel=old?pathname.slice(4):(pathname.startsWith(prefix)?pathname.slice(prefix.length-1):pathname);
  const target=path.resolve(gameRoot,'.'+rel);
  if(target!==gameRoot&&!target.startsWith(gameRoot+'/')){res.writeHead(403).end();return;}
  const file=target===gameRoot?path.join(gameRoot,'index.html'):target;
  let content=await fs.readFile(file);
  if(path.extname(file)==='.html')content=Buffer.from(content.toString().replace(/(<head\b[^>]*>)/i,`$1<base href="${old?'/old/':prefix}">`));
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'self'");
  res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json'})[path.extname(file)]||'application/octet-stream');res.end(content);
 }catch{res.writeHead(404).end();}
});
await new Promise(r=>server.listen(8085,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const errors=[],external=[];
try{
 for(const mobile of [false,true]){
  const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},isMobile:mobile,hasTouch:mobile,deviceScaleFactor:1});
  await context.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort();}
   if(url.pathname!=='/'&&!url.pathname.startsWith(prefix))external.push('Unversioned asset: '+url.pathname);
   return route.continue();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&/Content Security Policy|Refused to/.test(m.text()))errors.push(m.text());});
  await page.goto('http://127.0.0.1:8085/',{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();
  await page.waitForFunction(()=>window.starharbor?.snapshot().phase==='playing');
  const before=await page.evaluate(()=>window.starharbor.snapshot());
  if(mobile){
   const rect=await page.locator('#arena').boundingBox();const session=await context.newCDPSession(page);
   await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:rect.x+rect.width*.8,y:rect.y+rect.height*.5}]});
   await page.waitForFunction(x=>window.starharbor.snapshot().player.x>x+10,before.player.x);
   await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  }else{
   await page.keyboard.down('d');await page.waitForFunction(x=>window.starharbor.snapshot().player.x>x+10,before.player.x);await page.keyboard.up('d');
  }
  await page.getByRole('button',{name:'暂停游戏',exact:true}).click();
  await page.waitForFunction(()=>window.starharbor.snapshot().phase==='paused');
  if(suite.features.includes('pause-overlay'))await page.locator('#pause-message').waitFor({state:'visible'});
  const paused=await page.evaluate(()=>window.starharbor.snapshot().time);
  await page.evaluate(()=>new Promise(resolve=>{let n=0;function tick(){if(++n===15)resolve();else requestAnimationFrame(tick);}requestAnimationFrame(tick);}));
  assert.equal(await page.evaluate(()=>window.starharbor.snapshot().time),paused);
  await page.getByRole('button',{name:'继续游戏',exact:true}).click();
  await page.waitForFunction(t=>window.starharbor.snapshot().time>t,paused);
  if(suite.features.includes('pause-overlay'))await page.locator('#pause-message').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  const colors=await page.evaluate(()=>{
   const c=document.querySelector('canvas'),data=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
   const set=new Set();for(let i=0;i<data.length;i+=68)set.add(`${data[i]},${data[i+1]},${data[i+2]}`);return set.size;
  });assert.ok(colors>8,'Canvas must contain actual rendered gameplay');
  await page.screenshot({path:`${out}/${mobile?'mobile':'desktop'}.png`,fullPage:true});
  await context.close();
 }
 // 使用相同随机数、虚拟时钟和视口，注释/无用文件不会产生视觉差异。
 async function openCheck(old=false,storage=null,mobile=false){
  const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:900,height:800},isMobile:mobile,hasTouch:mobile,deviceScaleFactor:1});
  const page=await context.newPage();
  await context.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort();}
   return route.continue();
  });
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(({storage})=>{
   let seed=17;Math.random=()=>{seed=(seed*16807)%2147483647;return(seed-1)/2147483646;};
   if(storage==='blocked'){
    Storage.prototype.getItem=()=>{throw new Error('Storage unavailable');};
    Storage.prototype.setItem=()=>{throw new Error('Storage unavailable');};
   }else if(storage!==null&&localStorage.getItem('__starharbor_fixture_seeded')!=='1'){
    localStorage.setItem('sh_best',storage);localStorage.setItem('__starharbor_fixture_seeded','1');
   }
  },{storage});
  await page.clock.install({time:new Date('2026-01-01T00:00:00Z')});
  await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
  await page.goto('http://127.0.0.1:8085/'+(old?'old/':''));
  await page.clock.runFor(32);
  return {context,page};
 }
 async function readChecks(checks,old=false){
  const results=new Map();
  for(const phase of ['ready','playing','paused']){
   const group=checks.filter(c=>(c.phase||'ready')===phase);if(!group.length)continue;
   const {context,page}=await openCheck(old);
   try{
    if(phase==='playing'||phase==='paused'){
     await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();await page.clock.runFor(64);
    }
    if(phase==='paused'){
     await page.getByRole('button',{name:'暂停游戏',exact:true}).click();await page.clock.runFor(32);
    }
    for(const check of group){
     const element=page.locator(check.selector),key=JSON.stringify(check);
     if(check.kind==='visual')results.set(key,{image:await element.screenshot({animations:'disabled'})});
     else{
      const value=check.property==='visible'?await element.isVisible():(await element.count()?(check.property==='attribute'?await element.getAttribute(check.attribute):await element.textContent()):null);
      try{checkValue(value,check);results.set(key,{passed:true});}
      catch(error){results.set(key,{passed:false,error});}
     }
    }
   }finally{await context.close();}
  }
  return results;
 }
 const candidateResults=await readChecks(suite.checks.filter(c=>c.kind!=='core'));
 for(const result of candidateResults.values())if(result.error)throw result.error;
 const evidence=JSON.parse(await fs.readFile(out+'/core-evidence.json','utf8'));
 const previousResults=await readChecks(suite.new_checks.filter(c=>c.kind!=='core'),true);
 for(const check of suite.new_checks.filter(c=>c.kind!=='core')){
  const key=JSON.stringify(check),previous=previousResults.get(key),next=candidateResults.get(key);
  if(check.kind==='visual'){
   assert.ok(!previous.image.equals(next.image),'Visual proposal must change deterministic rendered arena');evidence.push(check);
  }else if(!previous.passed)evidence.push(check);
 }
 if(suite.new_checks.length)assert.ok(evidence.length,'No new acceptance check fails on the published baseline; comments/unused changes are not a release');
 if(suite.features.includes('accessibility')){
  const {context,page}=await openCheck();
  try{
   await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();await page.clock.runFor(32);
   assert.equal(await page.locator('#arena').getAttribute('tabindex'),'0','Arena must support keyboard focus');
   assert.ok(await page.locator('#arena').getAttribute('aria-label'),'Arena must have accessible instructions');
   assert.ok(await page.locator('#arena').evaluate(el=>document.activeElement===el),'Starting must move focus into the arena');
   await page.keyboard.press('Escape');await page.clock.runFor(32);
   assert.equal(await page.evaluate(()=>window.starharbor.snapshot().phase),'paused','Escape must pause');
   await page.keyboard.press('p');await page.clock.runFor(32);
   assert.equal(await page.evaluate(()=>window.starharbor.snapshot().phase),'playing','P must still resume');
  }finally{await context.close();}
 }
 if(suite.features.includes('onboarding')){
  const {context,page}=await openCheck();
  try{
   const guide=page.locator('#flight-guide');assert.ok(await guide.isVisible(),'Instructions must be visible before starting');
   const text=await guide.textContent();
   for(const term of ['15','30','P','补给'])assert.ok(text.includes(term),'Instructions must explain '+term);
  }finally{await context.close();}
 }
 if(suite.features.includes('best-score')){
  for(const storage of ['12345','invalid','blocked']){
   const {context,page}=await openCheck(false,storage);
   try{
    await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();await page.clock.runFor(64);
    assert.equal((await page.locator('#best').textContent()).trim(),storage==='12345'?'12345':'0','Best score must survive reload, invalid/unavailable storage');
    if(storage==='12345'){
     await page.reload();await page.clock.runFor(32);await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();await page.clock.runFor(32);
     assert.equal((await page.locator('#best').textContent()).trim(),'12345');
    }
   }finally{await context.close();}
  }
 }
 if(suite.features.includes('best-score')){
  const {context,page}=await openCheck(false,'0');
  try{
   await page.evaluate(()=>{Math.random=()=>.625;});
   await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();
   await page.clock.runFor(3000);
   await page.keyboard.down('w');await page.keyboard.down('a');await page.clock.runFor(2440);
   await page.keyboard.up('w');await page.keyboard.up('a');
   for(let i=0;i<30;i++){
    await page.clock.runFor(1000);
    if(await page.evaluate(()=>window.starharbor.snapshot().phase==='over'))break;
   }
   await page.keyboard.up('w');await page.keyboard.up('a');
   const snapshot=await page.evaluate(()=>window.starharbor.snapshot());
   assert.equal(snapshot.phase,'over','Actual game over must be reachable');
   assert.ok(snapshot.score>0,'Game over scenario must earn actual score');
   const persisted=await page.evaluate(()=>localStorage.getItem('sh_best'));
   assert.equal(Number(persisted),snapshot.score,'Game over must persist earned best score');
   await page.getByRole('button',{name:'再次出航 →',exact:true}).click();await page.clock.runFor(32);
   assert.equal(await page.evaluate(()=>window.starharbor.snapshot().phase),'playing');
   assert.equal((await page.locator('#best').textContent()).trim(),persisted);
   await page.reload();await page.clock.runFor(32);await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();await page.clock.runFor(32);
   assert.equal((await page.locator('#best').textContent()).trim(),persisted,'Earned best score must survive reload');
  }finally{await context.close();}
 }
 if(suite.features.includes('wave-feedback')){
  for(const mobile of [false,true]){
  const {context,page}=await openCheck(false,null,mobile);
  try{
   await page.evaluate(()=>{Math.random=()=>.625;});
   await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();
   const notice=page.locator('#wave-notice');
   assert.equal(await notice.isVisible(),false,'No wave notice before a transition');
   await page.clock.runFor(20100);
   const state=await page.evaluate(()=>window.starharbor.snapshot());
   assert.equal(state.phase,'playing','Wave scenario must stay playable');
   assert.equal(state.wave,2);
   assert.ok(state.waveNotice.life>0&&state.waveNotice.text,'Wave notice must carry readable live state');
   assert.equal(await notice.isVisible(),true,'Wave transition must be visible on screen');
   // 允许追加倒计时等辅助信息，核心波次文案必须保留在开头。
   assert.ok((await notice.textContent()).trim().startsWith(state.waveNotice.text),'Notice must preserve the core wave text');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Wave text must fit the viewport');
   await page.getByRole('button',{name:'暂停游戏',exact:true}).click();
   await page.clock.runFor(32);
   const life=await page.evaluate(()=>window.starharbor.snapshot().waveNotice.life);
   await page.clock.runFor(500);
   assert.equal(await page.evaluate(()=>window.starharbor.snapshot().waveNotice.life),life,'Pause must freeze the notice timer');
   assert.equal(await notice.isVisible(),false,'Wave notice must not obscure pause');
   await page.getByRole('button',{name:'继续游戏',exact:true}).click();
   await page.clock.runFor(64);assert.equal(await notice.isVisible(),true);
   await page.clock.runFor(2500);assert.equal(await notice.isVisible(),false,'Wave notice must expire');
  }finally{await context.close();}
  }
 }
 if(suite.features.includes('shield')){
  const {context,page}=await openCheck();
  try{
   await page.getByRole('button',{name:'开始游戏 →',exact:true}).click();await page.clock.runFor(32);
   await page.getByRole('button',{name:'启动护盾',exact:true}).click();await page.clock.runFor(32);
   assert.ok(await page.evaluate(()=>window.starharbor.snapshot().shield.active));
  }finally{await context.close();}
 }
 // 既有视觉契约不固定像素值，以便允许后续画面改进，但必须继续渲染真实画布。
 await fs.writeFile(out+'/evidence.json',JSON.stringify({new_checks:evidence.length,retained_checks:suite.checks.length}));
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log('PASS: desktop, mobile touch, keyboard, pause/resume, canvas, layout, runtime errors, offline assets');
}finally{await browser.close();await new Promise(r=>server.close(r));}
