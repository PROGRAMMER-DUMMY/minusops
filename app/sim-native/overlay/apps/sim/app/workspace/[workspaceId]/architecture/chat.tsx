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
import { openRouterChatKeys } from '@/hooks/queries/openrouter-chat'
import { aiProposalSchema, architectureReplySchema, type ArchitectureSubject, type ProposalReference } from '@/lib/api/contracts/minusops-architecture'
import { architectureKeys } from '@/hooks/queries/minusops-architecture'
import { ContextStatus } from './context-status'

type Binding = { revision: string; subject: ArchitectureSubject | null; enabled: boolean; draft_revision?: number; change_hash?: string }

export function ArchitectureChat({ workspaceId, runId, chatIds = [], binding, onBusyChange, onProposal }: {
  workspaceId: string; runId: string; chatIds?: string[]; binding: Binding; onBusyChange: (busy: boolean) => void
  onProposal?: (reference: ProposalReference) => void
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
        disabled={busy} options={(history.data ?? []).filter(item => chatIds.includes(item.id)).map((item) => ({ label: item.name, value: item.id }))}
        onChange={(id) => { setError(''); void setSelection({ architectureChat: id, architectureChatRun: runId, architectureChatWorkspace: workspaceId }) }} />
      <Button variant='ghost' className='p-0' aria-label='New architecture chat' disabled={!permissions.canAdmin || busy} onClick={() => void newChat()}>
        <Plus className='size-[14px]' />
      </Button>
    </div>
    {error && <p className='px-3 py-2 text-sm' role='alert'>{error}</p>}
    {chatId ? <Conversation key={`${workspaceId}:${runId}:${chatId}`} workspaceId={workspaceId} chatId={chatId} runId={runId} binding={binding} onBusyChange={setSending} onProposal={onProposal} />
      : <div className='flex flex-1 items-center justify-center p-4'>
        <Button variant='ghost' disabled={!permissions.canAdmin || busy} onClick={() => void newChat()}>
          {creating ? 'Opening chat…' : 'Start a chat for this run'}
        </Button>
      </div>}
  </div>
}

function Conversation({ workspaceId, chatId, runId, binding, onBusyChange, onProposal }: {
  workspaceId: string; chatId: string; runId: string; binding: Binding; onBusyChange: (busy: boolean) => void
  onProposal?: (reference: ProposalReference) => void
}) {
  const session = useSession()
  const permissions = useUserPermissionsContext()
  const client = useQueryClient()
  const chat = useChat(workspaceId, chatId, { ...getWorkflowCopilotUseChatOptions(),
    onStreamEnd: () => { void client.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId, chatId) }) },
    onToolResult: (name, success, result) => {
    if (name !== 'propose_source_edits' || !success) return
    const proposal = aiProposalSchema.safeParse(result)
    if (proposal.success && proposal.data.run_id === runId && proposal.data.revision === binding.revision) {
      onProposal?.({ chat_id: chatId, message_id: proposal.data.message_id, tool_call_id: proposal.data.tool_call_id })
    }
  } })
  const signature = JSON.stringify({ runId, ...binding })
  const [attached, setAttached] = useState('')
  const [attaching, setAttaching] = useState(false)
  const [attachmentError, setAttachmentError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const ready = !permissions.canAdmin || attached === signature
  useEffect(() => {
    if (!permissions.canAdmin || chat.isSending || chat.isReconnecting) return
    const controller = new AbortController()
    setAttaching(true); setAttachmentError('')
    void fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
      method: 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation: 'bind_chat', run_id: runId, chat_id: chatId, ...binding }),
    }).then(async (response) => {
      if (!response.ok) throw new Error('Context unavailable')
      const value = architectureReplySchema.parse(await response.json())
      if (!('status' in value) || value.status !== 'CONTEXT_BOUND') throw new Error('Context unavailable')
      setAttached(signature)
      await client.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId, chatId) })
      await client.invalidateQueries({ queryKey: architectureKeys.view(workspaceId, runId) })
    }).catch(() => {
      if (!controller.signal.aborted) setAttachmentError('Context unavailable. Refresh or start a new chat.')
    }).finally(() => { if (!controller.signal.aborted) setAttaching(false) })
    return () => controller.abort()
  }, [workspaceId, chatId, runId, signature, attempt, permissions.canAdmin, client, chat.isSending, chat.isReconnecting])
  useEffect(() => {
    onBusyChange(chat.isSending || chat.isReconnecting || attaching)
    return () => onBusyChange(false)
  }, [chat.isSending, chat.isReconnecting, attaching, onBusyChange])
  const submit: typeof chat.sendMessage = async (...args) => {
    if (!ready) { setAttachmentError('Context unavailable. Refresh or start a new chat.'); return }
    await chat.sendMessage(...args)
  }
  return <>
    <ContextStatus workspaceId={workspaceId} chatId={chatId} revision={binding.revision} changeHash={binding.change_hash} />
    {attachmentError && <div className='px-3 py-2 text-sm' role='alert'>{attachmentError}
      <Button variant='ghost' disabled={attaching} onClick={() => setAttempt((value) => value + 1)}>Reconnect context</Button></div>}
    {attaching && <p className='px-3 py-1 text-xs text-[var(--text-muted)]' role='status'>Connecting context…</p>}
    <div className='flex min-h-0 flex-1 flex-col' inert={!ready && !chat.isSending && !chat.isReconnecting}>
  <MothershipChat className='min-h-0 flex-1' workspaceId={workspaceId}
    messages={chat.messages} isSending={chat.isSending} isReconnecting={chat.isReconnecting}
    isLoading={chat.isChatHistoryPending} onSubmit={submit} onResend={submit} onStopGeneration={chat.stopGeneration}
    messageQueue={chat.messageQueue} editingQueuedId={chat.editingQueuedId} dispatchingHeadId={chat.dispatchingHeadId}
    onRemoveQueuedMessage={chat.removeFromQueue} onSendQueuedMessage={chat.sendNow}
    onEditQueuedMessage={chat.editQueuedMessage} onCancelQueueEdit={chat.cancelQueueEdit}
    userId={session.data?.user?.id} chatId={chatId} draftScopeKey={`${workspaceId}:architecture:${runId}:${chatId}`}
    layout='copilot-view' />
    </div>
  </>
}
