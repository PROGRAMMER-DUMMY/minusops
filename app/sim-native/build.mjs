import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const manifest = JSON.parse(readFileSync(new URL('./patch-manifest.json', import.meta.url), 'utf8'))
const result = spawnSync(process.execPath, ['node_modules/next/dist/bin/next', 'build', 'apps/sim',
  '--webpack', '--debug-build-paths', manifest.preview_build_paths.join(',')], { stdio: 'inherit' })
if (result.error) console.error('Native preview compiler could not start.')
if (result.signal) console.error('Native preview compiler stopped by signal:', result.signal)
if (result.status !== 0 && existsSync('/sys/fs/cgroup/memory.events')) {
  console.error('Compiler memory events:\n' + readFileSync('/sys/fs/cgroup/memory.events', 'utf8'))
}
process.exitCode = result.status ?? 1
