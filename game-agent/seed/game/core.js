// 游戏规则保持独立，便于固定测试与模型分轮改进。
export const WIDTH = 900, HEIGHT = 600;
export function createState() {
  return {phase:'ready',time:0,score:0,wave:1,hp:100,player:{x:450,y:300,r:12},enemies:[],bullets:[],particles:[],spawn:0,shot:0};
}
export function step(s, dt, input, random=Math.random) {
  if (s.phase !== 'playing') return s;
  dt = Math.max(0,Math.min(dt,0.05)); s.time+=dt; s.wave=1+Math.floor(s.time/20);
  let dx=input.x||0,dy=input.y||0; const length=Math.hypot(dx,dy);
  if(length>1){dx/=length;dy/=length;}
  s.player.x=Math.max(16,Math.min(WIDTH-16,s.player.x+dx*230*dt));
  s.player.y=Math.max(16,Math.min(HEIGHT-16,s.player.y+dy*230*dt));
  s.spawn-=dt;s.shot-=dt;
  if(s.spawn<=0 && s.enemies.length<75){
    const angle=random()*Math.PI*2;
    s.enemies.push({x:450+Math.cos(angle)*560,y:300+Math.sin(angle)*410,r:11,hp:1,speed:48+s.wave*5});
    s.spawn=Math.max(.22,1.15-s.wave*.07);
  }
  const target=s.enemies.reduce((best,e)=>!best||Math.hypot(e.x-s.player.x,e.y-s.player.y)<Math.hypot(best.x-s.player.x,best.y-s.player.y)?e:best,null);
  if(target && s.shot<=0){const a=Math.atan2(target.y-s.player.y,target.x-s.player.x);s.bullets.push({x:s.player.x,y:s.player.y,vx:Math.cos(a)*510,vy:Math.sin(a)*510,life:2});s.shot=.35;}
  for(const b of s.bullets){b.x+=b.vx*dt;b.y+=b.vy*dt;b.life-=dt;}
  for(const e of s.enemies){
    const a=Math.atan2(s.player.y-e.y,s.player.x-e.x);e.x+=Math.cos(a)*e.speed*dt;e.y+=Math.sin(a)*e.speed*dt;
    if(e.hp>0 && Math.hypot(e.x-s.player.x,e.y-s.player.y)<e.r+s.player.r){s.hp=Math.max(0,s.hp-18);e.hp=0;}
    for(const b of s.bullets){if(e.hp>0 && b.life>0 && Math.hypot(e.x-b.x,e.y-b.y)<e.r+4){e.hp=0;b.life=0;s.score++;for(let i=0;i<6;i++)s.particles.push({x:e.x,y:e.y,vx:(random()-.5)*140,vy:(random()-.5)*140,life:.6});}}
  }
  s.enemies=s.enemies.filter(e=>e.hp>0);s.bullets=s.bullets.filter(b=>b.life>0);
  for(const p of s.particles){p.x+=p.vx*dt;p.y+=p.vy*dt;p.life-=dt;}s.particles=s.particles.filter(p=>p.life>0);
  if(s.hp<=0)s.phase='over';return s;
}
