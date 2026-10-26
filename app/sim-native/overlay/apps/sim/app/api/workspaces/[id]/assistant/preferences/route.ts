import { assistantRoute } from '@/lib/openrouter-chat/server'

export const GET = assistantRoute('preferences', 'GET')
export const POST = assistantRoute('preferences', 'POST')
