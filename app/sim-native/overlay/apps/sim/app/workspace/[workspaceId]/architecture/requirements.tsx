'use client'

import { useState } from 'react'
import { Chip, ChipModal, ChipModalBody, ChipModalError, ChipModalField, ChipModalFooter, ChipModalHeader, ChipSelect } from '@sim/emcn'
import { hostReviewSchema, type HostReview } from '@/lib/api/contracts/minusops-architecture'
import { editorError } from './canvas-model'

export function RequirementsIntake({ workspaceId, runId, revision, disabled, onQueued }: {
  workspaceId: string; runId: string; revision: string; disabled: boolean; onQueued: (value: HostReview) => void
}) {
  const [input, setInput] = useState<{ request: string; mode: string; operationId: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit() {
    if (!input || disabled || busy) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation: 'queue_authoring', run_id: runId, revision, mode: input.mode,
          request: input.request, operation_id: input.operationId }),
      })
      if (!response.ok) throw new Error('Authoring intake unavailable')
      const result = hostReviewSchema.parse(await response.json())
      if (result.run_id !== runId || result.request_id !== input.operationId) throw new Error('Authoring intake changed')
      onQueued(result); setInput(null)
    } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  return <>
    <Chip disabled={disabled || busy} onClick={() => { setError(''); setInput({ request: '', mode: 'hybrid', operationId: crypto.randomUUID() }) }}>
      Design with an agent
    </Chip>
    {input && <ChipModal open dismissDisabled={busy} srTitle='Design architecture'
      onOpenChange={open => { if (!open && !busy) setInput(null) }}>
      <ChipModalHeader onClose={() => { if (!busy) setInput(null) }}>Design architecture</ChipModalHeader>
      <ChipModalBody>
        <ChipModalField type='textarea' title='What do you need?' value={input.request} required maxLength={4000}
          disabled={disabled || busy} onChange={request => setInput(value => value && ({ ...value, request }))} />
        <ChipSelect aria-label='Interview style' value={input.mode} options={[
          { value: 'hybrid', label: 'Key questions + recommendations' },
          { value: 'guided', label: 'Guide me through each decision' },
          { value: 'recommended', label: 'Recommend a design after essential facts' },
        ]} disabled={disabled || busy} onChange={mode => setInput(value => value && ({ ...value, mode }))} />
        <p className='text-xs text-[var(--text-tertiary)]'>A host agent gathers requirements and authors source. This queues the request.</p>
        <ChipModalError>{error}</ChipModalError>
      </ChipModalBody><ChipModalFooter onCancel={() => setInput(null)} cancelDisabled={busy} primaryAction={{
        label: busy ? 'Queueing…' : 'Send to host', onClick: submit, disabled: disabled || busy || !input.request.trim(),
      }} />
    </ChipModal>}
  </>
}
