import path from 'node:path';
import fs from 'node:fs';

// 只允许修改当前任务的少量文件；完整覆盖只用于验收声明和新文件。
export function checkFile(event, root, files) {
  const target = path.resolve(root, event.input.path ?? '');
  const relative = path.relative(root, target);
  if (!files.includes(relative)) return {block: true, reason: 'Only the files listed in the task are allowed. Their current source is already in the prompt.'};
  const existing = fs.existsSync(target) ? target : path.dirname(target);
  if (!fs.realpathSync(existing).startsWith(root + path.sep)) return {block: true, reason: 'Path escapes workspace.'};
  if (event.toolName === 'write' && fs.existsSync(target) && !['game/acceptance.json', 'NOTES.md'].includes(relative)) {
    return {block: true, reason: 'Use edit on existing source. Replacing the complete file with write is forbidden.'};
  }
  if (event.toolName === 'edit') {
    // Pi 1.3 的真实参数是 edits[]，兼容旧版的顶层字段，逐块检查。
    const edits = event.input.edits ?? [event.input];
    for (const edit of edits) {
      const old = edit.oldText ?? '', replacement = edit.newText ?? '';
      if (old.length > 300 && replacement.length < old.length * .4) return {block: true, reason: 'Destructive edit blocked. Preserve existing gameplay and make a small focused change.'};
    }
  }
  if (event.toolName === 'read') event.input.limit = Math.min(event.input.limit ?? 70, 100);
  return undefined;
}

export default function guard(pi) {
  const files = JSON.parse(process.env.STARHARBOR_FILES || '[]');
  let calls = 0, turns = 0, reads = 0;
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
    if (event.toolName === 'read' && ++reads > 2) return {block: true, reason: 'Read budget reached. Source is in the prompt: edit it now, then finish with a short summary.'};
    return checkFile(event, '/workspace', files);
  });
}
