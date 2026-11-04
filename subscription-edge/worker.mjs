const HEX = /^[a-f0-9]{64}$/;
const MAX_RULE = 16 * 1024 * 1024;
const MAX_SNAPSHOT = 512 * 1024;
const SAFE_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Cache-Control': 'private, no-store',
};

export async function sha256(value) {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function equal(left, right) {
  if (typeof right !== 'string' || left.length !== right.length) return false;
  let different = 0;
  for (let index = 0; index < left.length; index++) different |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return different === 0;
}

function reply(message, status = 200, headers = {}) {
  return new Response(message, { status, headers: { ...SAFE_HEADERS, ...headers } });
}

async function readLimited(request, maximum) {
  if (Number(request.headers.get('content-length') || 0) > maximum) throw new Error('oversized');
  if (!request.body) throw new Error('empty');
  const reader = request.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > maximum) {
        await reader.cancel();
        throw new Error('oversized');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

function validSnapshot(snapshot) {
  return snapshot?.schema === 1 && typeof snapshot.yaml === 'string'
    && snapshot.yaml.length > 100 && snapshot.yaml.length <= MAX_SNAPSHOT
    && HEX.test(snapshot.sha256) && Number.isSafeInteger(snapshot.updated_at)
    && snapshot.updated_at > 0 && snapshot.updated_at <= Date.now() / 1000 + 300
    && typeof snapshot.subscription_userinfo === 'string'
    && /^[\x20-\x7e]{0,512}$/.test(snapshot.subscription_userinfo)
    && Array.isArray(snapshot.rules) && snapshot.rules.length > 0 && snapshot.rules.length <= 64
    && snapshot.rules.every(rule => /^[A-Za-z0-9_-]+\.mrs$/.test(rule.name)
      && HEX.test(rule.sha256) && Number.isSafeInteger(rule.bytes) && rule.bytes > 12 && rule.bytes <= MAX_RULE);
}

async function publish(request, env, path) {
  if (request.method !== 'PUT') return reply('Method not allowed', 405, { Allow: 'PUT' });
  const bearer = request.headers.get('authorization') || '';
  if (!bearer.startsWith('Bearer ') || !equal(await sha256(bearer.slice(7)), env.PUBLISH_TOKEN_SHA256)) {
    return reply('Unauthorized', 401);
  }
  if (path === '/__publish/main') {
    let snapshot;
    try {
      snapshot = JSON.parse(new TextDecoder().decode(await readLimited(request, MAX_SNAPSHOT)));
    } catch {
      return reply('Invalid snapshot', 400);
    }
    if (!validSnapshot(snapshot) || await sha256(snapshot.yaml) !== snapshot.sha256) {
      return reply('Invalid snapshot', 400);
    }
    // 全部规则已写入后，最后切换唯一的订阅快照；失败时保留之前的版本。
    const keys = await Promise.all(snapshot.rules.map(rule => env.SNAPSHOTS.getWithMetadata(`rule:${rule.sha256}`, 'arrayBuffer')));
    if (keys.some((item, index) => !item.value || item.value.byteLength !== snapshot.rules[index].bytes)) {
      return reply('Rule set incomplete', 409);
    }
    await env.SNAPSHOTS.put('main', JSON.stringify(snapshot));
    return reply('Published', 200);
  }
  const match = /^\/__publish\/rules\/([a-f0-9]{64})$/.exec(path);
  if (!match) return reply('Not found', 404);
  let bytes;
  try { bytes = await readLimited(request, MAX_RULE); } catch { return reply('Invalid rule', 400); }
  if (bytes.length < 13 || bytes[0] !== 0x28 || bytes[1] !== 0xb5 || bytes[2] !== 0x2f || bytes[3] !== 0xfd
      || await sha256(bytes) !== match[1]) return reply('Invalid rule', 400);
  await env.SNAPSHOTS.put(`rule:${match[1]}`, bytes, { metadata: { bytes: bytes.length } });
  return reply('Stored', 200);
}

export async function handle(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (path.startsWith('/__publish')) return publish(request, env, path);
  if (!['GET', 'HEAD'].includes(request.method)) return reply('Method not allowed', 405, { Allow: 'GET, HEAD' });
  if (path === '/__health') {
    const snapshot = await env.SNAPSHOTS.get('main', 'json');
    return reply(JSON.stringify({ ready: Boolean(snapshot), updated_at: snapshot?.updated_at ?? null }), snapshot ? 200 : 503,
      { 'Content-Type': 'application/json' });
  }
  if (path === '/share/file/main') {
    const tokens = url.searchParams.getAll('token');
    if (tokens.length !== 1 || tokens[0].length > 512
        || !equal(await sha256(tokens[0]), env.SUBSCRIPTION_TOKEN_SHA256)) return reply('Unauthorized', 401);
    const snapshot = await env.SNAPSHOTS.get('main', 'json');
    if (!snapshot) return reply('No published subscription', 503);
    const age = Math.max(0, Math.floor(Date.now() / 1000) - snapshot.updated_at);
    return reply(request.method === 'HEAD' ? null : snapshot.yaml, 200, {
      'Content-Type': 'text/yaml; charset=utf-8',
      'Content-Disposition': 'attachment; filename="main.yaml"',
      'Subscription-Userinfo': snapshot.subscription_userinfo,
      'Profile-Update-Interval': '6',
      'X-Subscription-Updated-At': new Date(snapshot.updated_at * 1000).toISOString(),
      'X-Subscription-Age-Seconds': String(age),
      'X-Subscription-Stale': age > 3600 ? 'true' : 'false',
      ETag: `"${snapshot.sha256}"`,
    });
  }
  const match = /^\/rules\/([a-f0-9]{64})\/([A-Za-z0-9_-]+\.mrs)$/.exec(path);
  if (!match) return reply('Not found', 404);
  const item = await env.SNAPSHOTS.getWithMetadata(`rule:${match[1]}`, 'arrayBuffer');
  if (!item.value) return reply('Rule unavailable', 503, { 'Retry-After': '60' });
  return reply(request.method === 'HEAD' ? null : item.value, 200, {
    'Content-Type': 'application/octet-stream',
    'Cache-Control': 'public, max-age=86400, immutable',
    ETag: `"${match[1]}"`,
    'Content-Length': String(item.value.byteLength),
  });
}

export default {
  async fetch(request, env) {
    try { return await handle(request, env); } catch { return reply('Storage temporarily unavailable', 503); }
  },
};
