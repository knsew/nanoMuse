// No relay configured (NANOMUSE_CLOUD_URL unset, a fresh install): the service still
// starts and its routes answer, so the sign-in page can say a relay is needed; the
// three ways in refuse with `no_relay` instead of failing as a bare fetch error.
// The same in-memory context as cloud-off.test.mjs, without an account or a token.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Readable } from 'node:stream'
import { Service } from '@deepseek-ai/cordis'
import NanomuseCloud, { API_PREFIX, LLM_ROW } from '../lib/cloud.js'

process.env.NANOMUSE_PY = join(tmpdir(), 'nanomuse-runtime-that-does-not-exist')

function fakeContext() {
  const credentials = new Map()
  const settings = { [LLM_ROW]: { providers: {} } }
  const ctx = {
    reflect: { provide() {} },
    logger: { info() {}, warn() {}, debug() {}, error() {} },
    credentials: {
      async set(ref, value) { credentials.set(String(ref), value) },
      async resolve(ref) { return credentials.has(String(ref)) ? { value: credentials.get(String(ref)) } : undefined },
      async unset(ref) { credentials.delete(String(ref)) },
    },
    settings: {
      async update(ns, patch) {
        const row = settings[ns] ?? (settings[ns] = {})
        for (const [k, v] of Object.entries(patch)) row[k] = k === 'providers' ? { ...row.providers, ...v } : v
      },
      async mutate(ns, ops) {
        for (const op of ops) if (op.op === 'unset' && op.path[0] === 'providers') delete settings[ns].providers[op.path[1]]
      },
      describe() { return Object.entries(settings).map(([ns, value]) => ({ ns, value })) },
    },
    get() { return undefined },
    inject() {},
    effect(fn) { fn() },
    on() { return () => undefined },
  }
  return { ctx, credentials, settings }
}

async function cloud() {
  const home = await mkdtemp(join(tmpdir(), 'nm-cloud-no-relay-'))
  const statePath = join(home, 'cloud.json')
  const fake = fakeContext()
  const svc = new NanomuseCloud(fake.ctx, { baseURL: '', deviceName: 'test-desktop', statePath })
  svc.hub.start = () => undefined
  svc.refresh = async () => svc.status()
  await svc[Service.init]()
  const api = async (method, route, body) => {
    const req = Readable.from(body === undefined ? [] : [Buffer.from(JSON.stringify(body))])
    req.method = method
    req.url = `${API_PREFIX}${route}`
    req.headers = {}
    const res = { status: 0, body: undefined, writeHead(status) { res.status = status; return res }, end(text) { res.body = text ? JSON.parse(text) : undefined; return res } }
    await svc.handle(req, res)
    return res
  }
  return { svc, api, home, ...fake, done: () => rm(home, { recursive: true, force: true }) }
}

test('no relay configured: the service starts and /status answers signed out with an empty address', async () => {
  const out = await cloud()
  try {
    const res = await out.api('GET', '/status')
    assert.equal(res.status, 200)
    assert.equal(res.body.signedIn, false)
    assert.equal(res.body.baseURL, '')
  } finally {
    await out.done()
  }
})

test('no relay configured: the code, the verify and the password way in all refuse with no_relay', async () => {
  const out = await cloud()
  try {
    for (const [route, body] of [
      ['/code', { identifier: 'a@example.org' }],
      ['/verify', { identifier: 'a@example.org', code: '123456' }],
      ['/login', { identifier: 'a@example.org', password: 'secret' }],
    ]) {
      const res = await out.api('POST', route, body)
      assert.equal(res.status, 400, route)
      assert.equal(res.body.error.code, 'no_relay', route)
      assert.match(res.body.error.message, /NANOMUSE_CLOUD_URL/, route)
      assert.doesNotMatch(res.body.error.message, /[—–!]/, 'one plain sentence')
    }
    // still signed out, nothing half-adopted
    assert.equal((await out.api('GET', '/status')).body.signedIn, false)
  } finally {
    await out.done()
  }
})
