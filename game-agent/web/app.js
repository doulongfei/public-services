const $=id=>document.getElementById(id);
const phases={preparing:'准备中',generating:'模型开发中',testing:'回归测试',cooldown:'轮次间歇',paused:'已暂停',stopped:'已结束',completed:'一周已完成',interrupted:'正在恢复','waiting-disk':'等待磁盘空间'};
const tasks={'pause-overlay':'暂停提示层','best-score':'最高战绩','enemy-types':'敌机种类','wave-feedback':'波次提示','health-pickup':'补给机制','weapon-upgrade':'武器升级','hit-feedback':'命中反馈','touch-controls':'手机操控','difficulty':'难度平衡','accessibility':'操作与无障碍','boss':'精英敌机','shield':'防御能力','visual-polish':'画面完善','performance':'性能与生命周期','onboarding':'新手引导','self-review':'自主审查与改进','maintenance-canvas':'画布稳定性修复'};
let lastVersion='';
function date(value){return new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}
async function refresh(){
 try{
  const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('状态暂时不可用');const s=await response.json();
  $('phase').textContent=phases[s.phase]||s.phase;$('round').textContent=s.round??0;$('counts').textContent=`${s.accepted??0} / ${s.rejected??0}`;
  const seconds=Math.max(0,(s.deadline??Date.now()/1000)-Date.now()/1000);$('remaining').textContent=`${Math.floor(seconds/86400)}天 ${Math.floor(seconds/3600)%24}时 ${Math.floor(seconds/60)%60}分`;
  $('task').textContent=`当前任务：${tasks[s.current_task]||'首个版本准备中'} · 最新版本：${s.last_release||'基线'}${s.phase==='generating'&&s.round_started_at?' · 本轮已运行 '+Math.floor((Date.now()/1000-s.round_started_at)/60)+' 分钟':''}`;
  if(s.generation_file&&['generating','testing','cooldown'].includes(s.phase))$('task').textContent+=` · 阶段 ${s.generation_step}：${s.generation_file}`;
  const terminal=['stopped','completed'].includes(s.phase);
  for(const action of ['pause','resume','stop'])$(action).disabled=terminal;
  if(s.phase==='cooldown'&&s.retry_at>Date.now()/1000)$('task').textContent+=' · '+Math.ceil((s.retry_at-Date.now()/1000)/60)+' 分钟后继续';
  if(s.accepted_features?.length)$('task').textContent+=' · 已验收 '+s.accepted_features.length+' 项功能';
  $('dates').textContent=s.started_at?`${date(s.started_at)} 开始 / ${date(s.deadline)} 自动结束（北京时间）`:'';
  if(s.last_release&&s.last_release!==lastVersion){lastVersion=s.last_release;$('preview').src='/desktop.png?v='+encodeURIComponent(lastVersion);}
  $('history').replaceChildren(...[...(s.history||[])].reverse().map(item=>{
   const li=document.createElement('li'),tag=document.createElement('span'),body=document.createElement('div'),title=document.createElement('strong'),summary=document.createElement('p'),meta=document.createElement('small');
   tag.className='tag '+(['accepted','maintenance'].includes(item.result)?'':'rejected');tag.textContent=`#${item.round} ${{accepted:'已发布',maintenance:'维护修复',rejected:'验证失败',retrying:'退避重试',interrupted:'已中断',deferred:'重新排队'}[item.result]||item.result}`;title.textContent=tasks[item.task]||'自主改进';summary.textContent=item.summary||'已完成固定测试';meta.textContent=date(item.at)+(item.version?' · '+item.version:'');body.append(title,summary,meta);li.append(tag,body);return li;
  }));
 }catch(error){$('phase').textContent='连接重试中';}
}
async function control(action){
 const key=$('key').value.trim();if(!key){$('message').textContent='请先填写控制密钥。';return;}
 if(action==='stop'&&!confirm('结束本次一周任务？游戏会保留，任务不能从此页重启。'))return;
 try{
  const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body:JSON.stringify({action,text:$('feedback').value})});
  const result=await response.json();if(!response.ok)throw Error(result.error||'操作失败');
  $('message').textContent=action==='feedback'?'反馈已保存，下轮会读取。':'操作已保存，约 5 秒内生效。';await refresh();
 }catch(error){$('message').textContent=error.message;}
}
$('submit').addEventListener('click',()=>control('feedback'));
for(const action of ['pause','resume','stop'])$(action).addEventListener('click',()=>control(action));
refresh();setInterval(refresh,10000);
