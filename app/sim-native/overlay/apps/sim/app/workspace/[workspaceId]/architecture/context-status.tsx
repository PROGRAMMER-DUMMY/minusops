'use client'

import { Button, Popover, PopoverContent, PopoverTrigger } from '@sim/emcn'
import { useQuery } from '@tanstack/react-query'
import { requestJson } from '@/lib/api/client/request'
import { openRouterStatusContract } from '@/lib/api/contracts/openrouter-chat'
import { openRouterChatKeys } from '@/hooks/queries/openrouter-chat'

export function ContextStatus({ workspaceId, chatId, revision, changeHash }: { workspaceId: string; chatId: string; revision: string; changeHash?: string }) {
  const status = useQuery({ queryKey: openRouterChatKeys.status(workspaceId, chatId),
    queryFn: ({ signal }) => requestJson(openRouterStatusContract, { params: { id: workspaceId }, query: { chat_id: chatId }, signal }),
    enabled: !!workspaceId && !!chatId, staleTime: 30000, retry: false })
  const value = status.data?.context_observation
  if (!value?.binding.enabled || value.binding.revision !== revision || value.binding.change_hash !== changeHash) return null
  const kb = (size: number) => `${(size / 1024).toFixed(1)} KiB`
  return <div className='shrink-0 px-3 py-1'>
    <Popover><PopoverTrigger asChild><Button variant='ghost' className='h-6 text-xs' aria-label='Context sources and usage'>
      Sources · {value.included}
    </Button></PopoverTrigger>
      <PopoverContent align='start' className='max-h-[60vh] w-[300px] max-w-[90vw] overflow-auto p-3 text-xs'>
        <dl className='mb-3 grid grid-cols-2 gap-2'>
          <dt>Context</dt><dd>{kb(value.context_bytes)} / {kb(value.context_limit_bytes)}</dd>
          <dt>History</dt><dd>{kb(value.history_bytes)} / {kb(value.history_limit_bytes)}</dd>
          <dt title='Byte estimate. Provider instructions and tool rounds are excluded.'>Payload estimate</dt>
          <dd>~{value.estimated_payload_tokens.toLocaleString()} tokens</dd>
          <dt>Provider input</dt><dd>{value.provider_usage?.input_tokens?.toLocaleString() ?? 'Not reported'}</dd>
          <dt>Provider output</dt><dd>{value.provider_usage?.output_tokens?.toLocaleString() ?? 'Not reported'}</dd>
          <dt>Output limit</dt><dd>{value.max_output_tokens_per_completion.toLocaleString()} / completion</dd>
          <dt>Tool usage</dt><dd>Not observed separately</dd>
          <dt>Omitted excerpts</dt><dd>{value.omitted}</dd>
        </dl>
        <ul className='space-y-2'>{value.citations.map(source => <li key={source.id} title={`${source.id} · ${source.sha256}`}>
          <span className='break-all'>{source.path}:{source.start_line}–{source.end_line}</span>
        </li>)}</ul>
        <dl className='mt-3 grid grid-cols-2 gap-2 text-[var(--text-tertiary)]'>
          {Object.entries(value.evidence_status).map(([key, state]) => <div key={key} className='contents'>
            <dt className='capitalize'>{key}</dt><dd>{state.replaceAll('_', ' ')}</dd>
          </div>)}
        </dl>
      </PopoverContent>
    </Popover>
  </div>
}
