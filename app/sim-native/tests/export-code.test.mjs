import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compare } from '../export-code.mjs'

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
