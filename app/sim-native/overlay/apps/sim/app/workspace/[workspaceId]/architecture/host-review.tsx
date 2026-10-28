'use client'

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Chip } from '@sim/emcn'
import { hostReviewSchema, sourceChangeSchema, type SourceChange } from '@/lib/api/contracts/minusops-architecture'

const LABELS = { QUEUED: 'Queued for host', CLAIMED: 'Host reviewing', ANSWERED: 'Host replied',
  CHANGE_PROPOSED: 'Host proposed changes', NEEDS_INPUT: 'Needs input', CANCELLED: 'Cancelled', AUTHORED: 'Host authored source' }

export function HostReviewStatus({ workspaceId, runId, reference, canEdit, onChange, onRefresh }: {
  workspaceId: string; runId: string; reference: { request_id: string; revision: string }; canEdit: boolean
  onChange: (change: SourceChange) => void; onRefresh: () => void
}) {
  const client = useQueryClient()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const queryKey = ['architecture-host-review', workspaceId, runId, reference.request_id, reference.revision]
  async function send(operation: string, extra: Record<string, unknown> = {}, signal?: AbortSignal) {
    const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
      method: 'POST', credentials: 'same-origin', signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation, run_id: runId, ...reference, ...extra }),
    })
    if (!response.ok) throw new Error('Host review unavailable or changed. Refresh to try again.')
    return response.json()
  }
  const status = useQuery({ queryKey, queryFn: async ({ signal }) => {
    const value = hostReviewSchema.parse(await send('host_status', {}, signal))
    if (value.run_id !== runId || value.revision !== reference.revision || value.request_id !== reference.request_id) {
      throw new Error('Host review scope changed.')
    }
    return value
  }, retry: false, refetchInterval: query => ['QUEUED', 'CLAIMED'].includes(query.state.data?.status ?? '') ? 2000
    : query.state.data?.status === 'CHANGE_PROPOSED' && query.state.data.publication === 'NOT_OBSERVED' ? 5000 : false })
  async function act(operation: 'host_change' | 'cancel_host') {
    setBusy(true); setError('')
    try {
      const value = await send(operation, operation === 'cancel_host' ? { entry_digest: status.data?.entry_digest } : {})
      if (operation === 'host_change') onChange(sourceChangeSchema.parse(value))
      else client.setQueryData(queryKey, hostReviewSchema.parse(value))
    } catch { setError('Host review unavailable or changed. Refresh to try again.') }
    finally { setBusy(false) }
  }
  const value = status.data
  return <div className='mb-4 rounded-md border border-[var(--border)] p-3 text-xs' aria-label='Host review status'>
    <div className='flex flex-wrap items-center gap-2'>
      <span>{value ? value.publication === 'SOURCE_PUBLISHED' ? 'Source published'
        : value.publication === 'RECOVERY_REQUIRED' ? 'Host recovery required' : LABELS[value.status] : 'Loading host review…'}</span>
      <Chip disabled={status.isFetching || busy} onClick={() => void status.refetch()}>Check</Chip>
      {value?.status === 'CHANGE_PROPOSED' && value.publication === 'NOT_OBSERVED' &&
        <Chip disabled={busy} onClick={() => void act('host_change')}>View host diff</Chip>}
      {value && ['QUEUED', 'CLAIMED'].includes(value.status) &&
        <Chip disabled={!canEdit || busy} onClick={() => void act('cancel_host')}>Cancel review</Chip>}
      {(value?.publication === 'SOURCE_PUBLISHED' || value?.publication === 'SOURCE_AUTHORED') && <Chip disabled={busy} onClick={onRefresh}>Refresh source</Chip>}
    </div>
    {(error || status.isError) && <p className='mt-2' role='alert'>{error || 'Host status unavailable.'}</p>}
    {value?.message && <p className='mt-2 whitespace-pre-wrap break-words'>{value.message}</p>}
    {value?.publication === 'HISTORICAL_SOURCE_CHANGED' && <p className='mt-2'>Publication recorded; current source has changed.</p>}
    <details className='mt-2 text-[var(--text-tertiary)]'><summary>Handoff details</summary>
      <p className='mt-2 break-all'>{reference.request_id}</p>
      <p className='mt-2'>A host must claim this request. Its reply does not authorize deployment.</p>
      <code className='mt-2 block break-all'>minusctl canvas agent next --run {runId} --worker &lt;host&gt; --json</code>
    </details>
  </div>
}
