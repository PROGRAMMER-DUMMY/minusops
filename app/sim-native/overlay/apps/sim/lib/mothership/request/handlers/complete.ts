import type { StreamHandler } from './types'
import { flushSubagentThinkingBlock, flushThinkingBlock } from './types'

const LOCAL_COMPLETION_ERRORS = new Map<string, string>([
  ["invalid_input", "Invalid native chat request."],
  ["invalid_selection", "Select a supported workspace model first."],
  ["invalid_receipt", "The stream receipt does not match stored output."],
  ["not_found", "The chat execution was not found."],
  ["conflict", "The message or chat is already bound to another request."],
  ["busy", "A chat execution or runtime owner is already active."],
  ["unsafe_path", "Chat storage requires an unlinked local directory."],
  ["storage_unavailable", "Chat storage is unavailable."],
  ["assistant_unavailable", "The workspace assistant is unavailable."],
  ["provider_error", "The provider could not complete the request."],
  ["provider_protocol", "The assistant returned an unsupported event."],
  ["response_limit", "The assistant response exceeded its limit."],
  ["execution_unknown", "The previous execution was interrupted; its outcome is unknown."],
])

export const handleCompleteEvent: StreamHandler = (event, context) => {
  flushSubagentThinkingBlock(context)
  flushThinkingBlock(context)
  if (event.type !== 'complete') {
    context.streamComplete = true
    return
  }

  if (event.payload.usage) {
    context.usage = {
      prompt: (context.usage?.prompt || 0) + (event.payload.usage.input_tokens || 0),
      completion: (context.usage?.completion || 0) + (event.payload.usage.output_tokens || 0),
    }
  }

  if (event.payload.cost) {
    context.cost = {
      input: (context.cost?.input || 0) + (event.payload.cost.input || 0),
      output: (context.cost?.output || 0) + (event.payload.cost.output || 0),
      total: (context.cost?.total || 0) + (event.payload.cost.total || 0),
    }
  }

  if (process.env.MINUS_OPENROUTER_CHAT === 'true' && event.payload.status === 'error') {
    const reason = event.payload.reason
    const message = typeof reason === 'string' ? LOCAL_COMPLETION_ERRORS.get(reason) : undefined
    context.errors.push(message ?? 'The local OpenRouter chat could not complete the request.')
  }

  context.completionStatus = event.payload.status
  context.streamComplete = true
}
