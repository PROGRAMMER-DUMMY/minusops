import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(path.join(process.env.SIM_NATIVE_DEPENDENCIES_ROOT ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../sim'), 'package.json'))
const { transform } = require('esbuild')
const filename = new URL('../overlay/apps/sim/app/workspace/[workspaceId]/architecture/canvas-model.ts', import.meta.url)
const source = await readFile(filename, 'utf8')
const compiled = await transform(source, { loader: 'ts', format: 'cjs' })
const loaded = { exports: {} }
new Function('module', 'exports', compiled.code)(loaded, loaded.exports)
const { capturePositions, applyPositions, recordLayout, undoLayout, redoLayout, cardSummary, parseField, editorError, replacementEdits } = loaded.exports

const nodes = () => [
  { id: 'one', position: { x: 0, y: 0 }, selected: true, data: { source: 'untouched.tf', bucket: 'unchanged' } },
  { id: 'two', position: { x: 100, y: 50 }, selected: true, data: { source: 'other.tf' } },
]

test('one group drag undoes and redoes every position without changing source data or selection', () => {
  const initial = nodes()
  const moved = initial.map(node => ({ ...node, position: { x: node.position.x + 32, y: node.position.y + 16 } }))
  const history = recordLayout({ past: [], future: [] }, capturePositions(initial), capturePositions(moved))
  assert.equal(history.past.length, 1)
  const undone = undoLayout(history, capturePositions(moved))
  assert.deepEqual(applyPositions(moved, undone.positions), initial)
  const redone = redoLayout(undone.history, capturePositions(initial))
  assert.deepEqual(applyPositions(initial, redone.positions), moved)
  assert.deepEqual(initial, nodes())
})

test('selection-only changes do not consume undo and a new drag clears redo', () => {
  const initial = capturePositions(nodes())
  const history = recordLayout({ past: [], future: [initial] }, initial, capturePositions(nodes().map(n => ({ ...n, selected: false }))))
  assert.equal(history.past.length, 0)
  assert.equal(history.future.length, 1)
  const moved = initial.map(n => ({ ...n, x: n.x + 8 }))
  assert.equal(recordLayout(history, initial, moved).future.length, 0)
})

test('layout history is bounded and never applies removed or foreign node identities', () => {
  let history = { past: [], future: [] }
  for (let step = 0; step < 90; step++) history = recordLayout(history, [{ id: 'one', x: step, y: 0 }], [{ id: 'one', x: step + 1, y: 0 }])
  assert.equal(history.past.length, 50)
  assert.deepEqual(applyPositions(nodes(), [{ id: 'foreign', x: 9, y: 9 }]), nodes())
  assert.equal(undoLayout({ past: [], future: [] }, []).positions, null)
  assert.equal(redoLayout({ past: [], future: [] }, []).positions, null)
})

test('card summaries use only admitted public fields and preserve false and zero', () => {
  const node = { kind: 'resource', fields: { bucket: { value: 'landing' }, force_destroy: { value: false },
    number_of_workers: { value: 0 }, password: { value: 'never render' }, tags: { value: { token: 'never render' } } } }
  const rows = cardSummary(node, 0)
  assert.equal(rows.length, 3)
  assert.deepEqual(rows.map(row => row.value), ['landing', '0', 'false'])
  assert.ok(!JSON.stringify(rows).includes('never render'))
  assert.deepEqual(cardSummary({ kind: 'module', fields: {} }, 18), [{ label: 'Components', value: '18' }])
})

test('typed drafts preserve empty strings, false, zero, null and literal collections', () => {
  for (const [original, text, expected] of [['text', '', ''], [true, 'false', false], [3, '0', 0],
    [null, 'null', null], [[1], '[0,false]', [0, false]], [{ old: 1 }, '{"next":0}', { next: 0 }]]) {
    assert.deepEqual(parseField({ kind: 'literal', value: original }, text), expected)
  }
  assert.equal(parseField({ kind: 'reference', value: 'var.old' }, 'var.next'), 'var.next')
  for (const [value, text] of [[1, ''], [1, 'null'], [1, 'false'], [true, '0'], [[], '{}'], [{}, '[]'],
    [null, '""'], [0, '1e999'], [0, '9007199254740992']]) {
    assert.throws(() => parseField({ kind: 'literal', value }, text))
  }
})

test('editor errors disclose only fixed validation sentences', () => {
  assert.equal(editorError(new Error('Enter a valid number or JSON value.')), 'Enter a valid number or JSON value.')
  for (const error of [new Error('synthetic-private-value'), { message: 'unsafe value' }, null]) {
    assert.equal(editorError(error), 'Request unavailable. Refresh and try again.')
  }
})

test('replacement previews only editable literal strings without changing source or addresses', () => {
  const input = [{ id: 'one', address: 'raw-old', fields: { bucket: { kind: 'literal', editable: true, value: 'old-old' },
    ref: { kind: 'reference', editable: true, value: 'var.old' }, password: { kind: 'literal', editable: false, value: 'old' },
    number: { kind: 'literal', editable: true, value: 0 } } }]
  const before = JSON.stringify(input)
  assert.deepEqual(replacementEdits(input, 'old', ''), [{ node_id: 'one', field: 'bucket', value: '-' }])
  assert.deepEqual(replacementEdits(input, '', 'new'), [])
  assert.equal(JSON.stringify(input), before)
})
