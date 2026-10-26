// Run inside the disposable native dependency image after applying overlays.
// Check owned files against real native declarations; report unrelated baseline
// diagnostics separately so this is not mistaken for a full native-app check.
const fs = require('node:fs')
const path = require('node:path')
// Native source pins the native TypeScript 7 compiler.
const { spawnSync } = require('node:child_process')
const root = process.env.SIM_NATIVE_APP_ROOT ?? '/app/apps/sim'
const overlay = process.env.SIM_NATIVE_OVERLAY_ROOT ?? '/overlay'
const manifest = JSON.parse(fs.readFileSync(path.join(overlay, 'patch-manifest.json'), 'utf8'))
const nativeRoot = path.resolve(root, '../..')
const files = manifest.files.filter((file) => file.path.startsWith('apps/sim/'))
  .map((file) => path.join(nativeRoot, file.path))
const owned = new Set(files)
const configPath = path.join(root, 'openrouter-overlay-tsconfig.json')
fs.writeFileSync(configPath, JSON.stringify({ extends: path.join(root, 'tsconfig.json'),
  compilerOptions: { noEmit: true, incremental: false, skipLibCheck: true }, files,
  include: ['types/**/*.d.ts', 'next-env.d.ts'] }))
// Invoke the pinned binary directly so a timeout cannot leave its Windows child running.
const compiler = require(path.join(nativeRoot, 'node_modules/typescript/lib/getExePath.js')).default()
const result = spawnSync(compiler, ['--project', configPath, '--pretty', 'false'], { cwd: root, encoding: 'utf8', timeout: 180000,
  maxBuffer: 8 * 1024 * 1024, env: { ...process.env, GOMEMLIMIT: '3GiB' } })
if (result.error || result.signal) {
  console.error('Native compiler did not finish:', result.error?.code ?? result.signal)
  process.exit(1)
}
const diagnostics = result.stdout.split('\n').filter((line) => line.includes('error TS'))
const scoped = diagnostics.filter((line) => {
  const filename = line.split('(')[0]
  return !line.includes('(') || owned.has(path.resolve(root, filename))
})
console.log(`Owned files: ${files.length}; owned/config diagnostics: ${scoped.length}; unrelated diagnostics: ${diagnostics.length - scoped.length}`)
if (result.stderr) console.log(result.stderr)
if (scoped.length) console.log(scoped.join('\n'))
if (result.status && !diagnostics.length) console.error(`Compiler exited with status ${result.status}; no completed type-check result.`)
process.exitCode = scoped.length || (result.status && !diagnostics.length) ? 1 : 0
