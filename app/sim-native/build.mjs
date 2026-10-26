import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const manifest = JSON.parse(readFileSync(new URL('./patch-manifest.json', import.meta.url), 'utf8'))
const result = spawnSync(process.execPath, ['node_modules/next/dist/bin/next', 'build', 'apps/sim',
  '--turbopack', '--debug-build-paths', manifest.preview_build_paths.join(',')], { stdio: 'inherit' })
if (result.error) console.error('Native preview compiler could not start.')
process.exitCode = result.status ?? 1
