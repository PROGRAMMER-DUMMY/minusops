import 'server-only'
import { timingSafeEqual } from 'node:crypto'
import { NextRequest, NextResponse } from 'next/server'
import { z } from 'zod'
import { resolveCredentialTokenIdentity } from '@/lib/credentials/access'
import { resolveServiceAccountToken, resolveOAuthAccountId, getCredential } from '@/lib/oauth/credential-service'
import { completeOAuthCredentialToken, recordCredentialAccess } from '@/lib/oauth/token-resolution'
import { getUserEntityPermissions } from '@/lib/workspaces/permissions/utils'
import { projectConnection } from '@/lib/openrouter-chat/project-connection'
import { projectSetupReplySchema } from '@/lib/api/contracts/project-setup'
import { getConfluenceCloudId, transformPageData } from '@/tools/confluence/utils'
import { boundedBody, gateway } from '@/lib/openrouter-chat/server'

const headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
const requestSchema = z.object({workspace_id: z.uuid(), user_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/),
  project_id: z.uuid(), revision: z.number().int().positive(), model: z.string().min(1).max(200),
  operation: z.enum(['inspect', 'read']),
}).strict()

async function projectSetup(input: z.infer<typeof requestSchema>) {
  if (!await getUserEntityPermissions(input.user_id, 'workspace', input.workspace_id)) throw new Error('Access refused')
  const reply = await gateway(input.workspace_id, 'project-setup', 'GET', undefined,
    '?' + new URLSearchParams({user_id: input.user_id}))
  if (!reply.ok) throw new Error('Project unavailable')
  const value = projectSetupReplySchema.parse(reply.value)
  if (value.workspace_id !== input.workspace_id || value.user_id !== input.user_id) throw new Error('Scope changed')
  const project = value.projects.find(row => row.project_id === input.project_id)
  if (!project || project.revision !== input.revision || project.chat_model !== input.model
      || !project.credential_id || !project.source_urls.length) throw new Error('Project setup incomplete')
  const connection = await projectConnection(project.credential_id, input.workspace_id, input.user_id)
  if (!connection) throw new Error('Connection unavailable')
  return {project, connection}
}

async function readConfluence(setup: Awaited<ReturnType<typeof projectSetup>>, input: z.infer<typeof requestSchema>) {
  const {credential} = setup.connection
  let accessToken: string
  let domain: string | undefined
  let cloudId: string | undefined
  if (credential.type === 'service_account') {
    const resolved = await resolveServiceAccountToken(credential.id, credential.providerId)
    accessToken = resolved.accessToken; domain = resolved.domain; cloudId = resolved.cloudId
    recordCredentialAccess({actorId: input.user_id, workspaceId: input.workspace_id, resourceId: credential.id,
      providerId: credential.providerId, credentialType: 'service_account'})
  } else {
    const resolved = await resolveOAuthAccountId(credential.id)
    if (!resolved?.accountId || resolved.workspaceId !== input.workspace_id) throw new Error('Account unavailable')
    const identity = await resolveCredentialTokenIdentity(credential.id, input.workspace_id)
    if (identity?.kind !== 'oauth') throw new Error('Account unavailable')
    const account = await getCredential(crypto.randomUUID(), resolved.accountId, identity.userId)
    if (!account || account.providerId !== 'confluence') throw new Error('Provider unavailable')
    const result = await completeOAuthCredentialToken({requestId: crypto.randomUUID(), credential: account,
      resolvedCredentialId: resolved.accountId, actorId: input.user_id, workspaceId: input.workspace_id})
    if (!result.ok) throw new Error('Connection unavailable')
    accessToken = result.token.accessToken
  }
  return Promise.all(setup.project.source_urls.map(async reference => {
    const url = new URL(reference)
    if (url.protocol !== 'https:' || url.port || url.username || url.password || url.search || url.hash
        || !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$/i.test(url.hostname)) throw new Error('Site refused')
    const match = /^\/wiki\/(?:spaces\/[A-Za-z0-9_~.-]+\/)?pages\/([1-9][0-9]*)$/.exec(url.pathname)
    if (!match || domain && domain.toLowerCase() !== url.hostname.toLowerCase()) throw new Error('Source refused')
    const siteId = cloudId ?? await getConfluenceCloudId(url.hostname, accessToken)
    if (!/^[a-zA-Z0-9-]{1,100}$/.test(siteId)) throw new Error('Site unavailable')
    const response = await fetch(`https://api.atlassian.com/ex/confluence/${siteId}/wiki/api/v2/pages/${match[1]}?body-format=storage`, {
      method: 'GET', redirect: 'error', cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(15000),
      headers: {Authorization: `Bearer ${accessToken}`, Accept: 'application/json'},
    })
    if (!response.ok || response.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
      await response.body?.cancel().catch(() => {}); throw new Error('Source unavailable')
    }
    const raw = await boundedBody(response.body, 131072)
    if (raw.includes(accessToken)) throw new Error('Source refused')
    const data = JSON.parse(raw)
    if (data.id !== match[1] || data.status !== 'current') throw new Error('Source unavailable')
    const result = transformPageData(data)
    result.output.url = reference
    return {page_id: match[1], observation: {status: 'AVAILABLE', observed_at: new Date().toISOString(),
      result, included_page_ids: []}}
  }))
}

export async function projectSourcesRoute(request: NextRequest) {
  try {
    const token = process.env.MINUS_ASSISTANT_GATEWAY_TOKEN ?? ''
    const supplied = request.headers.get('X-Minus-Bridge-Token') ?? ''
    if (process.env.MINUS_OPENROUTER_CHAT !== 'true' || token.length < 16 || request.headers.has('origin')
        || Buffer.byteLength(token) !== Buffer.byteLength(supplied)
        || !timingSafeEqual(Buffer.from(token), Buffer.from(supplied))) throw new Error('Bridge refused')
    if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') throw new Error('Request refused')
    const input = requestSchema.parse(JSON.parse(await boundedBody(request.body, 16384)))
    const setup = await projectSetup(input)
    const documents = input.operation === 'read' ? await readConfluence(setup, input) : []
    if (JSON.stringify(await projectSetup(input)) !== JSON.stringify(setup)) throw new Error('Access changed')
    const value = {schema_version: 1, workspace_id: input.workspace_id, user_id: input.user_id,
      project_id: input.project_id, revision: input.revision, model: input.model,
      reader: setup.connection.reader.id, documents, execution_authority: 'NONE'}
    if (Buffer.byteLength(JSON.stringify(value)) > 1048576) throw new Error('Source limit')
    return NextResponse.json(value, {headers})
  } catch {
    return NextResponse.json({error: 'Project integration is unavailable or access was refused.'}, {status: 403, headers})
  }
}
