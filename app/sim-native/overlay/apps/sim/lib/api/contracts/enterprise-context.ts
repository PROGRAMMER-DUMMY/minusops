import { z } from 'zod'
import { defineRouteContract } from '@/lib/api/contracts/types'

const id = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/)
const text = z.string().min(1).max(2048)
const sha = z.string().regex(/^[a-f0-9]{64}$/)
const timestamp = z.iso.datetime({ offset: true }).refine((value) => /(?:Z|\+00:00)$/.test(value))
const scope = z.object({ tenant_id: id, project_id: id, workspace_id: id }).strict()

export const enterpriseContextRequestSchema = z.object({
  schema_version: z.literal(1),
  request_id: id,
  tenant_id: id,
  project_id: id,
  workspace_id: id,
  intent: z.enum(['explain', 'inspect', 'new', 'change', 'incident']),
  processing_destination: id,
  capability_id: id,
  capability_version: id,
  operation: id,
}).strict()

export const enterpriseContextBindingSchema = enterpriseContextRequestSchema.extend({
  user_id: id,
  source_ids: z.array(id).max(64),
  valid_from: timestamp,
  valid_until: timestamp,
  effects: z.tuple([z.literal('read')]),
  grant_id: id,
  grant_revision: id,
  admission_sha256: sha,
  capability_sha256: sha,
  authority: z.literal('NONE'),
}).strict()

const source = z.object({
  id,
  reference: text,
  revision: text,
  sha256: sha,
  owner: z.string().min(1).max(160).nullable(),
  observed_at: timestamp,
  status: z.literal('ACTIVE'),
  valid_from: timestamp.nullable(),
  valid_until: timestamp.nullable(),
  currentness: z.enum(['UNKNOWN', 'CURRENT']),
}).strict()
const claim = z.object({
  id,
  source_id: id,
  clause: text,
  statement: text,
  state: z.enum(['CLAIMED', 'OBSERVED', 'PROPOSED', 'UNKNOWN', 'CONFLICTING']),
  observed_at: timestamp.nullable(),
  kind: z.literal('reported_obligation').optional(),
}).strict()
const relationship = z.object({
  id,
  kind: z.enum(['depends_on', 'implements', 'verifies', 'supersedes']),
  from_id: id,
  to_id: id,
}).strict()
const conflict = z.object({
  id,
  claim_ids: z.array(id).min(2).max(128),
  owner: z.string().min(1).max(160).nullable(),
  reason: text,
}).strict()

export const enterpriseContextEvidenceSchema = z.object({
  schema_version: z.literal(1),
  scope,
  sources: z.array(source).max(64),
  claims: z.array(claim).max(128),
  relationships: z.array(relationship).max(256),
  conflicts: z.array(conflict).max(64),
  limitations: z.array(text).max(16),
  authority: z.literal('UNVERIFIED'),
  execution_authority: z.literal('NONE'),
  revision: sha,
}).strict()

export const enterpriseContextReplySchema = z.object({
  schema_version: z.literal(1),
  binding: enterpriseContextBindingSchema,
  evidence: enterpriseContextEvidenceSchema,
  execution_authority: z.literal('NONE'),
}).strict().refine(({ binding, evidence }) =>
  binding.tenant_id === evidence.scope.tenant_id
    && binding.project_id === evidence.scope.project_id
    && binding.workspace_id === evidence.scope.workspace_id)

export const enterpriseContextContract = defineRouteContract({
  method: 'POST', path: '/api/workspaces/[id]/assistant/project-context',
  params: z.object({ id: z.uuid() }).strict(), query: z.object({}).strict(),
  body: enterpriseContextRequestSchema,
  response: { mode: 'json', schema: enterpriseContextReplySchema },
})
