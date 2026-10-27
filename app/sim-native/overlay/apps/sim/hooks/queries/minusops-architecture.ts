import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { architectureActionSchema, architectureReplySchema, architectureWorkspaceSchema,
  type ArchitectureAction } from '@/lib/api/contracts/minusops-architecture'
import { openRouterChatKeys } from '@/hooks/queries/openrouter-chat'

export const architectureKeys = {
  workspace: (workspaceId: string) => ['minusops-architecture', workspaceId] as const,
  view: (workspaceId: string, runId?: string) => [...architectureKeys.workspace(workspaceId), 'view', runId ?? null] as const,
}

export function useArchitectureWorkspace(workspaceId: string, runId?: string) {
  return useQuery({
    queryKey: architectureKeys.view(workspaceId, runId),
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture${runId !== undefined ? `?run_id=${encodeURIComponent(runId)}` : ''}`, {
        credentials: 'same-origin', cache: 'no-store', signal,
      })
      if (!response.ok) throw new Error('Architecture unavailable. Check the local gateway and refresh.')
      return architectureWorkspaceSchema.parse(await response.json())
    },
    enabled: !!workspaceId, staleTime: 30000, retry: false,
  })
}

export function useArchitectureAction(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationKey: architectureKeys.workspace(workspaceId),
    onMutate: () => client.cancelQueries({ queryKey: architectureKeys.workspace(workspaceId) }),
    mutationFn: async (action: ArchitectureAction) => {
      const response = await fetch(`/api/workspaces/${workspaceId}/assistant/architecture`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(architectureActionSchema.parse(action)),
      })
      if (!response.ok) throw new Error('Architecture could not be opened. Refresh and try again.')
      return architectureReplySchema.parse(await response.json())
    },
    onSuccess: async (data) => {
      const workspace = architectureWorkspaceSchema.safeParse(data)
      if (workspace.success) client.setQueryData(architectureKeys.view(workspaceId, workspace.data.run_id ?? undefined), workspace.data)
      await client.invalidateQueries({ queryKey: architectureKeys.workspace(workspaceId) })
      await client.invalidateQueries({ queryKey: openRouterChatKeys.status(workspaceId) })
    },
  })
}
