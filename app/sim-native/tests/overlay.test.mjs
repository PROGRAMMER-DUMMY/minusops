import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, readdir, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { after, beforeEach, test } from 'node:test'

// Use the workspace's installed esbuild, Zod and Next runtime. Auth/DB access
// are replaced at the native helper boundary; fetch never reaches a network.
const buildRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repo = path.resolve(buildRoot, '../..')
const nativeSource = path.resolve(process.env.SIM_NATIVE_SOURCE_ROOT ?? path.join(repo, 'artifacts/references/canvas-upstreams/sim'))
const original = path.join(nativeSource, 'apps/sim')
const overlays = path.join(buildRoot, 'overlay/apps/sim')
const dependencyRoot = path.resolve(process.env.SIM_NATIVE_DEPENDENCIES_ROOT ?? path.join(repo, 'app/sim'))
const require = createRequire(path.join(dependencyRoot, 'package.json'))
const { build, transform } = require('esbuild')
const { NextRequest } = require('next/server')
const uuid = '12345678-1234-4234-8234-123456789abc'
const origin = 'http://localhost:3000'
const token = 'synthetic-local-bridge-token'
const key = 'synthetic-provider-key-never-live'
const envNames = ['MINUS_OPENROUTER_CHAT', 'MINUS_ASSISTANT_GATEWAY_URL', 'MINUS_ASSISTANT_GATEWAY_TOKEN', 'NEXT_PUBLIC_APP_URL']
const previousEnv = Object.fromEntries(envNames.map((name) => [name, process.env[name]]))
const previousFetch = globalThis.fetch
let fixture

test('browser bundles keep native permission/password helpers and logger fallback without Node modules', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'minusops-sim-browser-'))
  try {
    await writeFile(path.join(directory, 'next.config.mjs'), await readFile(path.join(buildRoot, 'dev-next.config.mjs')))
    await writeFile(path.join(directory, 'browser-password.mjs'), await readFile(path.join(buildRoot, 'browser-password.mjs')))
    await writeFile(path.join(directory, 'next.config.native.mjs'), 'export default { experimental:{}, serverExternalPackages:[] }')
    await writeFile(path.join(directory, 'package.json'), '{"dependencies":{}}')
    const config = (await import(pathToFileURL(path.join(directory, 'next.config.mjs')).href)).default
    const packages = path.join(directory, 'node_modules/@sim')
    for (const name of ['utils', 'platform-authz', 'audit']) await mkdir(path.join(packages, name), { recursive: true })
    await writeFile(path.join(packages, 'utils/package.json'), JSON.stringify({ exports: {
      './random': './random.js', './client-info': './client-info.js' } }))
    await writeFile(path.join(packages, 'utils/client-info.js'), 'exports.attributeUndeclaredClient = client => client')
    await writeFile(path.join(packages, 'platform-authz/package.json'), JSON.stringify({ exports: {
      './workspace': './workspace.js', './predicates': './predicates.js' } }))
    await writeFile(path.join(packages, 'platform-authz/workspace.js'), 'require("node:net"); throw new Error("Server authz imported by browser")')
    await writeFile(path.join(packages, 'audit/package.json'), '{"exports":{".":"./index.js"}}')
    await writeFile(path.join(packages, 'audit/index.js'), 'require("node:net"); throw new Error("Server audit imported by browser")')
    for (const [source, output] of [
      ['packages/utils/src/random.ts', path.join(packages, 'utils/random.js')],
      ['packages/platform-authz/src/predicates.ts', path.join(packages, 'platform-authz/predicates.js')],
      ['packages/logger/src/request-context.ts', path.join(directory, 'context.js')],
      ['packages/audit/src/types.ts', path.join(packages, 'audit/types.ts')],
    ]) {
      const result = await transform(await readFile(path.join(nativeSource, source), 'utf8'), { loader: 'ts', format: 'esm' })
      await writeFile(output, result.code)
    }
    const encryption = path.join(directory, 'lib/core/security/encryption.ts')
    await mkdir(path.dirname(encryption), { recursive: true })
    await writeFile(encryption, 'export const serverEncryption = true; export { randomBytes } from "node:crypto"')
    await writeFile(path.join(directory, 'browser.js'), `
      const ctx = require('./context.js');
      exports.context = ctx.runWithRequestContext({requestId:'browser'}, () => ctx.getRequestContext());
      exports.allowed = require('@sim/platform-authz/workspace').permissionSatisfies('admin', 'write');
      exports.password = require('@/lib/core/security/encryption').generatePassword();
      exports.resolvedPassword = require('./lib/core/security/encryption.ts').generatePassword();
      exports.auditResources = require('@sim/audit').AuditResourceType;`)
    await writeFile(path.join(directory, 'server.js'), `
      const ctx = require('./context.js'); const encryption = require('@/lib/core/security/encryption');
      exports.context = ctx.runWithRequestContext({requestId:'server'}, () => ctx.getRequestContext().requestId);
      exports.encryption = encryption.serverEncryption; exports.bytes = encryption.randomBytes(2).length;`)
    const { webpack } = require('next/dist/compiled/webpack/webpack')
    for (const isServer of [false, true]) {
      const target = isServer ? 'server' : 'browser'
      const compiled = config.webpack({ mode: 'development', context: directory, target: isServer ? 'node' : 'web',
        entry: `./${target}.js`, output: { path: path.join(directory, target), filename: 'bundle.cjs',
          globalObject: 'globalThis', library: { type: 'commonjs2' } }, plugins: [], optimization: {}, externals: [],
        resolve: { alias: { '@/lib/core/security/encryption$': encryption } },
      }, { isServer, webpack })
      if (isServer) assert.equal(compiled.resolve.alias['@/lib/core/security/encryption$'], encryption)
      const compiler = webpack(compiled)
      try {
        const stats = await new Promise((resolve, reject) => compiler.run((error, stats) => error ? reject(error) : resolve(stats)))
        assert.equal(stats.hasErrors(), false, stats.toString({ all: false, errors: true }))
        const result = require(path.join(directory, target, 'bundle.cjs'))
        if (isServer) {
          assert.equal(result.context, 'server')
          assert.equal(result.encryption, true)
          assert.equal(result.bytes, 2)
        } else {
          assert.equal(result.context, undefined)
          assert.equal(result.allowed, true)
          assert.equal(result.password.length, 24)
          assert.equal(result.resolvedPassword.length, 24)
          assert.equal(Object.keys(result.auditResources).length > 10, true)
          assert.equal(/^[A-Za-z0-9!@#$%^&*()_\-+=]+$/.test(result.password), true)
          assert.doesNotMatch(JSON.stringify(stats.toJson({ all: false, modules: true })), /node:(crypto|net|async_hooks)/)
        }
      } finally {
        await new Promise((resolve) => compiler.close(resolve))
      }
    }
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('Webpack emits PDF worker assets and resolves Monaco through its ESM entry', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'minusops-sim-assets-'))
  try {
    await writeFile(path.join(directory, 'next.config.mjs'), await readFile(path.join(buildRoot, 'dev-next.config.mjs')))
    await writeFile(path.join(directory, 'next.config.native.mjs'), 'export default { experimental:{}, serverExternalPackages:[] }')
    await writeFile(path.join(directory, 'package.json'), '{"dependencies":{}}')
    const config = (await import(pathToFileURL(path.join(directory, 'next.config.mjs')).href)).default
    const modules = path.join(directory, 'node_modules')
    const editor = path.join(modules, 'monaco-editor')
    await mkdir(path.join(editor, 'esm/vs/editor'), { recursive: true })
    await writeFile(path.join(editor, 'package.json'), JSON.stringify({ type: 'module',
      exports: { '.': { import: './esm/vs/editor/editor.main.js', require: './amd.cjs' }, './*': './*' } }))
    await writeFile(path.join(editor, 'esm/vs/editor/editor.main.js'), 'export const entry = "esm-editor"')
    await writeFile(path.join(editor, 'amd.cjs'), 'throw new Error("AMD entry used")')
    const pdf = path.join(modules, 'pdfjs-dist')
    await mkdir(path.join(pdf, 'legacy/build'), { recursive: true })
    await mkdir(path.join(pdf, 'build'), { recursive: true })
    await writeFile(path.join(pdf, 'package.json'), '{"type":"module","exports":{"./*":"./*"}}')
    const worker = 'export const sentinel = "raw-pdf-worker"'
    const renderer = 'export const sentinel = "raw-pdf-renderer"'
    await writeFile(path.join(pdf, 'legacy/build/pdf.worker.min.mjs'), worker)
    await writeFile(path.join(pdf, 'build/pdf.min.mjs'), renderer)
    await writeFile(path.join(directory, 'entry.js'), `
      exports.editor = require('monaco-editor').entry;
      exports.worker = new URL('pdfjs-dist/legacy/build/pdf.worker.min.mjs', import.meta.url);
      exports.renderer = new URL('pdfjs-dist/build/pdf.min.mjs', import.meta.url);`)
    const { webpack } = require('next/dist/compiled/webpack/webpack')
    const compiler = webpack(config.webpack({ mode: 'development', context: directory, target: 'node',
      entry: './entry.js', output: { path: path.join(directory, 'out'), filename: 'bundle.cjs', library: { type: 'commonjs2' } },
      resolve: { alias: {} }, plugins: [], optimization: {},
      externals: [({ request }, callback) => request?.startsWith('pdfjs-dist/')
        ? callback(new Error('PDF URL reached module externals')) : callback()],
    }, { isServer: true, webpack }))
    try {
      const stats = await new Promise((resolve, reject) => compiler.run((error, stats) => error ? reject(error) : resolve(stats)))
      assert.equal(stats.hasErrors(), false, stats.toString({ all: false, errors: true }))
      const result = require(path.join(directory, 'out/bundle.cjs'))
      assert.equal(result.editor, 'esm-editor')
      assert.equal(await readFile(result.worker, 'utf8'), worker)
      assert.equal(await readFile(result.renderer, 'utf8'), renderer)
    } finally {
      await new Promise((resolve) => compiler.close(resolve))
    }
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('preview compiler preserves failure status and reports interrupted children', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'minusops-sim-compiler-'))
  try {
    await writeFile(path.join(directory, 'build.mjs'), await readFile(path.join(buildRoot, 'build.mjs')))
    await writeFile(path.join(directory, 'patch-manifest.json'), JSON.stringify({ preview_build_paths: ['app/workspace/**'] }))
    const cli = path.join(directory, 'node_modules/next/dist/bin/next')
    await mkdir(path.dirname(cli), { recursive: true })
    await mkdir(path.join(directory, 'apps/sim'), { recursive: true })
    await writeFile(cli, 'process.exit(process.cwd().endsWith(require("node:path").join("apps", "sim")) && process.argv.includes("--webpack") ? 17 : 9)')
    const result = spawnSync(process.execPath, ['build.mjs'], { cwd: directory, encoding: 'utf8', timeout: 10000 })
    assert.equal(result.status, 17)
    const workerPath = path.join(directory, 'page-worker.cjs')
    await writeFile(workerPath, 'exports.limits = () => ({heap: require("node:v8").getHeapStatistics().heap_size_limit / 1024**2, secret: process.env.BETTER_AUTH_SECRET})')
    await writeFile(cli, `const assert = require('node:assert/strict');
      const { Worker } = require(${JSON.stringify(require.resolve('next/dist/lib/worker'))});
      const worker = new Worker(${JSON.stringify(workerPath)}, {numWorkers: 1, isolatedMemory: true,
        debuggerPortOffset: -1, exposedMethods: ['limits']});
      (async () => { try { const {heap, secret} = await worker.limits();
        assert.ok(heap >= 8192 && heap < 8300, String(heap));
        assert.match(secret, /^[a-f0-9]{64}$/); assert.notEqual(secret, 'existing-build-secret');
      } finally { await worker.end() } })().catch(error => { console.error(error); process.exitCode = 1 });`)
    const worker = spawnSync(process.execPath, ['build.mjs'], { cwd: directory, encoding: 'utf8', timeout: 10000,
      env: { ...process.env, BETTER_AUTH_SECRET: 'existing-build-secret', NODE_OPTIONS: '--max-old-space-size=12288' } })
    assert.equal(worker.status, 0, worker.stderr)
    if (process.platform !== 'win32') {
      await writeFile(cli, 'process.kill(process.pid, "SIGTERM")')
      const killed = spawnSync(process.execPath, ['build.mjs'], { cwd: directory, encoding: 'utf8', timeout: 10000 })
      assert.equal(killed.status, 1)
      assert.match(killed.stderr, /stopped by signal: SIGTERM/)
    }
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('preview build disables maps and persistent build cache while retaining native settings', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'minusops-sim-config-'))
  try {
    await writeFile(path.join(directory, 'next.config.mjs'), await readFile(path.join(buildRoot, 'dev-next.config.mjs')))
    await writeFile(path.join(directory, 'next.config.native.mjs'),
      'export default { poweredByHeader: false, experimental: { preloadEntriesOnStart: false }, serverExternalPackages: ["ws"] }')
    await writeFile(path.join(directory, 'package.json'), JSON.stringify({ dependencies: { openai: '1', react: '1' } }))
    const config = (await import(pathToFileURL(path.join(directory, 'next.config.mjs')).href)).default
    assert.equal(config.poweredByHeader, false)
    assert.equal(config.experimental.preloadEntriesOnStart, false)
    assert.equal(config.experimental.cpus, 1)
    assert.equal(config.productionBrowserSourceMaps, false)
    assert.equal(config.experimental.serverSourceMaps, false)
    assert.equal(config.enablePrerenderSourceMaps, false)
    assert.equal(config.experimental.turbopackFileSystemCacheForBuild, false)
    assert.equal(config.experimental.turbopackSourceMaps, false)
    assert.equal(config.experimental.turbopackInputSourceMaps, false)
    assert.equal(config.experimental.turbopackMinify, true)
    assert.equal(config.experimental.turbopackScopeHoisting, false)
    assert.equal(config.experimental.webpackBuildWorker, true)
    assert.equal(config.experimental.parallelServerCompiles, false)
    assert.equal(config.experimental.serverMinification, false)
    const { TraceEntryPointsPlugin } = require('next/dist/build/webpack/plugins/next-trace-entrypoints-plugin')
    const trace = new TraceEntryPointsPlugin({ rootDir: original, appDir: path.join(original, 'app'), appDirEnabled: true })
    const retained = {}
    const webpack = config.webpack({ cache: {}, devtool: 'source-map', optimization: { minimize: true },
      resolve: { alias: {} }, externals: [], plugins: [trace, retained] }, { isServer: true })
    assert.equal(webpack.cache, false)
    assert.equal(webpack.devtool, false)
    assert.equal(webpack.parallelism, 2)
    assert.equal(webpack.optimization.minimize, false)
    assert.equal(webpack.optimization.concatenateModules, false)
    assert.deepEqual(trace.traceIgnores, ['**/*'])
    assert.deepEqual(webpack.plugins, [trace, retained])
    assert.deepEqual(config.outputFileTracingIncludes, {})
    assert.deepEqual(config.outputFileTracingExcludes, { '**/*': ['**/*'] })
    assert.deepEqual(config.serverExternalPackages, ['ws', 'openai'])
    assert.deepEqual(await config.redirects(), [{ source: '/', destination: '/workspace', permanent: false }])
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('full-image preview refuses standalone packaging', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'minusops-sim-standalone-'))
  try {
    await writeFile(path.join(directory, 'next.config.mjs'), await readFile(path.join(buildRoot, 'dev-next.config.mjs')))
    await writeFile(path.join(directory, 'next.config.native.mjs'), 'export default { output: "standalone" }')
    await writeFile(path.join(directory, 'package.json'), '{"dependencies":{}}')
    await assert.rejects(import(pathToFileURL(path.join(directory, 'next.config.mjs')).href), /full source and dependencies/)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('selective preview build retains every native workspace, API and model directory route', async () => {
  const manifest = JSON.parse(await readFile(path.join(buildRoot, 'patch-manifest.json'), 'utf8'))
  const { resolveBuildPaths } = require('next/dist/lib/resolve-build-paths')
  const { appPaths } = await resolveBuildPaths(manifest.preview_build_paths, original, ['tsx', 'ts', 'jsx', 'js'])
  const selected = new Set(appPaths)
  const files = (await readdir(path.join(original, 'app'), { recursive: true })).map((file) => file.replaceAll('\\', '/'))
  const expected = files.filter((file) => /^(workspace\/|api\/|\.well-known\/|\(landing\)\/models\/)/.test(file) && /\/(page|route)\.(tsx|ts)$/.test(file))
  assert.ok(expected.length > 100)
  for (const file of expected) assert.ok(selected.has(`/${file}`), file)
  assert.ok(selected.has('/workspace/[workspaceId]/home/page.tsx'))
  assert.ok(selected.has('/api/health/route.ts'))
  assert.ok(selected.has('/(landing)/models/(shell)/page.tsx'))
  assert.equal(selected.has('/(landing)/page.tsx'), false)
})

const result = await build({
  entryPoints: [path.join(overlays, 'lib/openrouter-chat/server.ts')],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  plugins: [{ name: 'native-boundaries', setup(builder) {
    builder.onResolve({ filter: /^server-only$/ }, () => ({ path: 'marker', namespace: 'boundary' }))
    builder.onResolve({ filter: /^@\/lib\/(auth|core\/utils\/with-route-handler|workspaces\/permissions\/utils)$/ },
      ({ path: name }) => ({ path: name, namespace: 'boundary' }))
    builder.onLoad({ filter: /.*/, namespace: 'boundary' }, ({ path: name }) => ({ contents:
      name === 'marker' ? '' : name.endsWith('/auth')
        ? 'export const getSession = async () => globalThis.__openRouterTest.session'
        : name.endsWith('/with-route-handler') ? 'export const withRouteHandler = handler => handler'
          : `export const getUserEntityPermissions = async (user, type, id) => {
              globalThis.__openRouterTest.permissionCalls.push([user, type, id]);
              return globalThis.__openRouterTest.permission;
            }`, loader: 'js' }))
    builder.onResolve({ filter: /^@\// }, ({ path: name }) => ({ path:
      path.join(name === '@/lib/api/contracts/types' ? original : overlays, `${name.slice(2)}.ts`) }))
  } }],
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
const { assistantRoute } = module.exports

const preferences = { model: 'vendor/chat-1', effort: null, max_tokens: 2048 }
const model = {
  id: 'vendor/chat-1', name: 'Chat One', context_length: 32768,
  reasoning_efforts: ['low', 'high'], tools: null, pricing: null,
  capabilities_verified: false, access_verified: false, max_output_tokens: 4096,
  supported_parameters: ['max_tokens'],
}
const catalog = (rows = [model]) => ({ provider: 'openrouter', models: rows, stale: false,
  catalog_scope: 'account_filtered', ttl_seconds: 300, access_verified: false })
const status = { provider: 'openrouter', providers: {
  openrouter: { configured: true, source: 'vault', reference: 'vault:openrouter', status: 'configured' },
  openai: { forbidden: 'must be stripped' },
}, workflow_tools: false, preferences }
function json(value, options = {}) { return Response.json(value, options) }
function queue(...responses) { fixture.responses.push(...responses) }
async function call(action, method = 'GET', body, options = {}) {
  const headers = { origin, 'content-type': 'application/json', ...options.headers }
  for (const [name, value] of Object.entries(headers)) if (value === null) delete headers[name]
  const request = new NextRequest(`${origin}/api/workspaces/${uuid}/assistant/${action}${options.query ?? ''}`, {
    method, headers, ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }),
  })
  return assistantRoute(action, method)(request, { params: Promise.resolve({ id: options.id ?? uuid }) })
}
async function refused(response, expected) {
  assert.equal(response.status, expected)
  const text = await response.text()
  assert.equal(text.includes(key), false)
  assert.equal(text.includes(token), false)
  assert.equal(text.includes('private diagnostic'), false)
  assert.equal(response.headers.get('cache-control'), 'no-store')
}
beforeEach(() => {
  fixture = { session: { user: { id: 'native-user' } }, permission: 'admin', permissionCalls: [], calls: [], responses: [] }
  globalThis.__openRouterTest = fixture
  process.env.MINUS_OPENROUTER_CHAT = 'true'
  process.env.MINUS_ASSISTANT_GATEWAY_URL = 'http://host.docker.internal:5180'
  process.env.MINUS_ASSISTANT_GATEWAY_TOKEN = token
  process.env.NEXT_PUBLIC_APP_URL = origin
  globalThis.fetch = async (url, options) => {
    fixture.calls.push({ url, options })
    const response = fixture.responses.shift()
    assert.ok(response, 'Unexpected gateway call; live network is forbidden')
    if (response instanceof Error) throw response
    return response
  }
})
after(() => {
  globalThis.fetch = previousFetch
  delete globalThis.__openRouterTest
  for (const [name, value] of Object.entries(previousEnv)) {
    if (value === undefined) delete process.env[name]
    else process.env[name] = value
  }
})

test('flag, native session, workspace UUID and membership fail before gateway access', async () => {
  process.env.MINUS_OPENROUTER_CHAT = 'false'
  await refused(await call('status'), 404)
  process.env.MINUS_OPENROUTER_CHAT = 'true'
  fixture.session = null
  await refused(await call('status'), 401)
  fixture.session = { user: { id: 'native-user' } }
  await refused(await call('status', 'GET', undefined, { id: '../other' }), 400)
  fixture.permission = null
  await refused(await call('status'), 403)
  assert.equal(fixture.calls.length, 0)
  assert.deepEqual(fixture.permissionCalls.at(-1), ['native-user', 'workspace', uuid])
})

test('all writes require admin and exact same origin', async () => {
  for (const permission of ['read', 'write', null]) {
    fixture.permission = permission
    await refused(await call('key', 'POST', { key }), 403)
    await refused(await call('preferences', 'POST', preferences), 403)
    await refused(await call('key', 'DELETE'), 403)
  }
  fixture.permission = 'admin'
  for (const headers of [{ origin: null }, { origin: 'https://evil.example' },
    { 'sec-fetch-site': 'same-site' }, { 'sec-fetch-site': 'cross-site' }]) {
    await refused(await call('key', 'POST', { key }, { headers }), 403)
  }
  assert.equal(fixture.calls.length, 0)
})

test('public origin is pinned across Docker port mapping and rejects malformed configuration', async () => {
  process.env.NEXT_PUBLIC_APP_URL = 'http://localhost:3010'
  queue(json({ success: true }))
  const response = await call('key', 'POST', { key }, { headers: { origin: 'http://localhost:3010' } })
  assert.equal(response.status, 200)
  await refused(await call('key', 'POST', { key }), 403)
  for (const value of ['//evil.example', 'file:///tmp/key', 'https://user:secret@localhost:3010',
    'http://localhost:3010/?url=evil', 'http://localhost:3010/unexpected', 'not a URL']) {
    process.env.NEXT_PUBLIC_APP_URL = value
    await refused(await call('key', 'POST', { key }), 502)
  }
  assert.equal(fixture.calls.length, 1)
})

test('members may read sanitized OpenRouter-only status through fixed transport', async () => {
  fixture.permission = 'read'
  queue(json(status))
  const response = await call('status', 'GET', undefined, {
    headers: { cookie: 'native-session-private', authorization: 'Bearer browser-private' },
  })
  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), { provider: 'openrouter', providers: { openrouter: status.providers.openrouter },
    workflow_tools: false, preferences })
  const { url, options } = fixture.calls[0]
  assert.equal(url, `http://host.docker.internal:5180/workspaces/${uuid}/status`)
  assert.equal(options.headers['X-Minus-Bridge-Token'], token)
  assert.equal(options.headers.cookie, undefined)
  assert.equal(options.headers.authorization, undefined)
  assert.equal(options.redirect, 'error')
  assert.equal(options.credentials, 'omit')
  assert.equal(options.cache, 'no-store')
  assert.ok(options.signal instanceof AbortSignal)
})

test('credential save and deletion return no gateway body or key echo', async () => {
  queue(json({ key, token, private: 'diagnostic' }))
  const saved = await call('key', 'POST', { key })
  assert.equal(saved.status, 200)
  assert.deepEqual(await saved.json(), { success: true })
  assert.deepEqual(JSON.parse(fixture.calls[0].options.body), { key })
  queue(json({ key }))
  const deleted = await call('key', 'DELETE')
  assert.deepEqual(await deleted.json(), { success: true })
  assert.equal(fixture.calls[1].options.method, 'DELETE')
  assert.equal(fixture.calls[1].options.body, undefined)
  await refused(await call('key', 'DELETE', '{}'), 400)
})

test('Docker-free transport uses only the exact loopback gateway', async () => {
  process.env.MINUS_ASSISTANT_GATEWAY_URL = 'http://127.0.0.1:5180'
  queue(json(status))
  assert.equal((await call('status')).status, 200)
  assert.equal(fixture.calls[0].url, `http://127.0.0.1:5180/workspaces/${uuid}/status`)
  for (const value of ['http://127.0.0.1:5181', 'http://127.0.0.1:5180@foreign.example', 'https://foreign.example']) {
    process.env.MINUS_ASSISTANT_GATEWAY_URL = value
    await refused(await call('status'), 502)
  }
  assert.equal(fixture.calls.length, 1)
})

test('gateway errors and thrown exceptions cannot reflect credentials', async () => {
  for (const value of [json({ error: `${key} ${token} private diagnostic` }, { status: 400 }),
    json({ error: key }, { status: 503 }), new Error(`${key} ${token} private diagnostic`)]) {
    queue(value)
    await refused(await call('key', 'POST', { key }), value.status === 400 ? 400 : 502)
  }
})

test('gateway token reflection, including JSON escaping, is refused', async () => {
  queue(json({ ...catalog(), models: [{ ...model, name: token }] }))
  await refused(await call('models'), 502)
  const escaped = token.split('').map((char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`).join('')
  queue(new Response(JSON.stringify(catalog()).replace('Chat One', escaped), { headers: { 'content-type': 'application/json' } }))
  await refused(await call('models'), 502)
})

test('arbitrary origins, absent token, query parameters and bad JSON content type fail closed', async () => {
  process.env.MINUS_ASSISTANT_GATEWAY_URL = 'https://evil.example'
  await refused(await call('status'), 502)
  process.env.MINUS_ASSISTANT_GATEWAY_URL = 'http://host.docker.internal:5180'
  delete process.env.MINUS_ASSISTANT_GATEWAY_TOKEN
  await refused(await call('status'), 502)
  await refused(await call('models', 'GET', undefined, { query: '?url=https://evil.example' }), 400)
  await refused(await call('key', 'POST', { key }, { headers: { 'content-type': 'text/plain' } }), 415)
  assert.equal(fixture.calls.length, 0)
})

test('duplicate, escaped duplicate, oversized and malformed credential bodies are refused', async () => {
  for (const body of [`{"key":"${key}","key":"other-secret"}`, `{"key":"${key}","\\u006bey":"other-secret"}`,
    '{', JSON.stringify({ key: 'short' }), JSON.stringify({ key, url: 'https://evil.example' }),
    JSON.stringify({ key: `${key}\n` })]) await refused(await call('key', 'POST', body), 400)
  await refused(await call('key', 'POST', ' '.repeat(16385)), 413)
  assert.equal(fixture.calls.length, 0)
})

test('current account catalog accepts exact raw model and advertised effort before preference save', async () => {
  const selected = { ...preferences, effort: 'high' }
  queue(json(catalog()), json(selected))
  const response = await call('preferences', 'POST', selected)
  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), selected)
  assert.equal(fixture.calls[0].url.endsWith('/models'), true)
  assert.equal(fixture.calls[0].options.method, 'GET')
  assert.equal(fixture.calls[1].url.endsWith('/preferences'), true)
  assert.deepEqual(JSON.parse(fixture.calls[1].options.body), selected)
})

test('cross-provider model, missing model, unsupported effort and output bound refuse saving', async () => {
  for (const value of [{ ...preferences, model: 'openai:gpt-4' },
    { ...preferences, model: 'vendor/not-in-account' }, { ...preferences, effort: 'max' },
    { ...preferences, max_tokens: 4097 }]) {
    queue(json(catalog()))
    await refused(await call('preferences', 'POST', value), 400)
  }
  queue(json(catalog([])))
  await refused(await call('preferences', 'POST', preferences), 400)
  assert.ok(fixture.calls.every(({ options }) => options.method === 'GET'))
})

test('unset selection has explicit nullable effort and no silent model fallback', async () => {
  const unset = { model: null, effort: null, max_tokens: 2048 }
  queue(json(unset))
  assert.deepEqual(await (await call('preferences', 'POST', unset)).json(), unset)
  assert.equal(fixture.calls.length, 1)
  assert.equal(fixture.calls[0].url.endsWith('/preferences'), true)
  await refused(await call('preferences', 'POST', { ...unset, effort: 'high' }), 400)
})

test('nonfinite, duplicate and unknown preferences fail before catalog calls', async () => {
  for (const body of [JSON.stringify({ ...preferences, provider: 'openai' }),
    '{"model":null,"effort":null,"max_tokens":1e999}',
    '{"model":null,"effort":null,"max_tokens":0}',
    '{"model":null,"effort":null,"max_tokens":32769}',
    '{"model":null,"effort":null,"max_tokens":20,"max_tokens":21}',
    JSON.stringify({ ...preferences, effort: 'invented' })]) {
    await refused(await call('preferences', 'POST', body), 400)
  }
  assert.equal(fixture.calls.length, 0)
})

test('stale, public, cross-provider, duplicate and malformed catalog cannot be used', async () => {
  for (const value of [{ ...catalog(), stale: true }, { ...catalog(), catalog_scope: 'public' },
    { ...catalog(), provider: 'openai' }, catalog([model, model]),
    catalog([{ ...model, pricing: { prompt: -1, completion: null, request: null, unit: 'USD' } }]),
    catalog([{ ...model, id: '../bad' }])]) {
    queue(json(value))
    await refused(await call('preferences', 'POST', preferences), 502)
  }
  assert.ok(fixture.calls.every(({ options }) => options.method === 'GET'))
})

test('missing credential catalog refuses without public-provider fallback', async () => {
  queue(json({ error: 'credential missing' }, { status: 400 }))
  await refused(await call('models'), 400)
  assert.equal(fixture.calls.length, 1)
  assert.equal(fixture.calls[0].url, `http://host.docker.internal:5180/workspaces/${uuid}/models`)
})

test('upstream redirects, content types, duplicate JSON and oversized replies refuse', async () => {
  for (const response of [new Response(null, { status: 302, headers: { location: 'https://evil.example' } }),
    new Response(JSON.stringify(catalog()), { headers: { 'content-type': 'text/plain' } }),
    new Response('{"model":null,"model":null,"effort":null,"max_tokens":2048}', { headers: { 'content-type': 'application/json' } }),
    new Response(' '.repeat(4194305), { headers: { 'content-type': 'application/json' } })]) {
    queue(response)
    await refused(await call('preferences'), 502)
  }
})

test('all overlay TypeScript/TSX parses and manifest exactly binds non-EE files', async () => {
  const manifest = JSON.parse(await readFile(path.join(buildRoot, 'patch-manifest.json'), 'utf8'))
  const found = []
  async function walk(dir) {
    for (const item of await readdir(dir, { withFileTypes: true })) {
      const full = path.join(dir, item.name)
      if (item.isDirectory()) await walk(full)
      else found.push(path.relative(path.join(buildRoot, 'overlay'), full).split(path.sep).join('/'))
    }
  }
  await walk(path.join(buildRoot, 'overlay'))
  assert.deepEqual(found.sort(), manifest.files.map((item) => item.path).sort())
  for (const file of manifest.files) {
    assert.match(file.path, /^apps\/(sim|realtime)\//)
    assert.doesNotMatch(file.path, /(^|\/)(ee|generated)(\/|$)|use-chat\./)
    const content = await readFile(path.join(buildRoot, 'overlay', file.path))
    assert.equal(createHash('sha256').update(content).digest('hex'), file.sha256, file.path)
    if (file.upstream_sha256) {
      const upstream = await readFile(path.join(nativeSource, file.path))
      assert.equal(createHash('sha256').update(upstream).digest('hex'), file.upstream_sha256, file.path)
    }
    await transform(content.toString(), { loader: file.path.endsWith('.tsx') ? 'tsx' : 'ts' })
  }
  for (const [name, expected] of Object.entries(manifest.build_adapters)) {
    assert.equal(createHash('sha256').update(await readFile(path.join(buildRoot, name))).digest('hex'), expected, name)
  }
  assert.equal(manifest.interface.send_path_modified, false)
})

test('hosted selector implementation is preserved behind isolated opt-in component', async () => {
  const relative = 'app/workspace/[workspaceId]/home/components/user-input/components/model-selector.tsx'
  const before = await readFile(path.join(original, relative), 'utf8')
  const after = await readFile(path.join(overlays, relative), 'utf8')
  const originalBody = before.slice(before.indexOf('export function ModelSelector')).replace('export function ModelSelector', 'function HostedModelSelector').trim()
  const changedBody = after.slice(after.indexOf('function HostedModelSelector'), after.indexOf('\nexport function ModelSelector')).trim()
  assert.equal(changedBody.replaceAll('\r\n', '\n'), originalBody.replaceAll('\r\n', '\n'))
  const independent = await readFile(path.join(overlays, relative.replace('model-selector.tsx', 'openrouter-model-selector.tsx')), 'utf8')
  assert.doesNotMatch(independent, /Mothership|FastMode|saveChatEffort|usePreferenceStore/)
  assert.match(independent, /DropdownMenuSearchInput/)
  assert.match(independent, /OpenRouter model/)
})

test('native self-hosted BYOK navigation needs only the local flag, never enterprise', async () => {
  const navigationPath = path.join(overlays, 'components/settings/navigation.ts')
  const navigation = await readFile(navigationPath, 'utf8')
  const icons = navigation.match(/import \{([\s\S]*?)\} from '@sim\/emcn\/icons'/)[1].split(',').map((name) => name.trim()).filter(Boolean)
  const compiled = await build({ entryPoints: [navigationPath], bundle: true, write: false, platform: 'node', format: 'cjs', tsconfigRaw: {},
    plugins: [{ name: 'decorative-icons-only', setup(builder) {
      builder.onResolve({ filter: /^(@sim\/emcn\/icons|@\/components\/icons)$/ }, ({ path: name }) => ({ path: name, namespace: 'icons' }))
      builder.onLoad({ filter: /.*/, namespace: 'icons' }, ({ path: name }) => ({ contents:
        (name === '@sim/emcn/icons' ? icons : ['CodeIcon', 'McpIcon', 'SlackIcon']).map((icon) => `export const ${icon}=()=>null`).join('\n'), loader: 'js' }))
      builder.onResolve({ filter: /^@sim\/platform-authz\/workspace$/ }, () => ({ path:
        path.join(nativeSource, 'packages/platform-authz/src/predicates.ts') }))
      builder.onResolve({ filter: /^@\/lib\/navigation\/paths$/ }, () => ({ path: path.join(original, 'lib/navigation/paths.ts') }))
    } }],
  })
  const loaded = { exports: {} }
  new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(require, loaded, loaded.exports)
  const api = loaded.exports
  const deployment = { hosted: false, billingEnabled: false, chatEnabled: true, azureConfigured: false,
    cohereConfigured: false, features: {} }
  const options = { permission: 'admin', permissionConfig: {}, deployment,
    entitlements: { customBlocks: false, forks: false, inbox: false, sandboxes: false } }
  assert.equal(api.resolveWorkspaceNavigation(options).some((item) => item.id === 'byok'), false)
  const byok = api.resolveWorkspaceNavigation({ ...options, openRouterChat: true }).find((item) => item.id === 'byok')
  assert.equal(byok.canMutate, true)
  const read = api.resolveWorkspaceNavigation({ ...options, permission: 'read', openRouterChat: true }).find((item) => item.id === 'byok')
  assert.equal(read.canMutate, false)
  assert.equal(api.isSelfHostedOverrideEnabled('openrouter-chat', deployment, true), true)
  assert.equal(api.isSelfHostedOverrideEnabled('openrouter-chat', deployment, false), false)
  assert.equal(api.resolveWorkspaceNavigation({ ...options, openRouterChat: true }).some((item) => item.id === 'forks'), false)
})
