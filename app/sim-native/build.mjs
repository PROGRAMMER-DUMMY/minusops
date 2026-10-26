import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { randomBytes } from 'node:crypto'
import { fileURLToPath } from 'node:url'

const manifest = JSON.parse(readFileSync(new URL('./patch-manifest.json', import.meta.url), 'utf8'))
const root = process.cwd()
const env = { ...process.env, BETTER_AUTH_SECRET: randomBytes(32).toString('hex') }
// shortcut: this internal preview renders on request; qualify pre-rendering before production.
for (const mode of ['compile', 'generate-env']) {
  const result = spawnSync(process.execPath, ['--require', fileURLToPath(new URL('./build-traces.cjs', import.meta.url)),
    path.join(root, 'node_modules/next/dist/bin/next'), 'build',
    '--webpack', '--experimental-build-mode', mode, '--debug-build-paths', manifest.preview_build_paths.join(',')],
    { cwd: path.join(root, 'apps/sim'), stdio: 'inherit', env })
  if (result.error) console.error('Native preview compiler could not start.')
  if (result.signal) console.error('Native preview compiler stopped by signal:', result.signal)
  if (result.status !== 0 && existsSync('/sys/fs/cgroup/memory.events')) {
    console.error('Compiler memory events:\n' + readFileSync('/sys/fs/cgroup/memory.events', 'utf8'))
  }
  if (result.status !== 0) { process.exitCode = result.status ?? 1; break }
}
