import {createState,step,WIDTH,HEIGHT} from './core.js';
import {render} from './render.js';
const canvas=document.querySelector('#arena'),ctx=canvas.getContext('2d');
const overlay=document.querySelector('#overlay'),start=document.querySelector('#start'),pause=document.querySelector('#pause');
let state=createState(),last=performance.now(),pointer=null;const keys=new Set();
function launch(){state=createState();state.phase='playing';overlay.hidden=true;pause.textContent='Ⅱ';pause.setAttribute('aria-label','暂停游戏');}
function togglePause(){if(state.phase==='playing'){state.phase='paused';pause.textContent='▶';pause.setAttribute('aria-label','继续游戏');}else if(state.phase==='paused'){state.phase='playing';pause.textContent='Ⅱ';pause.setAttribute('aria-label','暂停游戏');}}
start.addEventListener('click',launch);pause.addEventListener('click',togglePause);
window.addEventListener('keydown',e=>{if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight',' '].includes(e.key))e.preventDefault();keys.add(e.key.toLowerCase());if(e.key.toLowerCase()==='p'&&!e.repeat)togglePause();});
window.addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));
window.addEventListener('blur',()=>{keys.clear();pointer=null;if(state.phase==='playing')togglePause();});
canvas.addEventListener('pointerdown',e=>{canvas.setPointerCapture(e.pointerId);pointer={x:e.clientX,y:e.clientY};});
canvas.addEventListener('pointermove',e=>{if(pointer)pointer={x:e.clientX,y:e.clientY};});
for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,()=>pointer=null);
function frame(now){
 const dt=(now-last)/1000;last=now;
 let input={x:Number(keys.has('d')||keys.has('arrowright'))-Number(keys.has('a')||keys.has('arrowleft')),y:Number(keys.has('s')||keys.has('arrowdown'))-Number(keys.has('w')||keys.has('arrowup'))};
 if(pointer){const r=canvas.getBoundingClientRect(),dx=(pointer.x-r.left)/r.width*WIDTH-state.player.x,dy=(pointer.y-r.top)/r.height*HEIGHT-state.player.y,dist=Math.hypot(dx,dy);input=dist>8?{x:dx/Math.max(40,dist),y:dy/Math.max(40,dist)}:{x:0,y:0};}
 step(state,dt,input);render(ctx,state);
 document.querySelector('#score').textContent=state.score;document.querySelector('#health').textContent=state.hp;
 const secs=Math.floor(state.time);document.querySelector('#time').textContent=String(Math.floor(secs/60)).padStart(2,'0')+':'+String(secs%60).padStart(2,'0');document.querySelector('#wave').textContent=`第 ${state.wave} 波`;
 if(state.phase==='over' && overlay.hidden){overlay.hidden=false;document.querySelector('#result-title').textContent='航道暂时失守';document.querySelector('#result-detail').textContent=`坚持 ${Math.floor(state.time)} 秒，击落 ${state.score} 架无人机。再来一次？`;start.textContent='再次出航 →';}
 requestAnimationFrame(frame);
}
// 固定只读快照接口供回归测试检查状态；游戏规则本身仍在 core.js 中运行。
window.starharbor={snapshot:()=>JSON.parse(JSON.stringify(state))};
requestAnimationFrame(frame);
