'use client'

import { useEffect, useState } from 'react'
import { Chip, ChipModal, ChipModalBody, ChipModalError, ChipModalField, ChipModalFooter, ChipModalHeader, ChipSelect } from '@sim/emcn'
import type { CompositionCatalog, SourceGraph } from '@/lib/api/contracts/minusops-architecture'
import { editorError, parseField } from './canvas-model'
import { ConfigurationField } from './fields'

type Command = (value: Record<string, unknown>) => Promise<void>
export const MODULE_DRAG_TYPE = 'application/x-minusops-module'

export function CompositionPalette({ catalog, disabled, onCommand, drop, onPlacement, onDropConsumed }: {
  catalog: CompositionCatalog; disabled: boolean; onCommand: Command
  drop: { id: string; x: number; y: number } | null
  onPlacement: (address: string, position: { x: number; y: number }) => void
  onDropConsumed: () => void
}) {
  const [dialog, setDialog] = useState<{ operationId: string; name: string; inputs: Record<string, string>; position?: { x: number; y: number } } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const module = catalog.modules[0]
  function open(position?: { x: number; y: number }) {
    if (!module) return
    setError(''); setDialog({ operationId: crypto.randomUUID(), name: '', position, inputs: Object.fromEntries(
      Object.entries(module.fields).map(([name, field]) => [name, typeof field.default === 'string' ? field.default
        : field.default === undefined ? '' : JSON.stringify(field.default, null, 2)])) })
  }
  useEffect(() => { if (drop && !disabled) { open(drop); onDropConsumed() } }, [drop?.id])
  if (!module) return null
  function close() { if (!busy) setDialog(null) }
  async function submit() {
    if (!dialog || disabled || busy) return
    setBusy(true); setError('')
    try {
      const inputs = Object.fromEntries(Object.entries(module.fields).map(([name, field]) => [name,
        parseField({ kind: 'literal', editable: true, value: field.default ?? '' }, dialog.inputs[name] ?? '')]))
      if (dialog.position) onPlacement(`module.${dialog.name}`, dialog.position)
      await onCommand({ type: 'insert_module', operation_id: dialog.operationId, module_id: module.module_id,
        module_content_hash: module.module_content_hash, descriptor_digest: module.digest, name: dialog.name, inputs })
      setDialog(null)
    } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  return <div className='border-[var(--border)] border-b p-3'>
    <p className='mb-2 text-xs text-[var(--text-tertiary)]'>Add infrastructure · AWS</p>
    <Chip disabled={disabled || busy} draggable={!disabled && !busy} onDragStart={event => {
      event.dataTransfer.setData(MODULE_DRAG_TYPE, JSON.stringify({ run_id: catalog.run_id, revision: catalog.revision,
        module_id: module.module_id, module_content_hash: module.module_content_hash, descriptor_digest: module.digest }))
      event.dataTransfer.effectAllowed = 'copy'
    }} onClick={() => open()}>S3 medallion storage</Chip>
    {dialog && <ChipModal open dismissDisabled={busy} srTitle='Add S3 storage' onOpenChange={open => { if (!open) close() }}>
      <ChipModalHeader onClose={close}>Add S3 storage</ChipModalHeader>
      <ChipModalBody>
        <ChipModalField type='input' title='Terraform label' value={dialog.name} required maxLength={80} disabled={busy}
          onChange={name => setDialog(value => value && ({ ...value, name }))} />
        {Object.entries(module.fields).map(([name, field]) => <ConfigurationField key={name} name={name}
          field={{ kind: 'literal', editable: true, value: field.default ?? '' }} text={dialog.inputs[name]}
          disabled={disabled || busy} onChange={text => setDialog(value => value && ({ ...value, inputs: { ...value.inputs, [name]: text } }))} />)}
        <p className='text-xs text-[var(--text-tertiary)]'>Use existing key, logging bucket and permissions-boundary identifiers.</p>
        <ChipModalError>{error}</ChipModalError>
      </ChipModalBody>
      <ChipModalFooter onCancel={close} cancelDisabled={busy} primaryAction={{ label: busy ? 'Checking…' : 'Review insertion',
        onClick: submit, disabled: disabled || busy || !/^[A-Za-z_][A-Za-z_0-9-]{0,79}$/.test(dialog.name)
          || Object.entries(module.fields).some(([name, field]) => field.required && !dialog.inputs[name]?.trim()),
      }} />
    </ChipModal>}
  </div>
}

export type ConnectionIntent = { source: string; target: string; field?: string } | { target: string; field: string }

export function ConnectionDialog({ catalog, graph, intent, disabled, onClose, onCommand }: {
  catalog: CompositionCatalog; graph: SourceGraph; intent: ConnectionIntent; disabled: boolean
  onClose: () => void; onCommand: Command
}) {
  const [field, setField] = useState(intent.field ?? '')
  const [replacement, setReplacement] = useState('')
  const [operationId] = useState(() => crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const target = catalog.ports.find(item => item.node_id === intent.target)
  const origin = 'source' in intent ? catalog.ports.find(item => item.node_id === intent.source) : null
  const choices = target?.ports.filter(port => port.direction === 'input' && (origin
    ? port.source_module === origin.module_id && origin.ports.some(output => output.id === port.source_port && output.direction === 'output')
    : 'field' in intent && port.id === intent.field)) ?? []
  const selected = choices.find(port => port.id === field) ?? choices[0]
  const disconnect = !('source' in intent)
  async function submit() {
    if (busy || disabled || !selected) return
    setBusy(true); setError('')
    try {
      await onCommand(disconnect ? { type: 'disconnect', operation_id: operationId, target_id: intent.target,
        field: selected.id, replacement } : { type: 'connect', operation_id: operationId,
        source_id: 'source' in intent ? intent.source : '', target_id: intent.target, field: selected.id,
        port: selected.source_port, key: selected.source_key ?? null })
      onClose()
    } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  return <ChipModal open dismissDisabled={busy} srTitle={disconnect ? 'Disconnect component' : 'Connect components'}
    onOpenChange={open => { if (!open && !busy) onClose() }}>
    <ChipModalHeader onClose={() => { if (!busy) onClose() }}>{disconnect ? 'Disconnect component' : 'Connect components'}</ChipModalHeader>
    <ChipModalBody>
      <p className='mb-3 break-words text-sm'>{graph.nodes.find(node => node.id === intent.target)?.address}</p>
      {choices.length ? <>
        <ChipSelect aria-label='Target input' value={selected?.id ?? ''} options={choices.map(port => ({ value: port.id,
          label: `${port.id} · ${port.value_type}` }))} onChange={setField} disabled={disabled || busy} />
        {disconnect ? <ChipModalField type='input' title='Existing replacement identifier' value={replacement}
          onChange={setReplacement} disabled={disabled || busy} required />
          : <p className='mt-3 break-words text-xs'>{graph.nodes.find(node => 'source' in intent && node.id === intent.source)?.address}
            .{selected?.source_port}{selected?.source_key ? `[${selected.source_key}]` : ''}</p>}
      </> : <p className='text-sm'>No compatible source-bound ports. Review this connection in source.</p>}
      <ChipModalError>{error}</ChipModalError>
    </ChipModalBody><ChipModalFooter onCancel={onClose} cancelDisabled={busy} primaryAction={{
      label: busy ? 'Checking…' : 'Review connection', onClick: submit,
      disabled: disabled || busy || !selected || disconnect && !replacement.trim(),
    }} />
  </ChipModal>
}
