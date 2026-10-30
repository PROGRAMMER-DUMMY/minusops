import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { requestJson } from '@/lib/api/client/request'
import { bindProjectChatContract, projectChatContract, projectCatalogContract } from '@/lib/api/contracts/enterprise-chat'
import { useOpenRouterChatStore } from '@/stores/openrouter-chat/store'

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
    queryFn: ({signal}) => requestJson(projectCatalogContract, {params: {id: workspaceId}, query: {}, signal}),
    enabled: !!workspaceId && !!userId, staleTime: 0, retry: false,
  })
}
