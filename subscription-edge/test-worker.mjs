import assert from 'node:assert/strict';
import test from 'node:test';
import worker, { sha256 } from './worker.mjs';

class MemoryKV {
  constructor() { this.data = new Map(); this.reads = 0; this.failWrite = false; }
  async put(key, value, options = {}) {
    if (this.failWrite) throw new Error('write unavailable');
    this.data.set(key, { value, metadata: options.metadata });
  }
  async get(key, type) {
    this.reads++;
    const item = this.data.get(key);
    return item ? (type === 'json' ? JSON.parse(item.value) : item.value) : null;
  }
  async getWithMetadata(key) {
    this.reads++;
    const item = this.data.get(key);
    return item ? { value: item.value, metadata: item.metadata } : { value: null, metadata: null };
  }
}

async function fixture() {
  const rule = new Uint8Array([0x28, 0xb5, 0x2f, 0xfd, ...new Array(20).fill(7)]);
  const digest = await sha256(rule);
  const yaml = 'proxies:\n' + '# sample\n'.repeat(30);
  const snapshot = { schema: 1, yaml, sha256: await sha256(yaml), updated_at: Math.floor(Date.now() / 1000),
    subscription_userinfo: 'upload=0; download=123; total=999', rules: [{ name: 'cn.mrs', sha256: digest, bytes: rule.length }] };
  const env = { SNAPSHOTS: new MemoryKV(), SUBSCRIPTION_TOKEN_SHA256: await sha256('test-reader'),
    PUBLISH_TOKEN_SHA256: await sha256('test-publisher') };
  return { env, snapshot, rule, digest };
}

function call(env, path, init = {}) {
  return worker.fetch(new Request('https://sub.example.test' + path, init), env);
}

function publishInit(body) {
  return { method: 'PUT', headers: { Authorization: 'Bearer test-publisher' }, body };
}

test('missing, invalid and duplicate reader tokens fail before reading private storage', async () => {
  const { env } = await fixture();
  for (const query of ['', '?token=wrong', '?token=test-reader&token=test-reader']) {
    assert.equal((await call(env, '/share/file/main' + query)).status, 401);
  }
  assert.equal(env.SNAPSHOTS.reads, 0);
});

test('publisher authorization cannot be replaced by the reader token', async () => {
  const { env } = await fixture();
  assert.equal((await call(env, '/__publish/main', { method: 'PUT', body: '{}' })).status, 401);
  assert.equal((await call(env, '/__publish/main', { method: 'PUT', headers: { Authorization: 'Bearer test-reader' }, body: '{}' })).status, 401);
  assert.equal(env.SNAPSHOTS.data.size, 0);
});

test('missing rules, corrupted rule bytes and wrong YAML checksum cannot replace a snapshot', async () => {
  const { env, snapshot, rule, digest } = await fixture();
  assert.equal((await call(env, '/__publish/main', publishInit(JSON.stringify(snapshot)))).status, 409);
  assert.equal((await call(env, '/__publish/rules/' + 'a'.repeat(64), publishInit(rule))).status, 400);
  assert.equal((await call(env, '/__publish/rules/' + digest, publishInit(rule))).status, 200);
  assert.equal((await call(env, '/__publish/main', publishInit(JSON.stringify({ ...snapshot, yaml: 'wrong'.repeat(40) })))).status, 400);
  assert.equal(await env.SNAPSHOTS.get('main'), null);
});

test('published subscription and rules are served without contacting an origin', async () => {
  const { env, snapshot, rule, digest } = await fixture();
  await call(env, '/__publish/rules/' + digest, publishInit(rule));
  assert.equal((await call(env, '/__publish/main', publishInit(JSON.stringify(snapshot)))).status, 200);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('origin is offline'); };
  try {
    const result = await call(env, '/share/file/main?token=test-reader');
    assert.equal(result.status, 200);
    assert.equal(await result.text(), snapshot.yaml);
    assert.equal(result.headers.get('subscription-userinfo'), snapshot.subscription_userinfo);
    assert.equal(result.headers.get('cache-control'), 'private, no-store');
    const rules = await call(env, `/rules/${digest}/cn.mrs`);
    assert.deepEqual(new Uint8Array(await rules.arrayBuffer()), rule);
    assert.equal(rules.headers.get('etag'), `"${digest}"`);
    assert.equal((await call(env, '/share/file/main?token=test-reader', { method: 'HEAD' })).body, null);
  } finally { globalThis.fetch = originalFetch; }
});

test('storage failure leaves the previous valid snapshot downloadable', async () => {
  const { env, snapshot, rule, digest } = await fixture();
  await env.SNAPSHOTS.put('main', JSON.stringify(snapshot));
  await env.SNAPSHOTS.put('rule:' + digest, rule);
  env.SNAPSHOTS.failWrite = true;
  assert.equal((await call(env, '/__publish/main', publishInit(JSON.stringify(snapshot)))).status, 503);
  assert.equal(await (await call(env, '/share/file/main?token=test-reader')).text(), snapshot.yaml);
});

test('stale snapshots remain available with an explicit age indicator', async () => {
  const { env, snapshot } = await fixture();
  snapshot.updated_at -= 7200;
  await env.SNAPSHOTS.put('main', JSON.stringify(snapshot));
  const response = await call(env, '/share/file/main?token=test-reader');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('x-subscription-stale'), 'true');
  assert.equal(Number(response.headers.get('x-subscription-age-seconds')) >= 7200, true);
});

test('unknown paths, unsupported methods, missing snapshot and missing assets fail closed', async () => {
  const { env } = await fixture();
  assert.equal((await call(env, '/share/file/other?token=test-reader')).status, 404);
  assert.equal((await call(env, '/rules/' + 'f'.repeat(64) + '/cn.mrs')).status, 503);
  assert.equal((await call(env, '/share/file/main?token=test-reader')).status, 503);
  assert.equal((await call(env, '/__health')).status, 503);
  assert.equal((await call(env, '/share/file/main?token=test-reader', { method: 'POST' })).status, 405);
});

test('oversized and malicious snapshot fields are rejected', async () => {
  const { env, snapshot } = await fixture();
  for (const value of [
    { ...snapshot, subscription_userinfo: 'bad\r\nheader' },
    { ...snapshot, updated_at: Math.floor(Date.now() / 1000) + 86400 },
    { ...snapshot, rules: [{ ...snapshot.rules[0], name: '../private.mrs' }] },
  ]) {
    assert.equal((await call(env, '/__publish/main', publishInit(JSON.stringify(value)))).status, 400);
  }
  assert.equal((await call(env, '/__publish/main', publishInit('x'.repeat(512 * 1024 + 1)))).status, 400);
});
