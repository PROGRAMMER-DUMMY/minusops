'use client'

import { useEffect, useRef, useState } from 'react'
import { Button, Chip, ChipInput } from '@sim/emcn'
import { Box } from '@sim/emcn/icons'
import type { SourceGraph } from '@/lib/api/contracts/minusops-architecture'
import { editorError, replacementEdits } from './canvas-model'

export function ArchitectureSearch({ graph, icons, disabled, focusRequest, onSelect, onCommand }: {
  graph: SourceGraph; icons: Record<string, string>; disabled: boolean; focusRequest: number
  onSelect: (id: string) => void; onCommand: (value: Record<string, unknown>) => Promise<void>
}) {
  const [search, setSearch] = useState('')
  const [replace, setReplace] = useState('')
  const [index, setIndex] = useState(-1)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { if (focusRequest) input.current?.focus() }, [focusRequest])
  const matching = graph.nodes.filter(node => ['resource', 'data', 'module'].includes(node.kind)
    && (node.address.toLowerCase().includes(search.trim().toLowerCase())
      || Object.values(node.fields).some(field => field.kind === 'literal' && typeof field.value === 'string'
        && field.value.toLowerCase().includes(search.trim().toLowerCase()))))
  const edits = replacementEdits(graph.nodes, search, replace)
  function move(direction: number) {
    if (!matching.length) return
    const next = (index + direction + matching.length) % matching.length
    setIndex(next); onSelect(matching[next].id)
  }
  async function review() {
    if (disabled || busy || !edits.length || edits.length > 32) return
    setBusy(true); setError('')
    try { await onCommand({ type: 'field_edit', subject: { kind: 'source_node', id: edits[0].node_id }, edits }) }
    catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  return <>
    <div className='p-3'><ChipInput ref={input} aria-label='Search infrastructure' placeholder='Search components…' value={search}
      onChange={event => { setSearch(event.target.value); setIndex(-1); setError('') }}
      onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); move(event.shiftKey ? -1 : 1) } }} />
      {search && <div className='mt-2 flex items-center gap-2 text-xs'>
        <span>{matching.length} matches</span><Chip disabled={!matching.length} onClick={() => move(-1)}>Previous</Chip>
        <Chip disabled={!matching.length} onClick={() => move(1)}>Next</Chip>
      </div>}
      <details className='mt-3 text-xs'><summary className='cursor-pointer'>Replace configuration</summary>
        <ChipInput className='mt-2' aria-label='Replacement text' placeholder='Replace with…' value={replace}
          onChange={event => { setReplace(event.target.value); setError('') }} />
        <p className='my-2'>{edits.length} editable string fields · exact text</p>
        <div className='max-h-40 space-y-2 overflow-auto'>{edits.slice(0, 32).map(edit => <div key={edit.node_id + edit.field}>
          <p className='break-words'>{graph.nodes.find(node => node.id === edit.node_id)?.address}.{edit.field}</p>
          <p className='break-words text-[var(--text-tertiary)]'>{edit.value}</p>
        </div>)}</div>
        <Chip disabled={disabled || busy || !edits.length || edits.length > 32} onClick={() => void review()}>Review replacement</Chip>
        {edits.length > 32 && <p className='mt-2'>Narrow the search to 32 fields or fewer.</p>}
        {error && <p className='mt-2' role='alert'>{error}</p>}
      </details>
    </div>
    <div className='min-h-0 flex-1 overflow-auto px-2 pb-3'>{matching.map(node => <Button key={node.id} variant='ghost'
      className='flex w-full justify-start gap-2 text-left' title={node.address} onClick={() => onSelect(node.id)}>
      {icons[node.resource_type] ? <img src={icons[node.resource_type]} alt='' className='size-4 shrink-0' /> : <Box className='size-4 shrink-0' />}
      <span className='truncate text-sm'>{node.address}</span>
    </Button>)}</div>
  </>
}
