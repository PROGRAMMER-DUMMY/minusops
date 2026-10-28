import 'server-only'
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { db } from '@sim/db'
import { copilotChats } from '@sim/db/schema'
import { and, eq, isNull } from 'drizzle-orm'
import {
  openRouterKeySchema, openRouterModelsSchema, openRouterPreferencesSchema,
  openRouterScopeSchema, openRouterStatusSchema, openRouterStatusQuerySchema,
} from '@/lib/api/contracts/openrouter-chat'
import { getSession } from '@/lib/auth'
import { withRouteHandler } from '@/lib/core/utils/with-route-handler'
import { getUserEntityPermissions } from '@/lib/workspaces/permissions/utils'
import { architectureActionSchema, architectureReplySchema, architectureQuerySchema } from '@/lib/api/contracts/minusops-architecture'

const GATEWAY = 'http://host.docker.internal:5180'
const LOCAL_GATEWAY = 'http://127.0.0.1:5180'
const MAX_BODY = 16384
const MAX_RESPONSE = 4194304
const HEADERS = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }
type Action = 'status' | 'key' | 'models' | 'preferences' | 'architecture'
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
    return { ok: false as const, status: response.status === 400 ? 400 : 502 }
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
      : action === 'architecture' ? architectureReplySchema : openRouterPreferencesSchema
  const value = schema.parse(strictJson(raw))
  if (JSON.stringify(value).includes(token)) throw new Error('Assistant response refused')
  return { ok: true as const, value }
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
        if (permission !== 'admin' && action !== 'architecture') return refused(403, 'forbidden')
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
              : action === 'architecture' ? architectureActionSchema : openRouterPreferencesSchema
            const parsed = input.safeParse(strictJson(raw))
            if (!parsed.success) return refused(400, 'invalid_request')
            body = parsed.data
          }
        } catch (error) {
          return refused(error instanceof BodyLimit ? 413 : 400, 'invalid_request')
        }
      }
      if (method !== 'GET' && permission !== 'admin') {
        const inspection = action === 'architecture' && architectureActionSchema.safeParse(body)
        if (!inspection || !inspection.success || !['plan', 'delivery', 'export', 'draft', 'catalog', 'presence', 'presentation', 'clipboard', 'host_status', 'host_change'].includes(inspection.data.operation)) {
          return refused(403, 'forbidden')
        }
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
      return NextResponse.json(reply.value, { headers: HEADERS })
    } catch {
      return refused(502, 'assistant_unavailable')
    }
  })
}
