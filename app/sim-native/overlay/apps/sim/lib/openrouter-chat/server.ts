import 'server-only'
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { db } from '@sim/db'
import { copilotChats } from '@sim/db/schema'
import { and, eq, isNull, sql } from 'drizzle-orm'
import {
  openRouterKeySchema, openRouterModelsSchema, openRouterPreferencesSchema,
  openRouterScopeSchema, openRouterStatusSchema, openRouterStatusQuerySchema,
} from '@/lib/api/contracts/openrouter-chat'
import { getSession } from '@/lib/auth'
import { withRouteHandler } from '@/lib/core/utils/with-route-handler'
import { getUserEntityPermissions } from '@/lib/workspaces/permissions/utils'
import { architectureActionSchema, architectureReplySchema, architectureQuerySchema } from '@/lib/api/contracts/minusops-architecture'
import { enterpriseContextRequestSchema, enterpriseContextReplySchema } from '@/lib/api/contracts/enterprise-context'
import { projectChatQuerySchema, projectChatRequestSchema, projectChatReplySchema, projectCatalogReplySchema } from '@/lib/api/contracts/enterprise-chat'

const GATEWAY = 'http://host.docker.internal:5180'
const LOCAL_GATEWAY = 'http://127.0.0.1:5180'
const MAX_BODY = 16384
const MAX_RESPONSE = 4194304
const HEADERS = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }
type Action = 'status' | 'key' | 'models' | 'preferences' | 'architecture' | 'project-context' | 'project-chat' | 'projects'
type Method = 'GET' | 'POST' | 'DELETE'
interface RouteContext { params: Promise<{ id: string }> }
class BodyLimit extends Error {}

function refused(status: number, code: string) {
  return NextResponse.json({ error: 'OpenRouter settings are unavailable or the request was refused.', code },
    { status, headers: HEADERS })
}

function browserOrigin(request: NextRequest) {
  const configured = process.env.NEXT_PUBLIC_APP_URL
  if (configured === undefined) return new URL(request.url).origin
  const url = new URL(configured)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || url.pathname !== '/' || url.search || url.hash || configured !== configured.trim()) {
    throw new Error('Assistant public origin unavailable')
  }
  return url.origin
}

async function boundedBody(stream: ReadableStream<Uint8Array> | null, maximum: number) {
  if (!stream) return ''
  const reader = stream.getReader()
  const parts: Uint8Array[] = []
  let size = 0
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    void reader.cancel().catch(() => {})
  }, 30000)
  try {
    while (true) {
      const next = await reader.read()
      if (next.done) break
      size += next.value.length
      if (size > maximum) throw new BodyLimit('Assistant byte limit')
      parts.push(next.value)
    }
    if (timedOut) throw new BodyLimit('Assistant read timeout')
  } finally {
    clearTimeout(timer)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
  const bytes = new Uint8Array(size)
  let offset = 0
  for (const part of parts) { bytes.set(part, offset); offset += part.length }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
}

function strictJson(raw: string): unknown {
  // Tokenize strings as units so escaped keys and nested duplicate members are refused.
  const tokens = raw.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g) ?? []
  const stack: Array<Set<string> | null> = []
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]
    if (token === '{' || token === '[') {
      stack.push(token === '{' ? new Set<string>() : null)
      if (stack.length > 12) throw new Error('Assistant depth limit')
    } else if (token === '}' || token === ']') stack.pop()
    else if (token.startsWith('"') && tokens[i + 1] === ':') {
      const keys = stack[stack.length - 1]
      const key: unknown = JSON.parse(token)
      if (!keys || typeof key !== 'string' || keys.has(key)) throw new Error('Duplicate assistant member')
      keys.add(key)
    }
  }
  return JSON.parse(raw)
}

async function ownsChat(workspaceId: string, userId: string, chatId: string) {
  const rows = await db.select({ id: copilotChats.id }).from(copilotChats).where(and(
    eq(copilotChats.id, chatId), eq(copilotChats.workspaceId, workspaceId), eq(copilotChats.userId, userId),
    isNull(copilotChats.deletedAt))).limit(1)
  return rows.length === 1
}

async function projectMarker(workspaceId: string, userId: string, chatId: string) {
  const [row] = await db.select({ config: copilotChats.config }).from(copilotChats).where(and(
    eq(copilotChats.id, chatId), eq(copilotChats.workspaceId, workspaceId), eq(copilotChats.userId, userId),
    isNull(copilotChats.deletedAt))).limit(1)
  const config = row?.config as Record<string, unknown> | null
  return config?.enterpriseProjectId
}

async function gateway(workspaceId: string, action: Action, method: Method, body?: unknown, query = '') {
  const origin = process.env.MINUS_ASSISTANT_GATEWAY_URL ?? GATEWAY
  const token = process.env.MINUS_ASSISTANT_GATEWAY_TOKEN ?? ''
  if (![GATEWAY, LOCAL_GATEWAY].includes(origin) || !/^[!-~]{16,512}$/.test(token)) throw new Error('Assistant gateway unavailable')
  const response = await fetch(`${origin}/workspaces/${workspaceId}/${action}${query}`, {
    method, redirect: 'error', cache: 'no-store', credentials: 'omit',
    signal: AbortSignal.timeout(30000),
    headers: { 'X-Minus-Bridge-Token': token, 'Content-Type': 'application/json', Accept: 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    await response.body?.cancel().catch(() => {})
    return { ok: false as const, status: ['project-context', 'project-chat'].includes(action) && [400, 403, 409].includes(response.status)
      ? response.status : response.status === 400 ? 400 : 502 }
  }
  if (action === 'key') {
    await response.body?.cancel().catch(() => {})
    return { ok: true as const, value: { success: true } }
  }
  if (response.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
    await response.body?.cancel().catch(() => {})
    throw new Error('Assistant response type')
  }
  const raw = await boundedBody(response.body, MAX_RESPONSE)
  if (raw.includes(token)) throw new Error('Assistant response refused')
  const schema = action === 'status' ? openRouterStatusSchema
    : action === 'models' ? openRouterModelsSchema
      : action === 'architecture' ? architectureReplySchema
        : action === 'project-context' ? enterpriseContextReplySchema
          : action === 'project-chat' ? projectChatReplySchema
            : action === 'projects' ? projectCatalogReplySchema : openRouterPreferencesSchema
  const value = schema.parse(strictJson(raw))
  if (JSON.stringify(value).includes(token)) throw new Error('Assistant response refused')
  return { ok: true as const, value }
}

function checkedProjectChat(value: unknown, workspaceId: string, chatId: string) {
  const parsed = projectChatReplySchema.parse(value)
  if (parsed.chat_id !== chatId || (parsed.selection && parsed.selection.workspace_id !== workspaceId)) {
    throw new Error('Project chat binding mismatch')
  }
  return parsed
}

/** Recheck current source and grant revisions before exposing any local Chat transcript. */
export async function requireProjectChatAccess(chatId: string, userId: string, workspaceId: string) {
  const marker = await projectMarker(workspaceId, userId, chatId)
  if (process.env.MINUS_OPENROUTER_CHAT !== 'true') {
    if (marker !== undefined) throw new Error('Project chat access refused')
    return null
  }
  projectChatQuerySchema.parse({ chat_id: chatId })
  openRouterScopeSchema.parse({ id: workspaceId })
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(userId)
      || !await ownsChat(workspaceId, userId, chatId)
      || !await getUserEntityPermissions(userId, 'workspace', workspaceId)) {
    throw new Error('Project chat access refused')
  }
  const reply = await gateway(workspaceId, 'project-chat', 'GET', undefined,
    '?' + new URLSearchParams({ chat_id: chatId, user_id: userId }))
  if (!reply.ok) throw new Error('Project chat access refused')
  const parsed = checkedProjectChat(reply.value, workspaceId, chatId)
  if (marker !== undefined && (parsed.status !== 'PROJECT_BOUND' || parsed.project_id !== marker)) {
    throw new Error('Project chat binding unavailable')
  }
  if (!await ownsChat(workspaceId, userId, chatId)
      || !await getUserEntityPermissions(userId, 'workspace', workspaceId)) {
    throw new Error('Project chat access refused')
  }
  return parsed
}

export function assistantRoute(action: Action, method: Method) {
  return withRouteHandler(async (request: NextRequest, context: RouteContext) => {
    try {
      if (process.env.MINUS_OPENROUTER_CHAT !== 'true') return refused(404, 'disabled')
      const session = await getSession()
      if (!session?.user?.id) return refused(401, 'unauthorized')
      const parsedScope = openRouterScopeSchema.safeParse(await context.params)
      const url = new URL(request.url)
      const entries = [...url.searchParams.entries()]
      if (!parsedScope.success || new Set(entries.map(([key]) => key)).size !== entries.length) return refused(400, 'invalid_request')
      const workspaceId = parsedScope.data.id
      const permission = await getUserEntityPermissions(session.user.id, 'workspace', workspaceId)
      if (!permission) return refused(403, 'forbidden')
      let query = ''
      if (method === 'GET' && action === 'architecture') {
        const parsed = architectureQuerySchema.safeParse(Object.fromEntries(entries))
        if (!parsed.success) return refused(400, 'invalid_request')
        query = '?' + new URLSearchParams({ ...(parsed.data.run_id ? { run_id: parsed.data.run_id } : {}), user_id: session.user.id })
      } else if (method === 'GET' && action === 'projects') {
        if (entries.length) return refused(400, 'invalid_request')
        query = '?' + new URLSearchParams({user_id: session.user.id})
      } else if (method === 'GET' && action === 'project-chat') {
        const parsed = projectChatQuerySchema.safeParse(Object.fromEntries(entries))
        if (!parsed.success) return refused(400, 'invalid_request')
        if (!await ownsChat(workspaceId, session.user.id, parsed.data.chat_id)) return refused(403, 'forbidden')
        query = '?' + new URLSearchParams({ chat_id: parsed.data.chat_id, user_id: session.user.id })
      } else if (method === 'GET' && action === 'status') {
        const parsed = openRouterStatusQuerySchema.safeParse(Object.fromEntries(entries))
        if (!parsed.success) return refused(400, 'invalid_request')
        if (parsed.data.chat_id) {
          if (!await ownsChat(workspaceId, session.user.id, parsed.data.chat_id)) return refused(403, 'forbidden')
          query = '?' + new URLSearchParams({ chat_id: parsed.data.chat_id, user_id: session.user.id })
        }
      } else if (entries.length) return refused(400, 'invalid_request')
      if (request.method !== method) return refused(405, 'invalid_method')
      if (method !== 'GET') {
        if (permission !== 'admin' && action !== 'architecture'
            && !(['project-context', 'project-chat'].includes(action) && method === 'POST')) return refused(403, 'forbidden')
        if (request.headers.get('origin') !== browserOrigin(request)
            || ['cross-site', 'same-site'].includes(request.headers.get('sec-fetch-site') ?? '')) {
          return refused(403, 'origin_refused')
        }
      }
      let body: unknown
      if (method !== 'GET') {
        try {
          const raw = await boundedBody(request.body, action === 'architecture' ? 1048576 : MAX_BODY)
          if (method === 'DELETE') {
            if (raw.trim()) return refused(400, 'invalid_request')
          } else {
            if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
              return refused(415, 'invalid_request')
            }
            const input = action === 'key' ? openRouterKeySchema
              : action === 'architecture' ? architectureActionSchema
                : action === 'project-context' ? enterpriseContextRequestSchema
                  : action === 'project-chat' ? projectChatRequestSchema : openRouterPreferencesSchema
            const parsed = input.safeParse(strictJson(raw))
            if (!parsed.success) return refused(400, 'invalid_request')
            body = parsed.data
          }
        } catch (error) {
          return refused(error instanceof BodyLimit ? 413 : 400, 'invalid_request')
        }
      }
      if (method !== 'GET' && permission !== 'admin' && !['project-context', 'project-chat'].includes(action)) {
        const inspection = action === 'architecture' && architectureActionSchema.safeParse(body)
        if (!inspection || !inspection.success || !['plan', 'delivery', 'export', 'draft', 'catalog', 'presence', 'presentation', 'clipboard', 'host_status', 'host_change'].includes(inspection.data.operation)) {
          return refused(403, 'forbidden')
        }
      }
      if (action === 'project-context') {
        const selected = enterpriseContextRequestSchema.parse(body)
        if (selected.workspace_id !== workspaceId) return refused(400, 'invalid_request')
        body = { ...selected, user_id: session.user.id }
      }
      if (action === 'project-chat' && method === 'POST') {
        const selected = projectChatRequestSchema.parse(body)
        if (!await ownsChat(workspaceId, session.user.id, selected.chat_id)) return refused(403, 'forbidden')
        const marker = await projectMarker(workspaceId, session.user.id, selected.chat_id)
        if (marker !== undefined && marker !== selected.project_id) return refused(409, 'context_mismatch')
        // Mark before binding: a bridge failure must not relabel source-bearing history as plain Chat.
        await db.update(copilotChats).set({
          config: sql`COALESCE(${copilotChats.config}, '{}'::jsonb) || jsonb_build_object('enterpriseProjectId', ${selected.project_id}::text)`,
        }).where(and(eq(copilotChats.id, selected.chat_id), eq(copilotChats.workspaceId, workspaceId),
          eq(copilotChats.userId, session.user.id), isNull(copilotChats.deletedAt)))
        body = { ...selected, user_id: session.user.id }
      }
      if (action === 'architecture' && method === 'POST') {
        const parsed = architectureActionSchema.parse(body)
        if (parsed.operation === 'bind_chat' || parsed.operation === 'proposal') {
          if (!await ownsChat(workspaceId, session.user.id, parsed.chat_id)) return refused(403, 'forbidden')
          body = { ...parsed, user_id: session.user.id }
        }
        if (parsed.operation === 'command' || parsed.operation === 'draft' || parsed.operation === 'catalog' || parsed.operation === 'presence' || parsed.operation === 'clipboard') body = { ...parsed, user_id: session.user.id }
        if (parsed.operation === 'layout' && parsed.draft_revision !== undefined) body = { ...parsed, user_id: session.user.id }
      }
      if (action === 'preferences' && method === 'POST') {
        const preferences = openRouterPreferencesSchema.parse(body)
        if (preferences.model !== null) {
          const reply = await gateway(workspaceId, 'models', 'GET')
          if (!reply.ok) return refused(reply.status, 'models_unavailable')
          const catalog = openRouterModelsSchema.parse(reply.value)
          const selected = catalog.models.find((item) => item.id === preferences.model)
          if (!selected || (preferences.effort !== null && !selected.reasoning_efforts.includes(preferences.effort))
              || (selected.max_output_tokens !== null && preferences.max_tokens > selected.max_output_tokens)) {
            return refused(400, 'selection_unavailable')
          }
        }
      }
      const reply = await gateway(workspaceId, action, method, body, query)
      if (!reply.ok) return refused(reply.status, 'assistant_refused')
      if (action === 'projects') {
        const value = projectCatalogReplySchema.parse(reply.value)
        if (value.workspace_id !== workspaceId || value.user_id !== session.user.id) return refused(502, 'context_mismatch')
        const current = await getSession()
        if (current?.user?.id !== session.user.id
            || !await getUserEntityPermissions(session.user.id, 'workspace', workspaceId)) return refused(403, 'forbidden')
        return NextResponse.json(value, {headers: HEADERS})
      }
      if (action === 'project-chat') {
        const chatId = method === 'GET' ? url.searchParams.get('chat_id')!
          : (body as { chat_id: string }).chat_id
        const value = checkedProjectChat(reply.value, workspaceId, chatId)
        if (method === 'POST' && (value.status !== 'PROJECT_BOUND'
            || value.project_id !== (body as { project_id: string }).project_id)) return refused(502, 'context_mismatch')
        const currentSession = await getSession()
        if (currentSession?.user?.id !== session.user.id
            || !await getUserEntityPermissions(session.user.id, 'workspace', workspaceId)
            || !await ownsChat(workspaceId, session.user.id, chatId)) return refused(403, 'forbidden')
        return NextResponse.json(value, { headers: HEADERS })
      }
      if (action === 'project-context') {
        const value = enterpriseContextReplySchema.parse(reply.value)
        if (Object.entries(body as Record<string, unknown>).some(([key, selected]) =>
          (value.binding as Record<string, unknown>)[key] !== selected)) return refused(502, 'context_mismatch')
        const currentSession = await getSession()
        if (currentSession?.user?.id !== session.user.id
            || !await getUserEntityPermissions(session.user.id, 'workspace', workspaceId)) return refused(403, 'forbidden')
        const deliveredAt = Date.now()
        if (Date.parse(value.binding.valid_from) > deliveredAt || Date.parse(value.binding.valid_until) <= deliveredAt
            || value.evidence.sources.some((source) => source.valid_until !== null
              && Date.parse(source.valid_until) <= deliveredAt)) return refused(403, 'context_expired')
        return NextResponse.json(value, { headers: HEADERS })
      }
      return NextResponse.json(reply.value, { headers: HEADERS })
    } catch {
      return refused(502, 'assistant_unavailable')
    }
  })
}
