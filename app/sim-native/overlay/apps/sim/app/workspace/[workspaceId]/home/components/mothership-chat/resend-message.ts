import { isChatContext, UNAVAILABLE_CONTEXT_NOTICE } from '@/lib/mothership/chat/context'
import type { ChatMessage } from '@/app/workspace/[workspaceId]/home/types'
import type { ChatContext } from '@/stores/panel'

export function resendMessagePayload(message: ChatMessage) {
  if (message.role !== 'user' || message.origin === 'task') {
    return { error: 'Only questions you sent can be retried.' } as const
  }
  const files = message.fileAttachments
  if (message.attachments?.some((attachment) => !files?.some((file) => file.id === attachment.id && file.key))) {
    return { error: 'The original attachments are unavailable. Reattach them in the composer before sending.' } as const
  }
  const contexts: ChatContext[] = []
  for (const context of message.contexts ?? []) {
    if (!isChatContext(context)) return { error: UNAVAILABLE_CONTEXT_NOTICE } as const
    contexts.push(context)
  }
  return { files, contexts, options: { requestMode: message.requestMode } }
}
