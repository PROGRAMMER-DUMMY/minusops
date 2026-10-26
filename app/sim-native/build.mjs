import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'

const manifest = JSON.parse(readFileSync(new URL('./patch-manifest.json', import.meta.url), 'utf8'))
const root = process.cwd()
const result = spawnSync(process.execPath, [path.join(root, 'node_modules/next/dist/bin/next'), 'build',
  '--webpack', '--debug-build-paths', manifest.preview_build_paths.join(',')],
  { cwd: path.join(root, 'apps/sim'), stdio: 'inherit' })
if (result.error) console.error('Native preview compiler could not start.')
if (result.signal) console.error('Native preview compiler stopped by signal:', result.signal)
if (result.status !== 0 && existsSync('/sys/fs/cgroup/memory.events')) {
  console.error('Compiler memory events:\n' + readFileSync('/sys/fs/cgroup/memory.events', 'utf8'))
}
process.exitCode = result.status ?? 1
