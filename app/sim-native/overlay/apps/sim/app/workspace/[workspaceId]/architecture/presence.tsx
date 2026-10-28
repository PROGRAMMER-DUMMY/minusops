'use client'

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useReactFlow, ViewportPortal } from '@xyflow/react'
import { Cursor } from '@sim/emcn/icons'
import { architecturePresenceSchema } from '@/lib/api/contracts/minusops-architecture'

export function ArchitecturePresence({ workspaceId, runId, revision, selection }: {
  workspaceId: string; runId: string; revision: string; selection: string | null
}) {
  const [sessionId] = useState(() => crypto.randomUUID())
  const current = useRef(selection)
  current.current = selection
  const flow = useReactFlow()
  const pointer = useRef<{ x: number; y: number } | null>(null)
  const anchor = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const canvas = anchor.current?.closest('main')
    if (!canvas) return
    const move = (event: PointerEvent) => { pointer.current = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }) }
    const leave = () => { pointer.current = null }
    canvas.addEventListener('pointermove', move, { passive: true })
    canvas.addEventListener('pointerleave', leave)
    return () => { canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerleave', leave) }
  }, [flow])
  const query = useQuery({ queryKey: ['architecture-presence', workspaceId, runId, revision, sessionId],
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation: 'presence', run_id: runId,
          revision, session_id: sessionId, selection: current.current, cursor: pointer.current }),
      })
      if (!response.ok) throw new Error('Presence unavailable')
      const result = architecturePresenceSchema.parse(await response.json())
      if (result.run_id !== runId || result.revision !== revision) throw new Error('Presence changed')
      return result
    }, retry: false, refetchInterval: 8000, refetchIntervalInBackground: false, staleTime: 5000 })
  const editors = !query.isError && Date.now() - query.dataUpdatedAt < 20000 ? query.data?.editors ?? [] : []
  return <>
    <div className='pointer-events-none absolute top-3 left-3 z-10 text-xs text-[var(--text-tertiary)]' aria-live='polite'>
      {editors.length > 0 && <span title='Advisory presence. Source proposals stay private.'>{editors.length} other {editors.length === 1 ? 'editor' : 'editors'}</span>}
    </div>
    <ViewportPortal>{editors.filter(editor => editor.cursor).map(editor => <div key={editor.session_id}
      className='pointer-events-none absolute flex items-center gap-1 text-xs text-[var(--text-tertiary)]'
      style={{ transform: `translate(${editor.cursor!.x}px, ${editor.cursor!.y}px)` }} title='Advisory presence'>
      <Cursor className='size-4' /><span className='rounded bg-[var(--surface-2)] px-1'>{editor.label}</span>
    </div>)}</ViewportPortal>
    <span className='hidden' ref={anchor} />
  </>
}
