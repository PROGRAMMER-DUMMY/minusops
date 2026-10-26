'use client'

import { useState } from 'react'
import { OpenRouterIcon } from '@/components/icons'
import { BYOKKeyManager } from '@/app/workspace/[workspaceId]/settings/components/byok/byok-key-manager'
import { SettingsEmptyState } from '@/app/workspace/[workspaceId]/settings/components/settings-empty-state'
import { SettingsSection } from '@/app/workspace/[workspaceId]/settings/components/settings-section/settings-section'
import {
  deleteOpenRouterChatKey, saveOpenRouterChatKey, useOpenRouterChat,
} from '@/hooks/queries/openrouter-chat'

interface OpenRouterChatSettingsProps { workspaceId: string; canManage: boolean }
const providers = [{ id: 'openrouter', name: 'OpenRouter', icon: OpenRouterIcon,
  description: 'Local OpenRouter Chat. No workflow actions yet.', placeholder: 'Enter your OpenRouter API key' }]

export function OpenRouterChatSettings({ workspaceId, canManage }: OpenRouterChatSettingsProps) {
  const chat = useOpenRouterChat(workspaceId)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const manage = canManage && !chat.status.isError && !chat.status.isPending
  const keyAction = async (key?: string) => {
    setBusy(true)
    setError(null)
    try {
      if (key === undefined) await deleteOpenRouterChatKey(workspaceId)
      else await saveOpenRouterChatKey(workspaceId, key)
      await chat.refresh()
    } catch {
      setError('OpenRouter key settings could not be updated.')
      throw new Error('OpenRouter key settings could not be updated.')
    } finally { setBusy(false) }
  }
  return (
    <SettingsSection label='OpenRouter Chat'>
      {chat.status.isError && <SettingsEmptyState variant='inline'>OpenRouter gateway unavailable.</SettingsEmptyState>}
      {error && <SettingsEmptyState variant='inline'>{error}</SettingsEmptyState>}
      <BYOKKeyManager
        providers={providers} configuredProviderIds={new Set(chat.configured ? ['openrouter'] : [])}
        isLoading={chat.status.isPending} isSaving={busy} isDeleting={busy} showSearch={false}
        capabilities={{ add: manage, update: manage, delete: manage }}
        keyUsageDescription='This key is kept in the host operating system credential vault for local OpenRouter Chat. No workflow actions yet.'
        lastKeyDeleteMessage='The vault key will be removed. Local OpenRouter Chat requires a saved vault key.'
        onSave={(_provider, key) => keyAction(key)} onDelete={() => keyAction()}
      />
    </SettingsSection>
  )
}
