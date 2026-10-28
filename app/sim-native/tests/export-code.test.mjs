import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { compare, exportCode, fileHash, snapshot } from '../export-code.mjs'

const file = (name, hash = 'a', bytes = 3) => ({ path: `.next/${name}`, bytes, sha256: hash.repeat(64) })
const runtime = { node: 'pinned', files: { lock: 'pinned' } }
const inventory = (files) => ({ runtime, files })

test('compiled delta keeps identical assets and records exact changes and removals', () => {
  const old = inventory([file('keep.js'), file('replace.js'), file('remove.js')])
  const current = inventory([file('keep.js'), file('replace.js', 'b'), file('added.js')])
  assert.deepEqual(compare(current, old), { changed: [file('replace.js', 'b'), file('added.js')], removed: ['.next/remove.js'] })
  assert.deepEqual(compare(old, old), { changed: [], removed: [] })
})

test('compiled delta refuses dependency drift and unsafe or duplicate file identities', () => {
  const old = inventory([file('keep.js')])
  assert.throws(() => compare({ ...old, runtime: { node: 'other' } }, old))
  for (const row of [{ ...file('a'), path: '.next/../secret' }, { ...file('a'), path: '/operator/file' },
    { ...file('a'), path: '.next\\file' }, { ...file('a'), sha256: 'not-a-hash' },
    file('a', 'a', -1), file('a', 'a', 256 * 1024 ** 2 + 1)]) {
    assert.throws(() => compare(inventory([row]), old))
  }
  assert.throws(() => compare(inventory([file('same.js'), file('same.js')]), old))
  assert.throws(() => compare(inventory([file('same.js'), file('SAME.js')]), old))
})

test('actual compiled export admits the maintained renderer and retains exact overlay hashes', () => {
  const directory = fs.mkdtempSync(path.join(tmpdir(), 'native-code-export-'))
  const root = path.join(directory, 'app')
  function write(name, value) {
    const target = path.join(root, name)
    fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, value)
  }
  try {
    for (const name of ['bun.lock', 'package.json', 'apps/sim/package.json', 'node_modules/next/package.json', 'apps/sim/.next/keep.js']) write(name, '{}')
    const baseline = path.join(directory, 'baseline.json')
    fs.writeFileSync(baseline, JSON.stringify({ version: 1, base_ci_source: 'a'.repeat(40),
      base_ci_config: 'sha256:' + 'b'.repeat(64), ...snapshot(root) }))
    write('apps/sim/.next/added.js', 'compiled fixture')
    const file = 'packages/workflow-renderer/src/workflow-block/block-view.tsx'
    write(file, 'maintained renderer fixture')
    const files = [{ path: file, sha256: fileHash(path.join(root, file)), upstream_sha256: null }]
    write('../overlay/patch-manifest.json', JSON.stringify({ files }))
    const output = path.join(directory, 'export'); fs.mkdirSync(output)
    exportCode(root, output, baseline, 'c'.repeat(40), fileHash(baseline))
    const metadata = JSON.parse(fs.readFileSync(path.join(output, 'native-code-metadata.json')))
    assert.deepEqual(metadata.overlay_files, files)
    assert.deepEqual(metadata.changed.map(file => file.path), ['.next/added.js'])
    assert.equal(fileHash(path.join(output, metadata.archive)), metadata.archive_sha256)
  } finally {
    assert.equal(path.dirname(directory), path.resolve(tmpdir()))
    assert.ok(path.basename(directory).startsWith('native-code-export-'))
    fs.rmSync(directory, { recursive: true })
  }
})
