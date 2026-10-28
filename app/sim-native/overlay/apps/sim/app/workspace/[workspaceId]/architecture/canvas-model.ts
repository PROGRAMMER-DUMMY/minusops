import type { SourceNode } from '@/lib/api/contracts/minusops-architecture'

export type Position = { id: string; x: number; y: number }
export type LayoutHistory = { past: Position[][]; future: Position[][] }

const EDITOR_ERRORS = new Set(['Enter a valid number or JSON value.', 'Keep the current configuration type.',
  'Enter a finite number within the supported range.', 'Source changed; discard stale drafts before continuing.',
  'The edited declaration is no longer available.', 'Draft receipt does not match this edit. Refresh before continuing.',
  'Architecture unavailable or changed. Refresh and try again.'])
export function editorError(error: unknown): string {
  return error instanceof Error && EDITOR_ERRORS.has(error.message) ? error.message : 'Request unavailable. Refresh and try again.'
}

export function parseField(field: SourceNode['fields'][string], text: string): unknown {
  if (field.kind === 'reference' || typeof field.value === 'string') return text
  let value: unknown
  try { value = JSON.parse(text) } catch { throw new Error('Enter a valid number or JSON value.') }
  if (field.value === null ? value !== null : Array.isArray(field.value) ? !Array.isArray(value)
    : typeof value !== typeof field.value || value === null || Array.isArray(value)) {
    throw new Error('Keep the current configuration type.')
  }
  if (typeof value === 'number' && (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) {
    throw new Error('Enter a finite number within the supported range.')
  }
  return value
}

export function capturePositions(nodes: { id: string; position: { x: number; y: number } }[]): Position[] {
  return nodes.map(({ id, position }) => ({ id, x: position.x, y: position.y }))
}

export function applyPositions<T extends { id: string; position: { x: number; y: number } }>(nodes: T[], positions: Position[]): T[] {
  const byId = new Map(positions.map(({ id, x, y }) => [id, { x, y }]))
  return nodes.map(node => byId.has(node.id) ? { ...node, position: byId.get(node.id)! } : node)
}

export function recordLayout(history: LayoutHistory, before: Position[], after: Position[]): LayoutHistory {
  if (JSON.stringify(before) === JSON.stringify(after)) return history
  return { past: [...history.past.slice(-49), before], future: [] }
}

export function undoLayout(history: LayoutHistory, current: Position[]) {
  const positions = history.past.at(-1) ?? null
  return { positions, history: positions ? { past: history.past.slice(0, -1), future: [current, ...history.future] } : history }
}

export function redoLayout(history: LayoutHistory, current: Position[]) {
  const positions = history.future[0] ?? null
  return { positions, history: positions ? { past: [...history.past, current], future: history.future.slice(1) } : history }
}

const SUMMARY_FIELDS = ['bucket', 'name', 'runtime', 'instance_type', 'engine', 'worker_type', 'number_of_workers', 'force_destroy']

export function replacementEdits(nodes: SourceNode[], find: string, replacement: string) {
  if (!find || find === replacement) return []
  return nodes.flatMap(node => Object.entries(node.fields).flatMap(([field, value]) => value.editable
    && value.kind === 'literal' && typeof value.value === 'string' && value.value.includes(find)
    ? [{ node_id: node.id, field, value: value.value.replaceAll(find, replacement) }] : []))
}

export function cardSummary(node: Pick<SourceNode, 'kind' | 'fields'>, children: number) {
  if (node.kind === 'module') return [{ label: 'Components', value: String(children) }]
  return SUMMARY_FIELDS.flatMap(name => {
    const value = node.fields[name]?.value
    return ['string', 'number', 'boolean'].includes(typeof value)
      ? [{ label: name.replaceAll('_', ' '), value: String(value) }] : []
  }).slice(0, 3)
}
