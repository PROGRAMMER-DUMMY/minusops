import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { requestJson } from '@/lib/api/client/request'
import {
  deleteOpenRouterKeyContract, openRouterModelsContract, openRouterPreferencesContract,
  openRouterStatusContract, saveOpenRouterKeyContract, saveOpenRouterPreferencesContract,
  type OpenRouterPreferences,
} from '@/lib/api/contracts/openrouter-chat'
import { useOpenRouterChatStore } from '@/stores/openrouter-chat/store'

export const OPENROUTER_CHAT_STALE_TIME = 30000
export const openRouterChatKeys = {
  all: ['openrouter-chat'] as const,
  workspace: (id: string) => [...openRouterChatKeys.all, id] as const,
  status: (id: string) => [...openRouterChatKeys.workspace(id), 'status'] as const,
  models: (id: string) => [...openRouterChatKeys.workspace(id), 'models'] as const,
  preferences: (id: string) => [...openRouterChatKeys.workspace(id), 'preferences'] as const,
}

export function useOpenRouterChat(workspaceId: string) {
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: openRouterChatKeys.status(workspaceId),
    queryFn: ({ signal }) => requestJson(openRouterStatusContract, { params: { id: workspaceId }, query: {}, signal }),
    enabled: !!workspaceId, staleTime: OPENROUTER_CHAT_STALE_TIME, retry: false,
  })
  const preferences = useQuery({
    queryKey: openRouterChatKeys.preferences(workspaceId),
    queryFn: ({ signal }) => requestJson(openRouterPreferencesContract, { params: { id: workspaceId }, query: {}, signal }),
    enabled: !!workspaceId, staleTime: OPENROUTER_CHAT_STALE_TIME, retry: false,
  })
  const configured = status.data?.providers.openrouter.configured === true
  const models = useQuery({
    queryKey: openRouterChatKeys.models(workspaceId),
    queryFn: ({ signal }) => requestJson(openRouterModelsContract, { params: { id: workspaceId }, query: {}, signal }),
    enabled: !!workspaceId && configured, staleTime: OPENROUTER_CHAT_STALE_TIME, retry: false,
  })
  const setPreferences = useOpenRouterChatStore((state) => state.setPreferences)
  const setModels = useOpenRouterChatStore((state) => state.setModels)
  useEffect(() => {
    if (preferences.data) setPreferences(workspaceId, preferences.data)
  }, [workspaceId, preferences.data, setPreferences])
  useEffect(() => {
    setModels(workspaceId, configured && !models.isError ? (models.data?.models ?? []) : [])
  }, [workspaceId, configured, models.data, models.isError, setModels])

  const savePreferences = useMutation({
    mutationFn: (value: OpenRouterPreferences) => requestJson(saveOpenRouterPreferencesContract,
      { params: { id: workspaceId }, query: {}, body: value }),
    onSuccess: (value) => {
      queryClient.setQueryData(openRouterChatKeys.preferences(workspaceId), value)
      setPreferences(workspaceId, value)
    },
  })
  const refresh = async () => {
    queryClient.removeQueries({ queryKey: openRouterChatKeys.models(workspaceId) })
    setModels(workspaceId, [])
    await queryClient.invalidateQueries({ queryKey: openRouterChatKeys.workspace(workspaceId) })
  }
  return { status, preferences, models, configured, savePreferences, refresh }
}

// Credentials are transient arguments, never React Query mutation-cache variables.
export async function saveOpenRouterChatKey(workspaceId: string, key: string) {
  try {
    await requestJson(saveOpenRouterKeyContract, { params: { id: workspaceId }, query: {}, body: { key } })
  } catch { throw new Error('Unable to save the OpenRouter key.') }
}
export async function deleteOpenRouterChatKey(workspaceId: string) {
  try {
    await requestJson(deleteOpenRouterKeyContract, { params: { id: workspaceId }, query: {} })
  } catch { throw new Error('Unable to delete the OpenRouter key.') }
}
