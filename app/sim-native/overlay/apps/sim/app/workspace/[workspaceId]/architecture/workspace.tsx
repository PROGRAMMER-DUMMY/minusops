'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Chip, ChipInput, ChipSelect, ChipTextarea } from '@sim/emcn'
import { Box } from '@sim/emcn/icons'
import { WorkflowBlockView, WorkflowEdgeView, useCanvasColorMode } from '@sim/workflow-renderer'
import { Background, Controls, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow,
  type EdgeProps, type Node, type NodeProps } from '@xyflow/react'
import { useParams, useRouter } from 'next/navigation'
import { architectureWorkspaceSchema, sourceChangeSchema, type ArchitectureSubject,
  type ArchitectureWorkspace as Workspace, type SourceChange, type SourceNode } from '@/lib/api/contracts/minusops-architecture'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { openRouterChatKeys } from '@/hooks/queries/openrouter-chat'
import '@xyflow/react/dist/style.css'

type Resource = Node<SourceNode & { icon?: string; collapsed: boolean; toggle: () => void }, 'resource'>

function ResourceBlock({ id, data, selected }: NodeProps<Resource>) {
  const Icon = data.icon ? ({ className }: { className?: string }) => <img src={data.icon} className={className} alt='' /> : Box
  return <WorkflowBlockView id={id} type='infrastructure' name={data.address.split('.').at(-1) ?? data.address}
    isEnabled isLocked={false} hasRing={!!selected} ringStyles='ring-1 ring-[var(--border)]'
    Icon={Icon} iconBgColor='var(--surface-5)' horizontalHandles shouldShowDefaultHandles
    hasContentBelowHeader conditionRows={[]} routerRows={[]} wouldCreateConnectionCycle={() => false}
    cursorConnectionsEnabled={false} typeLabel={data.resource_type.replace(/^aws_/, '')}
    rows={<div className='truncate px-3 py-2 text-xs' title={data.address}>{data.address}</div>}
    actionBar={data.kind === 'module' ? <Chip onClick={data.toggle}>{data.collapsed ? 'Expand' : 'Collapse'}</Chip> : undefined} />
}
function ReferenceEdge(props: EdgeProps) {
  return <WorkflowEdgeView {...props} diffStatus={null} runStatus={undefined} isPreviewRun={false} />
}
const nodeTypes = { resource: ResourceBlock }
const edgeTypes = { reference: ReferenceEdge }
const format = (value: unknown) => JSON.stringify(value, null, 2)

function Evidence({ value, kind }: { value: unknown; kind: string }) {
  const record = value && typeof value === 'object' ? value as Record<string, unknown> : null
  if (!record) return <p>No evidence loaded.</p>
  if (record.status === 'UNKNOWN') return <p>{String(record.reason)}</p>
  if (record.kind !== (kind === 'Plan' ? 'minusops-review-graph' : 'minusops-canvas-delivery')) return <p>No matching evidence loaded.</p>
  const views = record.views as { workflow?: { execution_observed?: string }; delivery?: { route?: string; stage_mapping?: string } } | undefined
  return <>
    {kind === 'Plan' ? <p>{Array.isArray(record.nodes) ? record.nodes.length : 0} resources in the qualified saved plan.</p>
      : <dl className='space-y-3'><div><dt className='text-xs'>Delivery route</dt><dd>{views?.delivery?.route ?? 'UNKNOWN'}</dd></div>
        <div><dt className='text-xs'>Workflow execution</dt><dd>{views?.workflow?.execution_observed ?? 'UNKNOWN'}</dd></div>
        <div><dt className='text-xs'>Stage mapping</dt><dd>{views?.delivery?.stage_mapping ?? 'UNKNOWN'}</dd></div></dl>}
    <details className='mt-4'><summary className='cursor-pointer'>Evidence</summary>
      <pre className='mt-3 whitespace-pre-wrap break-words text-xs'>{format(record)}</pre></details>
  </>
}

export function ArchitectureWorkspace() {
  return <ReactFlowProvider><WorkspaceView /></ReactFlowProvider>
}

function WorkspaceView() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const router = useRouter()
  const permissions = useUserPermissionsContext()
  const queryClient = useQueryClient()
  const colorMode = useCanvasColorMode()
  const [data, setData] = useState<Workspace | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<Resource>([])
  const [collapsed, setCollapsed] = useState<string[]>([])
  const [subject, setSubject] = useState<ArchitectureSubject | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [change, setChange] = useState<SourceChange | null>(null)
  const [tab, setTab] = useState('Configuration')
  const [evidence, setEvidence] = useState<{ tab: string; run_id: string; revision: string; value: unknown } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const sequence = useRef(0)
  const flow = useReactFlow<Resource>()
  const graph = data?.architecture
  const selected = graph?.nodes.find((node) => subject?.kind === 'source_node' && node.id === subject.id)
  const edge = graph?.edges.find((item) => subject?.kind === 'source_edge' && item.id === subject.id)
  const editable = permissions.canAdmin && !busy

  async function request(operation?: Record<string, unknown>, signal?: AbortSignal) {
    const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
      method: operation ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal,
      ...(operation ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operation) } : {}),
    })
    if (!response.ok) throw new Error('Architecture unavailable or changed. Refresh and try again.')
    return response.json()
  }
  useEffect(() => {
    const controller = new AbortController()
    const current = ++sequence.current
    setData(null)
    request(undefined, controller.signal).then((value) => {
      if (sequence.current === current) setData(architectureWorkspaceSchema.parse(value))
    }).catch(() => { if (!controller.signal.aborted) setError('Architecture unavailable. Check the local gateway and refresh.') })
    return () => controller.abort()
  }, [workspaceId])

  useEffect(() => {
    if (!graph) { setNodes([]); return }
    const positions = new Map(data?.layout?.revision === graph.revision ? data.layout.positions.map((item) => [item.id, item]) : [])
    const rows = new Map<string, number>()
    const offsets = new Map<string, number>([['', 0]])
    let offset = Math.ceil(graph.nodes.filter((node) => !node.parent_id).length / 3) * 170 + 200
    for (const module of graph.nodes.filter((node) => node.kind === 'module')) {
      offsets.set(module.id, offset)
      offset += Math.ceil(graph.nodes.filter((node) => node.parent_id === module.id).length / 3) * 170 + 200
    }
    setNodes(graph.nodes.map((node) => {
      const row = rows.get(node.parent_id ?? '') ?? 0
      rows.set(node.parent_id ?? '', row + 1)
      return { id: node.id, type: 'resource', deletable: false, position: positions.get(node.id) ?? {
        x: (row % 3) * 380, y: (offsets.get(node.parent_id ?? '') ?? 0) + Math.floor(row / 3) * 170 },
        data: { ...node, collapsed: false, toggle: () => {} } }
    }))
    setCollapsed(graph.nodes.filter((node) => node.kind === 'module').map((node) => node.id))
    setSubject(null); setDrafts({}); setChange(null); setTab('Configuration'); setEvidence(null)
  }, [graph?.revision, graph?.baseline.run_id])

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch { setError('Request unavailable. Refresh the source and check your configuration.') }
    finally { setBusy(false) }
  }
  const binding = () => ({ run_id: data?.run_id, revision: graph?.revision })
  function toggle(id: string) {
    const expanding = collapsed.includes(id)
    setCollapsed((value) => value.includes(id) ? value.filter((item) => item !== id) : [...value, id])
    if (expanding) setTimeout(() => flow.fitView({ nodes: nodes.filter((node) => node.data.parent_id === id), padding: 0.2, duration: 200 }), 100)
  }
  function select(value: ArchitectureSubject, focus = false) {
    setSubject(value); setDrafts({}); setChange(null); setTab('Configuration')
    const node = graph?.nodes.find((item) => item.id === value.id)
    const parents = new Set<string>()
    let parent = node?.parent_id
    while (parent) { parents.add(parent); parent = graph?.nodes.find((item) => item.id === parent)?.parent_id }
    setCollapsed((items) => items.filter((id) => !parents.has(id)))
    if (focus && node) {
      const position = flow.getNode(node.id)?.position
      if (position) setTimeout(() => flow.setCenter(position.x + 128, position.y + 60, { zoom: 0.9, duration: 200 }), 100)
    }
  }
  const displayed = useMemo(() => {
    const byId = new Map(nodes.map((node) => [node.id, node.data]))
    return nodes.map((node) => {
      let parent = node.data.parent_id
      let hidden = !['resource', 'data', 'module'].includes(node.data.kind) && subject?.id !== node.id
      while (parent) { hidden ||= collapsed.includes(parent); parent = byId.get(parent)?.parent_id ?? null }
      return { ...node, hidden, selected: subject?.id === node.id, data: { ...node.data,
        icon: data?.icons[node.data.resource_type], collapsed: collapsed.includes(node.id), toggle: () => toggle(node.id) } }
    })
  }, [nodes, collapsed, subject, data?.icons])
  const edges = useMemo(() => {
    const shown = new Set(displayed.filter((node) => !node.hidden).map((node) => node.id))
    return (graph?.edges ?? []).filter((item) => shown.has(item.source) && shown.has(item.target)).map((item) => ({
      id: item.id, source: item.source, target: item.target, type: 'reference', sourceHandle: 'source', targetHandle: 'target',
      selected: subject?.id === item.id, deletable: false,
    }))
  }, [graph?.edges, displayed, subject])

  async function stage() {
    if (!selected || !subject) return
    const edits = Object.entries(drafts).map(([field, text]) => selected.fields[field].kind === 'reference'
      ? { node_id: selected.id, field, expression: text }
      : { node_id: selected.id, field, value: typeof selected.fields[field].value === 'string' ? text : JSON.parse(text) })
    setChange(sourceChangeSchema.parse(await request({ operation: 'stage', ...binding(), subject, edits })))
    setTab('Changes')
  }
  async function download() {
    const reviewed = sourceChangeSchema.parse(await request({ operation: 'review', ...binding(), change }))
    const url = URL.createObjectURL(new Blob([format(reviewed)], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${data?.run_id}-source-change.json`; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
    setNotice('Proposal downloaded. Host review is required before source publication.')
  }
  const file = graph?.files.find((item) => item.path === selected?.source.path)
  return <div className='flex h-full min-h-0 flex-col bg-[var(--surface-1)]'>
    <header className='flex flex-wrap items-center gap-3 border-[var(--border)] border-b px-4 py-3'>
      <h1 className='font-medium text-sm'>Architecture</h1>
      <ChipSelect aria-label='Architecture run' searchable placeholder='Select a run' value={data?.run_id ?? ''}
        options={(data?.runs ?? []).map((run) => ({ label: run.name, value: run.run_id }))} disabled={!editable}
        onChange={(run_id) => perform(async () => {
          setData(architectureWorkspaceSchema.parse(await request({ operation: 'select', run_id })))
          await queryClient.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
        })} />
      <span className='text-xs text-[var(--text-tertiary)]'>{graph ? `${graph.nodes.length} declarations · Source${graph.gaps.length ? ` · ${graph.gaps.length} gaps` : ''}` : ''}</span>
      <Chip disabled={busy} onClick={() => perform(async () => setData(architectureWorkspaceSchema.parse(await request())))}>Refresh</Chip>
      <Chip disabled={!graph || busy} onClick={() => flow.fitView({ padding: 0.15, duration: 200 })}>Fit</Chip>
      <Chip disabled={!graph || !editable} onClick={() => perform(async () => {
        await request({ operation: 'layout', ...binding(), positions: flow.getNodes().map(({ id, position }) => ({ id, ...position })) })
        setNotice('Layout saved.')
      })}>Save layout</Chip>
      {data?.focus && <Chip disabled={!editable} onClick={() => perform(async () => {
        await request({ operation: 'clear_focus', run_id: data.run_id })
        await queryClient.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
        setData((value) => value ? { ...value, focus: null } : value)
        setNotice('AI context cleared.')
      })}>Clear AI context</Chip>}
    </header>
    {(error || notice) && <div className='border-[var(--border)] border-b px-4 py-2 text-sm' role={error ? 'alert' : 'status'}>{error || notice}</div>}
    <div className='flex min-h-0 flex-1'>
      <main className='relative min-w-0 flex-1' aria-label='Infrastructure architecture canvas'>
        {graph?.nodes.length ? <ReactFlow nodes={displayed} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          onNodesChange={onNodesChange} onNodeClick={(_, node) => select({ kind: 'source_node', id: node.id })}
          onEdgeClick={(_, item) => select({ kind: 'source_edge', id: item.id })} colorMode={colorMode}
          nodesConnectable={false} nodesDraggable={permissions.canAdmin} edgesReconnectable={false}
          deleteKeyCode={null} fitView minZoom={0.08} maxZoom={2} onlyRenderVisibleElements>
          <Background gap={22} size={1} /><Controls showInteractive={false} />
        </ReactFlow> : <div className='flex h-full items-center justify-center text-sm text-[var(--text-tertiary)]'>
          {data?.source_status === 'UNAVAILABLE' ? 'Source unavailable. Clear AI context or select another run.'
            : data?.run_id ? 'No authored Terraform in this run.' : 'Select a run to view its architecture.'}</div>}
      </main>
      <aside className='flex w-[360px] shrink-0 flex-col overflow-hidden border-[var(--border)] border-l' aria-label='Architecture inspector'>
        <div className='p-3'><ChipSelect aria-label='Find declaration' searchable placeholder='Find declaration'
          options={(graph?.nodes ?? []).map((node) => ({ label: node.address, value: node.id }))}
          value={selected?.id ?? ''} onChange={(id) => select({ kind: 'source_node', id }, true)} /></div>
        <div className='flex flex-wrap gap-1 border-[var(--border)] border-b px-3 pb-3'>
          {['Configuration', 'Source', 'Changes', 'Plan', 'Delivery'].map((value) => <Chip key={value} active={tab === value}
            disabled={busy || !graph} onClick={() => {
              setTab(value); setEvidence(null)
              const target = graph && data?.run_id ? { tab: value, run_id: data.run_id, revision: graph.revision } : null
              if ((value === 'Plan' || value === 'Delivery') && target) perform(async () => {
              const result = await request({ operation: value.toLowerCase(), run_id: target.run_id, revision: target.revision })
              setEvidence({ ...target, value: result })
            }) }}>{value}</Chip>)}
        </div>
        <div className='flex-1 overflow-auto p-4 text-sm'>
          {tab === 'Configuration' && <>
            {!selected && !edge && <p>Select a declaration or connection.</p>}
            {edge && <><p className='mb-2 font-medium'>Declared reference</p>
              <p className='mb-2 break-words text-xs'>{graph?.nodes.find((node) => node.id === edge.source)?.address}</p>
              <p className='mb-3 break-words text-xs'>→ {graph?.nodes.find((node) => node.id === edge.target)?.address}</p>
              <details><summary>Evidence</summary><pre className='whitespace-pre-wrap break-words text-xs'>{format(edge)}</pre></details></>}
            {selected && <><p className='mb-3 break-words font-medium'>{selected.address}</p>
              {Object.entries(selected.fields).map(([name, field]) => <div key={name} className='mb-3'>
                <label htmlFor={`field-${name}`} className='mb-1 block text-xs'>{name}</label>
                {typeof field.value === 'string' ? <ChipInput id={`field-${name}`} aria-label={name}
                  value={drafts[name] ?? field.value} readOnly={!field.editable || !editable}
                  onChange={(event) => { setDrafts((value) => ({ ...value, [name]: event.target.value })); setChange(null) }} />
                  : <ChipTextarea id={`field-${name}`} aria-label={name} value={drafts[name] ?? format(field.value)}
                    readOnly={!field.editable || !editable}
                    onChange={(event) => { setDrafts((value) => ({ ...value, [name]: event.target.value })); setChange(null) }} />}
              </div>)}
              <Chip disabled={!editable || !Object.keys(drafts).length} onClick={() => perform(stage)}>Review changes</Chip>
            </>}
            {subject && <Chip disabled={!editable} onClick={() => perform(async () => {
              await request({ operation: 'focus', ...binding(), subject })
              await queryClient.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
              router.push(`/workspace/${workspaceId}/home`)
            })}>Ask AI about selection</Chip>}
          </>}
          {tab === 'Source' && (selected ? <><p className='mb-3 text-xs'>{selected.source.path}:{selected.source.start_line}</p>
            <pre className='whitespace-pre-wrap break-words text-xs'>{file?.preview?.split('\n').slice(selected.source.start_line - 1, selected.source.end_line).join('\n') ?? 'Source withheld.'}</pre></> : <p>Select a declaration.</p>)}
          {tab === 'Changes' && (change ? <>{change.files.map((item) => <section key={item.path} className='mb-4'>
            <p className='mb-2 font-medium'>{item.path}</p><pre className='whitespace-pre-wrap break-words text-xs'>{item.diff}</pre></section>)}
            <Chip disabled={!editable} onClick={() => perform(download)}>Download reviewed proposal</Chip>
            <p className='mt-3 text-xs'>Proposed source only. A new plan and host review are required.</p></> : <p>No proposed changes.</p>)}
          {(tab === 'Plan' || tab === 'Delivery') && (busy ? <p>Loading…</p> : <Evidence value={evidence?.tab === tab
            && evidence.run_id === data?.run_id && evidence.revision === graph?.revision ? evidence.value : null} kind={tab} />)}
        </div>
      </aside>
    </div>
  </div>
}
