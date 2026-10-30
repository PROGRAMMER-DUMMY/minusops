import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { requestJson } from '@/lib/api/client/request'
import { bindProjectChatContract, projectChatContract } from '@/lib/api/contracts/enterprise-chat'

export const projectChatKeys = {
  chat: (workspaceId: string, userId: string | undefined, chatId: string | undefined) =>
    ['enterprise-project-chat', workspaceId, userId, chatId] as const,
}

export function useProjectChat(workspaceId: string, userId?: string, chatId?: string) {
  const queryClient = useQueryClient()
  const key = projectChatKeys.chat(workspaceId, userId, chatId)
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
    onSuccess: (value) => queryClient.setQueryData(projectChatKeys.chat(workspaceId, userId, value.chat_id), value),
  })
  return { binding, bind }
}
