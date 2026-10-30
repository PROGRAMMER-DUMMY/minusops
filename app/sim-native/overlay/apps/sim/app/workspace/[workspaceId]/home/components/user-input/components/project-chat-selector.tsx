'use client'

import { useRef, useState } from 'react'
import { Button, Input } from '@sim/emcn'
import { useRouter } from 'next/navigation'
import { requestJson } from '@/lib/api/client/request'
import { projectChatRequestSchema } from '@/lib/api/contracts/enterprise-chat'
import { createMothershipChatContract } from '@/lib/api/contracts/mothership-chats'
import { useChatSurface } from '@/app/workspace/[workspaceId]/home/components/chat-surface-context'
import { useFeatureFlag } from '@/app/workspace/[workspaceId]/providers/feature-flags-provider'
import { useProjectChat } from '@/hooks/queries/enterprise-chat'

function LocalProjectChatSelector({ workspaceId, busy }: { workspaceId: string; busy: boolean }) {
  const router = useRouter()
  const { chatId, userId } = useChatSurface()
  const { binding, bind } = useProjectChat(workspaceId, userId, chatId)
  const [projectId, setProjectId] = useState('')
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)
  const createdChat = useRef<string | undefined>(undefined)
  const unavailable = !!chatId && binding.isError
  const bound = !unavailable && binding.data?.status === 'PROJECT_BOUND'
  const choose = async () => {
    setSaving(true)
    setFailed(false)
    try {
      let target = chatId ?? createdChat.current
      if (!target) {
        const created = await requestJson(createMothershipChatContract, { body: { workspaceId } })
        target = created.id
        createdChat.current = target
      }
      const value = projectChatRequestSchema.parse({ chat_id: target, project_id: projectId.trim() })
      await bind.mutateAsync(value)
      if (!chatId) router.push(`/workspace/${workspaceId}/chat/${target}`)
    } catch {
      setFailed(true)
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className='flex flex-col gap-1 px-3 pt-3 text-xs' aria-label='Project context'>
      {bound ? <p>Project: {binding.data?.project_id}. Start a new chat to choose another project.</p> : (
        <div className='flex items-center gap-2'>
          <Input aria-label='Project ID' placeholder='Project ID' value={projectId} maxLength={160}
            onChange={(event) => setProjectId(event.target.value)} disabled={saving || busy || unavailable}
            className='h-7 flex-1 text-xs' />
          <Button variant='outline' onClick={choose}
            disabled={!userId || !projectId.trim() || saving || busy || unavailable || (!!chatId && binding.isPending)}>
            {saving ? 'Checking project...' : 'Use project'}
          </Button>
        </div>
      )}
      {(failed || unavailable) && <p role='alert'>Project access could not be verified. Check your access or start a new chat.</p>}
    </div>
  )
}

export function ProjectChatSelector(props: { workspaceId: string; busy: boolean }) {
  const enabled = useFeatureFlag('openrouter-chat')
  return enabled ? <LocalProjectChatSelector {...props} /> : null
}
