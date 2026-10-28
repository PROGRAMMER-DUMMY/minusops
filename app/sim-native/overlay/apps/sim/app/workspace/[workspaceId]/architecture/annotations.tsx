'use client'

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useReactFlow, ViewportPortal } from '@xyflow/react'
import { Button, Chip, ChipInput, ChipModal, ChipModalBody, ChipModalError, ChipModalField,
  ChipModalFooter, ChipModalHeader, ChipSelect } from '@sim/emcn'
import { architecturePresentationSchema, type ArchitecturePresentation } from '@/lib/api/contracts/minusops-architecture'
import { editorError } from './canvas-model'

type Note = ArchitecturePresentation['notes'][number]

export function ArchitectureAnnotations({ workspaceId, runId, revision, disabled }: {
  workspaceId: string; runId: string; revision: string; disabled: boolean
}) {
  const flow = useReactFlow()
  const client = useQueryClient()
  const key = ['architecture-presentation', workspaceId, runId]
  const [edit, setEdit] = useState<Note | null>(null)
  const [pending, setPending] = useState<Note[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function request(operation: Record<string, unknown>, signal?: AbortSignal) {
    const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
      method: 'POST', credentials: 'same-origin', signal, cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: runId, revision, ...operation }),
    })
    if (!response.ok) throw new Error('Presentation unavailable')
    const result = architecturePresentationSchema.parse(await response.json())
    if (result.workspace_id !== workspaceId || result.run_id !== runId) throw new Error('Presentation changed')
    return result
  }
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => request({ operation: 'presentation' }, signal),
    staleTime: 30000, retry: false })
  const notes = pending ?? query.data?.notes ?? []
  async function save(next: Note[]) {
    if (disabled || busy || !query.data) return
    setPending(next); setBusy(true); setError('')
    try {
      const result = await request({ operation: 'save_presentation', presentation_revision: query.data.presentation_revision, notes: next })
      client.setQueryData(key, result); setPending(null); setEdit(null)
    } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  async function reload() {
    const result = await query.refetch()
    if (!result.error) { setPending(null); setError('') }
  }
  return <>
    <div className='absolute top-3 right-3 z-10 flex items-center gap-2'>
      <Chip disabled={disabled || busy || !query.data || notes.length >= 64} onClick={() => {
        const center = flow.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 3 })
        setError(''); setEdit({ id: crypto.randomUUID(), kind: 'note', label: '', text: '', ...center })
      }}>Add note</Chip>
      {pending && !edit && <Chip disabled={disabled || busy} onClick={() => void save(pending)}>Save notes</Chip>}
    </div>
    {error && !edit && <div role='alert' className='absolute top-12 right-3 z-10 max-w-xs rounded bg-[var(--surface-2)] p-2 text-xs'>
      {error}<Chip disabled={busy} onClick={() => void reload()}>Reload shared notes</Chip></div>}
    <ViewportPortal>{notes.map(note => <div key={note.id} className='pointer-events-auto absolute w-[250px] rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-3 shadow-sm'
      style={{ transform: `translate(${note.x}px, ${note.y}px)` }} aria-label={`${note.kind}: ${note.label}`}
      onPointerDown={event => {
        if (disabled || busy || (event.target as Element).closest('button')) return
        event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId)
        const element = event.currentTarget
        const start = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY })
        const move = (pointer: PointerEvent) => {
          const point = flow.screenToFlowPosition({ x: pointer.clientX, y: pointer.clientY })
          setPending(notes.map(item => item.id === note.id ? { ...item, x: Math.max(-100000, Math.min(100000, note.x + point.x - start.x)),
            y: Math.max(-100000, Math.min(100000, note.y + point.y - start.y)) } : item))
        }
        const stop = () => { element.removeEventListener('pointermove', move); element.removeEventListener('pointerup', stop); element.removeEventListener('pointercancel', stop) }
        element.addEventListener('pointermove', move); element.addEventListener('pointerup', stop); element.addEventListener('pointercancel', stop)
      }}>
      <div className='flex items-center justify-between gap-2'><p className='break-words font-medium text-sm'>{note.label}</p>
        <Button variant='ghost' className='text-xs' disabled={disabled || busy} onClick={() => { setError(''); setEdit(note) }}>Edit</Button></div>
      <p className='my-2 whitespace-pre-wrap break-words text-xs'>{note.text}</p>
      <p className='text-[10px] text-[var(--text-tertiary)]'>{note.kind === 'component' ? 'Display component' : note.kind === 'group' ? 'Visual group' : 'Note'} · Presentation only</p>
    </div>)}</ViewportPortal>
    {edit && <ChipModal open dismissDisabled={busy} srTitle='Edit presentation' onOpenChange={open => { if (!open && !busy) setEdit(null) }}>
      <ChipModalHeader onClose={() => { if (!busy) setEdit(null) }}>Presentation</ChipModalHeader><ChipModalBody>
        <ChipSelect aria-label='Presentation kind' value={edit.kind} options={['note', 'component', 'group'].map(value => ({ value, label: value }))}
          disabled={disabled || busy} onChange={kind => setEdit(value => value && ({ ...value, kind: kind as Note['kind'] }))} />
        <ChipModalField type='input' title='Label' value={edit.label} required maxLength={120} disabled={disabled || busy}
          onChange={label => setEdit(value => value && ({ ...value, label }))} />
        <ChipModalField type='textarea' title='Text' value={edit.text} maxLength={4096} disabled={disabled || busy}
          onChange={text => setEdit(value => value && ({ ...value, text }))} />
        <div className='flex gap-2'>{(['x', 'y'] as const).map(axis => <ChipInput key={axis} aria-label={`Presentation ${axis}`} type='number'
          value={String(edit[axis])} min={-100000} max={100000} disabled={disabled || busy} onChange={event => {
            if (Number.isFinite(event.target.valueAsNumber)) setEdit(value => value && ({ ...value, [axis]: event.target.valueAsNumber }))
          }} />)}</div>
        {notes.some(note => note.id === edit.id) && <Chip disabled={disabled || busy} onClick={() => void save(notes.filter(note => note.id !== edit.id))}>Remove note</Chip>}
        <ChipModalError>{error}</ChipModalError>
        {error && <Chip disabled={busy} onClick={() => void reload()}>Reload shared notes</Chip>}
      </ChipModalBody><ChipModalFooter onCancel={() => setEdit(null)} cancelDisabled={busy} primaryAction={{ label: 'Save presentation',
        onClick: () => void save([...notes.filter(note => note.id !== edit.id), edit]), disabled: disabled || busy || !edit.label.trim() }} />
    </ChipModal>}
  </>
}
