import path from 'node:path';
import fs from 'node:fs';

// 只允许修改游戏文件，轮次、发布和测试由容器外的控制器负责。
export default function guard(pi) {
  let calls = 0;
  pi.on('turn_start', async (event, ctx) => {
    if (event.turnIndex >= 8) ctx.abort();
  });
  pi.on('tool_call', async (event, ctx) => {
    if (++calls > 12) { ctx.abort(); return {block: true, reason: 'Round tool budget exhausted.'}; }
    if (!['read', 'edit', 'write'].includes(event.toolName)) return {block: true, reason: 'Only file tools are permitted.'};
    const target = path.resolve('/workspace', event.input.path ?? '');
    const allowed = /^\/workspace\/game\/[a-z0-9][a-z0-9_.-]{0,48}\.(js|css|html|json)$/.test(target)
      || target === '/workspace/NOTES.md';
    if (!allowed) return {block: true, reason: 'Only game files and NOTES.md are allowed.'};
    const existing = fs.existsSync(target) ? target : path.dirname(target);
    if (!fs.realpathSync(existing).startsWith('/workspace/')) return {block: true, reason: 'Path escapes workspace.'};
    if (event.toolName === 'read') event.input.limit = Math.min(event.input.limit ?? 70, 100);
    return undefined;
  });
}
