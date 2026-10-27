'use client'

import { useEffect, useState } from 'react'
import { Button, ChipSelect } from '@sim/emcn'
import { Plus } from '@sim/emcn/icons'
import { useQueryClient } from '@tanstack/react-query'
import { parseAsString, useQueryStates } from 'nuqs'
import { z } from 'zod'
import { requestJson } from '@/lib/api/client/request'
import { createMothershipChatContract } from '@/lib/api/contracts/mothership-chats'
import { useSession } from '@/lib/auth/client'
import { MothershipChat } from '@/app/workspace/[workspaceId]/home/components/mothership-chat'
import { getWorkflowCopilotUseChatOptions, useChat } from '@/app/workspace/[workspaceId]/home/hooks'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { mothershipChatKeys, useMothershipChats } from '@/hooks/queries/mothership-chats'

export function ArchitectureChat({ workspaceId, runId, onBusyChange }: {
  workspaceId: string; runId: string; onBusyChange: (busy: boolean) => void
}) {
  const permissions = useUserPermissionsContext()
  const queryClient = useQueryClient()
  const history = useMothershipChats(workspaceId)
  const [selection, setSelection] = useQueryStates({ architectureChat: parseAsString, architectureChatRun: parseAsString,
    architectureChatWorkspace: parseAsString }, { history: 'replace' })
  const parsed = z.string().uuid().safeParse(selection.architectureChat)
  const chatId = selection.architectureChatWorkspace === workspaceId && selection.architectureChatRun === runId && parsed.success ? parsed.data : undefined
  const [creating, setCreating] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const busy = creating || sending
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])

  async function newChat() {
    setCreating(true); setError('')
    try {
      // An existing native chat id keeps the native send hook on this canvas route.
      const created = await requestJson(createMothershipChatContract, { body: { workspaceId, mode: 'agent' } })
      await setSelection({ architectureChat: z.string().uuid().parse(created.id), architectureChatRun: runId, architectureChatWorkspace: workspaceId })
      await queryClient.invalidateQueries({ queryKey: mothershipChatKeys.list(workspaceId) })
    } catch { setError('Chat could not be opened. Try again.') }
    finally { setCreating(false) }
  }

  return <div className='flex h-full min-h-0 flex-col' aria-label='Architecture chat'>
    <div className='flex shrink-0 items-center justify-between gap-2 border-[var(--border)] border-y bg-[var(--surface-4)] px-3 py-1.5'>
      <ChipSelect aria-label='Chat history' searchable placeholder='New chat' value={chatId ?? ''}
        disabled={busy} options={(history.data ?? []).map((item) => ({ label: item.name, value: item.id }))}
        onChange={(id) => { setError(''); void setSelection({ architectureChat: id, architectureChatRun: runId, architectureChatWorkspace: workspaceId }) }} />
      <Button variant='ghost' className='p-0' aria-label='New architecture chat' disabled={!permissions.canAdmin || busy} onClick={() => void newChat()}>
        <Plus className='size-[14px]' />
      </Button>
    </div>
    {error && <p className='px-3 py-2 text-sm' role='alert'>{error}</p>}
    {chatId ? <Conversation key={`${workspaceId}:${runId}:${chatId}`} workspaceId={workspaceId} chatId={chatId} runId={runId} onBusyChange={setSending} />
      : <div className='flex flex-1 items-center justify-center p-4'>
        <Button variant='ghost' disabled={!permissions.canAdmin || busy} onClick={() => void newChat()}>
          {creating ? 'Opening chat…' : 'Start a chat for this run'}
        </Button>
      </div>}
  </div>
}

function Conversation({ workspaceId, chatId, runId, onBusyChange }: {
  workspaceId: string; chatId: string; runId: string; onBusyChange: (busy: boolean) => void
}) {
  const session = useSession()
  const chat = useChat(workspaceId, chatId, getWorkflowCopilotUseChatOptions())
  useEffect(() => {
    onBusyChange(chat.isSending || chat.isReconnecting)
    return () => onBusyChange(false)
  }, [chat.isSending, chat.isReconnecting, onBusyChange])
  return <MothershipChat className='min-h-0 flex-1' workspaceId={workspaceId}
    messages={chat.messages} isSending={chat.isSending} isReconnecting={chat.isReconnecting}
    isLoading={chat.isChatHistoryPending} onSubmit={chat.sendMessage} onStopGeneration={chat.stopGeneration}
    messageQueue={chat.messageQueue} editingQueuedId={chat.editingQueuedId} dispatchingHeadId={chat.dispatchingHeadId}
    onRemoveQueuedMessage={chat.removeFromQueue} onSendQueuedMessage={chat.sendNow}
    onEditQueuedMessage={chat.editQueuedMessage} onCancelQueueEdit={chat.cancelQueueEdit}
    userId={session.data?.user?.id} chatId={chatId} draftScopeKey={`${workspaceId}:architecture:${runId}:${chatId}`}
    layout='copilot-view' />
}
