// The stage's desk: approvals raced against the chat card, holds of the hands, what the
// other devices see of the connectors, the model rules.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApprovalDesk, HoldDesk, pickChatModel, pickHandsModel, readSharedConnectors, sharedConnectors, takesImages } from '../lib/desk.js'

const req = (callId, sessionId = 's1') => ({ agent: { id: 'a1', session: { id: sessionId } }, toolName: 'bash', callId, reason: 'rm -rf build', displayReason: { en: 'Remove the build folder', zh: '删除 build 目录' } })

test('an approval the stage answers first wins; the chat card is let go', async () => {
  let changes = 0
  const desk = new ApprovalDesk(() => changes++)
  let chatResolve
  const chat = () => new Promise((resolve) => { chatResolve = resolve })
  const outcome = desk.handle(req('c1'), chat)
  await new Promise((r) => setImmediate(r))
  assert.equal(desk.list().length, 1)
  assert.equal(desk.list()[0].summary, 'Remove the build folder')
  assert.equal(desk.list()[0].summaryZh, '删除 build 目录')
  assert.equal(desk.decide('c1', false), true)
  assert.equal(await outcome, 'rejected')
  assert.equal(desk.list().length, 0)
  assert.equal(desk.decide('c1', true), false)
  chatResolve('allowed-once')
  assert.ok(changes >= 2)
})

test('the stage\'s answer withdraws the chat card through a signal of its own; the asker\'s signal still cancels both', async () => {
  const desk = new ApprovalDesk(() => undefined)
  const asker = new AbortController()
  const request = { ...req('c1b'), signal: asker.signal }
  // the chat card, as the bridge shows it: it reads the request's signal when it is shown and ends with it
  let cardSignal
  const chat = () => new Promise((resolve, reject) => {
    cardSignal = request.signal
    cardSignal.addEventListener('abort', () => reject(cardSignal.reason), { once: true })
  })
  const outcome = desk.handle(request, chat)
  await new Promise((r) => setImmediate(r))
  assert.notEqual(cardSignal, asker.signal, 'the card follows a signal of its own')
  assert.equal(desk.decide('c1b', true), true)
  assert.equal(await outcome, 'allowed-once')
  assert.equal(cardSignal.aborted, true, 'the card is taken down')
  assert.equal(asker.signal.aborted, false, 'the tool call itself is not cancelled')
  assert.equal(request.signal, asker.signal, 'the request is put back as it came')

  // the asker gives up (the turn is stopped): stage and card both end
  const asker2 = new AbortController()
  const request2 = { ...req('c1c'), signal: asker2.signal }
  let card2
  const chat2 = () => new Promise((_resolve, reject) => {
    card2 = request2.signal
    card2.addEventListener('abort', () => reject(card2.reason), { once: true })
  })
  const outcome2 = desk.handle(request2, chat2)
  await new Promise((r) => setImmediate(r))
  asker2.abort(new Error('stopped'))
  assert.equal(await outcome2, 'cancelled')
  assert.equal(card2.aborted, true)
  assert.equal(desk.list().length, 0)
})

test('an approval the chat answers first clears the stage', async () => {
  const desk = new ApprovalDesk(() => undefined)
  const outcome = await desk.handle(req('c2'), async () => 'allowed-once')
  assert.equal(outcome, 'allowed-once')
  assert.equal(desk.list().length, 0)
})

test('a hands step names the app it asked in; another tool names none', async () => {
  const desk = new ApprovalDesk(() => undefined, () => false, (r) => (r.toolName === 'mcp__nanomuse__computer_act' ? 'WeChat' : ''))
  void desk.handle({ ...req('c4'), toolName: 'mcp__nanomuse__computer_act' }, () => new Promise(() => undefined))
  void desk.handle(req('c5'), () => new Promise(() => undefined))
  await new Promise((r) => setImmediate(r))
  assert.deepEqual(desk.list().map((a) => a.app), ['WeChat', ''])
})

test('a step in a scheduled run names the task that started it', async () => {
  const desk = new ApprovalDesk(() => undefined, () => false, () => 'WeChat', (r) => (r.agent.session.id === 's-task' ? 'schedule-a' : ''))
  void desk.handle(req('c6', 's-task'), () => new Promise(() => undefined))
  void desk.handle(req('c7'), () => new Promise(() => undefined))
  await new Promise((r) => setImmediate(r))
  assert.deepEqual(desk.list().map((a) => a.scheduleId), ['schedule-a', ''])
})

test('how a question was settled is told: a grant at once, the stage or the card as the person, a cancelled one as such, with the wait', async () => {
  const told = []
  const settled = (req, outcome, by, waitedMs) => told.push([req.callId, outcome, by, typeof waitedMs === 'number' && waitedMs >= 0])
  const desk = new ApprovalDesk(() => undefined, (req) => req.callId === 'g1', () => '', () => '', settled)
  assert.equal(await desk.handle(req('g1'), async () => 'rejected'), 'allowed-once')
  assert.deepEqual(told, [['g1', 'allowed-once', 'grant', true]])

  const stage = desk.handle(req('p1'), () => new Promise(() => undefined))
  await new Promise((r) => setImmediate(r))
  desk.decide('p1', true)
  assert.equal(await stage, 'allowed-once')
  assert.deepEqual(told[1], ['p1', 'allowed-once', 'person', true])

  assert.equal(await desk.handle(req('p2'), async () => 'rejected'), 'rejected')
  assert.deepEqual(told[2], ['p2', 'rejected', 'person', true])

  const asker = new AbortController()
  const cancelled = desk.handle({ ...req('p3'), signal: asker.signal }, () => new Promise(() => undefined))
  await new Promise((r) => setImmediate(r))
  asker.abort(new Error('stopped'))
  assert.equal(await cancelled, 'cancelled')
  assert.deepEqual(told[3], ['p3', 'cancelled', 'cancelled', true])
})

test('the watcher hears a question come up and go, once each, and nothing for a granted one', async () => {
  const seen = []
  const desk = new ApprovalDesk(() => undefined, (r) => r.agent.session.id === 's-granted', () => '', () => '', undefined, { asked: (row) => seen.push(`asked ${row.id}`), gone: (id, outcome) => seen.push(`gone ${id} ${outcome}`) })
  const pending = desk.handle(req('c8'), () => new Promise(() => undefined))
  await new Promise((r) => setImmediate(r))
  assert.deepEqual(seen, ['asked c8'])
  assert.equal(desk.decide('c8', true), true)
  assert.equal(await pending, 'allowed-once')
  await desk.handle(req('c9', 's-granted'), async () => 'rejected')
  assert.deepEqual(seen, ['asked c8', 'gone c8 allowed-once'])
})

test('a request a grant covers is allowed without a card', async () => {
  const desk = new ApprovalDesk(() => undefined, (r) => r.agent.session.id === 's-granted')
  let asked = false
  assert.equal(await desk.handle(req('c6', 's-granted'), async () => { asked = true; return 'rejected' }), 'allowed-once')
  assert.equal(asked, false)
  assert.equal(await desk.handle(req('c7', 's-other'), async () => 'rejected'), 'rejected')
})

test('approvals of an excluded session go straight to the next answerer', async () => {
  const desk = new ApprovalDesk(() => undefined)
  desk.exclude('task-session')
  let listed = -1
  const outcome = await desk.handle(req('c3', 'task-session'), async () => { listed = desk.list().length; return 'rejected' })
  assert.equal(outcome, 'rejected')
  assert.equal(listed, 0)
})

test('a hold pauses the hands until Done, and a second take-over only refreshes it', async () => {
  const desk = new HoldDesk(() => undefined)
  assert.equal(await desk.wait('s1'), 'clear')
  const hold = desk.begin('s1', 'computer', 'agent', 'type the password')
  assert.equal(hold.status, 'on')
  assert.equal(hold.type, 'hold')
  const again = desk.begin('s1', 'computer', 'user', '')
  assert.equal(again.id, hold.id)
  assert.equal(again.by, 'user')
  assert.equal(again.reason, 'type the password')
  assert.equal(desk.list().length, 1)
  const waiting = desk.wait('s1')
  setTimeout(() => desk.done(hold.id), 10)
  assert.equal(await waiting, 'done')
  assert.equal(desk.list().length, 0)
  assert.equal(desk.done(hold.id), false)
  assert.equal(await desk.wait('s1', undefined, 5), 'clear')
  const h2 = desk.begin('s2', 'browser', 'user', 'x')
  assert.equal(await desk.wait('s2', undefined, 5), 'timeout')
  desk.done(h2.id)
})

test('shared connectors carry what and where, never how', () => {
  const rows = sharedConnectors([
    { service: 'notion', id: 'cn_1', label: 'Notion', url: 'https://mcp.notion.com/mcp', auth: 'oauth', state: 'ok', connectedAt: 1, updatedAt: 2 },
    { service: 'custom', id: 'cn_2', label: 'My server', url: 'https://x.example/mcp', auth: 'header', state: 'error', connectedAt: 3, updatedAt: 0 },
    // saved before addresses with a user in them were refused: the profile never carries it
    { service: 'custom', id: 'cn_3', label: 'Old one', url: 'https://me:s3cret@y.example:8443/mcp?x=1', auth: 'header', state: 'ok', connectedAt: 4, updatedAt: 0 },
  ], 'Office PC', 'pc-dsh-abc')
  assert.equal(rows.length, 3)
  assert.equal(rows[0].id, 'notion')
  assert.equal(rows[0].auth, 'oauth')
  assert.equal(rows[0].enabled, true)
  assert.equal(rows[1].id, 'cn_2')
  assert.equal(rows[1].auth, 'key')
  assert.equal(rows[1].enabled, false)
  assert.equal(rows[1].device_id, 'pc-dsh-abc')
  assert.equal(rows[1].url, 'https://x.example/mcp')
  assert.equal(rows[2].url, 'https://y.example:8443/mcp?x=1')
  for (const row of rows) for (const field of Object.keys(row)) assert.doesNotMatch(field, /token|secret|key|authorization|password/i)
  assert.doesNotMatch(JSON.stringify(rows), /s3cret/)
  const back = readSharedConnectors([...rows, { nope: true }, { id: 'x', auth: 'weird' }])
  assert.equal(back.length, 4)
  assert.equal(back[3].auth, 'open')
})

test('the chat and hands defaults follow the relay lane recommendation, with old menus supported', () => {
  const current = [
    { id: 'halogen-qwen3.8-flash-next', kind: 'chat', recommended: true, recommendedFor: ['chat', 'gui'], inputModalities: ['text', 'image'], for: ['chat', 'gui'] },
  ]
  assert.equal(pickChatModel(current)?.id, 'halogen-qwen3.8-flash-next')
  assert.equal(pickHandsModel(current)?.id, 'halogen-qwen3.8-flash-next')
  const models = [
    { id: 'deepseek-v4.1-flash', kind: 'chat', recommended: false, inputModalities: ['text', 'image'], for: ['chat'] },
    { id: 'deepseek-v4', kind: 'chat', recommended: true, inputModalities: ['text', 'image'], for: ['chat'] },
    { id: 'qwen3.8-27b', kind: 'chat', recommended: false, inputModalities: ['text', 'image'], for: ['gui'] },
    { id: 'qwen3-vl', kind: 'chat', recommended: true, inputModalities: ['text', 'image'], for: ['chat', 'gui'] },
  ]
  assert.equal(pickChatModel(models)?.id, 'deepseek-v4.1-flash')
  assert.equal(pickHandsModel(models)?.id, 'qwen3.8-27b')
  const future = [
    { id: 'old-chat', kind: 'chat', recommended: true, inputModalities: ['text'], for: ['chat'] },
    { id: 'next', kind: 'chat', recommended: true, recommendedFor: ['chat', 'gui'], inputModalities: ['text', 'image'], for: ['chat', 'gui'] },
  ]
  assert.equal(pickChatModel(future)?.id, 'next')
  assert.equal(pickHandsModel(future)?.id, 'next')
  assert.equal(pickChatModel([{ id: 'old', kind: 'chat', recommended: true, inputModalities: ['text'] }])?.id, 'old')
  assert.equal(pickHandsModel([{ id: 'old', kind: 'chat', recommended: true, inputModalities: ['text'] }]), undefined)
  assert.equal(takesImages({ id: 'deepseek-v4', inputModalities: ['text', 'image'] }), false)
  assert.equal(takesImages({ id: 'deepseek-v4.1-flash', inputModalities: ['text'] }), true)
  assert.equal(takesImages({ id: 'deepseek-ocr', inputModalities: [] }), true)
  assert.equal(takesImages({ id: 'qwen3-vl', inputModalities: ['text', 'image'] }), true)
})

test('the provider row carries the image budget of a request through the relay: 2 MiB of base64 images (about the newest four to eight screenshots), 2 Mpx a picture, the per-image byte cap left to dsh', async () => {
  const { providerRowFor, IMAGE_BUDGET } = await import('../lib/cloud.js')
  const row = providerRowFor('https://relay.test/v1', [
    { id: 'deepseek-v4.1', name: 'DeepSeek V4.1', kind: 'chat', recommended: true, inputModalities: ['text', 'image'], for: ['chat'] },
    { id: 'qwen3.8-27b', name: 'Qwen', kind: 'chat', recommended: false, inputModalities: ['text', 'image'], for: ['gui'] },
    { id: 'wan-video', name: 'Wan', kind: 'video', recommended: false, inputModalities: ['text'], for: ['video'] },
  ])
  assert.equal(row.api, 'openai-completions')
  assert.equal(row.baseURL, 'https://relay.test/v1')
  assert.equal(row.maxRequestImageBytes, 2 * 1024 * 1024)
  assert.equal(row.requestImagePixelBudget, 2 * 1024 * 1024)
  assert.equal(row.requestImageMaxBytes, undefined)
  assert.deepEqual(IMAGE_BUDGET, { maxRequestImageBytes: 2 * 1024 * 1024, requestImagePixelBudget: 2 * 1024 * 1024 })
  // far under the relay's body cap (16 MB by default), with room for the text
  assert.ok(row.maxRequestImageBytes < 6 * 1024 * 1024)
  assert.deepEqual(row.models.map((m) => m.id), ['deepseek-v4.1'])
  assert.deepEqual(row.models[0].input, ['text', 'image'])
  assert.equal(row.headers, undefined, 'no device id, no header')
  const named = providerRowFor('https://relay.test/v1', [], 'pc-dsh-0a1b2c3d4e5f')
  assert.deepEqual(named.headers, { 'x-nanomuse-device': 'pc-dsh-0a1b2c3d4e5f' }, 'the device rides on every chat call for the record')
})
