import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { architectureWorkspaceSchema } from '@/lib/api/contracts/minusops-architecture'
import { openRouterChatKeys } from '@/hooks/queries/openrouter-chat'

export const architectureKeys = {
  workspace: (workspaceId: string) => ['minusops-architecture', workspaceId] as const,
}

export function useArchitectureWorkspace(workspaceId: string) {
  return useQuery({
    queryKey: architectureKeys.workspace(workspaceId),
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
        credentials: 'same-origin', cache: 'no-store', signal,
      })
      if (!response.ok) throw new Error('Architecture unavailable. Check the local gateway and refresh.')
      return architectureWorkspaceSchema.parse(await response.json())
    },
    enabled: !!workspaceId, staleTime: 30000, retry: false,
  })
}

export function useSelectArchitecture(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationKey: architectureKeys.workspace(workspaceId),
    onMutate: () => client.cancelQueries({ queryKey: architectureKeys.workspace(workspaceId) }),
    mutationFn: async (runId: string) => {
      const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation: 'select', run_id: runId }),
      })
      if (!response.ok) throw new Error('Architecture could not be opened. Refresh and try again.')
      return architectureWorkspaceSchema.parse(await response.json())
    },
    onSuccess: async (data) => {
      client.setQueryData(architectureKeys.workspace(workspaceId), data)
      await client.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
    },
  })
}
