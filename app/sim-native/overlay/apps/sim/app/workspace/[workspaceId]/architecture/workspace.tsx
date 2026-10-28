'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useIsMutating, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Chip, ChipSelect, cn } from '@sim/emcn'
import { Box, ChevronDown, X } from '@sim/emcn/icons'
import { WorkflowBlockView, WorkflowEdgeView, SubBlockRowView, useCanvasColorMode, CONNECTION_PICKER_Z } from '@sim/workflow-renderer'
import { ReactFlow, ReactFlowProvider, SelectionMode, useNodesInitialized, useNodesState, useReactFlow, useStoreApi,
  type EdgeProps, type Node, type NodeProps } from '@xyflow/react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { architectureDraftSchema, compositionCatalogSchema, hostReviewSchema, sourceChangeSchema, sourceEditsSchema, type ArchitectureDraft, type ArchitectureSubject,
  type ArchitectureWorkspace as Workspace, type SourceChange, type SourceNode } from '@/lib/api/contracts/minusops-architecture'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { openRouterChatKeys } from '@/hooks/queries/openrouter-chat'
import { architectureKeys, useArchitectureWorkspace, useArchitectureAction } from '@/hooks/queries/minusops-architecture'
import { usePanelStore } from '@/stores/panel'
import { useTerminalStore } from '@/stores/terminal'
import { PANEL_WIDTH, TERMINAL_HEIGHT } from '@/stores/constants'
import { usePanelResize } from '@/app/workspace/[workspaceId]/w/[workflowId]/components/panel/hooks/use-panel-resize'
import { useTerminalResize } from '@/app/workspace/[workspaceId]/w/[workflowId]/components/terminal/hooks/use-terminal-resize'
import { ArchitectureChat } from './chat'
import { cardSummary, editorError, parseField } from './canvas-model'
import { NativeCanvasControls, useNativeInteractions } from './native-interactions'
import { ConfigurationField } from './fields'
import { HostReviewStatus } from './host-review'
import { NodeActions } from './node-actions'
import { CompositionPalette, ConnectionDialog, MODULE_DRAG_TYPE, type ConnectionIntent, type ModuleDrop } from './composition-tools'
import { ArchitectureSearch } from './search'
import { ArchitecturePresence } from './presence'
import { ArchitectureAnnotations } from './annotations'
import { ArchitectureEvidence } from './evidence'
import { RequirementsIntake } from './requirements'
import '@xyflow/react/dist/style.css'

type Resource = Node<SourceNode & { icon?: string; collapsed: boolean; childCount: number; canConnect?: boolean; toggle: () => void }, 'resource'>

function ResourceBlock({ id, data, selected }: NodeProps<Resource>) {
  const Icon = data.icon ? ({ className }: { className?: string }) => <img src={data.icon} className={className} alt='' /> : Box
  const rows = cardSummary(data, data.childCount)
  return <div title={data.address}><WorkflowBlockView id={id} type='infrastructure' name={data.address.split('.').at(-1) ?? data.address}
    isEnabled isLocked={false} hasRing={!!selected} ringStyles='ring-1 ring-[var(--border)]'
    Icon={Icon} iconBgColor='var(--surface-5)' horizontalHandles shouldShowDefaultHandles
    hasContentBelowHeader={rows.length > 0} conditionRows={[]} routerRows={[]} wouldCreateConnectionCycle={() => false}
    cursorConnectionsEnabled={false} typeLabel={data.resource_type.replace(/^aws_/, '')}
    showErrorOutput={false}
    connectionsEnabled={data.canConnect === true}
    sourceReconnectEnabled={data.canConnect === true}
    inputDragEnabled={data.canConnect === true}
    rows={rows.map(row => <SubBlockRowView key={row.label} title={row.label} displayValue={row.value} />)}
    actionBar={data.kind === 'module' ? <Chip onClick={data.toggle}>{data.collapsed ? 'Expand' : 'Collapse'}</Chip> : undefined} /></div>
}
function ReferenceEdge(props: EdgeProps) {
  return <WorkflowEdgeView {...props} diffStatus={null} runStatus={undefined} isPreviewRun={false} />
}
const nodeTypes = { resource: ResourceBlock }
const edgeTypes = { reference: ReferenceEdge }
const format = (value: unknown) => JSON.stringify(value, null, 2)
function candidateEdits(change: SourceChange) {
  if (change.schema_version === 1) return change.edits.edits
  const last = change.operations.operations.at(-1)
  const parsed = last?.type === 'field_edit' ? sourceEditsSchema.safeParse(last.edits) : null
  return parsed?.success ? parsed.data : null
}

export function ArchitectureWorkspace() {
  return <ReactFlowProvider><WorkspaceView /></ReactFlowProvider>
}

function WorkspaceView() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const router = useRouter()
  const runId = useSearchParams().get('run') ?? undefined
  const permissions = useUserPermissionsContext()
  const queryClient = useQueryClient()
  const colorMode = useCanvasColorMode()
  const workspace = useArchitectureWorkspace(workspaceId, runId)
  const action = useArchitectureAction(workspaceId)
  const selecting = useIsMutating({ mutationKey: architectureKeys.workspace(workspaceId) }) > 0
  const data = workspace.data
  const setData = (update: (previous: Workspace | undefined) => Workspace | undefined) =>
    queryClient.setQueryData<Workspace>(architectureKeys.view(workspaceId, runId), update)
  const [nodes, setNodes, onNodesChange] = useNodesState<Resource>([])
  const [collapsed, setCollapsed] = useState<string[]>([])
  const [subject, setSubject] = useState<ArchitectureSubject | null>(null)
  const [draftsByNode, setDraftsByNode] = useState<Record<string, Record<string, string>>>({})
  const [draftRevision, setDraftRevision] = useState<string | null>(null)
  const [focusTarget, setFocusTarget] = useState<{ id: string; children: boolean } | null>(null)
  const [change, setChange] = useState<SourceChange | null>(null)
  const [proposal, setProposal] = useState<ArchitectureDraft | null>(null)
  const pendingCommand = useRef<{ key: string; body: Record<string, unknown> } | null>(null)
  const [editorTab, setEditorTab] = useState('Configuration')
  const [evidenceTab, setEvidenceTab] = useState('Changes')
  const [searchFocus, setSearchFocus] = useState(0)
  const [connection, setConnection] = useState<ConnectionIntent | null>(null)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; edgeId?: string } | null>(null)
  const [drop, setDrop] = useState<ModuleDrop | null>(null)
  const pendingPlacement = useRef(new Map<string, { x: number; y: number }>())
  const [chatBusy, setChatBusy] = useState(false)
  const activeTab = usePanelStore((state) => state.activeTab)
  const setActiveTab = usePanelStore((state) => state.setActiveTab)
  const mobileOpen = usePanelStore((state) => state.isMobilePanelOpen)
  const setMobileOpen = usePanelStore((state) => state.setIsMobilePanelOpen)
  const { handlePointerDown: panelResize } = usePanelResize()
  const panelWidth = usePanelStore((state) => state.panelWidth)
  const setPanelWidth = usePanelStore((state) => state.setPanelWidth)
  const { handlePointerDown: terminalResize } = useTerminalResize()
  const terminalHeight = useTerminalStore((state) => state.terminalHeight)
  const setTerminalHeight = useTerminalStore((state) => state.setTerminalHeight)
  const [evidence, setEvidence] = useState<{ tab: string; run_id: string; revision: string; value: unknown } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const fitted = useRef('')
  const canvasRoot = useRef<HTMLElement>(null)
  const flow = useReactFlow<Resource>()
  const flowStore = useStoreApi()
  const cancelledConnection = useRef(false)
  const initialized = useNodesInitialized()
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !flowStore.getState().connection.inProgress) return
      event.preventDefault(); cancelledConnection.current = true; flowStore.getState().cancelConnection()
    }
    document.addEventListener('keydown', cancel)
    return () => document.removeEventListener('keydown', cancel)
  }, [flowStore])
  const sourceGraph = data?.architecture
  const graph = proposal?.revision === sourceGraph?.revision && proposal?.change?.schema_version === 2
    ? proposal.change.proposal_graph : sourceGraph
  const loadedSource = useRef('')
  const loadedRun = useRef('')
  const selected = graph?.nodes.find((node) => subject?.kind === 'source_node' && node.id === subject.id)
  const edge = graph?.edges.find((item) => subject?.kind === 'source_edge' && item.id === subject.id)
  const drafts = selected ? draftsByNode[selected.id] ?? {} : {}
  const proposedFields = new Map((proposal?.revision === sourceGraph?.revision ? proposal?.edits ?? [] : [])
    .filter(edit => edit.node_id === selected?.id).map(edit => [edit.field, 'expression' in edit ? edit.expression
      : typeof edit.value === 'string' ? edit.value : format(edit.value)]))
  const dirty = Object.values(draftsByNode).some(fields => Object.keys(fields).length > 0)
  const staleDraft = dirty && draftRevision !== sourceGraph?.revision
  const setDrafts = (update: (previous: Record<string, string>) => Record<string, string>) => {
    if (!selected || staleDraft) return
    setDraftRevision(sourceGraph?.revision ?? null)
    setDraftsByNode(previous => ({ ...previous, [selected.id]: update(previous[selected.id] ?? {}) }))
  }
  const canNavigate = permissions.canAdmin && !busy && !chatBusy && !selecting
  const canLayout = canNavigate && data?.metadata?.locked !== true
  const isDraft = data?.metadata?.design_status === 'DRAFT'
  const editable = canLayout && !isDraft && !staleDraft
  const clearSelection = useCallback(() => setSubject(null), [])
  const interactions = useNativeInteractions({ root: canvasRoot, revision: sourceGraph?.revision,
    enabled: canLayout, setNodes, onClear: clearSelection })
  const loadError = workspace.isError ? 'Architecture unavailable. Check the local gateway and refresh.' : ''

  async function request(operation?: Record<string, unknown>, signal?: AbortSignal) {
    const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
      method: operation ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal,
      ...(operation ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operation) } : {}),
    })
    if (!response.ok) throw new Error('Architecture unavailable or changed. Refresh and try again.')
    return response.json()
  }
  const catalogQuery = useQuery({
    queryKey: ['architecture-composition-catalog', workspaceId, data?.run_id, sourceGraph?.revision, proposal?.draft_revision ?? 0],
    enabled: !!sourceGraph && !!data?.run_id && !isDraft && proposal?.revision === sourceGraph.revision,
    queryFn: async ({ signal }) => {
      const result = compositionCatalogSchema.parse(await request({ operation: 'catalog', run_id: data?.run_id,
        revision: sourceGraph?.revision, draft_revision: proposal?.draft_revision ?? 0 }, signal))
      if (result.run_id !== data?.run_id || result.revision !== sourceGraph?.revision
        || result.draft_revision !== (proposal?.draft_revision ?? 0)) throw new Error('Composition catalog changed.')
      return result
    }, retry: false, staleTime: 30000,
  })
  const catalog = catalogQuery.data
  useEffect(() => {
    if (!graph) { setNodes([]); return }
    const sourceChanged = loadedSource.current !== sourceGraph?.revision
    const sameRun = loadedRun.current === graph.baseline.run_id
    loadedRun.current = graph.baseline.run_id
    if (!sameRun) { pendingPlacement.current.clear(); setDrop(null) }
    loadedSource.current = sourceGraph?.revision ?? ''
    const positions = new Map(data?.layout?.positions.map((item) => [item.id, item]) ?? [])
    const rows = new Map<string, number>()
    const offsets = new Map<string, number>([['', 0]])
    const primary = (node: SourceNode) => ['module', 'resource', 'data'].includes(node.kind)
    let offset = Math.ceil(graph.nodes.filter(node => !node.parent_id && primary(node)).length / 3) * 220 + 120
    for (const module of graph.nodes.filter((node) => node.kind === 'module')) {
      offsets.set(module.id, offset)
      offset += Math.ceil(graph.nodes.filter(node => node.parent_id === module.id && primary(node)).length / 3) * 220 + 120
    }
    setNodes(previous => {
      const occupied = new Map<string | null, { x: number; y: number }[]>()
      for (const node of sameRun ? previous : []) {
        if (!primary(node.data)) continue
        const peers = occupied.get(node.data.parent_id) ?? []
        peers.push(node.position); occupied.set(node.data.parent_id, peers)
      }
      return [...graph.nodes].sort((a, b) => Number(primary(b)) - Number(primary(a))).map((node) => {
      const row = rows.get(node.parent_id ?? '') ?? 0
      rows.set(node.parent_id ?? '', row + 1)
      const existing = sameRun ? previous.find(item => item.id === node.id) : undefined
      let position = existing?.position ?? pendingPlacement.current.get(node.address) ?? positions.get(node.id) ?? {
        x: (row % 3) * 360, y: (offsets.get(node.parent_id ?? '') ?? 0) + Math.floor(row / 3) * 220 }
      if (!sourceChanged && !existing && !positions.has(node.id) && !pendingPlacement.current.has(node.address) && primary(node)) {
        const peers = occupied.get(node.parent_id) ?? []
        while (peers.some(point => Math.abs(point.x - position.x) < 336 && Math.abs(point.y - position.y) < 180)) {
          position = { ...position, y: position.y + 220 }
        }
        peers.push(position); occupied.set(node.parent_id, peers)
      }
      return { id: node.id, type: 'resource', deletable: false, selected: existing?.selected ?? subject?.id === node.id,
        position,
        data: { ...node, collapsed: false, childCount: graph.nodes.filter(child => child.parent_id === node.id && primary(child)).length,
          toggle: () => {} } }
    }) })
    const modules = graph.nodes.filter(node => node.kind === 'module').map(node => node.id)
    setCollapsed(previous => sourceChanged ? modules : [...new Set([...previous,
      ...modules.filter(id => !nodes.some(node => node.id === id))])])
    if (sourceChanged) {
      setSubject(null); setChange(null); setProposal(null); setConnection(null); setContextMenu(null); pendingCommand.current = null
      setEditorTab('Configuration'); setEvidence(null); setFocusTarget(null)
    } else if (subject && !graph.nodes.some(node => node.id === subject.id) && !graph.edges.some(edge => edge.id === subject.id)) setSubject(null)
  }, [graph?.revision, sourceGraph?.revision, graph?.baseline.run_id])

  useEffect(() => {
    if (data?.draft && sourceGraph && data.draft.revision === sourceGraph.revision) {
      setProposal(data.draft); setChange(data.draft.change)
    }
  }, [data?.draft, sourceGraph?.revision])

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (error) { setError(editorError(error)) }
    finally { setBusy(false) }
  }
  const binding = () => ({ run_id: data?.run_id, revision: sourceGraph?.revision })
  function toggle(id: string) {
    const expanding = collapsed.includes(id)
    setCollapsed((value) => value.includes(id) ? value.filter((item) => item !== id) : [...value, id])
    if (expanding) setFocusTarget({ id, children: true })
  }
  function select(value: ArchitectureSubject, focus = false, preserveSelection = false) {
    setSubject(value); setEditorTab('Configuration')
    if (!preserveSelection) setNodes(items => items.map(node => ({ ...node, selected: node.id === value.id })))
    setActiveTab('editor'); setMobileOpen(true)
    const node = graph?.nodes.find((item) => item.id === value.id)
    const parents = new Set<string>()
    let parent = node?.parent_id
    while (parent) { parents.add(parent); parent = graph?.nodes.find((item) => item.id === parent)?.parent_id }
    setCollapsed((items) => items.filter((id) => !parents.has(id)))
    if (focus && node) setFocusTarget({ id: node.id, children: false })
  }
  const displayed = useMemo(() => {
    const byId = new Map(nodes.map((node) => [node.id, node.data]))
    return nodes.map((node) => {
      let parent = node.data.parent_id
      let hidden = !['resource', 'data', 'module'].includes(node.data.kind) && subject?.id !== node.id
      while (parent) { hidden ||= collapsed.includes(parent); parent = byId.get(parent)?.parent_id ?? null }
      return { ...node, hidden, connectable: editable && !!catalog?.ports.some(port => port.node_id === node.id && port.ports.length > 0), data: { ...node.data,
        canConnect: editable && !!catalog?.ports.some(port => port.node_id === node.id && port.ports.length > 0),
        icon: data?.icons[node.data.resource_type], collapsed: collapsed.includes(node.id), toggle: () => toggle(node.id) } }
    })
  }, [nodes, collapsed, subject, data?.icons, catalog, editable])
  const edges = useMemo(() => {
    const shown = new Set(displayed.filter((node) => !node.hidden).map((node) => node.id))
    return (graph?.edges ?? []).filter((item) => shown.has(item.source) && shown.has(item.target)).map((item) => ({
      id: item.id, source: item.source, target: item.target, type: 'reference', sourceHandle: 'source', targetHandle: 'target',
      // Moving a required input to another target needs an explicit disconnect replacement.
      reconnectable: editable && !!item.field && !!catalog?.ports.some(node => node.node_id === item.target
        && node.ports.some(port => port.id === item.field && port.direction === 'input')) ? 'source' as const : false,
      // Reconnect anchors must receive the pointer above native card outline hit areas.
      selected: subject?.id === item.id, zIndex: subject?.id === item.id ? CONNECTION_PICKER_Z : 0, deletable: false,
    }))
  }, [graph?.edges, displayed, subject, catalog, editable])

  useEffect(() => {
    if (!focusTarget || !initialized) return
    const frame = requestAnimationFrame(() => {
      const targets = flow.getNodes().filter(node => !node.hidden &&
        (focusTarget.children ? node.data.parent_id === focusTarget.id : node.id === focusTarget.id))
      if (targets.length) void flow.fitView({ nodes: targets, padding: 0.25, maxZoom: 1, duration: 200 })
      setFocusTarget(null)
    })
    return () => cancelAnimationFrame(frame)
  }, [focusTarget, initialized, flow, displayed])

  useEffect(() => {
    if (!sourceGraph || !initialized || fitted.current === sourceGraph.revision) return
    const frame = requestAnimationFrame(() => {
      void flow.fitView({ padding: 0.15, maxZoom: 1 })
      fitted.current = sourceGraph.revision
    })
    return () => cancelAnimationFrame(frame)
  }, [sourceGraph?.revision, initialized, flow])

  async function command(value: Record<string, unknown>) {
    if (!sourceGraph || !data?.run_id) return
    setBusy(true)
    try {
    const key = JSON.stringify({ run_id: data.run_id, revision: sourceGraph.revision, command: value })
    if (pendingCommand.current?.key !== key) pendingCommand.current = { key, body: { operation: 'command',
      ...binding(), expected_draft_revision: proposal?.draft_revision ?? 0,
      command_id: typeof value.operation_id === 'string' ? value.operation_id : crypto.randomUUID(), command: value } }
    const result = architectureDraftSchema.parse(await request(pendingCommand.current.body))
    if (result.workspace_id !== workspaceId || result.run_id !== data.run_id || result.revision !== sourceGraph.revision
      || result.receipt?.command_id !== pendingCommand.current.body.command_id) throw new Error('Draft receipt does not match this edit. Refresh before continuing.')
    pendingCommand.current = null
    setProposal(result); setChange(result.change)
    if (result.change?.schema_version === 2) {
      const added = result.change.proposal_graph.nodes.filter(node => !node.parent_id && !graph?.nodes.some(before => before.id === node.id))
      if (added.length) {
        setNodes(previous => previous.map(node => ({ ...node, selected: false })))
        setSubject({ kind: 'source_node', id: added[0].id }); setFocusTarget({ id: added[0].id, children: false })
        setActiveTab('editor')
      }
    }
    setData(previous => previous ? { ...previous, draft: result } : previous)
    setDraftsByNode({}); setDraftRevision(null)
    setEvidenceTab('Changes'); setTerminalHeight(Math.max(206, terminalHeight))
    } finally { setBusy(false) }
  }
  async function stage() {
    if (staleDraft) throw new Error('Source changed; discard stale drafts before continuing.')
    const edits = Object.entries(draftsByNode).flatMap(([id, fields]) => {
      const node = graph?.nodes.find(item => item.id === id)
      if (!node) throw new Error('The edited declaration is no longer available.')
      return Object.entries(fields).map(([field, text]) => node.fields[field].kind === 'reference'
        ? { node_id: id, field, expression: text }
        : { node_id: id, field, value: parseField(node.fields[field], text) })
    })
    if (!edits.length) return
    await command({ type: 'field_edit', subject: { kind: 'source_node', id: edits[0].node_id }, edits })
  }
  async function download() {
    const reviewed = sourceChangeSchema.parse(await request({ operation: 'review', ...binding(), change }))
    const url = URL.createObjectURL(new Blob([format(reviewed)], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${data?.run_id}-source-change.json`; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
    setNotice('Proposal downloaded. Host review is required before source publication.')
  }
  const file = graph?.files.find((item) => item.path === selected?.source.path)
  return <div className='flex h-full min-h-0 bg-[var(--bg)]'>
    <div className='flex min-w-0 flex-1 flex-col'>
    <header className='flex flex-wrap items-center gap-3 border-[var(--border)] border-b px-4 py-3'>
      <h1 className='font-medium text-sm'>Architecture</h1>
      <ChipSelect aria-label='Architecture run' searchable placeholder='Select a run' value={data?.run_id ?? ''}
        options={(data?.runs ?? []).map((run) => ({ label: run.name, value: run.run_id }))} disabled={!canNavigate}
        onChange={(run_id) => perform(async () => {
          await action.mutateAsync({ operation: 'select', run_id })
          router.push(`/workspace/${workspaceId}/architecture?run=${encodeURIComponent(run_id)}`)
        })} />
      <span className='text-xs text-[var(--text-tertiary)]'>{graph ? `${graph.nodes.length} declarations · ${isDraft ? 'Unpublished design' : proposal?.change ? 'Proposed changes' : 'Source'}${graph.gaps.length ? ` · ${graph.gaps.length} gaps` : ''}` : ''}</span>
      {data?.metadata?.locked && <span className='text-xs text-[var(--text-muted)]'>Locked</span>}
      {sourceGraph && data?.run_id && (isDraft || !sourceGraph.nodes.length) && <RequirementsIntake key={data.run_id}
        workspaceId={workspaceId} runId={data.run_id} revision={sourceGraph.revision} disabled={!canLayout}
        onQueued={value => { setData(previous => previous ? { ...previous, host_review: { request_id: value.request_id, revision: value.revision } } : previous)
          setEvidenceTab('Changes'); setTerminalHeight(Math.max(206, terminalHeight)) }} />}
      <Chip disabled={busy || chatBusy || selecting} onClick={() => perform(async () => {
        const result = await workspace.refetch()
        if (result.error) throw result.error
      })}>Refresh</Chip>
      <Chip disabled={!graph || busy || !initialized} onClick={() => {
        setFocusTarget(null); void flow.fitView({ padding: 0.15, maxZoom: 1, duration: 200 })
      }}>Fit</Chip>
      {graph && <NodeActions key={`${data?.run_id}:${sourceGraph?.revision}:${proposal?.draft_revision ?? 0}`}
        graph={graph} selected={nodes.filter(node => node.selected).map(node => node.data)}
        disabled={!editable || dirty} onCommand={command} context={contextMenu} onCloseContext={() => setContextMenu(null)}
        workspaceId={workspaceId} runId={data?.run_id ?? ''} sourceRevision={sourceGraph?.revision ?? ''}
        draftRevision={proposal?.draft_revision ?? 0} canvasRoot={canvasRoot}
        onPlacement={(address, position) => pendingPlacement.current.set(address, position)}
        onEdgeInspect={id => select({ kind: 'source_edge', id })}
        canDisconnect={id => { const item = graph.edges.find(edge => edge.id === id); return !!item?.field && !!catalog?.ports.some(
          node => node.node_id === item.target && node.ports.some(port => port.id === item.field && port.direction === 'input')) }}
        onEdgeDisconnect={id => { const item = graph.edges.find(edge => edge.id === id); if (item?.field) setConnection({ target: item.target, field: item.field }) }} />}
      <Chip disabled={!graph || !canLayout} onClick={() => perform(async () => {
        const positions = flow.getNodes().map(({ id, position }) => ({ id, ...position }))
        const result = await request({ operation: 'layout', ...binding(), draft_revision: proposal?.draft_revision ?? 0,
          expected_layout_hash: data?.layout_hash, positions })
        setData(previous => previous && sourceGraph ? { ...previous, layout: { revision: sourceGraph.revision, positions },
          layout_hash: result.layout_hash } : previous)
        setNotice('Layout saved.')
      })}>Save layout</Chip>
      {data?.focus && <Chip disabled={!canNavigate} onClick={() => perform(async () => {
        await request({ operation: 'clear_focus', run_id: data.run_id })
        await queryClient.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
        setData((value) => value ? { ...value, focus: null, context_enabled: false } : value)
        setNotice('AI context cleared.')
      })}>Clear AI context</Chip>}
      {data?.run_id && !data.context_enabled && <Chip disabled={!canNavigate} onClick={() => perform(async () => {
        if (data.run_id) await action.mutateAsync({ operation: 'select', run_id: data.run_id })
      })}>Use architecture context</Chip>}
    </header>
    {(error || loadError || notice) && <div className='border-[var(--border)] border-b px-4 py-2 text-sm' role={error || loadError ? 'alert' : 'status'}>{error || loadError || notice}</div>}
    {staleDraft && <div role='alert' className='flex items-center gap-2 border-[var(--border)] border-b px-4 py-2 text-sm'>
      Source changed. Unsaved fields need a fresh review.
      <Chip onClick={() => { setDraftsByNode({}); setDraftRevision(null); setChange(null) }}>Discard stale fields</Chip>
    </div>}
      <main ref={canvasRoot} tabIndex={0} className='relative min-h-0 min-w-0 flex-1 outline-none' aria-label='Infrastructure architecture canvas'
        onDragOver={event => { if (editable && !dirty && event.dataTransfer.types.includes(MODULE_DRAG_TYPE)) {
          event.preventDefault(); event.dataTransfer.dropEffect = 'copy'
          const box = event.currentTarget.getBoundingClientRect(), view = flow.getViewport()
          const x = event.clientX < box.left + 40 ? 12 : event.clientX > box.right - 40 ? -12 : 0
          const y = event.clientY < box.top + 40 ? 12 : event.clientY > box.bottom - 40 ? -12 : 0
          if (x || y) void flow.setViewport({ ...view, x: view.x + x, y: view.y + y })
        } }}
        onDrop={event => {
          if (!editable || dirty || !catalog || !event.dataTransfer.types.includes(MODULE_DRAG_TYPE)) return
          event.preventDefault()
          try {
            const raw = event.dataTransfer.getData(MODULE_DRAG_TYPE)
            if (raw.length > 2048) throw new Error('Drag payload unavailable')
            const value = JSON.parse(raw)
            const module = catalog.modules.find(module => module.module_id === value.module_id)
            if (!module || value.run_id !== data?.run_id || value.revision !== sourceGraph?.revision
              || module.digest !== value.descriptor_digest || module.module_content_hash !== value.module_content_hash) throw new Error('Drag source changed')
            if ((event.target as Element).closest('.react-flow__edge')) {
              setNotice('This module has no admitted input for insertion into a reference. Add it beside the connection.'); return
            }
            const point = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY })
            setDrop({ id: crypto.randomUUID(), ...point }); setActiveTab('toolbar')
          } catch (error) { setError(editorError(error)) }
        }}
        onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
            event.preventDefault(); setActiveTab('toolbar'); setSearchFocus(value => value + 1)
          }
          if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') {
            event.preventDefault()
            const bounds = event.currentTarget.getBoundingClientRect()
            setContextMenu({ x: bounds.left + 32, y: bounds.top + 32 })
          }
        }}>
        {graph?.nodes.length ? <ReactFlow nodes={displayed} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          onNodesChange={onNodesChange} onNodeClick={(_, node) => select({ kind: 'source_node', id: node.id }, false, true)}
          onNodeContextMenu={(event, node) => {
            event.preventDefault()
            select({ kind: 'source_node', id: node.id }, false, !!node.selected)
            setContextMenu({ x: event.clientX, y: event.clientY })
          }}
          onSelectionContextMenu={event => { event.preventDefault(); setContextMenu({ x: event.clientX, y: event.clientY }) }}
          onPaneContextMenu={event => { event.preventDefault(); setNodes(nodes => nodes.map(node => ({ ...node, selected: false })))
            setSubject(null); setContextMenu({ x: event.clientX, y: event.clientY }) }}
          onEdgeContextMenu={(event, edge) => { event.preventDefault(); setContextMenu({ x: event.clientX, y: event.clientY, edgeId: edge.id }) }}
          onEdgeClick={(_, item) => select({ kind: 'source_edge', id: item.id })} colorMode={colorMode}
          nodesConnectable={editable && !!catalog} nodesDraggable={canLayout} edgesReconnectable={editable}
          onConnectStart={() => { cancelledConnection.current = false }}
          onConnect={value => { if (!cancelledConnection.current && value.source && value.target) setConnection({ source: value.source, target: value.target }) }}
          onConnectEnd={(event, state) => {
            if (cancelledConnection.current || state.isValid || state.toNode || !editable || dirty || !catalog
              || state.fromHandle?.type !== 'target' || (event.target as Element)?.closest?.('.react-flow__node')) return
            const target = state.fromNode?.id
            const port = catalog.ports.find(node => node.node_id === target)?.ports.find(port =>
              port.direction === 'input' && port.source_module === catalog.modules[0]?.module_id && !!port.source_port)
            if (!target || !port?.source_port) { setNotice('No compatible insertion adapter for this input.'); return }
            const pointer = 'changedTouches' in event ? event.changedTouches[0] : event
            if (!pointer) return
            const point = flow.screenToFlowPosition({ x: pointer.clientX, y: pointer.clientY })
            setDrop({ id: crypto.randomUUID(), ...point, connection: { target_id: target, field: port.id,
              port: port.source_port, key: port.source_key ?? null } }); setActiveTab('toolbar')
          }}
          onReconnect={(edge, value) => { if (!cancelledConnection.current && value.source && value.target === edge.target) setConnection({ source: value.source,
            target: value.target, field: graph?.edges.find(item => item.id === edge.id)?.field }) }}
          onPaneClick={clearSelection} panOnScroll selectionMode={SelectionMode.Partial}
          selectionOnDrag={interactions.mode === 'cursor'} panOnDrag={interactions.mode === 'hand'}
          multiSelectionKeyCode={['Meta', 'Control', 'Shift']} selectionKeyCode='Shift'
          onNodeDragStart={interactions.begin} onNodeDragStop={interactions.finish}
          onSelectionDragStart={interactions.begin} onSelectionDragStop={interactions.finish}
          snapToGrid snapGrid={[16, 16]} autoPanOnNodeDrag={canLayout}
          deleteKeyCode={null} minZoom={0.08} maxZoom={2}
          className='h-full bg-[var(--bg)] [--xy-background-color:var(--bg)]' />
          : <div className='flex h-full items-center justify-center text-sm text-[var(--text-tertiary)]'>
          {data?.source_status === 'UNAVAILABLE' ? 'Source unavailable. Clear AI context or select another run.'
            : data?.run_id ? 'No authored Terraform in this run.' : 'Select a run to view its architecture.'}</div>}
        <NativeCanvasControls actions={interactions} />
        {sourceGraph && data?.run_id && <ArchitectureAnnotations key={data.run_id} workspaceId={workspaceId}
          runId={data.run_id} revision={sourceGraph.revision} disabled={!canLayout} />}
        {sourceGraph && data?.run_id && <ArchitecturePresence key={`${data.run_id}:${sourceGraph.revision}`}
          workspaceId={workspaceId} runId={data.run_id} revision={sourceGraph.revision}
          selection={sourceGraph.nodes.some(node => node.id === subject?.id) ? subject!.id : null} />}
        <Chip className='absolute top-14 right-3 md:hidden' onClick={() => setMobileOpen(true)}>Panel</Chip>
      </main>

      <section className='terminal-container relative flex shrink-0 flex-col overflow-hidden border-[var(--border)] border-t bg-[var(--bg)]'
        aria-label='Architecture evidence'>
        <div role='separator' tabIndex={0} aria-orientation='horizontal' aria-label='Resize evidence'
          aria-valuenow={terminalHeight} aria-valuemin={TERMINAL_HEIGHT.MIN}
          className='absolute inset-x-0 top-[-4px] z-20 h-2 cursor-ns-resize focus-visible:outline focus-visible:outline-[var(--selection)]'
          onPointerDown={terminalResize} onKeyDown={(event) => {
            if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
              event.preventDefault()
              setTerminalHeight(Math.min(window.innerHeight * TERMINAL_HEIGHT.MAX_PERCENTAGE,
                terminalHeight + (event.key === 'ArrowUp' ? 24 : -24)))
            }
          }} />
        <div className='flex shrink-0 items-center gap-1 px-3 py-1'>
          {['Changes', 'Plan', 'Delivery'].map((value) => <Button key={value} variant={evidenceTab === value ? 'active' : 'ghost'}
            className='h-[28px] rounded-md text-[12.5px]' disabled={busy || !graph} onClick={() => {
              setEvidenceTab(value); setEvidence(null)
              setTerminalHeight(Math.max(206, terminalHeight))
              const target = sourceGraph && data?.run_id ? { tab: value, run_id: data.run_id, revision: sourceGraph.revision } : null
              if ((value === 'Plan' || value === 'Delivery') && target) void perform(async () => {
                const result = await request({ operation: value.toLowerCase(), run_id: target.run_id, revision: target.revision })
                setEvidence({ ...target, value: result })
              })
            }}>{value}</Button>)}
          <Button variant='ghost' aria-label='Toggle evidence' className='ml-auto' onClick={() => setTerminalHeight(terminalHeight > TERMINAL_HEIGHT.MIN
            ? TERMINAL_HEIGHT.MIN : 206)}><ChevronDown className={cn('size-[14px]', terminalHeight <= TERMINAL_HEIGHT.MIN && 'rotate-180')} /></Button>
        </div>
        <div className='min-h-0 flex-1 overflow-auto p-4 text-sm'>
          {evidenceTab === 'Changes' && data?.host_review && data.run_id && <HostReviewStatus workspaceId={workspaceId}
            runId={data.run_id} reference={data.host_review} canEdit={canNavigate} onChange={setChange}
            onRefresh={() => void perform(async () => {
              if (isDraft && data?.host_review) await request({ operation: 'adopt_authored', run_id: data.run_id,
                ...data.host_review, metadata_revision: data.metadata?.metadata_revision })
              await workspace.refetch()
            })} />}
          {evidenceTab === 'Changes' && <div className='mb-3 flex flex-wrap items-center gap-2'>
            <Chip disabled={!editable || dirty || !proposal?.can_undo} onClick={() => perform(() => command({ type: 'undo' }))}>Undo edit</Chip>
            <Chip disabled={!editable || dirty || !proposal?.can_redo} onClick={() => perform(() => command({ type: 'redo' }))}>Redo edit</Chip>
            <Chip disabled={!editable || (!dirty && !proposal?.can_undo && !proposal?.can_redo)} onClick={() => perform(async () => {
              if (proposal?.can_undo || proposal?.can_redo) await command({ type: 'reset' })
              else { setDraftsByNode({}); setDraftRevision(null) }
            })}>Discard edits</Chip>
            {dirty && <Chip disabled={!editable} onClick={() => perform(stage)}>Review changes</Chip>}
          </div>}
          {evidenceTab === 'Changes' && (change ? <>{change.files.map((item) => <section key={item.path} className='mb-4'>
            <p className='mb-2 font-medium'>{item.path}</p><pre className='whitespace-pre-wrap break-words text-xs'>{item.diff}</pre></section>)}
            <Chip disabled={!editable} onClick={() => perform(download)}>Download reviewed proposal</Chip>
            <Chip disabled={!editable || dirty} onClick={() => perform(async () => {
              const queued = hostReviewSchema.parse(await request({ operation: 'queue_review', ...binding(), change }))
              setData(previous => previous ? { ...previous, host_review: { request_id: queued.request_id, revision: queued.revision } } : previous)
            })}>Send to host</Chip>
            {candidateEdits(change) && change.change_hash !== proposal?.change?.change_hash && <>
              <Chip disabled={!editable || dirty} onClick={() => perform(() => {
                const edits = candidateEdits(change)!
                return command({ type: 'field_edit', subject: { kind: 'source_node', id: edits[0].node_id }, edits })
              })}>Keep proposed edits</Chip>
              <Chip disabled={busy} onClick={() => setChange(proposal?.change ?? null)}>Dismiss proposal</Chip>
            </>}
            <p className='mt-3 text-xs'>Proposed source only. A new plan and host review are required.</p></> : <p role={busy ? 'status' : undefined}>{busy ? 'Checking source…' : 'No proposed changes.'}</p>)}
          {(evidenceTab === 'Plan' || evidenceTab === 'Delivery') && (busy ? <p>Loading…</p> : <ArchitectureEvidence value={evidence?.tab === evidenceTab
            && evidence.run_id === data?.run_id && evidence.revision === sourceGraph?.revision ? evidence.value : null} kind={evidenceTab} proposed={!!proposal?.change} />)}
        </div>
      </section>
    </div>

    <aside className={cn('panel-container relative flex shrink-0 flex-col overflow-hidden border-[var(--border)] border-l bg-[var(--bg)] md:max-w-[max(290px,40vw)]',
      'max-md:absolute max-md:inset-0 max-md:z-[var(--z-dropdown)] max-md:w-full!', !mobileOpen && 'max-md:hidden')}
      aria-label='Architecture panel'>
      <div className='flex shrink-0 items-center gap-1 px-2 py-3.5'>
        {([{ id: 'copilot', label: 'Chat' }, { id: 'toolbar', label: 'Toolbar' }, { id: 'editor', label: 'Editor' }] as const).map((item) =>
          <Button key={item.id} id={`architecture-${item.id}-tab`} data-tab-button={item.id}
            variant={activeTab === item.id ? 'active' : 'ghost'} className='h-[28px] rounded-md border border-transparent text-[12.5px]'
            onClick={() => {
              setActiveTab(item.id)
              if (item.id === 'editor' && !subject && data?.focus) select(data.focus.subject, true)
            }}>{item.label}</Button>)}
        <Button variant='ghost' className='ml-auto md:hidden' aria-label='Close architecture panel' onClick={() => setMobileOpen(false)}><X className='size-4' /></Button>
      </div>
      <div data-tab-content='copilot' className={activeTab === 'copilot' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}>
        {data?.run_id && sourceGraph ? <><div className={proposal?.change ? 'px-3 pb-2 text-xs text-[var(--text-tertiary)]' : 'hidden'}>
          Context includes unpublished changes.</div><ArchitectureChat key={`${workspaceId}:${data.run_id}`} workspaceId={workspaceId} runId={data.run_id} chatIds={data.chat_ids}
          onProposal={reference => void perform(async () => {
            const result = sourceChangeSchema.parse(await request({ operation: 'proposal', ...binding(), ...reference }))
            setChange(result); setEvidenceTab('Changes'); setTerminalHeight(Math.max(206, terminalHeight))
            const affected = new Set((candidateEdits(result) ?? []).map(edit => edit.node_id))
            setNodes(nodes => nodes.map(node => ({ ...node, selected: affected.has(node.id) })))
          })}
          binding={{ revision: sourceGraph.revision, subject: subject ?? data.focus?.subject ?? null, enabled: data.context_enabled,
            ...(proposal?.change ? { draft_revision: proposal.draft_revision, change_hash: proposal.change.change_hash } : {}) }} onBusyChange={setChatBusy} /></>
          : <p className='p-4 text-sm'>Select a run to start.</p>}
      </div>
      <div data-tab-content='toolbar' className={activeTab === 'toolbar' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}>
        {catalog && <CompositionPalette key={`${sourceGraph?.revision}:${catalog.draft_revision}`} catalog={catalog}
          disabled={!editable || dirty} onCommand={command} drop={drop}
          canvasRoot={canvasRoot}
          onPlacement={(address, point) => pendingPlacement.current.set(address, point)} onDropConsumed={() => setDrop(null)} />}
        {catalogQuery.isError && <p role='status' className='p-3 text-xs text-[var(--text-tertiary)]'>Composition tools unavailable. Refresh to check source.</p>}
        <div className='border-[var(--border)] border-y bg-[var(--surface-4)] px-3 py-1.5 text-sm'>In this run</div>
        {graph ? <ArchitectureSearch graph={graph} icons={data?.icons ?? {}} disabled={!editable || dirty}
          focusRequest={searchFocus} onSelect={id => select({ kind: 'source_node', id }, true)} onCommand={command} />
          : <p className='px-2 text-sm'>Select a run to inspect its components.</p>}
      </div>
      <div data-tab-content='editor' className={activeTab === 'editor' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}>
        <div className='p-3'><ChipSelect aria-label='Find declaration' searchable placeholder='Find declaration'
          options={(graph?.nodes ?? []).map((node) => ({ label: node.address, value: node.id }))}
          value={selected?.id ?? ''} onChange={(id) => select({ kind: 'source_node', id }, true)} /></div>
        <div className='flex flex-wrap gap-1 border-[var(--border)] border-b px-3 pb-3'>
          {['Configuration', 'Source'].map((value) => <Chip key={value} active={editorTab === value}
            disabled={busy || !graph} onClick={() => setEditorTab(value)}>{value}</Chip>)}
        </div>
        <div className='flex-1 overflow-auto p-4 text-sm'>
          {editorTab === 'Configuration' && <>
            {!selected && !edge && <p>Select a declaration or connection.</p>}
            {edge && <><p className='mb-2 font-medium'>Declared reference</p>
              <p className='mb-2 break-words text-xs'>{graph?.nodes.find((node) => node.id === edge.source)?.address}</p>
              <p className='mb-3 break-words text-xs'>→ {graph?.nodes.find((node) => node.id === edge.target)?.address}</p>
              {edge.field && catalog?.ports.some(node => node.node_id === edge.target
                && node.ports.some(port => port.id === edge.field && port.direction === 'input'))
                && <Chip disabled={!editable || dirty} onClick={() => setConnection({ target: edge.target, field: edge.field! })}>Disconnect</Chip>}
              <details><summary>Evidence</summary><pre className='whitespace-pre-wrap break-words text-xs'>{format(edge)}</pre></details></>}
            {selected && <><p className='mb-3 break-words font-medium'>{selected.address}</p>
              <p className='mb-3 text-xs text-[var(--text-tertiary)]'>{selected.source.path}:{selected.source.start_line}
                {selected.kind === 'module' ? ' · Module inputs' : selected.parent_id ? ' · Shared module source' : ''}</p>
              {Object.entries(selected.fields).map(([name, field]) => <div key={`${selected.id}:${name}`}><ConfigurationField
                name={name} field={field} text={drafts[name] ?? proposedFields.get(name)} disabled={!editable}
                onChange={text => setDrafts(value => ({ ...value, [name]: text }))} />
                {field.overridden_by && <Chip onClick={() => select({ kind: 'source_node', id: field.overridden_by!.node_id }, true)}>Edit module input</Chip>}
              </div>)}
              <Chip disabled={!editable || !dirty} onClick={() => perform(stage)}>Review changes</Chip>
            </>}
            {subject && <Chip disabled={!canNavigate} onClick={() => perform(async () => {
              if (!proposal?.change) await request({ operation: 'focus', ...binding(), subject })
              await queryClient.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
              setData((value) => value && graph ? { ...value, focus: { revision: graph.revision, subject,
                label: selected?.address ?? 'Selected connection' }, context_enabled: true } : value)
              setActiveTab('copilot')
            })}>Ask AI about selection</Chip>}
          </>}
          {editorTab === 'Source' && (selected ? <><p className='mb-3 text-xs'>{selected.source.path}:{selected.source.start_line}</p>
            <pre className='whitespace-pre-wrap break-words text-xs'>{file?.preview?.split('\n').slice(selected.source.start_line - 1, selected.source.end_line).join('\n') ?? 'Source withheld.'}</pre></> : <p>Select a declaration.</p>)}
        </div>
      </div>
      <div role='separator' tabIndex={0} aria-orientation='vertical' aria-label='Resize panel' onPointerDown={panelResize}
        aria-valuenow={panelWidth} aria-valuemin={PANEL_WIDTH.MIN}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault()
            setPanelWidth(Math.min(Math.max(PANEL_WIDTH.MIN, window.innerWidth * PANEL_WIDTH.MAX_PERCENTAGE),
              panelWidth + (event.key === 'ArrowLeft' ? 24 : -24)))
          }
        }} className='absolute inset-y-0 left-[-4px] z-20 hidden w-2 cursor-ew-resize focus-visible:outline focus-visible:outline-[var(--selection)] md:block' />
    </aside>
    {connection && catalog && graph && <ConnectionDialog key={JSON.stringify(connection)} intent={connection}
      catalog={catalog} graph={graph} disabled={!editable || dirty} onCommand={command} onClose={() => setConnection(null)} />}
  </div>
}
