// 证明来自固定时钟下真实页面的差异；模型不能写入这些观测结果。
import {coreValue, checkValue} from './checks.mjs';
export const proofAttributes = ['aria-label','aria-describedby','aria-pressed','aria-live','aria-atomic','tabindex','role','title'];
const protectedIds = new Set(['start','pause','shield-btn']);
const signature = node => JSON.stringify([node.tag,node.text,node.attributes]);

function changedText(previous, current) {
  if (!previous) return current;
  const before=[...previous],after=[...current];let start=0,end=0;
  while(start<before.length&&start<after.length&&before[start]===after[start])start++;
  while(end<before.length-start&&end<after.length-start&&before.at(-1-end)===after.at(-1-end))end++;
  for(const padding of [12,24,after.length]){
    const value=after.slice(Math.max(0,start-padding),Math.min(after.length,after.length-end+padding)).join('');
    if(value.trim()&&!previous.includes(value))return value;
  }
  return null;
}

export async function observeScene(page) {
  return page.evaluate(attributes => {
    const elements = [...document.querySelectorAll('body *')];
    if (elements.length > 1024) throw new Error('DOM observation budget exceeded');
    const ids = new Set();
    return elements.map(element => {
      if (element.id && ids.has(element.id)) throw new Error('Duplicate live element ID: '+element.id);
      if (element.id) ids.add(element.id);
      const style = getComputedStyle(element);
      const visible = element.getClientRects().length > 0 && style.visibility !== 'hidden' && !element.closest('[aria-hidden="true"]');
      const values = {};
      for (const name of attributes) {
        const value = element.getAttribute(name);
        if (value !== null && value.length <= 300) values[name] = value;
      }
      return {id:element.id,tag:element.tagName.toLowerCase(),text:element.textContent,visible,attributes:values};
    }).filter(node => (node.visible || node.id) && node.text.length <= 500);
  }, proofAttributes);
}

export function uiProofs(before, after, phase) {
  const oldIds = new Map(before.filter(node=>node.id).map(node=>[node.id,node]));
  const oldSemantics = new Set(before.filter(node=>node.visible).map(signature));
  const candidates = [];
  for (const node of [...after].sort((a,b)=>a.text.length-b.text.length)) {
    if (!node.visible || !/^[a-zA-Z][a-zA-Z0-9_-]{0,48}$/.test(node.id)) continue;
    const old = oldIds.get(node.id);
    // 只给原有元素加 ID 不会改善界面，不应自动算作新功能。
    if (!old && oldSemantics.has(signature(node))) continue;
    const base = {kind:'ui',selector:'#'+node.id,phase,op:'eq'};
    if(old && !old.visible && (node.text.trim()||Object.keys(node.attributes).length))candidates.push({...base,property:'visible',value:true});
    for (const attribute of proofAttributes) {
      const value = node.attributes[attribute];
      if (!value || value === old?.attributes[attribute]) continue;
      if (protectedIds.has(node.id) && ['aria-label','role','tabindex'].includes(attribute)) continue;
      if (attribute === 'role' && ['button','a','input','select','textarea'].includes(node.tag)) continue;
      if (attribute === 'aria-describedby' && value.split(/\s+/).some(id=>!after.some(element=>element.id===id))) continue;
      const flexible=['aria-label','title','aria-describedby'].includes(attribute);
      const expected=flexible?changedText(old?.attributes[attribute],value):value;
      if(expected)candidates.push({...base,op:flexible?'contains':'eq',property:'attribute',attribute,value:expected});
    }
    if (!protectedIds.has(node.id) && node.text.trim() && node.text.length <= 300 && node.text !== old?.text) {
      const value=changedText(old?.text,node.text);
      if(value)candidates.push({...base,op:'contains',property:'text',value});
    }
  }
  return candidates;
}

// 只自动证明预定义的玩法不变量，不能靠添加无用字段生成核心改进证明。
export function coreProofs(before, after) {
  const quiet={spawn:999,shot:999};
  const probes=[
    {kind:'core',initial:quiet,steps:180,path:'waveNotice.life',op:'gte',value:0},
    {kind:'core',initial:{...quiet,time:19.99},steps:180,path:'waveNotice.life',op:'gte',value:0},
    {kind:'core',initial:{...quiet,shield:{active:false,cooldown:.01}},path:'shield.cooldown',op:'gte',value:0},
    {kind:'core',initial:{...quiet,hp:99,pickups:[{kind:'health',x:450,y:300,r:8,life:1}]},path:'hp',op:'lte',value:100},
    {kind:'core',initial:{...quiet,hp:1,enemies:[{x:450,y:300,hp:1,r:11,speed:0}],pickups:[{kind:'health',x:450,y:300,r:8,life:1}]},path:'phase',op:'eq',value:'over'},
  ];
  const checks=[],observations=[];
  for(const check of probes){
    const previous=coreValue(before,check),current=coreValue(after,check);
    let currentPassed=true;try{checkValue(current,check);}catch{currentPassed=false;}
    observations.push({check,previous,current,current_passed:currentPassed});
    if(previous===undefined||current===undefined)continue;
    if(!currentPassed)continue;
    try{checkValue(previous,check);}catch{checks.push(check);}
  }
  return {checks,observations};
}
