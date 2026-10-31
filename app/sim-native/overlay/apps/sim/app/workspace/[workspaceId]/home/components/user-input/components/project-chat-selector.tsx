'use client'

import { useRef, useState } from 'react'
import { Button, ChipConfirmModal, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@sim/emcn'
import { ChevronDown } from '@sim/emcn/icons'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { requestJson } from '@/lib/api/client/request'
import { createMothershipChatContract } from '@/lib/api/contracts/mothership-chats'
import { useFeatureFlag } from '@/app/workspace/[workspaceId]/providers/feature-flags-provider'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { RowActionsMenu } from '@/app/workspace/[workspaceId]/settings/components/row-actions-menu/row-actions-menu'
import { useProjectCatalog, useProjectChat, useProjectSetup } from '@/hooks/queries/enterprise-chat'
import type { ProjectSetup } from '@/lib/api/contracts/project-setup'

interface ProjectChatSelectorProps {
  workspaceId: string
  userId?: string
  chatId?: string
  busy: boolean
}

function LocalProjectChatSelector({ workspaceId, userId, chatId, busy }: ProjectChatSelectorProps) {
  const router = useRouter()
  const { binding, bind } = useProjectChat(workspaceId, userId, chatId)
  const catalog = useProjectCatalog(workspaceId, userId)
  const {canAdmin} = useUserPermissionsContext()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const {setup, update} = useProjectSetup(workspaceId, userId, open)
  const [deleting, setDeleting] = useState<ProjectSetup | null>(null)
  const pending = useRef<{chatId: string; projectId: string} | null>(null)
  const unavailable = !!chatId && binding.isError
  const bound = !unavailable && binding.data?.status === 'PROJECT_BOUND'
  const projects = !catalog.isError && !catalog.isFetching ? catalog.data?.projects ?? [] : []
  const selected = projects.find(project => project.project_id === binding.data?.project_id)
  const label = unavailable ? 'Project unavailable' : bound
    ? `Project: ${selected?.name ?? 'Selected project'}` : 'Choose project'

  const choose = async (projectId: string) => {
    setSaving(true)
    setError('')
    try {
      let next = pending.current
      if (!next || next.projectId !== projectId) {
        const created = await requestJson(createMothershipChatContract, { body: { workspaceId } })
        next = {chatId: created.id, projectId}
        pending.current = next
      }
      await bind.mutateAsync({chat_id: next.chatId, project_id: projectId})
      setOpen(false)
      router.push(`/workspace/${workspaceId}/chat/${next.chatId}`)
    } catch {
      setError('The project could not be opened. Check access and try again.')
      setOpen(true)
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!deleting) return
    try {
      await update.mutateAsync({operation: 'delete', project_id: deleting.project_id, revision: deleting.revision})
      pending.current = null
      setDeleting(null)
      if (binding.data?.project_id === deleting.project_id) router.push(`/workspace/${workspaceId}/home`)
    } catch {
      setDeleting(null)
      setError('The project could not be deleted. Check access or refresh if it changed.')
      setOpen(true)
    }
  }

  return (
    <header className='flex h-12 shrink-0 items-center px-3 pr-20 md:px-6 md:pr-24' aria-label='Chat project'>
      <DropdownMenu open={open} onOpenChange={value => {
        setOpen(value)
        if (value) {
          setError('')
          void catalog.refetch()
          if (chatId) void binding.refetch()
        }
      }}>
        <DropdownMenuTrigger asChild>
          <Button variant='ghost' size='sm' disabled={!userId || busy || saving}
            aria-label={label} className='max-w-full gap-2'>
            <span className='truncate'>{saving ? 'Opening project...' : label}</span>
            <ChevronDown className='size-3 shrink-0' aria-hidden='true' />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align='start' className='w-72 max-w-[calc(100vw-2rem)]'>
          {catalog.isFetching ? <DropdownMenuItem disabled>Checking project access...</DropdownMenuItem>
            : projects.length ? projects.map(project => (
              <div key={project.project_id} className='flex min-w-0 items-center gap-1'>
              <DropdownMenuItem className='min-w-0 flex-1' aria-label={project.name} title={project.name}
                disabled={saving || busy || (bound && project.project_id === binding.data?.project_id)}
                onSelect={event => {
                  event.preventDefault()
                  if (project.setup_status && project.setup_status !== 'ADMITTED') {
                    setError('This project needs source access and consent for the selected model. Open its actions menu and choose Configure.')
                  } else void choose(project.project_id)
                }}>
                <span className='min-w-0 flex-1 truncate'>{project.name}</span>
                {project.setup_status && project.setup_status !== 'ADMITTED'
                  && <span className='ml-auto shrink-0 text-xs text-[var(--text-muted)]'>Needs setup</span>}
              </DropdownMenuItem>
              <RowActionsMenu label={`Actions for ${project.name}`} triggerClassName='shrink-0'
                actions={[
                  {label: 'Configure', onSelect: () => {
                    setOpen(false)
                    router.push(`/workspace/${workspaceId}/projects?project=${encodeURIComponent(project.project_id)}`)
                  }},
                  {label: 'Delete', destructive: true,
                    disabled: !canAdmin || saving || busy || update.isPending || !setup.data?.projects.some(row => row.project_id === project.project_id),
                    onSelect: () => {
                      const row = setup.data?.projects.find(row => row.project_id === project.project_id)
                      if (row) {setDeleting(row); setOpen(false)}
                    }},
                ]} />
              </div>
            )) : <p className='px-2 py-2 text-sm text-[var(--text-muted)]' role='status'>
              {catalog.isError ? 'Project access could not be checked.' : 'No projects yet. Create or import a project to get started.'}
            </p>}
          {chatId && <p className='px-2 py-2 text-xs text-[var(--text-muted)]'>Choosing another project starts a new chat.</p>}
          {error && <p role='alert' className='px-2 py-2 text-xs'>{error}</p>}
          <DropdownMenuItem asChild><Link href={`/workspace/${workspaceId}/projects`}>Create or manage projects</Link></DropdownMenuItem>
          <DropdownMenuItem asChild><Link href={`/workspace/${workspaceId}/integrations`}>Integrations</Link></DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChipConfirmModal open={!!deleting} onOpenChange={value => {if (!value && !update.isPending) setDeleting(null)}}
        srTitle='Delete project' title='Delete project' defaultAction='dismiss'
        text={['Delete ', {text: deleting?.name ?? 'this project', bold: true},
          '? This removes its saved setup and disables its project chats. Connected accounts and Confluence pages are kept.']}
        confirm={{label: 'Delete', onClick: () => {void remove()}, pending: update.isPending, pendingLabel: 'Deleting...'}} />
    </header>
  )
}

export function ProjectChatSelector(props: ProjectChatSelectorProps) {
  const enabled = useFeatureFlag('openrouter-chat')
  return enabled ? <LocalProjectChatSelector {...props} /> : null
}
