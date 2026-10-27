import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, beforeEach, test } from 'node:test'

// The same test file runs against maintained overlays and active build artifacts.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const maintained = path.basename(root) === 'sim-native'
const repo = path.resolve(root, maintained ? '../..' : '../../../..')
const upstream = path.resolve(process.env.SIM_NATIVE_SOURCE_ROOT ?? path.join(repo, 'artifacts/references/canvas-upstreams/sim'))
const require = createRequire(path.join(process.env.SIM_NATIVE_DEPENDENCIES_ROOT ?? path.join(repo, 'app/sim'), 'package.json'))
const { build } = require('esbuild')
const relative = 'apps/sim/lib/mothership/request/handlers/complete.ts'
const patchedPath = path.join(root, maintained ? 'overlay' : 'patches', relative)
const originalPath = path.join(upstream, relative)
const oldFlag = process.env.MINUS_OPENROUTER_CHAT
const fixture = () => ({ errors: ['prior error'], streamComplete: false, completionStatus: undefined,
  usage: { prompt: 2, completion: 3 }, cost: { input: 0.1, output: 0.2, total: 0.3 }, calls: [] })

async function handler(entryPoint) {
  const result = await build({ entryPoints: [entryPoint], bundle: true, write: false, platform: 'node',
    format: 'cjs', tsconfigRaw: {}, plugins: [{ name: 'thinking-flush-boundary', setup(builder) {
      builder.onResolve({ filter: /^\.\/types$/ }, () => ({ path: 'thinking', namespace: 'boundary' }))
      builder.onLoad({ filter: /.*/, namespace: 'boundary' }, () => ({ contents: `
        export const flushSubagentThinkingBlock = context => context.calls.push('subagent');
        export const flushThinkingBlock = context => context.calls.push('thinking');`, loader: 'js' }))
    } }],
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
  return module.exports.handleCompleteEvent
}
const complete = await handler(patchedPath)
const originalComplete = await handler(originalPath)
beforeEach(() => { process.env.MINUS_OPENROUTER_CHAT = 'true' })
after(() => {
  if (oldFlag === undefined) delete process.env.MINUS_OPENROUTER_CHAT
  else process.env.MINUS_OPENROUTER_CHAT = oldFlag
})

test('known runtime completion reasons produce only their fixed safe sentences', () => {
  for (const [reason, message] of [
    ['invalid_input', 'Invalid native chat request.'],
    ['invalid_selection', 'Select a supported workspace model first.'],
    ['invalid_receipt', 'The stream receipt does not match stored output.'],
    ['not_found', 'The chat execution was not found.'],
    ['conflict', 'The message or chat is already bound to another request.'],
    ['busy', 'A chat execution or runtime owner is already active.'],
    ['unsafe_path', 'Chat storage requires an unlinked local directory.'],
    ['storage_unavailable', 'Chat storage is unavailable.'],
    ['assistant_unavailable', 'The workspace assistant is unavailable.'],
    ['provider_error', 'The provider could not complete the request.'],
    ['provider_auth', 'The provider rejected authentication. Check the configured credential.'],
    ['provider_insufficient_credit', 'The provider reported insufficient credit. Check the provider account balance.'],
    ['provider_forbidden', 'The provider denied access. Check account and model permissions.'],
    ['provider_request', 'The provider rejected the request. Check model options and input size.'],
    ['provider_rate_limit', 'The provider rate limit was reached. Try again later.'],
    ['provider_timeout', 'The provider request timed out. Try again later.'],
    ['provider_unavailable', 'The provider is temporarily unavailable. Try again later.'],
    ['timeout', 'The assistant request exceeded its time limit.'],
    ['architecture_changed', 'Architecture changed. Refresh the canvas before sending.'],
    ['provider_protocol', 'The assistant returned an unsupported event.'],
    ['response_limit', 'The assistant response exceeded its limit.'],
    ['execution_unknown', 'The previous execution was interrupted; its outcome is unknown.'],
  ]) {
    const context = fixture()
    complete({ type: 'complete', payload: { status: 'error', reason } }, context)
    assert.deepEqual(context.errors, ['prior error', message])
    assert.deepEqual(context.calls, ['subagent', 'thinking'])
    assert.equal(context.streamComplete, true)
    assert.equal(context.completionStatus, 'error')
  }
})

test('unknown or malformed reasons never echo provider text, credentials or object keys', () => {
  const secret = 'synthetic-provider-credential-never-live'
  for (const reason of [undefined, null, '', secret, `provider_error: ${secret}`, '__proto__',
    'constructor', { provider_error: secret }, ['provider_error'], 123]) {
    const context = fixture()
    complete({ type: 'complete', payload: { status: 'error', reason, message: secret, error: secret } }, context)
    assert.deepEqual(context.errors, ['prior error', 'The local OpenRouter chat could not complete the request.'])
    assert.equal(JSON.stringify(context.errors).includes(secret), false)
  }
})

test('flag-disabled and successful or cancelled completions preserve upstream behavior exactly', () => {
  for (const flag of [undefined, 'false', 'TRUE', 'true']) {
    if (flag === undefined) delete process.env.MINUS_OPENROUTER_CHAT
    else process.env.MINUS_OPENROUTER_CHAT = flag
    for (const status of flag === 'true' ? ['complete', 'cancelled'] : ['complete', 'cancelled', 'error']) {
      const event = { type: 'complete', payload: { status, reason: 'provider_error',
        usage: { input_tokens: 5, output_tokens: 7 }, cost: { input: 0.4, output: 0.5, total: 0.9 },
        activityReceipt: { emitterId: 'local:synthetic', sequence: 8 }, textLength: 12 } }
      const snapshot = structuredClone(event)
      const expected = fixture()
      const actual = fixture()
      originalComplete(event, expected)
      complete(event, actual)
      assert.deepEqual(actual, expected)
      assert.deepEqual(event, snapshot)
      assert.deepEqual(actual.usage, { prompt: 7, completion: 10 })
      assert.equal(actual.cost.total, 1.2)
    }
  }
})

test('error completions retain additive usage/cost and untouched receipt data', () => {
  const event = { type: 'complete', payload: { status: 'error', reason: 'provider_error',
    usage: { input_tokens: 5, output_tokens: 7 }, cost: { total: 0.9 },
    activityReceipt: { emitterId: 'local:synthetic', sequence: 8 } } }
  const snapshot = structuredClone(event)
  const expected = fixture()
  const actual = fixture()
  originalComplete(event, expected)
  complete(event, actual)
  expected.errors.push('The provider could not complete the request.')
  assert.deepEqual(actual, expected)
  assert.deepEqual(event, snapshot)
})

test('empty accounting and non-complete events still match the original handler', () => {
  for (const event of [{ type: 'complete', payload: { status: 'complete' } },
    { type: 'complete', payload: { status: 'complete', usage: {}, cost: {} } },
    { type: 'unrelated', payload: { status: 'error', reason: 'provider_error' } }]) {
    const expected = { errors: [], calls: [] }
    const actual = { errors: [], calls: [] }
    originalComplete(event, expected)
    complete(event, actual)
    assert.deepEqual(actual, expected)
  }
})

test('overlay contains all original completion handling plus only fixed error mapping', async () => {
  const upstreamSource = (await readFile(originalPath, 'utf8')).replaceAll('\r\n', '\n')
  const patched = (await readFile(patchedPath, 'utf8')).replaceAll('\r\n', '\n')
  const withoutMapping = patched.replace(/const LOCAL_COMPLETION_ERRORS = new Map<string, string>\(\[[\s\S]*?\]\)\n\n/, '')
    .replace(/  if \(process\.env\.MINUS_OPENROUTER_CHAT === 'true' && event\.payload\.status === 'error'\) \{[\s\S]*?\n  \}\n\n/, '')
  assert.equal(withoutMapping, upstreamSource)
})
