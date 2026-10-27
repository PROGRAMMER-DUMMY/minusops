'use client'

import { useRef, useState } from 'react'
import {
  Button, Chip, ChipConfirmModal, ChipModal, ChipModalBody, ChipModalError,
  ChipModalField, ChipModalFooter, ChipModalHeader, chipVariants, cn,
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger, OverflowText, RowActions, rowActionsGroupClass,
} from '@sim/emcn'
import {
  Download, Duplicate, Lock, MoreHorizontal, Pencil, Plus, SquareArrowUpRight,
  Trash, Unlock, Workflow,
} from '@sim/emcn/icons'
import { useIsMutating } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import {
  architectureWorkspaceSchema, type ArchitectureAction, type ArchitectureWorkspace,
} from '@/lib/api/contracts/minusops-architecture'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { architectureKeys, useArchitectureAction, useArchitectureWorkspace } from '@/hooks/queries/minusops-architecture'
import { SIDEBAR_ITEM_GAP_CLASS, SIDEBAR_SECTION_GAP_CLASS } from '../constants'
import { SidebarRowAction } from './sidebar-row-actions'
import { SidebarSection } from './sidebar-section/sidebar-section'

type ArchitectureRow = ArchitectureWorkspace['runs'][number]
type Dialog =
  | { kind: 'create'; name: string; operationId: string; submittedName: string | null }
  | { kind: 'rename'; name: string; run: ArchitectureRow }
  | { kind: 'delete'; run: ArchitectureRow }
type Intent =
  | Extract<ArchitectureAction, { operation: 'create' | 'rename' | 'lock' | 'delete' }>
  | { operation: 'duplicate'; run: ArchitectureRow }
  | { operation: 'export'; run: ArchitectureRow }

export function ArchitectureList({ workspaceId, collapsed }: { workspaceId: string; collapsed: boolean }) {
  const searchParams = useSearchParams()
  const requestedRun = searchParams.get('run') ?? undefined
  const workspace = useArchitectureWorkspace(workspaceId, requestedRun)
  const mutation = useArchitectureAction(workspaceId)
  const mutating = useIsMutating({ mutationKey: architectureKeys.workspace(workspaceId) }) > 0
  const { canAdmin } = useUserPermissionsContext()
  const router = useRouter()
  const pathname = usePathname()
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const [menuRun, setMenuRun] = useState<string | null>(null)
  const inFlight = useRef(false)
  const busy = working || mutating
  const href = `/workspace/${workspaceId}/architecture`
  const active = pathname === href
  const activeRun = requestedRun ?? workspace.data?.run_id
  const runs = workspace.data?.runs ?? []
  const runHref = (runId: string) => `${href}?run=${encodeURIComponent(runId)}`
  const message = workspace.isError ? 'Architectures unavailable' : workspace.isPending ? 'Loading...' : !runs.length ? 'No architectures yet' : null
  const nameDialog = dialog && dialog.kind !== 'delete' ? dialog : null
  const dialogLocked = dialog && dialog.kind !== 'create'
    ? (runs.find((run) => run.run_id === dialog.run.run_id) ?? dialog.run).locked
    : false

  function closeDialog() {
    if (inFlight.current || busy) return
    setDialog(null)
    setError(null)
  }

  function newArchitecture() {
    if (!canAdmin || busy || inFlight.current) return
    setError(null)
    setDialog({ kind: 'create', name: '', operationId: crypto.randomUUID(), submittedName: null })
  }

  function editName(name: string) {
    if (!nameDialog || busy) return
    setError(null)
    if (nameDialog.kind === 'create') {
      const newIntent = nameDialog.submittedName !== null && name.trim() !== nameDialog.submittedName
      setDialog({ ...nameDialog, name,
        operationId: newIntent ? crypto.randomUUID() : nameDialog.operationId,
        submittedName: newIntent ? null : nameDialog.submittedName })
    } else {
      setDialog({ ...nameDialog, name })
    }
  }

  async function perform(intent: Intent) {
    if (inFlight.current || busy || (!canAdmin && intent.operation !== 'export')) return
    inFlight.current = true
    setWorking(true)
    setError(null)
    setMenuRun(null)
    try {
      let action: ArchitectureAction
      if (intent.operation === 'duplicate' || intent.operation === 'export') {
        const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture?run_id=${encodeURIComponent(intent.run.run_id)}`, {
          credentials: 'same-origin', cache: 'no-store',
        })
        if (!response.ok) throw new Error('Architecture unavailable')
        const fresh = architectureWorkspaceSchema.parse(await response.json())
        const { metadata, architecture } = fresh
        if (fresh.run_id !== intent.run.run_id || !metadata || metadata.run_id !== intent.run.run_id || !architecture) {
          throw new Error('Architecture unavailable')
        }
        action = intent.operation === 'duplicate'
          ? { operation: 'duplicate', run_id: metadata.run_id,
              metadata_revision: metadata.metadata_revision, revision: architecture.revision,
              operation_id: crypto.randomUUID() }
          : { operation: 'export', run_id: metadata.run_id, revision: architecture.revision }
      } else {
        action = intent
      }
      const reply = await mutation.mutateAsync(action)
      if (action.operation === 'export') {
        if (!('kind' in reply) || reply.kind !== 'minusops-architecture-design-export') {
          throw new Error('Architecture export unavailable')
        }
        const url = URL.createObjectURL(new Blob([JSON.stringify(reply, null, 2)], { type: 'application/json' }))
        const download = document.createElement('a')
        try {
          download.href = url
          download.download = `architecture-${action.run_id}.json`
          document.body.appendChild(download)
          download.click()
        } finally {
          download.remove()
          window.setTimeout(() => URL.revokeObjectURL(url), 1000)
        }
      } else {
        const updated = architectureWorkspaceSchema.parse(reply)
        if (action.operation === 'create' || action.operation === 'duplicate') {
          if (!updated.run_id) throw new Error('Architecture destination unavailable')
          router.push(runHref(updated.run_id))
        } else if (action.operation === 'delete' && active && activeRun === action.run_id) {
          const next = updated.runs.find((run) => run.run_id === updated.run_id) ?? updated.runs[0]
          router.replace(next ? runHref(next.run_id) : href)
        }
        setDialog(null)
      }
    } catch {
      setError(intent.operation === 'create'
        ? 'Architecture could not be created. Retry this request or refresh to check its status.'
        : 'Architecture action could not be completed. Refresh and try again.')
    } finally {
      inFlight.current = false
      setWorking(false)
    }
  }

  function submitDialog() {
    if (!dialog || busy || !canAdmin || dialogLocked) return
    if (dialog.kind === 'delete') {
      void perform({ operation: 'delete', run_id: dialog.run.run_id, metadata_revision: dialog.run.metadata_revision })
    } else if (dialog.name.trim()) {
      const name = dialog.name.trim()
      if (dialog.kind === 'create') {
        setDialog({ ...dialog, submittedName: name })
        void perform({ operation: 'create', name, operation_id: dialog.operationId })
      } else {
        void perform({ operation: 'rename', run_id: dialog.run.run_id,
          metadata_revision: dialog.run.metadata_revision, name })
      }
    }
  }

  return <>
    <SidebarSection title='Architectures' railCollapsed={collapsed}
      className={cn(SIDEBAR_SECTION_GAP_CLASS, 'shrink-0')}
      action={collapsed ? undefined : <Button variant='quiet' size='icon' aria-label='New architecture'
        title={canAdmin ? 'New architecture' : 'Admin access required'} disabled={!canAdmin || busy}
        onClick={newArchitecture}><Plus className='size-[16px]' /></Button>}>
      <div className={cn(SIDEBAR_ITEM_GAP_CLASS, 'flex min-w-0 flex-col px-2')} aria-busy={busy}>
        {collapsed ? <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Chip fullWidth leftIcon={Workflow} active={active} aria-label='Architectures' title='Architectures' />
          </DropdownMenuTrigger>
          <DropdownMenuContent side='right' align='start'>
            <DropdownMenuItem disabled={!canAdmin || busy} onSelect={newArchitecture}>
              <Plus />New architecture
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {message && <DropdownMenuItem disabled>{message}</DropdownMenuItem>}
            {runs.map((run) => <DropdownMenuItem key={run.run_id} asChild
              active={active && activeRun === run.run_id}>
              <Link href={runHref(run.run_id)} aria-current={active && activeRun === run.run_id ? 'page' : undefined}>
                <OverflowText label={run.name} />
                {run.locked && <Lock aria-label='Locked' />}
              </Link>
            </DropdownMenuItem>)}
          </DropdownMenuContent>
        </DropdownMenu> : <>
          {message && <p className='px-2 text-[var(--text-muted)] text-small'
            role={workspace.isError ? 'alert' : 'status'}>{message}</p>}
          {runs.map((run) => <div key={run.run_id}
            className={cn('relative', rowActionsGroupClass)}>
            <Link href={runHref(run.run_id)}
              className={cn(chipVariants({ active: (active && activeRun === run.run_id) || menuRun === run.run_id, fullWidth: true }),
                run.locked || menuRun === run.run_id ? 'pr-[28px]' : 'group-focus-within/row-actions:pr-[28px] group-hover/row-actions:pr-[28px]',
                '[@media(any-pointer:coarse)]:pr-[28px] [@media(hover:none)]:pr-[28px]')}
              aria-label={run.locked ? `${run.name} (locked)` : run.name}
              aria-current={active && activeRun === run.run_id ? 'page' : undefined}
              title={run.design_status === 'DRAFT' ? `${run.name} (unpublished design)` : run.name}>
              <OverflowText label={run.name} className='flex-1 text-[var(--text-body)]' />
            </Link>
            <RowActions open={menuRun === run.run_id} className='-translate-y-1/2 absolute top-1/2 right-2'
              indicator={run.locked ? <Lock aria-label='Locked' className='size-[12px] text-[var(--text-icon)]' /> : undefined}>
              <DropdownMenu modal={false} open={menuRun === run.run_id}
                onOpenChange={(open) => setMenuRun(open ? run.run_id : null)}>
                <DropdownMenuTrigger asChild>
                  <SidebarRowAction aria-label={`Options for ${run.name}`} disabled={busy}>
                    <MoreHorizontal className='size-[14px] text-[var(--text-icon)]' />
                  </SidebarRowAction>
                </DropdownMenuTrigger>
                <DropdownMenuContent align='start' side='right'>
                  <DropdownMenuItem asChild disabled={busy}>
                    <a href={runHref(run.run_id)} target='_blank' rel='noopener noreferrer'
                      onClick={(event) => { if (busy) event.preventDefault() }}>
                      <SquareArrowUpRight />Open in new tab
                    </a>
                  </DropdownMenuItem>
                  <DropdownMenuItem disabled={!canAdmin || busy || run.locked} onSelect={() => {
                    setError(null)
                    setDialog({ kind: 'rename', run, name: run.name })
                  }}><Pencil />Rename</DropdownMenuItem>
                  <DropdownMenuItem disabled={!canAdmin || busy} onSelect={() => void perform({
                    operation: 'lock', run_id: run.run_id, metadata_revision: run.metadata_revision, locked: !run.locked,
                  })}>{run.locked ? <Unlock /> : <Lock />}{run.locked ? 'Unlock' : 'Lock'}</DropdownMenuItem>
                  <DropdownMenuItem disabled={!canAdmin || busy} title='Duplicate as an unpublished design'
                    onSelect={() => void perform({ operation: 'duplicate', run })}><Duplicate />Duplicate</DropdownMenuItem>
                  <DropdownMenuItem disabled={busy} onSelect={() => void perform({ operation: 'export', run })}>
                    <Download />Export
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem disabled={!canAdmin || busy || run.locked} onSelect={() => {
                    setError(null)
                    setDialog({ kind: 'delete', run })
                  }}><Trash />Delete</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </RowActions>
          </div>)}
        </>}
        {!dialog && <ChipModalError>{error}</ChipModalError>}
      </div>
    </SidebarSection>
    {nameDialog && <ChipModal open onOpenChange={(open) => { if (!open) closeDialog() }}
      dismissDisabled={busy} srTitle={nameDialog.kind === 'create' ? 'New architecture' : 'Rename architecture'}>
      <ChipModalHeader onClose={closeDialog}>
        {nameDialog.kind === 'create' ? 'New architecture' : 'Rename architecture'}
      </ChipModalHeader>
      <ChipModalBody>
        <ChipModalField type='input' title='Name' value={nameDialog.name} onChange={editName}
          placeholder='Architecture name' maxLength={120} autoComplete='off' required
          disabled={!canAdmin || busy || dialogLocked} />
        {nameDialog.kind === 'rename' && <p className='px-2 text-[var(--text-muted)] text-caption'>Changes the display name only.</p>}
        <ChipModalError>{error}</ChipModalError>
      </ChipModalBody>
      <ChipModalFooter onCancel={closeDialog} cancelDisabled={busy} primaryAction={{
        label: busy ? 'Saving...' : nameDialog.kind === 'create' ? error ? 'Retry create' : 'Create' : 'Rename',
        onClick: submitDialog, disabled: !canAdmin || busy || dialogLocked || !nameDialog.name.trim(),
      }} />
    </ChipModal>}
    {dialog?.kind === 'delete' && <ChipConfirmModal open
      onOpenChange={(open) => { if (!open) closeDialog() }} title='Remove from Architectures?'
      text='Infrastructure and run history are retained.'
      confirm={{ label: 'Remove', onClick: submitDialog, pending: busy, pendingLabel: 'Removing...',
        disabled: !canAdmin || dialogLocked }}>
      <ChipModalError>{error}</ChipModalError>
    </ChipConfirmModal>}
  </>
}
