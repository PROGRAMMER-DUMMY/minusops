import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import type { OpenRouterModel, OpenRouterPreferences } from '@/lib/api/contracts/openrouter-chat'

interface PublicWorkspaceSelection {
  preferences: OpenRouterPreferences
  models: OpenRouterModel[]
}
interface OpenRouterChatState {
  workspaces: Record<string, PublicWorkspaceSelection>
  setPreferences: (workspaceId: string, value: OpenRouterPreferences) => void
  setModels: (workspaceId: string, models: OpenRouterModel[]) => void
  clear: (workspaceId: string) => void
}
const defaults: OpenRouterPreferences = { model: null, effort: null, max_tokens: 2048 }

// Only public selections enter this store; keys never enter Zustand or browser persistence.
export const useOpenRouterChatStore = create<OpenRouterChatState>()(devtools((set) => ({
  workspaces: {},
  setPreferences: (workspaceId, preferences) => set((state) => ({
    workspaces: { ...state.workspaces, [workspaceId]: {
      models: state.workspaces[workspaceId]?.models ?? [], preferences,
    } },
  })),
  setModels: (workspaceId, models) => set((state) => ({
    workspaces: { ...state.workspaces, [workspaceId]: {
      preferences: state.workspaces[workspaceId]?.preferences ?? defaults, models,
    } },
  })),
  clear: (workspaceId) => set((state) => {
    const workspaces = { ...state.workspaces }
    delete workspaces[workspaceId]
    return { workspaces }
  }),
}), { name: 'openrouter-chat-public' }))
