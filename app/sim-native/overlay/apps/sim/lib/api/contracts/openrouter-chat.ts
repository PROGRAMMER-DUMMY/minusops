import { z } from 'zod'
import { defineRouteContract } from '@/lib/api/contracts/types'

const scope = z.object({ id: z.uuid() }).strict()
const noInput = z.object({}).strict()
export const openRouterStatusQuerySchema = z.object({ chat_id: z.uuid().optional() }).strict()
const modelId = z.string().min(1).max(200).regex(/^~?[A-Za-z0-9][A-Za-z0-9._:/-]*$/)
  .refine((value) => !value.includes('..'))
const effort = z.enum(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'])
const money = z.number().finite().nonnegative().max(1e15)

export const openRouterPreferencesSchema = z.object({
  model: modelId.nullable(),
  effort: effort.nullable(),
  max_tokens: z.number().int().min(1).max(32768),
}).strict().refine((value) => value.model !== null || value.effort === null)
export type OpenRouterPreferences = z.infer<typeof openRouterPreferencesSchema>

const providerStatus = z.object({
  configured: z.boolean(),
  source: z.literal('vault').nullable(),
  reference: z.literal('vault:openrouter').nullable(),
  status: z.enum(['configured', 'credential_missing', 'vault_unavailable', 'invalid_credential']),
})
const recordedSpend = z.object({
  currency: z.literal('USD'), recorded_usd: money.nullable(), limit_usd: money.nullable(),
  status: z.enum(['not_started', 'unlimited', 'available', 'pending', 'limit_reached', 'cost_unknown', 'unavailable']),
  pending_calls: z.number().int().nonnegative(), unknown_calls: z.number().int().nonnegative(),
  completed_calls: z.number().int().nonnegative(), provider_billing_cap: z.literal(false),
})
export const openRouterStatusSchema = z.object({
  provider: z.literal('openrouter'),
  providers: z.object({ openrouter: providerStatus }),
  preferences: openRouterPreferencesSchema,
  workflow_tools: z.literal(false), recorded_spend: recordedSpend.nullable().optional(),
  architecture_context: z.object({ run_id: z.string().max(200), revision: z.string().regex(/^[a-f0-9]{64}$/),
    label: z.string().min(1).max(200) }).nullable().optional(),
})
export type OpenRouterStatus = z.infer<typeof openRouterStatusSchema>

const model = z.object({
  id: modelId, name: z.string().min(1).max(200),
  context_length: z.number().int().positive().nullable(),
  reasoning_efforts: z.array(effort).max(7), tools: z.boolean().nullable(),
  pricing: z.object({ prompt: money.nullable(), completion: money.nullable(),
    request: money.nullable(), unit: z.string().max(100) }).nullable(),
  capabilities_verified: z.boolean(), access_verified: z.literal(false),
  max_output_tokens: z.number().int().positive().nullable(),
  supported_parameters: z.array(z.string().max(100)).max(100).nullable().optional(),
})
export type OpenRouterModel = z.infer<typeof model>
export const openRouterModelsSchema = z.object({
  provider: z.literal('openrouter'), models: z.array(model).max(4096), stale: z.literal(false),
  catalog_scope: z.literal('account_filtered'), ttl_seconds: z.number().int().min(0).max(300),
  access_verified: z.literal(false),
}).refine((value) => new Set(value.models.map((item) => item.id)).size === value.models.length)
export type OpenRouterModels = z.infer<typeof openRouterModelsSchema>

export const openRouterKeySchema = z.object({
  key: z.string().min(8).max(4096).regex(/^[!-~]+$/),
}).strict()

const path = '/api/workspaces/[id]/assistant'
export const openRouterStatusContract = defineRouteContract({
  method: 'GET', path: `${path}/status`, params: scope, query: openRouterStatusQuerySchema,
  response: { mode: 'json', schema: openRouterStatusSchema },
})
export const openRouterModelsContract = defineRouteContract({
  method: 'GET', path: `${path}/models`, params: scope, query: noInput,
  response: { mode: 'json', schema: openRouterModelsSchema },
})
export const openRouterPreferencesContract = defineRouteContract({
  method: 'GET', path: `${path}/preferences`, params: scope, query: noInput,
  response: { mode: 'json', schema: openRouterPreferencesSchema },
})
export const saveOpenRouterPreferencesContract = defineRouteContract({
  method: 'POST', path: `${path}/preferences`, params: scope, query: noInput,
  body: openRouterPreferencesSchema,
  response: { mode: 'json', schema: openRouterPreferencesSchema },
})
export const saveOpenRouterKeyContract = defineRouteContract({
  method: 'POST', path: `${path}/key`, params: scope, query: noInput, body: openRouterKeySchema,
  response: { mode: 'json', schema: z.object({ success: z.literal(true) }) },
})
export const deleteOpenRouterKeyContract = defineRouteContract({
  method: 'DELETE', path: `${path}/key`, params: scope, query: noInput,
  response: { mode: 'json', schema: z.object({ success: z.literal(true) }) },
})

export const openRouterScopeSchema = scope
