'use client'

import { useMemo, useState } from 'react'
import {
  Chip, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuItemLabel,
  DropdownMenuRadioGroup, DropdownMenuSearchInput,
} from '@sim/emcn'
import { Brain, Check, Sparkles, Workflow } from '@sim/emcn/icons'
import { useParams, usePathname, useRouter } from 'next/navigation'
import { ModelSettingTrigger } from '@/app/workspace/[workspaceId]/home/components/user-input/components/model-setting-trigger'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { useOpenRouterChat } from '@/hooks/queries/openrouter-chat'
import type { OpenRouterPreferences } from '@/lib/api/contracts/openrouter-chat'

export function OpenRouterModelSelector() {
  const params = useParams()
  const router = useRouter()
  const pathname = usePathname()
  const workspaceId = typeof params?.workspaceId === 'string' ? params.workspaceId : ''
  const permissions = useUserPermissionsContext()
  const chat = useOpenRouterChat(workspaceId)
  const [search, setSearch] = useState('')
  const choices = chat.models.isError || !chat.configured ? [] : (chat.models.data?.models ?? [])
  const preferences = chat.preferences.data
  const selected = choices.find((model) => model.id === preferences?.model)
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return choices.filter((model) => `${model.id} ${model.name}`.toLowerCase().includes(query))
  }, [choices, search])
  const canSave = permissions.canAdmin && !chat.savePreferences.isPending && !!preferences && !chat.preferences.isError
  const save = (value: OpenRouterPreferences) => chat.savePreferences.mutate(value)
  const label = selected?.name ?? (preferences?.model ? 'Model unavailable' : 'Select OpenRouter model')
  return (
    <div className='flex min-w-0 items-center gap-[inherit]'>
      {chat.status.data?.architecture_context && <Chip shape='round' aria-label='Selected architecture context'
        title={chat.status.data.architecture_context.run_id}
        aria-description={chat.status.data.architecture_context.label}
        leftAdornment={<Workflow className='size-[14px] shrink-0 text-[var(--text-icon)]' />}
        onClick={() => {
          if (pathname === `/workspace/${workspaceId}/architecture`) document.getElementById('architecture-editor-tab')?.click()
          else router.push(`/workspace/${workspaceId}/architecture`)
        }}>
        <span className='@max-[560px]/input-toolbar:hidden text-[var(--text-icon)] text-sm'>Context</span>
      </Chip>}
      <DropdownMenu modal={false}>
        <ModelSettingTrigger label='OpenRouter model' valueLabel={label} icon={Sparkles} showChevron />
        <DropdownMenuContent side='top' align='end' className='max-w-[min(420px,calc(100vw-32px))]'>
          <DropdownMenuSearchInput aria-label='Search OpenRouter models' placeholder='Search models...'
            value={search} onChange={(event) => setSearch(event.target.value)} />
          {!chat.configured && <DropdownMenuItem disabled>Add an OpenRouter key in workspace BYOK settings</DropdownMenuItem>}
          {chat.status.isError && <DropdownMenuItem disabled>OpenRouter gateway unavailable</DropdownMenuItem>}
          {chat.models.isError && <DropdownMenuItem disabled>OpenRouter models unavailable</DropdownMenuItem>}
          {chat.models.isPending && chat.configured && <DropdownMenuItem disabled>Loading models...</DropdownMenuItem>}
          {filtered.map((model) => (
            <DropdownMenuItem key={model.id} disabled={!canSave}
              onSelect={() => preferences && save({ ...preferences, model: model.id, effort: null })}>
              <DropdownMenuItemLabel label={`${model.name} (${model.id})`} />
              {selected?.id === model.id && <Check className='ml-auto! size-[16px]!' />}
            </DropdownMenuItem>
          ))}
          {chat.configured && chat.models.isSuccess && !filtered.length && <DropdownMenuItem disabled>No matching models</DropdownMenuItem>}
          {chat.savePreferences.isError && <DropdownMenuItem disabled>Selection could not be saved</DropdownMenuItem>}
        </DropdownMenuContent>
      </DropdownMenu>
      {!!selected?.reasoning_efforts.length && (
        <DropdownMenu>
          <ModelSettingTrigger label='OpenRouter reasoning effort' valueLabel={preferences?.effort ?? 'Provider default'} icon={Brain} />
          <DropdownMenuContent side='top' align='start'>
            <DropdownMenuRadioGroup aria-label='OpenRouter reasoning effort'>
              <DropdownMenuItem role='menuitemradio' aria-checked={preferences?.effort === null} disabled={!canSave}
                onSelect={() => preferences && save({ ...preferences, effort: null })}>Provider default</DropdownMenuItem>
              {selected.reasoning_efforts.map((effort) => (
                <DropdownMenuItem key={effort} role='menuitemradio' aria-checked={preferences?.effort === effort} disabled={!canSave}
                  onSelect={() => preferences && save({ ...preferences, effort })}>{effort}</DropdownMenuItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}
