import { z } from 'zod'
import { defineRouteContract } from '@/lib/api/contracts/types'
import { openRouterScopeSchema } from '@/lib/api/contracts/openrouter-chat'

const actor = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/)
const fields = {
  name: z.string().trim().min(1).max(160),
  description: z.string().trim().max(2000),
  source_urls: z.array(z.string().url().max(2048)).max(8),
}
const create = z.object({ operation: z.enum(['create', 'import']), ...fields, request_id: z.uuid() }).strict()
  .refine(value => value.operation !== 'import' || value.source_urls.length > 0)
const save = z.object({ operation: z.literal('save'), ...fields, project_id: z.uuid(),
  revision: z.number().int().positive(), credential_id: actor.nullable(),
  chat_model: z.string().min(1).max(200).nullable().optional() }).strict()
const remove = z.object({operation: z.literal('delete'), project_id: z.uuid(),
  revision: z.number().int().positive()}).strict()
export const projectSetupActionSchema = z.union([create, save, remove])
export type ProjectSetupAction = z.input<typeof projectSetupActionSchema>

export const projectSetupSchema = z.object({ project_id: z.uuid(), ...fields,
  credential_id: actor.nullable(), revision: z.number().int().positive(),
  chat_model: z.string().min(1).max(200).nullable().optional(),
  created_at: z.iso.datetime({offset: true}), updated_at: z.iso.datetime({offset: true}),
}).strict()
export type ProjectSetup = z.output<typeof projectSetupSchema>
export const projectSetupReplySchema = z.object({schema_version: z.literal(1),
  workspace_id: z.uuid(), user_id: actor, projects: z.array(projectSetupSchema).max(64),
  updated_project_id: z.uuid().nullable(),
  execution_authority: z.literal('NONE'),
}).strict().refine(value => new Set(value.projects.map(project => project.project_id)).size === value.projects.length
  && (value.updated_project_id === null || value.projects.some(project => project.project_id === value.updated_project_id)))

const path = '/api/workspaces/[id]/assistant/project-setup'
export const projectSetupContract = defineRouteContract({method: 'GET', path,
  params: openRouterScopeSchema, query: z.object({}).strict(),
  response: {mode: 'json', schema: projectSetupReplySchema},
})
export const updateProjectSetupContract = defineRouteContract({method: 'POST', path,
  params: openRouterScopeSchema, query: z.object({}).strict(), body: projectSetupActionSchema,
  response: {mode: 'json', schema: projectSetupReplySchema},
})
