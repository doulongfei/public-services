import path from 'node:path';
import fs from 'node:fs';

// 保留 Pi 原有精确替换语义，仅修正唯一匹配的多行缩进差异。
export function alignIndentation(content, oldText) {
  if (content.includes(oldText) || oldText.length < 40 || oldText.length > 4096) return oldText;
  const lines = oldText.split('\n');
  if (lines.length < 2 || lines.length > 50 || !lines[0].trim() || !lines.at(-1).trim()) return oldText;
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = lines.map(line => escape(line.replace(/^[ \t]+|[ \t]+$/g, ''))).join('[ \\t]*\\r?\\n[ \\t]*');
  const matcher = new RegExp(pattern, 'g'), first = matcher.exec(content);
  if (!first) return oldText;
  // 第二次搜索从起点后一位开始，重叠片段也必须算作有歧义。
  matcher.lastIndex = first.index + 1;
  return matcher.exec(content) ? oldText : first[0];
}

// 只允许修改当前任务的少量文件；完整覆盖只用于验收声明和新文件。
export function checkFile(event, root, files, anchors=[], options={}) {
  const target = path.resolve(root, event.input.path ?? '');
  const relative = path.relative(root, target);
  const readable = event.toolName === 'read' && (options.readFiles || []).includes(relative);
  if (!files.includes(relative) && !readable) return {block: true, reason: `Stage writable paths: ${files.join(', ')}. Read-only source paths: ${(options.readFiles || []).join(', ')}. Use exact file paths under /workspace; directories and metadata outside this stage are unavailable.`};
  const existing = fs.existsSync(target) ? target : path.dirname(target);
  if (!fs.realpathSync(existing).startsWith(root + path.sep)) return {block: true, reason: 'Path escapes workspace.'};
  if (event.toolName === 'write' && fs.existsSync(target) && !['game/acceptance.json', 'NOTES.md'].includes(relative)) {
    return {block: true, reason: 'Use edit on existing source. Replacing the complete file with write is forbidden.'};
  }
  if (relative === 'game/acceptance.json' && options.task && event.toolName !== 'read') {
    if (event.toolName !== 'write') return {block:true,reason:'Use ONE write call for the complete acceptance JSON object.'};
    const reason = acceptanceError(event.input.content, options.task);
    if (reason) return {block:true,reason:'Acceptance JSON rejected before writing: '+reason};
  }
  if (event.toolName === 'edit') {
    // Pi 1.3 的真实参数是 edits[]，兼容旧版的顶层字段，逐块检查。
    const edits = event.input.edits ?? [event.input];
    const content = fs.readFileSync(target, 'utf8');
    if (anchors.length) {
      for (const edit of edits) {
        if (anchors.includes(edit.oldText.trim())) {
          edit.oldText = edit.oldText.trim();
          edit.newText = edit.newText.trimStart();
        }
      }
      const selected = edits.map(edit => edit.oldText);
      if (edits.length !== anchors.length || anchors.some(anchor => selected.filter(value => value === anchor).length !== 1)) {
        return {block:true,reason:'This UI stage permits exactly two insertions after the supplied short anchors. Keep launch, pause, timers and the existing wave HUD unchanged.'};
      }
      for (const edit of edits) {
        if (!edit.newText.startsWith(edit.oldText) || edit.newText.split(edit.oldText).length !== 2) return {block:true,reason:'Preserve each original anchor verbatim and append display-only code.'};
        const added = edit.newText.slice(edit.oldText.length);
        if (/\bstate\.[\w.]+\s*(?:\+\+|--|[+*/%\-]?=(?!=|>))/.test(added)) return {block:true,reason:'UI reads state only. Core already decrements waveNotice.life in seconds; never assign to it or change game state.'};
      }
    }
    for (const edit of edits) {
      const old = edit.oldText ?? '', replacement = edit.newText ?? '';
      if (old.length > 300 && replacement.length < old.length * .4) return {block: true, reason: 'Destructive edit blocked. Preserve existing gameplay and make a small focused change.'};
      edit.oldText = alignIndentation(content, old);
    }
  }
  if (event.toolName === 'read') event.input.limit = Math.min(event.input.limit ?? 70, 100);
  return undefined;
}

// 提前返回常见格式错误；最终安全与场景边界仍由受信任的 acceptance.py 校验。
export function acceptanceError(content, task) {
  let data;
  try {data=JSON.parse(content);} catch {return 'Use valid JSON, with double-quoted keys and no comments or Markdown.';}
  if (!data || data.task!==task) return 'task must equal '+task;
  if (!Array.isArray(data.checks) || data.checks.length<1 || data.checks.length>4) return 'checks must contain 1-4 checks.';
  for (const c of data.checks) {
    if (!c || !['core','ui','visual'].includes(c.kind)) return 'kind must be core, ui or visual.';
    const fields = {core:['kind','initial','steps','input','path','op','value'],ui:['kind','selector','phase','property','attribute','op','value'],visual:['kind','selector','phase']};
    const extra = Object.keys(c).filter(key=>!fields[c.kind].includes(key));
    if (extra.length) return `Unsupported ${c.kind} check fields: ${extra.join(', ')}. UI/visual scenes do not simulate custom inputs or actions; choose a check observable in the stated scene.`;
    if (c.kind==='visual') {
      if (c.selector!=='#arena') return 'Visual selector must be #arena.';
      continue;
    }
    if (!['eq','gt','gte','lt','lte','contains','exists'].includes(c.op)) return 'Unsupported operator.';
    if (c.kind==='core') {
      if (c.steps!==undefined && (!Number.isInteger(c.steps)||c.steps<1||c.steps>4000)) return 'Core steps is an INTEGER from 1 to 4000, not a list of actions.';
      if (typeof c.path!=='string' || !/^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+){0,5}$/.test(c.path) || c.path.split('.').some(k=>['__proto__','prototype','constructor'].includes(k))) return 'Use an actual safe state field path.';
    } else {
      if (!/^#[a-zA-Z][a-zA-Z0-9_-]{0,48}$/.test(c.selector||'') || c.selector==='#id') return 'Use ONE actual element ID from the source; never a placeholder.';
      if (!['ready','playing','paused'].includes(c.phase||'ready')) return 'UI phase must be ready, playing or paused.';
      if (!['text','visible','attribute'].includes(c.property)) return 'UI property must be text, visible or attribute.';
      if (c.property==='attribute'&&!['aria-label','aria-describedby','aria-pressed','tabindex','role','title'].includes(c.attribute)) return 'Unsupported UI attribute.';
    }
  }
  return null;
}

export default function guard(pi) {
  const files = JSON.parse(process.env.STARHARBOR_FILES || '[]');
  const anchors = JSON.parse(process.env.STARHARBOR_INSERT_ANCHORS || '[]');
  const options = {task:process.env.STARHARBOR_TASK,readFiles:JSON.parse(process.env.STARHARBOR_READ_FILES||'[]')};
  let calls = 0, turns = 0, reads = 0, blocked = 0;
  pi.on('turn_start', async (event, ctx) => {
    // Pi 内部重试会重新从 turnIndex=0 开始，预算必须跨 agent_start 累计。
    if (++turns > 8) {
      process.stderr.write(JSON.stringify({type:'guard_limit',reason:'model_turns',limit:8})+'\n');
      ctx.abort();
    }
  });
  pi.on('tool_call', async (event, ctx) => {
    if (++calls > 12) {
      process.stderr.write(JSON.stringify({type:'guard_limit',reason:'tool_calls',limit:12})+'\n');
      ctx.abort(); return {block: true, reason: 'Round tool budget exhausted.'};
    }
    if (!['read', 'edit', 'write'].includes(event.toolName)) return {block: true, reason: 'Only file tools are permitted.'};
    const result = event.toolName === 'read' && ++reads > 2
      ? {block:true,reason:'Read budget reached. Source/diff is in the prompt: make the requested edit/write and finish.'}
      : checkFile(event, '/workspace', files, anchors, options);
    blocked = result?.block ? blocked+1 : 0;
    if (blocked>=3) {
      process.stderr.write(JSON.stringify({type:'guard_limit',reason:'repeated_tool_error',limit:3})+'\n');
      ctx.abort();
    }
    return result;
  });
}
