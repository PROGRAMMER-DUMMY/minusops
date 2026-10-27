import { z } from 'zod'

const hash = z.string().regex(/^[a-f0-9]{64}$/)
const id = z.string().regex(/^[a-f0-9]{32}$/)
export const runIdSchema = z.string().min(1).max(200).regex(/^[A-Za-z0-9][A-Za-z0-9._-]*$/)
  .refine((value) => !value.includes('..'))
const runId = runIdSchema
export const architectureQuerySchema = z.object({ run_id: runId.optional() }).strict()
const name = z.string().min(1).max(120).refine((value) => value === value.trim() && !value.includes('..'))
const metadata = z.object({ run_id: runId, name, locked: z.boolean(), metadata_revision: z.number().int().nonnegative(),
  design_status: z.enum(['SOURCE', 'DRAFT']) })
const subject = z.object({ kind: z.enum(['source_node', 'source_edge']), id }).strict()
const position = z.object({ id, x: z.number().finite().min(-100000).max(100000),
  y: z.number().finite().min(-100000).max(100000) }).strict()
const source = z.object({ path: z.string().max(1000), sha256: hash,
  start_line: z.number().int().positive(), end_line: z.number().int().positive() })
const sourceNode = z.object({ id, address: z.string().max(2000), kind: z.string().max(100),
  resource_type: z.string().max(200), parent_id: id.nullable(), source,
  fields: z.record(z.string().max(1000), z.object({ value: z.unknown(), kind: z.string(), editable: z.boolean() })),
  instance_resolution: z.string().default('DRAFT_ONLY'), execution_authority: z.literal('NONE') })
const sourceEdge = z.object({ id, source: id, target: id, kind: z.string(), basis: z.string(),
  field: z.string().optional() }).passthrough()
const graph = z.object({ schema_version: z.literal(1), kind: z.enum(['minusops-source-architecture', 'minusops-architecture-design-draft']),
  revision: hash, baseline: z.object({ run_id: runId }).passthrough(), nodes: z.array(sourceNode).max(1000),
  edges: z.array(sourceEdge).max(50000), files: z.array(z.object({ path: z.string(), sha256: hash,
    preview: z.string().nullable() }).passthrough()).max(128).default([]), gaps: z.array(z.unknown()).default([]),
  evidence_basis: z.string(), execution_authority: z.literal('NONE'), validation: z.string() })
export const architectureWorkspaceSchema = z.object({ schema_version: z.literal(1), run_id: runId.nullable(),
  source_status: z.enum(['UNSELECTED', 'AVAILABLE', 'UNAVAILABLE']),
  runs: z.array(metadata).max(1000), architecture: graph.nullable(), metadata: metadata.nullable().default(null),
  context_enabled: z.boolean().default(true),
  icons: z.record(z.string(), z.string().startsWith('data:image/svg+xml;base64,').max(100000)),
  focus: z.object({ revision: hash, subject, label: z.string().min(1).max(200) }).nullable(),
  layout: z.object({ revision: hash, positions: z.array(position).max(1000) }).nullable(),
  execution_authority: z.literal('NONE') })
export type ArchitectureWorkspace = z.infer<typeof architectureWorkspaceSchema>
export type SourceNode = z.infer<typeof sourceNode>
export type SourceEdge = z.infer<typeof sourceEdge>
export type SourceGraph = z.infer<typeof graph>
export type ArchitectureSubject = z.infer<typeof subject>
const edit = z.union([
  z.object({ node_id: id, field: z.string().min(1).max(1000), value: z.unknown() }).strict(),
  z.object({ node_id: id, field: z.string().min(1).max(1000), expression: z.string().min(1).max(4000) }).strict(),
])
export const sourceChangeSchema = z.object({ schema_version: z.literal(1), kind: z.literal('minusops-source-change'),
  request: z.record(z.string(), z.unknown()), edits: z.object({ edits: z.array(edit).min(1).max(32) }),
  revision: hash, files: z.array(z.object({ path: z.string(), before_sha256: hash, after_sha256: hash,
    content: z.string(), diff: z.string() })).min(1).max(32), execution_authority: z.literal('NONE'),
  validation: z.string(), change_hash: hash })
export type SourceChange = z.infer<typeof sourceChangeSchema>
const common = { run_id: runId, revision: hash }
export const architectureActionSchema = z.discriminatedUnion('operation', [
  z.object({ operation: z.literal('create'), name, operation_id: z.string().uuid() }).strict(),
  z.object({ operation: z.literal('rename'), run_id: runId, metadata_revision: z.number().int().nonnegative(), name }).strict(),
  z.object({ operation: z.literal('lock'), run_id: runId, metadata_revision: z.number().int().nonnegative(), locked: z.boolean() }).strict(),
  z.object({ operation: z.literal('delete'), run_id: runId, metadata_revision: z.number().int().nonnegative() }).strict(),
  z.object({ operation: z.literal('duplicate'), ...common, metadata_revision: z.number().int().nonnegative(), operation_id: z.string().uuid() }).strict(),
  z.object({ operation: z.literal('export'), ...common }).strict(),
  z.object({ operation: z.literal('bind_chat'), ...common, chat_id: z.string().uuid(), subject: subject.nullable(), enabled: z.boolean() }).strict(),
  z.object({ operation: z.literal('select'), run_id: runId }).strict(),
  z.object({ operation: z.literal('clear_focus'), run_id: runId }).strict(),
  z.object({ operation: z.literal('focus'), ...common, subject }).strict(),
  z.object({ operation: z.literal('layout'), ...common, positions: z.array(position).max(1000) }).strict(),
  z.object({ operation: z.literal('stage'), ...common, subject, edits: z.array(edit).min(1).max(32) }).strict(),
  z.object({ operation: z.literal('review'), ...common, change: sourceChangeSchema }).strict(),
  z.object({ operation: z.enum(['delivery', 'plan']), ...common }).strict(),
])
export type ArchitectureAction = z.infer<typeof architectureActionSchema>
export const architectureExportSchema = z.object({ kind: z.literal('minusops-architecture-design-export'), schema_version: z.literal(1),
  origin: z.object({ run_id: runId, revision: hash }), graph: z.record(z.string(), z.unknown()).refine((value) =>
    value.kind === 'minusops-architecture-design-draft' && value.validation === 'DRAFT_UNPUBLISHED' && graph.safeParse(value).success),
  execution_authority: z.literal('NONE'), omission_limits: z.array(z.string()).max(20) })
export const architectureReplySchema = z.union([architectureWorkspaceSchema, sourceChangeSchema,
  architectureExportSchema,
  z.object({ status: z.enum(['CONTEXT_SELECTED', 'CONTEXT_CLEARED', 'CONTEXT_BOUND', 'LAYOUT_SAVED', 'UNKNOWN']),
    execution_authority: z.literal('NONE'), reason: z.string().optional() }),
  z.object({ kind: z.literal('minusops-canvas-delivery'), inspection_only: z.literal(true),
    execution_authority: z.literal('NONE'), authenticity: z.literal('UNVERIFIED'),
    views: z.record(z.string(), z.unknown()), sources: z.array(z.unknown()), gaps: z.array(z.unknown()) }).passthrough(),
  z.object({ kind: z.literal('minusops-review-graph'), nodes: z.array(z.unknown()).max(10000),
    provenance: z.object({ binding_state: z.literal('CURRENT_PLAN_INSPECTION') }).passthrough() }).passthrough(),
])
export type ArchitectureReply = z.infer<typeof architectureReplySchema>
