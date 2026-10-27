'use client'

import { Chip, cn, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, OverflowText } from '@sim/emcn'
import { Workflow } from '@sim/emcn/icons'
import { useIsMutating } from '@tanstack/react-query'
import { usePathname, useRouter } from 'next/navigation'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { SidebarSection } from './sidebar-section/sidebar-section'
import { SIDEBAR_ITEM_GAP_CLASS, SIDEBAR_SECTION_GAP_CLASS } from '../constants'
import { architectureKeys, useArchitectureWorkspace, useSelectArchitecture } from '@/hooks/queries/minusops-architecture'

export function ArchitectureList({ workspaceId, collapsed }: { workspaceId: string; collapsed: boolean }) {
  const workspace = useArchitectureWorkspace(workspaceId)
  const selection = useSelectArchitecture(workspaceId)
  const selecting = useIsMutating({ mutationKey: architectureKeys.workspace(workspaceId) }) > 0
  const permissions = useUserPermissionsContext()
  const router = useRouter()
  const pathname = usePathname()
  const href = `/workspace/${workspaceId}/architecture`
  const active = pathname === href
  async function open(runId: string) {
    if (selecting) return
    if (runId !== workspace.data?.run_id) {
      try { await selection.mutateAsync(runId) } catch { return }
    }
    router.push(href)
  }
  const runs = workspace.data?.runs ?? []
  const message = workspace.isError ? 'Architectures unavailable' : workspace.isPending ? 'Loading…' : !runs.length ? 'No architectures yet' : null
  return <SidebarSection title='Architectures' railCollapsed={collapsed}
    className={cn(SIDEBAR_SECTION_GAP_CLASS, 'shrink-0')}>
    <div className={cn(SIDEBAR_ITEM_GAP_CLASS, 'flex min-w-0 flex-col px-2')}>
      {collapsed ? <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Chip fullWidth leftIcon={Workflow} active={active} aria-label='Architectures' title='Architectures' />
        </DropdownMenuTrigger>
        <DropdownMenuContent side='right' align='start'>
          {message && <DropdownMenuItem disabled>{message}</DropdownMenuItem>}
          {runs.map((run) => <DropdownMenuItem key={run.run_id} active={active && workspace.data?.run_id === run.run_id}
            disabled={selecting || (!permissions.canAdmin && workspace.data?.run_id !== run.run_id)}
            onSelect={() => void open(run.run_id)}><OverflowText label={run.name} /></DropdownMenuItem>)}
        </DropdownMenuContent>
      </DropdownMenu> : <>
        {message && <p className='px-2 text-[var(--text-muted)] text-small' role={workspace.isError ? 'alert' : 'status'}>{message}</p>}
        {runs.map((run) => <Chip key={run.run_id} fullWidth
          active={active && workspace.data?.run_id === run.run_id} aria-current={active && workspace.data?.run_id === run.run_id ? 'page' : undefined}
          title={run.run_id} disabled={selecting || (!permissions.canAdmin && workspace.data?.run_id !== run.run_id)}
          onClick={() => void open(run.run_id)}>{run.name}</Chip>)}
      </>}
      {selection.isError && <p className='px-2 text-small' role='alert'>Architecture could not be opened. Try again.</p>}
    </div>
  </SidebarSection>
}
