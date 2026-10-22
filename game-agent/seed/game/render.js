import {WIDTH,HEIGHT} from './core.js';
export function render(ctx,s){
  ctx.fillStyle='#0b1a23';ctx.fillRect(0,0,WIDTH,HEIGHT);
  ctx.strokeStyle='#1c3442';ctx.lineWidth=1;
  for(let x=0;x<WIDTH;x+=50){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,HEIGHT);ctx.stroke();}
  for(let y=0;y<HEIGHT;y+=50){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(WIDTH,y);ctx.stroke();}
  ctx.strokeStyle='#244352';ctx.beginPath();ctx.arc(450,300,165,0,Math.PI*2);ctx.stroke();
  ctx.fillStyle='#55788a';for(let i=0;i<50;i++){let x=(i*173+31)%WIDTH,y=(i*83+71)%HEIGHT;ctx.fillRect(x,y,1.6,1.6);}
  for(const b of s.bullets){ctx.fillStyle='#c2f5bb';ctx.beginPath();ctx.arc(b.x,b.y,3,0,7);ctx.fill();}
  for(const e of s.enemies){ctx.save();ctx.translate(e.x,e.y);ctx.rotate(s.time*.9);ctx.fillStyle='#f8a16b';ctx.strokeStyle='#ffc99a';ctx.beginPath();ctx.rect(-e.r,-e.r,e.r*2,e.r*2);ctx.fill();ctx.stroke();ctx.restore();}
  for(const p of s.particles){ctx.globalAlpha=Math.max(0,p.life/.6);ctx.fillStyle='#f4ba7c';ctx.fillRect(p.x,p.y,3,3);}ctx.globalAlpha=1;
  const p=s.player;ctx.save();ctx.translate(p.x,p.y);ctx.shadowBlur=25;ctx.shadowColor='#a5edba';ctx.fillStyle='#a5edba';ctx.beginPath();ctx.moveTo(0,-17);ctx.lineTo(12,13);ctx.lineTo(0,7);ctx.lineTo(-12,13);ctx.closePath();ctx.fill();ctx.shadowBlur=0;ctx.fillStyle='#25464c';ctx.fillRect(-3,-4,6,8);ctx.restore();
}
