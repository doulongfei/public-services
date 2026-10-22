import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium} from '/opt/runtime/node_modules/playwright/index.mjs';

const root='/workspace/game',out='/output';
const server=http.createServer(async(req,res)=>{
 try{
  const target=path.resolve(root,'.'+decodeURIComponent(new URL(req.url,'http://localhost').pathname));
  if(target!==root&&!target.startsWith(root+'/')){res.writeHead(403).end();return;}
  const file=target===root?path.join(root,'index.html'):target;
  const content=await fs.readFile(file);
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'");
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
  if(process.env.EXPECT_PAUSE_OVERLAY==='1')await page.locator('#pause-message').waitFor({state:'visible'});
  const paused=await page.evaluate(()=>window.starharbor.snapshot().time);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.evaluate(()=>window.starharbor.snapshot().time),paused);
  await page.getByRole('button',{name:'继续游戏',exact:true}).click();
  await page.waitForFunction(t=>window.starharbor.snapshot().time>t,paused);
  if(process.env.EXPECT_PAUSE_OVERLAY==='1')await page.locator('#pause-message').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  const colors=await page.evaluate(()=>{
   const c=document.querySelector('canvas'),data=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
   const set=new Set();for(let i=0;i<data.length;i+=68)set.add(`${data[i]},${data[i+1]},${data[i+2]}`);return set.size;
  });assert.ok(colors>8,'Canvas must contain actual rendered gameplay');
  await page.screenshot({path:`${out}/${mobile?'mobile':'desktop'}.png`,fullPage:true});
  await context.close();
 }
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log('PASS: desktop, mobile touch, keyboard, pause/resume, canvas, layout, runtime errors, offline assets');
}finally{await browser.close();await new Promise(r=>server.close(r));}
