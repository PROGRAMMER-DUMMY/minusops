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
  fields: z.record(z.string().max(1000), z.object({ value: z.unknown(), kind: z.string(), editable: z.boolean(),
    overridden_by: z.object({ node_id: id, field: z.string() }).optional(),
    type_evidence: z.object({ type: z.enum(['string', 'number', 'bool', 'list(string)', 'map(string)']), source }).optional() })),
  instance_resolution: z.string().default('DRAFT_ONLY'), execution_authority: z.literal('NONE') })
const sourceEdge = z.object({ id, source: id, target: id, kind: z.string(), basis: z.string(),
  field: z.string().optional() }).passthrough()
const graph = z.object({ schema_version: z.literal(1), kind: z.enum(['minusops-source-architecture', 'minusops-architecture-design-draft', 'minusops-proposed-source-architecture']),
  revision: hash, baseline: z.object({ run_id: runId }).passthrough(), nodes: z.array(sourceNode).max(1000),
  edges: z.array(sourceEdge).max(50000), files: z.array(z.object({ path: z.string(), sha256: hash,
    preview: z.string().nullable() }).passthrough()).max(128).default([]), gaps: z.array(z.unknown()).default([]),
  evidence_basis: z.string(), execution_authority: z.literal('NONE'), validation: z.string() })
export type SourceNode = z.infer<typeof sourceNode>
export type SourceEdge = z.infer<typeof sourceEdge>
export type SourceGraph = z.infer<typeof graph>
export type ArchitectureSubject = z.infer<typeof subject>
const edit = z.union([
  z.object({ node_id: id, field: z.string().min(1).max(1000), value: z.unknown() }).strict(),
  z.object({ node_id: id, field: z.string().min(1).max(1000), expression: z.string().min(1).max(4000) }).strict(),
])
export const sourceEditsSchema = z.array(edit).min(1).max(32)
const fieldChangeSchema = z.object({ schema_version: z.literal(1), kind: z.literal('minusops-source-change'),
  request: z.record(z.string(), z.unknown()), edits: z.object({ edits: z.array(edit).min(1).max(32) }),
  revision: hash, files: z.array(z.object({ path: z.string(), before_sha256: hash, after_sha256: hash,
    content: z.string(), diff: z.string() })).min(1).max(32), execution_authority: z.literal('NONE'),
  validation: z.string(), change_hash: hash })
const structuralChangeSchema = z.object({ schema_version: z.literal(2), kind: z.literal('minusops-structural-source-change'),
  request: z.record(z.string(), z.unknown()), operations: z.object({ operations: z.array(z.record(z.string(), z.unknown())).min(1).max(128) }),
  revision: hash, files: z.array(z.object({ operation: z.enum(['create', 'update', 'delete']), path: z.string(),
    before_sha256: hash.nullable(), after_sha256: hash.nullable(), content: z.string().nullable(), diff: z.string() })).min(1).max(32),
  proposal_graph: graph, execution_authority: z.literal('NONE'), validation: z.string(), change_hash: hash })
export const sourceChangeSchema = z.discriminatedUnion('schema_version', [fieldChangeSchema, structuralChangeSchema])
export type SourceChange = z.infer<typeof sourceChangeSchema>
export const aiProposalSchema = z.object({ kind: z.literal('minusops-ai-source-proposal'),
  message_id: z.string().uuid(), tool_call_id: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/),
  run_id: runId, revision: hash, edit_count: z.number().int().min(1).max(16), change_hash: hash,
  requires_review: z.literal(true), source_written: z.literal(false) }).strict()
export type ProposalReference = Pick<z.infer<typeof aiProposalSchema>, 'message_id' | 'tool_call_id'> & { chat_id: string }
const capabilities = z.object({ schema_version: z.literal(1),
  commands: z.array(z.enum(['field_edit', 'undo', 'redo', 'reset', 'duplicate_nodes', 'remove_nodes', 'insert_module', 'connect', 'disconnect', 'paste_nodes'])).max(10),
  max_commands: z.number().int().positive().max(128), max_edits: z.number().int().positive().max(32),
  publication: z.literal(false), execution_authority: z.literal('NONE') })
export const architectureDraftSchema = z.object({ schema_version: z.literal(1),
  kind: z.literal('minusops-architecture-proposal-draft'), workspace_id: z.string().uuid(), run_id: runId,
  revision: hash, draft_revision: z.number().int().min(0).max(128), status: z.enum(['STAGED', 'EMPTY']),
  edits: z.array(edit).max(32), change: sourceChangeSchema.nullable(), can_undo: z.boolean(), can_redo: z.boolean(),
  capabilities, execution_authority: z.literal('NONE'), receipt: z.object({ command_id: z.string().uuid(),
    applied_draft_revision: z.number().int().positive().max(128), replayed: z.boolean() }).optional() })
export type ArchitectureDraft = z.infer<typeof architectureDraftSchema>
export const compositionCatalogSchema = z.object({ kind: z.literal('minusops-composition-catalog'), run_id: runId,
  revision: hash, draft_revision: z.number().int().min(0).max(128), modules: z.array(z.object({ module_id: z.literal('storage-medallion-s3'),
    module_content_hash: hash, digest: hash, fields: z.record(z.string(), z.object({ type: z.string(), required: z.boolean(),
      default: z.unknown().optional(), description: z.string() }).passthrough()) }).passthrough()).max(1),
  ports: z.array(z.object({ node_id: id, module_id: z.string(), module_content_hash: hash, descriptor_digest: hash,
    ports: z.array(z.object({ id: z.string(), direction: z.enum(['input', 'output']), value_type: z.string(),
      keys: z.array(z.string()).optional(), source_module: z.string().optional(), source_port: z.string().optional(),
      source_key: z.string().nullable().optional() })) })).max(1000), execution_authority: z.literal('NONE'),
  provider_support: z.literal('AWS_ONLY'), module_release_authority: z.literal('UNVERIFIED') })
export type CompositionCatalog = z.infer<typeof compositionCatalogSchema>
const cursor = z.object({ x: z.number().finite().min(-100000).max(100000), y: z.number().finite().min(-100000).max(100000) }).strict()
export const architecturePresenceSchema = z.object({ kind: z.literal('minusops-architecture-presence'), run_id: runId, revision: hash,
  advisory: z.literal(true), expires_after_seconds: z.literal(20), execution_authority: z.literal('NONE'),
  editors: z.array(z.object({ session_id: z.string().uuid(), label: z.string().regex(/^Editor [a-f0-9]{6}$/),
    selection: id.nullable(), cursor: cursor.nullable() })).max(32) })
const annotation = z.object({ id: z.string().uuid(), kind: z.enum(['note', 'component', 'group']), label: z.string().min(1).max(120),
  text: z.string().max(4096), x: z.number().finite().min(-100000).max(100000), y: z.number().finite().min(-100000).max(100000) }).strict()
export const architecturePresentationSchema = z.object({ kind: z.literal('minusops-architecture-presentation'),
  workspace_id: z.string().uuid(), run_id: runId, presentation_revision: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
  notes: z.array(annotation).max(64), execution_authority: z.literal('NONE') }).strict()
export type ArchitecturePresentation = z.infer<typeof architecturePresentationSchema>
export const architectureClipboardSchema = z.object({ kind: z.literal('minusops-architecture-clipboard'), workspace_id: z.string().uuid(),
  run_id: runId, revision: hash, snapshot_revision: z.number().int().min(0).max(128), snapshot_digest: hash,
  nodes: z.array(z.object({ node_id: id, address: z.string().min(1).max(2000) }).strict()).min(1).max(16) }).strict()
export const hostReviewSchema = z.object({ kind: z.literal('minusops-host-review'),
  request_id: z.string().uuid(), request_digest: hash, entry_digest: hash, run_id: runId, revision: hash,
  status: z.enum(['QUEUED', 'CLAIMED', 'ANSWERED', 'CHANGE_PROPOSED', 'NEEDS_INPUT', 'CANCELLED', 'AUTHORED']),
  worker_identity: z.literal('UNVERIFIED_CORRELATION_LABEL'), message: z.string().max(2000), change_hash: hash.nullable(),
  publication: z.enum(['NOT_OBSERVED', 'RECOVERY_REQUIRED', 'ROLLED_BACK', 'SOURCE_PUBLISHED', 'SOURCE_AUTHORED', 'HISTORICAL_SOURCE_CHANGED']),
  current_revision: hash.nullable(), execution_authority: z.literal('NONE'), apply_eligibility: z.literal('UNKNOWN') })
export type HostReview = z.infer<typeof hostReviewSchema>
export const architectureWorkspaceSchema = z.object({ schema_version: z.literal(1), run_id: runId.nullable(),
  source_status: z.enum(['UNSELECTED', 'AVAILABLE', 'UNAVAILABLE']),
  runs: z.array(metadata).max(1000), architecture: graph.nullable(), metadata: metadata.nullable().default(null),
  context_enabled: z.boolean().default(true), capabilities: capabilities.optional(),
  chat_ids: z.array(z.string().uuid()).max(1000).default([]),
  draft: architectureDraftSchema.nullable().default(null),
  host_review: z.object({ request_id: z.string().uuid(), revision: hash }).nullable().default(null),
  icons: z.record(z.string(), z.string().startsWith('data:image/svg+xml;base64,').max(100000)),
  focus: z.object({ revision: hash, subject, label: z.string().min(1).max(200) }).nullable(),
  layout: z.object({ revision: hash, positions: z.array(position).max(1000) }).nullable(),
  layout_hash: hash.optional(),
  execution_authority: z.literal('NONE') })
export type ArchitectureWorkspace = z.infer<typeof architectureWorkspaceSchema>
const common = { run_id: runId, revision: hash }
export const architectureActionSchema = z.discriminatedUnion('operation', [
  z.object({ operation: z.literal('create'), name, operation_id: z.string().uuid() }).strict(),
  z.object({ operation: z.literal('rename'), run_id: runId, metadata_revision: z.number().int().nonnegative(), name }).strict(),
  z.object({ operation: z.literal('lock'), run_id: runId, metadata_revision: z.number().int().nonnegative(), locked: z.boolean() }).strict(),
  z.object({ operation: z.literal('delete'), run_id: runId, metadata_revision: z.number().int().nonnegative() }).strict(),
  z.object({ operation: z.literal('duplicate'), ...common, metadata_revision: z.number().int().nonnegative(), operation_id: z.string().uuid() }).strict(),
  z.object({ operation: z.literal('export'), ...common }).strict(),
  z.object({ operation: z.literal('bind_chat'), ...common, chat_id: z.string().uuid(), subject: subject.nullable(), enabled: z.boolean(),
    draft_revision: z.number().int().min(0).max(128).optional(), change_hash: hash.optional() }).strict()
    .refine(value => (value.draft_revision === undefined) === (value.change_hash === undefined)),
  z.object({ operation: z.literal('proposal'), ...common, chat_id: z.string().uuid(), message_id: z.string().uuid(),
    tool_call_id: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/) }).strict(),
  z.object({ operation: z.literal('select'), run_id: runId }).strict(),
  z.object({ operation: z.literal('clear_focus'), run_id: runId }).strict(),
  z.object({ operation: z.literal('focus'), ...common, subject }).strict(),
  z.object({ operation: z.literal('layout'), ...common, positions: z.array(position).max(1000),
    draft_revision: z.number().int().min(0).max(128).optional(), expected_layout_hash: hash.optional() }).strict(),
  z.object({ operation: z.literal('stage'), ...common, subject, edits: z.array(edit).min(1).max(32) }).strict(),
  z.object({ operation: z.literal('draft'), ...common }).strict(),
  z.object({ operation: z.literal('clipboard'), ...common, draft_revision: z.number().int().min(0).max(128), node_ids: z.array(id).min(1).max(16) }).strict(),
  z.object({ operation: z.literal('catalog'), ...common, draft_revision: z.number().int().min(0).max(128) }).strict(),
  z.object({ operation: z.literal('presence'), ...common, session_id: z.string().uuid(), selection: id.nullable(), cursor: cursor.nullable() }).strict(),
  z.object({ operation: z.literal('presentation'), ...common }).strict(),
  z.object({ operation: z.literal('save_presentation'), ...common, presentation_revision: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
    notes: z.array(annotation).max(64) }).strict(),
  z.object({ operation: z.literal('command'), ...common, command_id: z.string().uuid(),
    expected_draft_revision: z.number().int().min(0).max(128), command: z.union([
      z.object({ type: z.literal('field_edit'), subject: z.object({ kind: z.literal('source_node'), id }).strict(),
        edits: z.array(edit).min(1).max(32) }).strict(),
      z.object({ type: z.enum(['undo', 'redo', 'reset']) }).strict(),
      z.object({ type: z.literal('duplicate_nodes'), operation_id: z.string().uuid(),
        nodes: z.array(z.object({ node_id: id, name: z.string().regex(/^[A-Za-z_][A-Za-z_0-9-]{0,79}$/) }).strict()).min(1).max(16) }).strict(),
      z.object({ type: z.literal('paste_nodes'), operation_id: z.string().uuid(), snapshot_revision: z.number().int().min(0).max(128), snapshot_digest: hash,
        nodes: z.array(z.object({ node_id: id, name: z.string().regex(/^[A-Za-z_][A-Za-z_0-9-]{0,79}$/) }).strict()).min(1).max(16) }).strict(),
      z.object({ type: z.literal('remove_nodes'), operation_id: z.string().uuid(), node_ids: z.array(id).min(1).max(16) }).strict(),
      z.object({ type: z.literal('insert_module'), operation_id: z.string().uuid(), module_id: z.literal('storage-medallion-s3'),
        module_content_hash: hash, descriptor_digest: hash, name: z.string().regex(/^[A-Za-z_][A-Za-z_0-9-]{0,79}$/),
        inputs: z.record(z.string(), z.unknown()) }).strict(),
      z.object({ type: z.literal('connect'), operation_id: z.string().uuid(), target_id: id, field: z.string().max(80),
        source_id: id, port: z.string().max(80), key: z.string().max(80).nullable() }).strict(),
      z.object({ type: z.literal('disconnect'), operation_id: z.string().uuid(), target_id: id, field: z.string().max(80),
        replacement: z.string().max(2048) }).strict(),
    ]) }).strict(),
  z.object({ operation: z.literal('review'), ...common, change: sourceChangeSchema }).strict(),
  z.object({ operation: z.literal('queue_review'), ...common, change: sourceChangeSchema }).strict(),
  z.object({ operation: z.enum(['host_status', 'host_change']), ...common, request_id: z.string().uuid() }).strict(),
  z.object({ operation: z.literal('cancel_host'), ...common, request_id: z.string().uuid(), entry_digest: hash }).strict(),
  z.object({ operation: z.literal('queue_authoring'), ...common, mode: z.enum(['guided', 'recommended', 'hybrid']),
    request: z.string().trim().min(1).max(4000), operation_id: z.string().uuid() }).strict(),
  z.object({ operation: z.literal('adopt_authored'), ...common, request_id: z.string().uuid(), metadata_revision: z.number().int().nonnegative() }).strict(),
  z.object({ operation: z.enum(['delivery', 'plan']), ...common }).strict(),
])
export type ArchitectureAction = z.infer<typeof architectureActionSchema>
export const architectureExportSchema = z.object({ kind: z.literal('minusops-architecture-design-export'), schema_version: z.literal(1),
  origin: z.object({ run_id: runId, revision: hash }), graph: z.record(z.string(), z.unknown()).refine((value) =>
    value.kind === 'minusops-architecture-design-draft' && value.validation === 'DRAFT_UNPUBLISHED' && graph.safeParse(value).success),
  execution_authority: z.literal('NONE'), omission_limits: z.array(z.string()).max(20) })
export const architectureReplySchema = z.union([architectureWorkspaceSchema, sourceChangeSchema, architectureDraftSchema, hostReviewSchema,
  architectureExportSchema, compositionCatalogSchema, architecturePresenceSchema, architecturePresentationSchema, architectureClipboardSchema,
  z.object({ status: z.enum(['CONTEXT_SELECTED', 'CONTEXT_CLEARED', 'CONTEXT_BOUND', 'LAYOUT_SAVED', 'UNKNOWN']),
    execution_authority: z.literal('NONE'), reason: z.string().optional() }),
  z.object({ kind: z.literal('minusops-canvas-delivery'), inspection_only: z.literal(true),
    execution_authority: z.literal('NONE'), authenticity: z.literal('UNVERIFIED'),
    views: z.record(z.string(), z.unknown()), sources: z.array(z.unknown()), gaps: z.array(z.unknown()) }).passthrough(),
  z.object({ kind: z.literal('minusops-review-graph'), nodes: z.array(z.unknown()).max(10000),
    provenance: z.object({ binding_state: z.literal('CURRENT_PLAN_INSPECTION') }).passthrough() }).passthrough(),
])
export type ArchitectureReply = z.infer<typeof architectureReplySchema>
