import { z } from 'zod'
import { enterpriseContextRequestSchema } from '@/lib/api/contracts/enterprise-context'
import { openRouterScopeSchema } from '@/lib/api/contracts/openrouter-chat'
import { defineRouteContract } from '@/lib/api/contracts/types'

export const projectChatQuerySchema = z.object({ chat_id: z.uuid() }).strict()
export const projectChatRequestSchema = projectChatQuerySchema.extend({
  project_id: enterpriseContextRequestSchema.shape.project_id,
}).strict()
export const projectChatReplySchema = z.object({
  schema_version: z.literal(1),
  chat_id: z.uuid(),
  project_id: enterpriseContextRequestSchema.shape.project_id.nullable(),
  status: z.enum(['PROJECT_BOUND', 'UNBOUND']),
  selection: enterpriseContextRequestSchema.nullable(),
  revision: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
  execution_authority: z.literal('NONE'),
}).strict().refine((value) => value.status === 'UNBOUND'
  ? value.project_id === null && value.selection === null && value.revision === null
  : value.project_id !== null && value.selection !== null && value.revision !== null
    && value.selection.project_id === value.project_id)
export type ProjectChatReply = z.infer<typeof projectChatReplySchema>

const path = '/api/workspaces/[id]/assistant/project-chat'
export const projectChatContract = defineRouteContract({
  method: 'GET', path, params: openRouterScopeSchema, query: projectChatQuerySchema,
  response: { mode: 'json', schema: projectChatReplySchema },
})
export const bindProjectChatContract = defineRouteContract({
  method: 'POST', path, params: openRouterScopeSchema, query: z.object({}).strict(),
  body: projectChatRequestSchema,
  response: { mode: 'json', schema: projectChatReplySchema },
})

export const projectCatalogReplySchema = z.object({
  schema_version: z.literal(1), workspace_id: z.uuid(),
  user_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/),
  status: z.enum(['AVAILABLE', 'UNAVAILABLE']),
  projects: z.array(z.object({
    project_id: enterpriseContextRequestSchema.shape.project_id,
    name: z.string().trim().min(1).max(160),
  }).strict()).max(64),
  execution_authority: z.literal('NONE'),
}).strict().refine(value => new Set(value.projects.map(item => item.project_id)).size === value.projects.length
  && (value.status !== 'UNAVAILABLE' || value.projects.length === 0))

export const projectCatalogContract = defineRouteContract({
  method: 'GET', path: '/api/workspaces/[id]/assistant/projects', params: openRouterScopeSchema,
  query: z.object({}).strict(), response: {mode: 'json', schema: projectCatalogReplySchema},
})
