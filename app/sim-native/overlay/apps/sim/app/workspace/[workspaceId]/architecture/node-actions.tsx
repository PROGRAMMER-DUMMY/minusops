'use client'

import { useEffect, useState, type RefObject } from 'react'
import { Chip, ChipConfirmModal, ChipModal, ChipModalBody, ChipModalError, ChipModalField,
  ChipModalFooter, ChipModalHeader, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@sim/emcn'
import { Duplicate, MoreHorizontal, Trash } from '@sim/emcn/icons'
import { useReactFlow } from '@xyflow/react'
import { architectureClipboardSchema, type SourceGraph, type SourceNode } from '@/lib/api/contracts/minusops-architecture'
import { editorError } from './canvas-model'

export function NodeActions({ selected, graph, disabled, onCommand, context, onCloseContext, workspaceId, runId,
  sourceRevision, draftRevision, canvasRoot, onEdgeInspect, onEdgeDisconnect, canDisconnect, onPlacement }: {
  selected: SourceNode[]; graph: SourceGraph; disabled: boolean
  onCommand: (value: Record<string, unknown>) => Promise<void>
  context: { x: number; y: number; edgeId?: string } | null; onCloseContext: () => void
  workspaceId: string; runId: string; sourceRevision: string; draftRevision: number; canvasRoot: RefObject<HTMLElement | null>
  onEdgeInspect: (id: string) => void; onEdgeDisconnect: (id: string) => void; canDisconnect: (id: string) => boolean
  onPlacement: (address: string, position: { x: number; y: number }) => void
}) {
  const flow = useReactFlow()
  const [menuOpen, setMenuOpen] = useState(false)
  const [dialog, setDialog] = useState<{ type: 'duplicate_nodes' | 'remove_nodes' | 'paste_nodes'; operationId: string;
    snapshot?: { snapshot_revision: number; snapshot_digest: string };
    nodes: { node_id: string; name: string; address: string; position?: { x: number; y: number } }[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const supported = selected.length > 0 && selected.length <= 16 && selected.every(node => !node.parent_id
    && (node.kind === 'module' || node.kind === 'resource' && node.resource_type.startsWith('aws_'))
    && node.instance_resolution === 'DECLARED_BLOCK' && graph.files.some(file => file.path === node.source.path && file.preview !== null))
  function open(type: 'duplicate_nodes' | 'remove_nodes') {
    setError('')
    setDialog({ type, operationId: crypto.randomUUID(), nodes: selected.map(node => {
      const prefix = node.address.slice(0, node.address.lastIndexOf('.') + 1)
      let name = node.address.split('.').at(-1) + '_copy'
      while (graph.nodes.some(node => node.address === prefix + name)) name += '_copy'
      const position = flow.getNode(node.id)?.position
      return { node_id: node.id, name, address: node.address,
        position: position && { x: position.x + 48, y: position.y + 128 } }
    }) })
  }
  async function clipboard(action: 'copy' | 'cut' | 'paste') {
    if (busy || disabled || action !== 'paste' && !supported) return
    setBusy(true); setError('')
    try {
      if (action === 'paste') {
        const raw = await navigator.clipboard.readText()
        if (raw.length > 32768) throw new Error('Clipboard exceeds limit')
        const value = architectureClipboardSchema.parse(JSON.parse(raw))
        if (value.workspace_id !== workspaceId || value.run_id !== runId || value.revision !== sourceRevision) throw new Error('Clipboard scope changed')
        const positions = new Map(value.positions?.map(item => [item.id, item]) ?? [])
        const corner = value.positions?.length ? { x: Math.min(...value.positions.map(item => item.x)),
          y: Math.min(...value.positions.map(item => item.y)) } : null
        const point = context && !context.edgeId ? flow.screenToFlowPosition(context) : null
        const delta = point && corner ? { x: Math.round(point.x / 16) * 16 - corner.x,
          y: Math.round(point.y / 16) * 16 - corner.y } : { x: 48, y: 128 }
        setDialog({ type: 'paste_nodes', operationId: crypto.randomUUID(), snapshot: {
          snapshot_revision: value.snapshot_revision, snapshot_digest: value.snapshot_digest }, nodes: value.nodes.map(node => {
            const prefix = node.address.slice(0, node.address.lastIndexOf('.') + 1)
            let name = node.address.split('.').at(-1) + '_copy'
            while (graph.nodes.some(node => node.address === prefix + name)) name += '_copy'
            const position = positions.get(node.node_id)
            return { ...node, name, position: position && { x: position.x + delta.x, y: position.y + delta.y } }
          }) })
      } else {
        const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ operation: 'clipboard', run_id: runId, revision: sourceRevision,
            draft_revision: draftRevision, node_ids: selected.map(node => node.id) }),
        })
        if (!response.ok) throw new Error('Clipboard source changed')
        const value = architectureClipboardSchema.parse(await response.json())
        if (value.workspace_id !== workspaceId || value.run_id !== runId || value.revision !== sourceRevision) throw new Error('Clipboard scope changed')
        const positions = value.nodes.map(node => {
          const current = flow.getNode(node.node_id)
          if (!current) throw new Error('Clipboard selection changed')
          return { id: node.node_id, ...current.position }
        })
        await navigator.clipboard.writeText(JSON.stringify(architectureClipboardSchema.parse({ ...value, positions })))
        if (action === 'cut') await onCommand({ type: 'remove_nodes', operation_id: crypto.randomUUID(), node_ids: selected.map(node => node.id) })
      }
    } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target
      if (!(target instanceof Element) || !canvasRoot.current?.contains(target)
        || target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]') || !(event.ctrlKey || event.metaKey)) return
      const action = ({ c: 'copy', x: 'cut', v: 'paste' } as const)[event.key.toLowerCase() as 'c' | 'x' | 'v']
      if (action) { event.preventDefault(); void clipboard(action) }
    }
    document.addEventListener('keydown', listener)
    return () => document.removeEventListener('keydown', listener)
  }, [selected, graph, disabled, busy, draftRevision, sourceRevision, workspaceId, runId, context, flow])
  async function submit() {
    if (!dialog || busy || disabled) return
    setBusy(true); setError('')
    try {
      if (dialog.type !== 'remove_nodes') for (const node of dialog.nodes) {
        if (node.position) onPlacement(node.address.slice(0, node.address.lastIndexOf('.') + 1) + node.name,
          { x: Math.max(-100000, Math.min(100000, node.position.x)), y: Math.max(-100000, Math.min(100000, node.position.y)) })
      }
      await onCommand(dialog.type !== 'remove_nodes'
        ? { type: dialog.type, operation_id: dialog.operationId, ...dialog.snapshot, nodes: dialog.nodes.map(({ node_id, name }) => ({ node_id, name })) }
        : { type: dialog.type, operation_id: dialog.operationId, node_ids: dialog.nodes.map(node => node.node_id) })
      setDialog(null)
    } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  return <>
    <DropdownMenu modal={false} open={menuOpen || context !== null} onOpenChange={open => { setMenuOpen(open); if (!open) onCloseContext() }}>
      <DropdownMenuTrigger asChild>{context ? <span style={{ position: 'fixed', left: context.x, top: context.y, width: 1, height: 1 }} />
      : <Chip aria-label='Component actions' disabled={disabled || busy}><MoreHorizontal className='size-4' /></Chip>}
    </DropdownMenuTrigger><DropdownMenuContent align='start'>
      {context?.edgeId ? <>
        <DropdownMenuItem onSelect={() => onEdgeInspect(context.edgeId!)}>Inspect connection</DropdownMenuItem>
        <DropdownMenuItem disabled={disabled || busy || !canDisconnect(context.edgeId)} onSelect={() => onEdgeDisconnect(context.edgeId!)}>Disconnect</DropdownMenuItem>
      </> : <>
      <DropdownMenuItem disabled={!supported || disabled || busy} onSelect={() => void clipboard('copy')}>Copy</DropdownMenuItem>
      <DropdownMenuItem disabled={!supported || disabled || busy} onSelect={() => void clipboard('cut')}>Cut</DropdownMenuItem>
      <DropdownMenuItem disabled={disabled || busy} onSelect={() => void clipboard('paste')}>Paste</DropdownMenuItem>
      <DropdownMenuItem disabled={!supported || disabled || busy} onSelect={() => open('duplicate_nodes')}><Duplicate />{selected.length > 1 ? `Duplicate ${selected.length} items` : 'Duplicate'}</DropdownMenuItem>
      <DropdownMenuItem disabled={!supported || disabled || busy} onSelect={() => open('remove_nodes')}><Trash />Delete</DropdownMenuItem>
      </>}
    </DropdownMenuContent></DropdownMenu>
    {error && !dialog && <span role='alert' className='text-xs'>{error}</span>}
    {dialog && dialog.type !== 'remove_nodes' && <ChipModal open dismissDisabled={busy}
      srTitle={dialog.type === 'paste_nodes' ? 'Paste components' : 'Duplicate components'} onOpenChange={open => { if (!open && !busy) setDialog(null) }}>
      <ChipModalHeader onClose={() => { if (!busy) setDialog(null) }}>{dialog.type === 'paste_nodes' ? 'Paste components' : 'Duplicate components'}</ChipModalHeader>
      <ChipModalBody>{dialog.nodes.map((node, index) => <ChipModalField key={node.node_id} type='input'
        title={node.address} value={node.name} required maxLength={80} disabled={busy}
        onChange={name => setDialog(value => value && ({ ...value, nodes: value.nodes.map((item, i) => i === index ? { ...item, name } : item) }))} />)}
        <p className='px-2 text-caption text-[var(--text-muted)]'>Review resource names before publication.</p>
        <ChipModalError>{error}</ChipModalError>
      </ChipModalBody><ChipModalFooter onCancel={() => setDialog(null)} cancelDisabled={busy} primaryAction={{
        label: busy ? 'Checking…' : dialog.type === 'paste_nodes' ? 'Review paste' : 'Review duplicate', onClick: submit,
        disabled: disabled || busy || dialog.nodes.some(node => !/^[A-Za-z_][A-Za-z_0-9-]{0,79}$/.test(node.name)),
      }} />
    </ChipModal>}
    {dialog?.type === 'remove_nodes' && <ChipConfirmModal open onOpenChange={open => { if (!open && !busy) setDialog(null) }}
      title='Remove selected components?' text='Creates a source proposal. Referenced components must be disconnected first.'
      confirm={{ label: 'Review removal', onClick: submit, pending: busy, pendingLabel: 'Checking…', disabled }}>
      <ChipModalError>{error}</ChipModalError>
    </ChipConfirmModal>}
  </>
}
