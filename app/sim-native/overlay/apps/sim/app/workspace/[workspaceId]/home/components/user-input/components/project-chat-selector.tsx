'use client'

import { useRef, useState } from 'react'
import { Button, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@sim/emcn'
import { ChevronDown } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { requestJson } from '@/lib/api/client/request'
import { createMothershipChatContract } from '@/lib/api/contracts/mothership-chats'
import { useFeatureFlag } from '@/app/workspace/[workspaceId]/providers/feature-flags-provider'
import { useProjectCatalog, useProjectChat } from '@/hooks/queries/enterprise-chat'

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
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(false)
  const pending = useRef<{chatId: string; projectId: string} | null>(null)
  const unavailable = !!chatId && binding.isError
  const bound = !unavailable && binding.data?.status === 'PROJECT_BOUND'
  const projects = !catalog.isError && !catalog.isFetching ? catalog.data?.projects ?? [] : []
  const selected = projects.find(project => project.project_id === binding.data?.project_id)
  const label = unavailable ? 'Project unavailable' : bound
    ? `Project: ${selected?.name ?? 'Selected project'}` : 'Choose project'

  const choose = async (projectId: string) => {
    setSaving(true)
    setFailed(false)
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
      setFailed(true)
      setOpen(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <header className='flex h-12 shrink-0 items-center px-3 pr-20 md:px-6 md:pr-24' aria-label='Chat project'>
      <DropdownMenu open={open} onOpenChange={value => {
        setOpen(value)
        if (value) {
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
              <DropdownMenuItem key={project.project_id}
                disabled={saving || busy || (bound && project.project_id === binding.data?.project_id)}
                onSelect={event => {event.preventDefault(); void choose(project.project_id)}}>
                <span className='truncate'>{project.name}</span>
              </DropdownMenuItem>
            )) : <p className='px-2 py-2 text-sm text-[var(--text-muted)]' role='status'>
              {catalog.isError ? 'Project access could not be checked.' : 'No projects available. Ask your workspace administrator to connect a project.'}
            </p>}
          {chatId && <p className='px-2 py-2 text-xs text-[var(--text-muted)]'>Choosing another project starts a new chat.</p>}
          {failed && <p role='alert' className='px-2 py-2 text-xs'>The project could not be opened. Check access and try again.</p>}
          <DropdownMenuItem asChild><Link href={`/workspace/${workspaceId}/integrations`}>Integrations</Link></DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  )
}

export function ProjectChatSelector(props: ProjectChatSelectorProps) {
  const enabled = useFeatureFlag('openrouter-chat')
  return enabled ? <LocalProjectChatSelector {...props} /> : null
}
