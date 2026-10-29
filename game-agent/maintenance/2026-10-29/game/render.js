import {WIDTH,HEIGHT} from './core.js';
export function render(ctx,s){
  ctx.fillStyle='#0b1a23';ctx.fillRect(0,0,WIDTH,HEIGHT);
  if(s.damageFlash>0){ctx.fillStyle='rgba(200,40,40,'+Math.min(1,s.damageFlash*.8)+')';ctx.fillRect(0,0,WIDTH,HEIGHT);}
  ctx.strokeStyle='#1c3442';ctx.lineWidth=1;
  for(let x=0;x<WIDTH;x+=50){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,HEIGHT);ctx.stroke();}
  for(let y=0;y<HEIGHT;y+=50){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(WIDTH,y);ctx.stroke();}
  /* 竞技场边界轮廓 */
  ctx.save();
  ctx.strokeStyle='#3a6a7a';ctx.lineWidth=3;
  ctx.strokeRect(20,20,WIDTH-40,HEIGHT-40);
  ctx.strokeStyle='rgba(90,150,180,0.35)';ctx.lineWidth=1;ctx.setLineDash([6,8]);
  ctx.strokeRect(30,30,WIDTH-60,HEIGHT-60);
  ctx.setLineDash([]);
  ctx.restore();
  ctx.strokeStyle='#244352';ctx.beginPath();ctx.arc(450,300,165,0,Math.PI*2);ctx.stroke();
  ctx.fillStyle='#55788a';for(let i=0;i<50;i++){let x=(i*173+31)%WIDTH,y=(i*83+71)%HEIGHT;ctx.fillRect(x,y,1.6,1.6);}
  for(const b of s.bullets){ctx.fillStyle='#c2f5bb';ctx.beginPath();ctx.arc(b.x,b.y,3,0,6.28);ctx.fill();}
  for(const e of s.enemies){ctx.save();ctx.translate(e.x,e.y);ctx.rotate(s.time*.9);if(e.kind==='scout'){ctx.fillStyle='#e8c94a';ctx.strokeStyle='#f2da7a';ctx.beginPath();ctx.arc(0,0,e.r,0,Math.PI*2);ctx.fill();ctx.stroke();}else{ctx.fillStyle='#f8a16b';ctx.strokeStyle='#ffc99a';ctx.beginPath();ctx.rect(-e.r,-e.r,e.r*2,e.r*2);ctx.fill();ctx.stroke();}if(e.isBoss){ctx.restore();ctx.save();ctx.translate(e.x,e.y);ctx.shadowBlur=18;ctx.shadowColor='#ff2222';ctx.fillStyle='#cc1111';ctx.beginPath();ctx.arc(0,0,e.r+6,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;ctx.strokeStyle='#ff6666';ctx.lineWidth=2;ctx.beginPath();ctx.arc(0,0,e.r+6,0,Math.PI*2);ctx.stroke();const tele=e.telegraph??0;const isAttacking=e.telegraph!=null&&e.telegraph>0;ctx.font='bold 12px monospace';ctx.textAlign='center';if(isAttacking){ctx.fillStyle='rgba(255,80,80,'+Math.min(1,tele*.6)+')';ctx.fillText('⚠ 攻击准备 '+Math.ceil(tele)+'s',0,e.r+28);if(tele>0.8){ctx.fillStyle='#ff4444';ctx.font='bold 11px monospace';ctx.fillText('⚠ 躲避！',0,e.r+42);}else{ctx.fillStyle='#ffaa00';ctx.font='bold 12px monospace';ctx.fillText('⚠ 即将攻击',0,e.r+42);}}else{ctx.fillStyle='#ff6666';ctx.font='bold 11px monospace';ctx.fillText('BOSS',0,e.r+28);}ctx.textAlign='left';}ctx.restore();}
  for(const p of s.particles){ctx.globalAlpha=Math.max(0,p.life/.6);ctx.fillStyle='#f4ba7c';ctx.fillRect(p.x,p.y,3,3);}ctx.globalAlpha=1;
  for(const pk of s.pickups){if(pk.kind==='health'){const h=Math.min(1,pk.life/pk.r);const r=pk.r;ctx.save();ctx.beginPath();ctx.arc(pk.x,pk.y,r,0,Math.PI*2);ctx.clip();ctx.fillStyle=`hsl(${120*h},80%,${55+20*h}%)`;ctx.fill();ctx.restore();ctx.strokeStyle=`rgba(255,255,255,${0.3+0.4*h})`;ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(pk.x,pk.y,r,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#fff';ctx.fillRect(pk.x-2.5,pk.y-5.5,5,11);ctx.fillRect(pk.x-5.5,pk.y-2.5,11,5);}}
  {const barW=50,barH=6;const bx=s.player.x,by=s.player.y+22;ctx.save();ctx.beginPath();ctx.rect(bx-barW/2-1,by-1,barW+2,barH+2);ctx.clip();ctx.fillStyle='#1a2a2f';ctx.fillRect(bx-barW/2,by,barW,barH);const ratio=Math.max(0,Math.min(1,s.hp/s.maxHp));ctx.fillStyle=ratio>=1?'#6ad194':'#3a8a5a';ctx.fillRect(bx-barW/2,by,(barW)*ratio,barH);if(ratio<1){const grad=ctx.createLinearGradient(bx-barW/2,by,bx-barW/2*(1-ratio)+barW/2,0);grad.addColorStop(0,'#7ae8b0');grad.addColorStop(0.4,'#58b87a');grad.addColorStop(1,'#2d6e42');ctx.fillStyle=grad;ctx.fillRect(bx-barW/2,by,(barW)*ratio,barH);}ctx.strokeStyle='#3a7a5a';ctx.lineWidth=1.5;ctx.strokeRect(bx-barW/2,by,barW,barH);ctx.restore();}
  const p=s.player;ctx.save();ctx.translate(p.x,p.y);ctx.shadowBlur=25;ctx.shadowColor='#a5edba';ctx.fillStyle='#a5edba';ctx.beginPath();ctx.moveTo(0,-17);ctx.lineTo(p.r*1.3,13);ctx.lineTo(0,7);ctx.lineTo(-p.r*1.3,13);ctx.closePath();ctx.fill();ctx.strokeStyle='#5a9a8a';ctx.lineWidth=1.5;ctx.stroke();ctx.shadowBlur=0;
  /* 驾驶舱玻璃 */
  ctx.fillStyle='#a0ffe8';ctx.strokeStyle='#e8fff4';ctx.lineWidth=2.5;ctx.beginPath();ctx.ellipse(0,-5,3.5,4.5,0,0,6.28);ctx.fill();ctx.stroke();
  /* 引擎喷口 */
  const pulse=0.6+0.4*Math.sin(s.time*6);
  ctx.fillStyle='#ffa044';ctx.globalAlpha=pulse;ctx.beginPath();ctx.ellipse(0,12,3,2,0,0,6.28);ctx.fill();ctx.strokeStyle='rgba(255,180,80,0.6)';ctx.lineWidth=1;ctx.stroke();
  ctx.fillStyle='#ffdd66';ctx.globalAlpha=pulse*0.7;ctx.beginPath();ctx.ellipse(0,12,1.5,1,0,0,6.28);ctx.fill();
  ctx.globalAlpha=1;
  /* 尾焰辉光 */
  ctx.shadowBlur=10;ctx.shadowColor='#ff6622';ctx.fillStyle='#ff9955';ctx.globalAlpha=pulse*0.5;ctx.beginPath();ctx.ellipse(0,16,4,5,0,0,6.28);ctx.fill();ctx.globalAlpha=1;ctx.shadowBlur=0;
  // 绘制 HUD 前恢复飞船局部坐标，避免变换矩阵跨帧累积。
  ctx.restore();
  ctx.save();
  ctx.shadowColor='rgba(0,0,0,0.6)';
  ctx.shadowBlur=4;
  ctx.font='bold 14px monospace';
  ctx.fillStyle='#fff';
  ctx.textAlign='left';
  ctx.textBaseline='top';
  const lvl=s.level||1;
  ctx.fillText('武器 Lv.'+lvl,12,12);
  const blv=s.level||1;
  const bulletColor=blv>=3?'#ff5555':blv>=2?'#ffaa33':'#c2f5bb';
  ctx.fillStyle=bulletColor;
  ctx.fillText('自动瞄准',12,32);
  if(blv>=3){ctx.fillStyle='#ff5555';ctx.font='bold 12px monospace';ctx.fillText('■ ■ ■',12,50);}else if(blv>=2){ctx.fillStyle='#ffaa33';ctx.font='bold 12px monospace';ctx.fillText('■ ■',12,50);}else{ctx.fillStyle='#c2f5bb';ctx.font='bold 12px monospace';ctx.fillText('■',12,50);}
  ctx.textAlign='right';
  ctx.fillStyle='#fff';
  ctx.font='bold 14px monospace';
  /* 下一波进度条 */
  if(s.phase==='playing'){
    const barW=90,barH=5;
    const bx=WIDTH-12-barW,by=38;
    const t=s.time%20;
    const prog=Math.max(0,Math.min(1,t/20));
    ctx.save();
    ctx.fillStyle='#1a2a2f';ctx.fillRect(bx,by,barW,barH);
    ctx.fillStyle='#ffcc44';ctx.fillRect(bx,by,barW*prog,barH);
    ctx.fillStyle='rgba(255,230,130,0.5)';ctx.fillRect(bx,by,barW*prog,1.5);
    ctx.strokeStyle='#25464c';ctx.lineWidth=1;ctx.strokeRect(bx,by,barW,barH);
    ctx.restore();
    ctx.textAlign='right';ctx.font='bold 11px monospace';ctx.fillStyle='#ffdd88';
    ctx.fillText('第'+s.wave+'波 → '+Math.ceil(20-t)+'s',bx+barW,by-2);
  }
  ctx.textAlign='left';
  ctx.shadowBlur=0;
  ctx.shadowColor='rgba(0,0,0,0)';
  /* 护盾状态 */
  const shield=s.shield;
  if(shield){
    ctx.font='bold 13px monospace';
    if(shield.active){
      ctx.fillStyle='#44aaff';
      ctx.strokeStyle='rgba(68,170,255,0.6)';
      ctx.lineWidth=2;
      ctx.beginPath();
      ctx.arc(p.x,p.y,p.r+10,0,Math.PI*2);
      ctx.stroke();
    }
  }
  ctx.textAlign='left';
  ctx.restore();
}
