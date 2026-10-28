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
    await writeFile(path.join(directory, 'build-traces.cjs'), await readFile(path.join(buildRoot, 'build-traces.cjs')))
    await writeFile(path.join(directory, 'patch-manifest.json'), JSON.stringify({ preview_build_paths: ['app/workspace/**'] }))
    const cli = path.join(directory, 'node_modules/next/dist/bin/next')
    await mkdir(path.dirname(cli), { recursive: true })
    const compilerPackage = path.join(directory, 'node_modules/next/package.json')
    await writeFile(compilerPackage, JSON.stringify({ version: '16.4.1' }))
    const tracePath = path.join(directory, 'node_modules/next/dist/build/collect-build-traces.js')
    await mkdir(path.dirname(tracePath), { recursive: true })
    await writeFile(tracePath, 'throw new Error("Packaging tracer must not execute in this preview")')
    await mkdir(path.join(directory, 'apps/sim'), { recursive: true })
    await writeFile(cli, 'process.exit(process.cwd().endsWith(require("node:path").join("apps", "sim")) && process.argv.includes("--webpack") ? 17 : 9)')
    const result = spawnSync(process.execPath, ['build.mjs'], { cwd: directory, encoding: 'utf8', timeout: 10000 })
    assert.equal(result.status, 17)
    await mkdir(path.join(directory, '.next/server'), { recursive: true })
    const envChunk = path.join(directory, '.next/server/native-env.js')
    await writeFile(envChunk, 'const imported={env:{NEXT_PUBLIC_APP_URL:"native-env-preserved"}}; module.exports=imported/* .env */ .env.NEXT_PUBLIC_APP_URL')
    await writeFile(cli, `const assert = require('node:assert/strict');
      require('node:fs').appendFileSync(${JSON.stringify(path.join(directory, 'modes.txt'))}, process.argv[process.argv.indexOf('--experimental-build-mode') + 1] + '\\n');
      assert.equal(process.env.NODE_OPTIONS, '--max-old-space-size=12288');
      assert.match(process.env.BETTER_AUTH_SECRET, /^[a-f0-9]{64}$/);
      assert.notEqual(process.env.BETTER_AUTH_SECRET, 'existing-build-secret');
      require(${JSON.stringify(tracePath)}).collectBuildTraces();
      if (process.argv.includes('generate-env')) require(${JSON.stringify(require.resolve('next/dist/lib/inline-static-env'))}).inlineStaticEnv({distDir:${JSON.stringify(path.join(directory, '.next'))},config:{env:{},deploymentId:''}});`)
    const worker = spawnSync(process.execPath, ['build.mjs'], { cwd: directory, encoding: 'utf8', timeout: 10000,
      env: { ...process.env, BETTER_AUTH_SECRET: 'existing-build-secret', NODE_OPTIONS: '--max-old-space-size=12288', NEXT_PUBLIC_APP_URL:origin } })
    assert.equal(worker.status, 0, worker.stderr)
    assert.equal(await readFile(path.join(directory, 'modes.txt'), 'utf8'), 'compile\n')
    const loaded = { exports:{} }
    new Function('module', await readFile(envChunk, 'utf8'))(loaded)
    assert.equal(loaded.exports, 'native-env-preserved')
    await writeFile(compilerPackage, JSON.stringify({ version: '0.0.0' }))
    const unsupported = spawnSync(process.execPath, ['build.mjs'], { cwd: directory, encoding: 'utf8', timeout: 10000 })
    assert.equal(unsupported.status, 1)
    assert.match(unsupported.stderr, /Unsupported native preview compiler version/)
    await writeFile(compilerPackage, JSON.stringify({ version: '16.4.1' }))
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
    builder.onResolve({ filter: /^(?:@sim\/db(?:\/schema)?|drizzle-orm)$/ },
      ({ path: name }) => ({ path: name, namespace: 'database' }))
    builder.onLoad({ filter: /.*/, namespace: 'database' }, ({ path: name }) => ({ contents:
      name === '@sim/db/schema' ? `export const copilotChats = {
        id: 'id', workspaceId: 'workspaceId', userId: 'userId', deletedAt: 'deletedAt' };`
        : name === 'drizzle-orm' ? `
          export const eq = (column, value) => row => row[column] === value;
          export const isNull = column => row => row[column] === null;
          export const and = (...conditions) => row => conditions.every(condition => condition(row));`
          : `export const db = { select: fields => ({ from: table => ({ where: predicate => ({
              limit: async limit => {
                const fixture = globalThis.__openRouterTest;
                fixture.databaseCalls.push({ fields, table, limit });
                if (fixture.databaseError) throw fixture.databaseError;
                return fixture.chatRows.filter(predicate).slice(0, limit)
                  .map(row => Object.fromEntries(Object.entries(fields).map(([key, column]) => [key, row[column]])));
              }
            }) }) }) };`, loader: 'js' }))
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
  fixture = { session: { user: { id: 'native-user' } }, permission: 'admin', permissionCalls: [], calls: [], responses: [],
    chatRows: [], databaseCalls: [], databaseError: null }
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

test('tilde aliases survive catalog validation and exact preference selection', async () => {
  const alias = '~openai/gpt-astra-latest'
  const rows = [{ ...model, id: alias }]
  queue(json(catalog(rows)))
  const listed = await call('models')
  assert.equal(listed.status, 200)
  assert.equal((await listed.json()).models[0].id, alias)
  const selected = { ...preferences, model: alias }
  queue(json(catalog(rows)), json(selected))
  const saved = await call('preferences', 'POST', selected)
  assert.equal(saved.status, 200)
  assert.deepEqual(await saved.json(), selected)
  assert.equal(JSON.parse(fixture.calls[2].options.body).model, alias)
  const calls = fixture.calls.length
  for (const id of ['~', '~~vendor/chat', 'vendor/~chat', '~vendor/../chat', '~' + 'a'.repeat(200)]) {
    await refused(await call('preferences', 'POST', { ...preferences, model: id }), 400)
  }
  assert.equal(fixture.calls.length, calls)
})

test('native architecture reads are scoped and writes require workspace admin', async () => {
  const workspace = { schema_version: 1, run_id: null, source_status: 'UNSELECTED', runs: [], architecture: null,
    metadata: null, context_enabled: true, chat_ids: [], draft: null, host_review: null, icons: {}, focus: null, layout: null, execution_authority: 'NONE' }
  fixture.permission = 'read'
  queue(json(workspace))
  const response = await call('architecture')
  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), workspace)
  assert.equal(fixture.calls[0].url, `http://host.docker.internal:5180/workspaces/${uuid}/architecture?user_id=native-user`)
  await refused(await call('architecture', 'POST', { operation: 'select', run_id: 'source-test' }), 403)
  assert.equal(fixture.calls.length, 1)
  for (const operation of ['plan', 'delivery', 'export']) {
    queue(json({ status: 'UNKNOWN', execution_authority: 'NONE' }))
    assert.equal((await call('architecture', 'POST', { operation, run_id: 'source-test', revision: 'a'.repeat(64) })).status, 200)
  }
})

test('native architecture admits only bounded inspection and proposal operations', async () => {
  const workspace = { schema_version: 1, run_id: null, source_status: 'UNSELECTED', runs: [], architecture: null,
    metadata: null, context_enabled: true, chat_ids: [], draft: null, host_review: null, icons: {}, focus: null, layout: null, execution_authority: 'NONE' }
  queue(json(workspace))
  assert.equal((await call('architecture', 'POST', { operation: 'select', run_id: 'source-test' })).status, 200)
  assert.deepEqual(JSON.parse(fixture.calls[0].options.body), { operation: 'select', run_id: 'source-test' })
  for (const body of [{ operation: 'publish', run_id: 'source-test' },
    { operation: 'apply', run_id: 'source-test' }, { operation: 'shell', command: 'arbitrary' },
    { operation: 'select', run_id: '../source-test' }, { operation: 'select', run_id: null },
    { operation: 'select', run_id: 'source-test', path: '/operator' }]) {
    await refused(await call('architecture', 'POST', body), 400)
  }
  assert.equal(fixture.calls.length, 1)
  queue(json({ ...workspace, unexpected: token }))
  await refused(await call('architecture'), 502)
})

test('draft commands bind the trusted session actor and refuse reader or spoofed writes', async () => {
  const body = { operation: 'command', run_id: 'source-test', revision: 'a'.repeat(64),
    command_id: uuid, expected_draft_revision: 1, command: { type: 'undo' } }
  fixture.permission = 'read'
  await refused(await call('architecture', 'POST', body), 403)
  assert.equal(fixture.calls.length, 0)
  fixture.permission = 'admin'
  const draft = { schema_version: 1, kind: 'minusops-architecture-proposal-draft', workspace_id: uuid,
    run_id: 'source-test', revision: body.revision, draft_revision: 2, status: 'EMPTY', edits: [], change: null,
    can_undo: false, can_redo: true, capabilities: { schema_version: 1, commands: ['field_edit', 'undo', 'redo', 'reset'],
      max_commands: 128, max_edits: 32, publication: false, execution_authority: 'NONE' }, execution_authority: 'NONE',
    receipt: { command_id: uuid, applied_draft_revision: 2, replayed: false } }
  queue(json(draft))
  assert.equal((await call('architecture', 'POST', body)).status, 200)
  assert.deepEqual(JSON.parse(fixture.calls[0].options.body), { ...body, user_id: 'native-user' })
  await refused(await call('architecture', 'POST', { ...body, user_id: 'forged-user' }), 400)
  await refused(await call('architecture', 'GET', undefined, { query: '?run_id=source-test&user_id=forged-user' }), 400)
  assert.equal(fixture.calls.length, 1)
})

test('architecture queries forward only one explicit validated run and never accept write queries', async () => {
  const workspace = { schema_version: 1, run_id: null, source_status: 'UNSELECTED', runs: [], architecture: null,
    metadata: null, context_enabled: true, chat_ids: [], draft: null, host_review: null, icons: {}, focus: null, layout: null, execution_authority: 'NONE' }
  fixture.permission = 'read'
  queue(json(workspace))
  assert.equal((await call('architecture', 'GET', undefined, { query: '?run_id=source-test' })).status, 200)
  assert.equal(fixture.calls[0].url, `http://host.docker.internal:5180/workspaces/${uuid}/architecture?run_id=source-test&user_id=native-user`)
  for (const query of ['?run_id=source-test&run_id=other-run', '?run_id=source-test&run%5Fid=other-run',
    '?run_id=..%2Fprivate', '?run_id=', '?run_id=source-test&path=private', '?chat_id=source-test']) {
    await refused(await call('architecture', 'GET', undefined, { query }), 400)
  }
  fixture.permission = 'admin'
  await refused(await call('architecture', 'POST', { operation: 'select', run_id: 'source-test' },
    { query: '?run_id=other-run' }), 400)
  await refused(await call('models', 'GET', undefined, { query: '?run_id=source-test' }), 400)
  assert.equal(fixture.calls.length, 1)
  assert.equal(fixture.databaseCalls.length, 0)
})

test('composition catalog and pending layout use the session actor and separate reader permissions', async () => {
  const common = { run_id: 'source-test', revision: 'a'.repeat(64), draft_revision: 0 }
  const catalog = { operation: 'catalog', ...common }
  const layout = { operation: 'layout', ...common, positions: [] }
  fixture.permission = 'read'
  queue(json({ kind: 'minusops-composition-catalog', ...common, modules: [], ports: [], execution_authority: 'NONE',
    provider_support: 'AWS_ONLY', module_release_authority: 'UNVERIFIED' }))
  assert.equal((await call('architecture', 'POST', catalog)).status, 200)
  assert.deepEqual(JSON.parse(fixture.calls[0].options.body), { ...catalog, user_id: 'native-user' })
  await refused(await call('architecture', 'POST', layout), 403)
  fixture.permission = 'admin'
  queue(json({ status: 'LAYOUT_SAVED', execution_authority: 'NONE' }))
  assert.equal((await call('architecture', 'POST', layout)).status, 200)
  assert.deepEqual(JSON.parse(fixture.calls[1].options.body), { ...layout, user_id: 'native-user' })
  await refused(await call('architecture', 'POST', { ...catalog, user_id: 'forged' }), 400)
  await refused(await call('architecture', 'POST', { ...layout, user_id: 'forged' }), 400)
  assert.equal(fixture.calls.length, 2)
})

test('chat status and context binding require a live chat owned by the current user and workspace', async () => {
  const chatId = 'b076d0ab-2d86-4ddc-9268-0aee617c4dc4'
  const owner = { id: chatId, workspaceId: uuid, userId: 'native-user', deletedAt: null }
  const binding = { operation: 'bind_chat', run_id: 'source-test', revision: 'a'.repeat(64),
    chat_id: chatId, subject: null, enabled: true }
  for (const row of [null, { ...owner, id: uuid }, { ...owner, workspaceId: chatId },
    { ...owner, userId: 'other-user' }, { ...owner, deletedAt: new Date().toISOString() }]) {
    fixture.chatRows = row ? [row] : []
    await refused(await call('status', 'GET', undefined, { query: `?chat_id=${chatId}` }), 403)
    await refused(await call('architecture', 'POST', binding), 403)
  }
  assert.equal(fixture.calls.length, 0)
  fixture.chatRows = [owner]
  fixture.permission = 'read'
  queue(json(status))
  assert.equal((await call('status', 'GET', undefined, { query: `?chat_id=${chatId}` })).status, 200)
  assert.equal(fixture.calls[0].url,
    `http://host.docker.internal:5180/workspaces/${uuid}/status?chat_id=${chatId}&user_id=native-user`)
  await refused(await call('architecture', 'POST', binding), 403)
  fixture.permission = 'admin'
  queue(json({ status: 'CONTEXT_BOUND', execution_authority: 'NONE' }))
  assert.equal((await call('architecture', 'POST', binding)).status, 200)
  assert.deepEqual(JSON.parse(fixture.calls[1].options.body), { ...binding, user_id: 'native-user' })
  assert.equal(fixture.calls[1].url, `http://host.docker.internal:5180/workspaces/${uuid}/architecture`)
  assert.ok(fixture.databaseCalls.every(({ limit }) => limit === 1))
  fixture.databaseError = new Error('private diagnostic')
  await refused(await call('status', 'GET', undefined, { query: `?chat_id=${chatId}` }), 502)
  await refused(await call('architecture', 'POST', binding), 502)
  assert.equal(fixture.calls.length, 2)
})

test('chat queries and bindings reject spoofed ownership and malformed scope before database access', async () => {
  const chatId = 'b076d0ab-2d86-4ddc-9268-0aee617c4dc4'
  const binding = { operation: 'bind_chat', run_id: 'source-test', revision: 'a'.repeat(64),
    chat_id: chatId, subject: null, enabled: true }
  for (const query of ['?chat_id=../bad', '?chat_id=', `?chat_id=${chatId}&chat_id=${uuid}`,
    `?chat_id=${chatId}&chat%5Fid=${uuid}`, `?chat_id=${chatId}&user_id=other-user`,
    `?chat_id=${chatId}&run_id=other-run`]) {
    await refused(await call('status', 'GET', undefined, { query }), 400)
  }
  for (const body of [{ ...binding, user_id: 'other-user' }, { ...binding, chat_id: '../bad' },
    { ...binding, revision: 'stale' }, { ...binding, subject: { kind: 'source_node', id: 'unknown' } },
    { ...binding, enabled: 'true' }, { ...binding, path: '/private' }]) {
    await refused(await call('architecture', 'POST', body), 400)
  }
  assert.equal(fixture.calls.length, 0)
  assert.equal(fixture.databaseCalls.length, 0)
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
    assert.match(file.path, /^(?:apps\/(sim|realtime)\/|packages\/workflow-renderer\/src\/workflow-block\/block-view\.tsx$)/)
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

test('architecture conversations require an explicit matching workspace/run and valid native chat id', async () => {
  const filename = path.join(overlays, 'app/workspace/[workspaceId]/architecture/chat.tsx')
  const compiled = await build({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs',
    jsx: 'automatic', external: ['react', 'react/jsx-runtime', 'zod'], tsconfigRaw: {},
    plugins: [{ name: 'native-chat-boundaries', setup(builder) {
      builder.onResolve({ filter: /^@\/lib\/api\/contracts\/minusops-architecture$/ }, () => ({
        path: path.join(overlays, 'lib/api/contracts/minusops-architecture.ts'),
      }))
      builder.onResolve({ filter: /^(?:@|nuqs)/ }, ({ path: name }) => ({ path: name, namespace: 'boundary' }))
      builder.onLoad({ filter: /.*/, namespace: 'boundary' }, ({ path: name }) => {
        const values = {
          '@sim/emcn': `import React from 'react'; export const Button=({children,onClick,disabled,...props})=>React.createElement('button',{'aria-label':props['aria-label'],disabled},children); export const ChipSelect=()=>null; export const Popover=()=>null;export const PopoverTrigger=()=>null;export const PopoverContent=()=>null;`,
          '@sim/emcn/icons': 'export const Plus=()=>null;',
          '@tanstack/react-query': 'export const useQueryClient=()=>({invalidateQueries:()=>Promise.resolve()});export const useQuery=()=>({});',
          nuqs: 'export const parseAsString={}; export const useQueryStates=()=>[fixture.selection,()=>Promise.resolve()];',
          '@/lib/api/client/request': 'export const requestJson=()=>{throw Error("Rendering must not create a chat")};',
          '@/lib/api/contracts/mothership-chats': 'export const createMothershipChatContract={};',
          '@/lib/api/contracts/openrouter-chat': 'export const openRouterStatusContract={};',
          '@/lib/auth/client': 'export const useSession=()=>({data:{user:{id:"viewer"}}});',
          '@/app/workspace/[workspaceId]/home/components/mothership-chat': `import React from 'react'; export const MothershipChat=props=>{fixture.conversation=props;return React.createElement('div',{'data-chat':props.chatId})};`,
          '@/app/workspace/[workspaceId]/home/hooks': 'export const getWorkflowCopilotUseChatOptions=()=>({apiPath:"/api/mothership/chat"}); export const useChat=(...args)=>{fixture.calls.push(args);return {messages:[],isSending:false,isReconnecting:false,sendMessage:(...args)=>fixture.sent.push(args)}};',
          '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider': 'export const useUserPermissionsContext=()=>({canAdmin:true});',
          '@/hooks/queries/mothership-chats': 'export const mothershipChatKeys={list:()=>[]}; export const useMothershipChats=()=>({data:[]});',
          '@/hooks/queries/openrouter-chat': 'export const openRouterChatKeys={status:(id,chatId)=>["status",id,chatId]};',
          '@/hooks/queries/minusops-architecture': 'export const architectureKeys={view:(id,run)=>["architecture",id,run]};',
        }
        assert.ok(name in values, name)
        return { contents: values[name], loader: 'js' }
      })
    } }],
  })
  const React = require('react')
  const { renderToStaticMarkup } = require('react-dom/server')
  const chatId = 'b076d0ab-2d86-4ddc-9268-0aee617c4dc4'
  const valid = { architectureChat: chatId, architectureChatRun: 'source-test', architectureChatWorkspace: uuid }
  for (const selection of [{}, { ...valid, architectureChatRun: 'other-run' },
    { ...valid, architectureChatWorkspace: 'other-workspace' }, { ...valid, architectureChat: '../bad' }, valid]) {
    const state = { selection, calls: [], sent: [], proposals: [] }
    const loaded = { exports: {} }
    new Function('require', 'module', 'exports', 'fixture', compiled.outputFiles[0].text)(require, loaded, loaded.exports, state)
    const html = renderToStaticMarkup(React.createElement(loaded.exports.ArchitectureChat,
      { workspaceId: uuid, runId: 'source-test', binding: { revision: 'a'.repeat(64), subject: null, enabled: true },
        onBusyChange: () => {}, onProposal: value => state.proposals.push(value) }))
    if (selection === valid) {
      assert.match(html, new RegExp(`data-chat="${chatId}"`))
      assert.equal(state.calls.length, 1)
      assert.deepEqual(state.calls[0].slice(0, 2), [uuid, chatId])
      assert.equal(state.calls[0][2].apiPath, '/api/mothership/chat')
      assert.equal(typeof state.calls[0][2].onToolResult, 'function')
      const proposal = { kind: 'minusops-ai-source-proposal', message_id: uuid, tool_call_id: 'call_1',
        run_id: 'source-test', revision: 'a'.repeat(64), edit_count: 1, change_hash: 'b'.repeat(64),
        requires_review: true, source_written: false }
      const receive = state.calls[0][2].onToolResult
      receive('edit_workflow', true, proposal)
      receive('propose_source_edits', false, proposal)
      receive('propose_source_edits', true, { ...proposal, source_written: true })
      receive('propose_source_edits', true, { ...proposal, run_id: 'other-run' })
      receive('propose_source_edits', true, { ...proposal, revision: 'b'.repeat(64) })
      assert.deepEqual(state.proposals, [])
      receive('propose_source_edits', true, proposal)
      assert.deepEqual(state.proposals, [{ chat_id: chatId, message_id: uuid, tool_call_id: 'call_1' }])
      assert.match(html, /inert=""/)
      assert.equal(state.conversation.draftScopeKey, `${uuid}:architecture:source-test:${chatId}`)
      await state.conversation.onSubmit('must wait for context binding')
      assert.deepEqual(state.sent, [])
    } else {
      assert.match(html, /Start a chat for this run/)
      assert.equal(state.calls.length, 0)
    }
  }
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

test('architecture navigation isolates per-run queries and preserves cached views on action failure', async () => {
  const filename = path.join(overlays, 'hooks/queries/minusops-architecture.ts')
  const compiled = await build({ entryPoints: [filename], bundle: true, write: false, platform: 'node', format: 'cjs',
    packages: 'external', tsconfigRaw: {}, plugins: [{ name: 'architecture-query-boundaries', setup(builder) {
      builder.onResolve({ filter: /^@tanstack\/react-query$/ }, () => ({ path: 'queries', namespace: 'fixture' }))
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
        export const useQuery=options=>options;
        export const useMutation=options=>options;
        export const useQueryClient=()=>fixture.client;`, loader: 'js' }))
      builder.onResolve({ filter: /^@\/hooks\/queries\/openrouter-chat$/ }, () => ({ path: 'status', namespace: 'status' }))
      builder.onLoad({ filter: /.*/, namespace: 'status' }, () => ({ contents: 'export const openRouterChatKeys={status:id=>["status",id]};', loader: 'js' }))
      builder.onResolve({ filter: /^@\/lib\/api\/contracts\/minusops-architecture$/ }, () => ({ path: path.join(overlays, 'lib/api/contracts/minusops-architecture.ts') }))
    } }],
  })
  const saved = []
  const cancelled = []
  const invalidated = []
  const state = { client: { setQueryData: (...args) => saved.push(args),
    cancelQueries: async (args) => cancelled.push(args), invalidateQueries: async (args) => invalidated.push(args) } }
  const pending = []
  const calls = []
  const fetch = async (...args) => { calls.push(args); return pending.shift() }
  const loaded = { exports: {} }
  new Function('require', 'module', 'exports', 'fixture', 'fetch', compiled.outputFiles[0].text)(require, loaded, loaded.exports, state, fetch)
  const query = loaded.exports.useArchitectureWorkspace(uuid)
  const view = loaded.exports.useArchitectureWorkspace(uuid, 'source-test')
  const otherView = loaded.exports.useArchitectureWorkspace(uuid, 'other-run')
  const selection = loaded.exports.useArchitectureAction(uuid)
  const metadata = { run_id: 'source-test', name: 'Source test', locked: false, metadata_revision: 0, design_status: 'SOURCE' }
  const workspace = { schema_version: 1, run_id: 'source-test', source_status: 'UNAVAILABLE', runs: [metadata], metadata,
    context_enabled: true, chat_ids: [], draft: null, host_review: null, architecture: null, icons: {}, focus: null, layout: null, execution_authority: 'NONE' }
  const signal = new AbortController().signal
  pending.push(Response.json(workspace))
  assert.deepEqual(await query.queryFn({ signal }), workspace)
  assert.equal(calls[0][1].signal, signal)
  assert.deepEqual(query.queryKey, ['minusops-architecture', uuid, 'view', null])
  assert.deepEqual(view.queryKey, ['minusops-architecture', uuid, 'view', 'source-test'])
  assert.deepEqual(otherView.queryKey, ['minusops-architecture', uuid, 'view', 'other-run'])
  assert.notDeepEqual(view.queryKey, otherView.queryKey)
  assert.equal(calls[0][0], `/api/workspaces/${uuid}/assistant/architecture`)
  for (const [run, scoped] of [['source-test', view], ['other-run', otherView]]) {
    const scopedMetadata = { ...metadata, run_id: run }
    const scopedWorkspace = { ...workspace, run_id: run, metadata: scopedMetadata, runs: [metadata, { ...metadata, run_id: 'other-run' }] }
    pending.push(Response.json(scopedWorkspace))
    assert.deepEqual(await scoped.queryFn({ signal }), scopedWorkspace)
    assert.equal(calls.at(-1)[0], `/api/workspaces/${uuid}/assistant/architecture?run_id=${run}`)
    assert.equal(calls.at(-1)[1].cache, 'no-store')
    assert.equal(calls.at(-1)[1].credentials, 'same-origin')
  }
  assert.equal(loaded.exports.useArchitectureWorkspace('').enabled, false)
  await selection.onMutate()
  assert.deepEqual(cancelled, [{ queryKey: ['minusops-architecture', uuid] }])
  const beforeInvalid = calls.length
  await assert.rejects(selection.mutationFn({ operation: 'select', run_id: '../private' }))
  assert.equal(calls.length, beforeInvalid)
  pending.push(new Response('', { status: 403 }))
  await assert.rejects(selection.mutationFn({ operation: 'select', run_id: 'other-run' }), /could not be opened/)
  assert.deepEqual(saved, [])
  pending.push(Response.json({ ...workspace, execution_authority: 'APPLY' }))
  await assert.rejects(selection.mutationFn({ operation: 'select', run_id: 'source-test' }))
  assert.deepEqual(saved, [])
  pending.push(Response.json(workspace))
  const accepted = await selection.mutationFn({ operation: 'select', run_id: 'source-test' })
  await selection.onSuccess(accepted)
  assert.deepEqual(saved, [[view.queryKey, workspace]])
  assert.deepEqual(invalidated, [{ queryKey: ['minusops-architecture', uuid] }, { queryKey: ['status', uuid] }])
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body), { operation: 'select', run_id: 'source-test' })
  assert.match(calls.at(-1)[0], new RegExp(`/workspaces/${uuid}/assistant/architecture$`))
})

test('chat status hooks isolate chat query keys and send only the requested chat scope', async () => {
  const compiled = await build({ entryPoints: [path.join(overlays, 'hooks/queries/openrouter-chat.ts')],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', tsconfigRaw: {},
    plugins: [{ name: 'chat-status-query-boundaries', setup(builder) {
      builder.onResolve({ filter: /^(?:react$|@)/ }, ({ path: name }) => ({ path: name, namespace: 'boundary' }))
      builder.onLoad({ filter: /.*/, namespace: 'boundary' }, ({ path: name }) => {
        const values = {
          react: 'export const useEffect=()=>{};',
          '@tanstack/react-query': 'export const useQuery=options=>options; export const useMutation=options=>options; export const useQueryClient=()=>({});',
          '@/lib/api/client/request': 'export const requestJson=(...args)=>{fixture.calls.push(args);return Promise.resolve({})};',
          '@/lib/api/contracts/openrouter-chat': `export const openRouterStatusContract='status', openRouterPreferencesContract='preferences',
            openRouterModelsContract='models', saveOpenRouterPreferencesContract='save', saveOpenRouterKeyContract='key', deleteOpenRouterKeyContract='delete';`,
          '@/stores/openrouter-chat/store': 'export const useOpenRouterChatStore=selector=>selector({setPreferences:()=>{},setModels:()=>{}});',
        }
        assert.ok(name in values, name)
        return { contents: values[name], loader: 'js' }
      })
    } }],
  })
  const state = { calls: [] }
  const loaded = { exports: {} }
  new Function('require', 'module', 'exports', 'fixture', compiled.outputFiles[0].text)(require, loaded, loaded.exports, state)
  const chatId = 'b076d0ab-2d86-4ddc-9268-0aee617c4dc4'
  const api = loaded.exports
  const workspace = api.useOpenRouterChat(uuid)
  const first = api.useOpenRouterChat(uuid, chatId)
  const second = api.useOpenRouterChat(uuid, uuid)
  assert.deepEqual(workspace.status.queryKey, ['openrouter-chat', uuid, 'status'])
  assert.deepEqual(first.status.queryKey, ['openrouter-chat', uuid, 'status', chatId])
  assert.notDeepEqual(first.status.queryKey, second.status.queryKey)
  assert.deepEqual(first.preferences.queryKey, second.preferences.queryKey)
  assert.equal(api.useOpenRouterChat('').status.enabled, false)
  const signal = new AbortController().signal
  for (const hook of [workspace, first, second]) await hook.status.queryFn({ signal })
  assert.deepEqual(state.calls, [
    ['status', { params: { id: uuid }, query: {}, signal }],
    ['status', { params: { id: uuid }, query: { chat_id: chatId }, signal }],
    ['status', { params: { id: uuid }, query: { chat_id: uuid }, signal }],
  ])
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
