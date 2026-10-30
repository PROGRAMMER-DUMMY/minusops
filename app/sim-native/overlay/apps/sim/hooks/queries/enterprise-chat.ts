import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { requestJson } from '@/lib/api/client/request'
import { bindProjectChatContract, projectChatContract, projectCatalogContract } from '@/lib/api/contracts/enterprise-chat'
import { useOpenRouterChatStore } from '@/stores/openrouter-chat/store'
import { projectSetupContract, updateProjectSetupContract, type ProjectSetupAction } from '@/lib/api/contracts/project-setup'
import { listOAuthCredentialsContract } from '@/lib/api/contracts/credentials'

export const projectChatKeys = {
  chat: (workspaceId: string, userId: string | undefined, chatId: string | undefined, model?: string | null) =>
    ['enterprise-project-chat', workspaceId, userId, chatId, model] as const,
}

export function useProjectChat(workspaceId: string, userId?: string, chatId?: string) {
  const queryClient = useQueryClient()
  const model = useOpenRouterChatStore(state => state.workspaces[workspaceId]?.preferences.model)
  const key = projectChatKeys.chat(workspaceId, userId, chatId, model)
  const binding = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => requestJson(projectChatContract,
      { params: { id: workspaceId }, query: { chat_id: chatId! }, signal }),
    enabled: !!workspaceId && !!userId && !!chatId,
    staleTime: 0, retry: false,
  })
  const bind = useMutation({
    mutationFn: (value: { chat_id: string; project_id: string }) => requestJson(bindProjectChatContract,
      { params: { id: workspaceId }, query: {}, body: value }),
    retry: false,
    onSuccess: (value) => queryClient.setQueryData(projectChatKeys.chat(workspaceId, userId, value.chat_id, model), value),
  })
  return { binding, bind }
}

export function useProjectCatalog(workspaceId: string, userId?: string) {
  const model = useOpenRouterChatStore(state => state.workspaces[workspaceId]?.preferences.model)
  return useQuery({
    queryKey: ['enterprise-project-catalog', workspaceId, userId, model],
    queryFn: async ({signal}) => {
      const value = await requestJson(projectCatalogContract, {params: {id: workspaceId}, query: {}, signal})
      if (value.workspace_id !== workspaceId || value.user_id !== userId) throw new Error('Project session changed')
      return value
    },
    enabled: !!workspaceId && !!userId, staleTime: 0, retry: false,
  })
}

export function useProjectSetup(workspaceId: string, userId?: string) {
  const client = useQueryClient()
  const key = ['project-setup', workspaceId, userId] as const
  const setup = useQuery({queryKey: key, enabled: !!userId,
    queryFn: async ({signal}) => {
      const value = await requestJson(projectSetupContract, {params: {id: workspaceId}, query: {}, signal})
      if (value.workspace_id !== workspaceId || value.user_id !== userId) throw new Error('Project session changed')
      return value
    },
    staleTime: 0, retry: false,
  })
  const update = useMutation({
    mutationFn: async (body: ProjectSetupAction) => {
      const value = await requestJson(updateProjectSetupContract, {params: {id: workspaceId}, query: {}, body})
      if (value.workspace_id !== workspaceId || value.user_id !== userId) throw new Error('Project session changed')
      return value
    }, retry: false,
    onSuccess: async value => {
      client.setQueryData(key, value)
      await client.invalidateQueries({queryKey: ['enterprise-project-catalog', workspaceId, userId]})
    },
  })
  return {setup, update}
}

export function useProjectConnections(workspaceId: string, userId?: string, enabled = true) {
  return useQuery({queryKey: ['project-connections', workspaceId, userId], enabled: !!userId && enabled,
    queryFn: ({signal}) => requestJson(listOAuthCredentialsContract,
      {query: {provider: 'confluence', workspaceId}, signal}), staleTime: 0, retry: false,
  })
}
