// 游戏规则保持独立，便于固定测试与模型分轮改进。
export const WIDTH = 900, HEIGHT = 600;
const SHIELD_DURATION = 1.2;
const SHIELD_COOLDOWN = 4.0;
export function createState() {
  return {phase:'ready',time:0,score:0,wave:1,hp:100,maxHp:100,level:1,waveNotice:{text:'',life:2},damageFlash:0,player:{x:450,y:300,r:12},enemies:[],bullets:[],particles:[],pickups:[],spawn:0,shot:0,bossTelegraph:0,bossHp:0,shield:{active:false,cooldown:0}};
}
export function step(s, dt, input, random=Math.random) {
  if (s.phase !== 'playing') return s;
  dt = Math.max(0,Math.min(dt,0.05)); s.time+=dt;
  const newWave = 1+Math.floor(s.time/20);
  if (newWave !== s.wave) { s.wave=newWave; s.waveNotice={text:'第 '+s.wave+' 波',life:2}; }
  if (s.waveNotice.life > 0) { s.waveNotice.life -= dt; if (s.waveNotice.life < 0) s.waveNotice.life = 0; }
  if (s.damageFlash > 0) s.damageFlash -= dt;
  if (s.damageFlash < 0) s.damageFlash = 0;
  if (s.shield && s.shield.cooldown > 0) { s.shield.cooldown -= dt; }
  if (s.shield && s.shield.cooldown < 0) { s.shield.cooldown = 0; }
  if (s.shield && s.shield.active) {
    if (s.time > (s._shieldStart || 0) + SHIELD_DURATION) { s.shield.active = false; }
  }
  if (input && input.shield && s.phase === 'playing') {
    if (!s.shield) s.shield = {active:false, cooldown:0};
    if (!s.shield.active && s.shield.cooldown === 0) {
      s.shield.active = true;
      s._shieldStart = s.time;
      s.shield.cooldown = SHIELD_COOLDOWN;
    }
  }
  let dx=input.x||0,dy=input.y||0; const length=Math.hypot(dx,dy);
  if(length>1){dx/=length;dy/=length;}
  s.player.x=Math.max(16,Math.min(WIDTH-16,s.player.x+dx*230*dt));
  s.player.y=Math.max(16,Math.min(HEIGHT-16,s.player.y+dy*230*dt));
  s.spawn-=dt;s.shot-=dt;
  const level = Math.min(3, Math.floor(s.score / 15) + 1);
  if (level !== s.level) { s.level = level; }
  const shotInterval = level >= 3 ? 0.22 : level >= 2 ? 0.28 : 0.35;
  const bulletSpeed = 510 + level * 40;
  const bulletCount = level >= 3 ? 3 : level >= 2 ? 2 : 1;
  const bossSpawnTime = 120;
  if (s.time >= bossSpawnTime && !s._bossSpawned && s.enemies.length < 12) {
    s._bossSpawned = true;
    const angle = random() * Math.PI * 2;
    s.enemies.push({x:450+Math.cos(angle)*560,y:300+Math.sin(angle)*410,r:13,hp:5,kind:'elite',speed:42,isBoss:true,telegraph:3});
    s.bossTelegraph = 3;
    s.bossHp = 5;
  }
  if(s.spawn<=0 && s.enemies.length<75){
    const angle=random()*Math.PI*2;
    const isScout=random()<0.25;
    let speed=isScout?68+s.wave*5:48+s.wave*5;
    if(!isScout && speed>180) speed=180;
    if(isScout && speed>220) speed=220;
    s.enemies.push({x:450+Math.cos(angle)*560,y:300+Math.sin(angle)*410,r:isScout?7:11,hp:isScout?1:1,kind:isScout?'scout':'normal',speed:speed});
    s.spawn=Math.max(.35,1.15-s.wave*.07);
  }
  const target=s.enemies.reduce((best,e)=>!best||Math.hypot(e.x-s.player.x,e.y-s.player.y)<Math.hypot(best.x-s.player.x,best.y-s.player.y)?e:best,null);
  if(target && s.shot<=0){
    if(bulletCount===3){
      const a=Math.atan2(target.y-s.player.y,target.x-s.player.x);
      for(let i=-1;i<=1;i++){
        const spread=i*0.15;
        s.bullets.push({x:s.player.x,y:s.player.y,vx:Math.cos(a+spread)*bulletSpeed,vy:Math.sin(a+spread)*bulletSpeed,life:2});
      }
    } else if(bulletCount===2){
      const a=Math.atan2(target.y-s.player.y,target.x-s.player.x);
      s.bullets.push({x:s.player.x,y:s.player.y,vx:Math.cos(a)*bulletSpeed,vy:Math.sin(a)*bulletSpeed,life:2});
      const a2=Math.atan2(target.y-s.player.y,target.x-s.player.x)+0.15;
      s.bullets.push({x:s.player.x,y:s.player.y,vx:Math.cos(a2)*bulletSpeed,vy:Math.sin(a2)*bulletSpeed,life:2});
    } else {
      const a=Math.atan2(target.y-s.player.y,target.x-s.player.x);
      s.bullets.push({x:s.player.x,y:s.player.y,vx:Math.cos(a)*bulletSpeed,vy:Math.sin(a)*bulletSpeed,life:2});
    }
    s.shot=shotInterval;
  }
  for(const b of s.bullets){b.x+=b.vx*dt;b.y+=b.vy*dt;b.life-=dt;}
  for(const e of s.enemies){
    if(e.isBoss && e.telegraph > 0){ e.telegraph -= dt; continue; }
    const a=Math.atan2(s.player.y-e.y,s.player.x-e.x);e.x+=Math.cos(a)*e.speed*dt;e.y+=Math.sin(a)*e.speed*dt;
    if(e.hp>0 && Math.hypot(e.x-s.player.x,e.y-s.player.y)<e.r+s.player.r){
      if (s.shield && s.shield.active) { e.hp=0; }
      else { s.hp=Math.max(0,s.hp-18); s.damageFlash=0.3; e.hp=0; }
    }
    let killed=false;
    for(const b of s.bullets){if(e.hp>0 && b.life>0 && Math.hypot(e.x-b.x,e.y-b.y)<e.r+4){if(e.isBoss){e.hp--;s.bossHp=e.hp;if(e.hp<=0){s.bossTelegraph=0;s.score+=50;}}else{e.hp=0;s.score++;}b.life=0;for(let i=0;i<6;i++)s.particles.push({x:e.x,y:e.y,vx:(random()-.5)*140,vy:(random()-.5)*140,life:.6});}}
    if(e.hp===0&&!killed && !e.isBoss){killed=true;if(random()<0.15){s.pickups.push({kind:'health',x:e.x,y:e.y,r:8,life:15});}}
  }
  s.enemies=s.enemies.filter(e=>e.hp>0);
  s.bullets=s.bullets.filter(b=>b.life>0 && b.x>-32 && b.x<932 && b.y>-32 && b.y<632);
  for(const p of s.particles){p.x+=p.vx*dt;p.y+=p.vy*dt;p.life-=dt;}s.particles=s.particles.filter(p=>p.life>0);
  for(const pk of s.pickups){pk.life-=dt;}s.pickups=s.pickups.filter(pk=>pk.life>0);
  for(let i=0;i<s.pickups.length;i++){
    const pk=s.pickups[i];
    if(Math.hypot(pk.x-s.player.x,pk.y-s.player.y)<pk.r+s.player.r){
      if(pk.kind==='health' && s.hp > 0) s.hp=Math.min(100,s.hp+25);
      s.pickups.splice(i,1);i--;
    }
  }
  if(s.hp<=0)s.phase='over';return s;
}
