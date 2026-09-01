// Cube deployment configuration.
//
// Every secret here comes from the environment, never from this file: it is committed to the
// domain repo and exported by `minusctl export`. The Athena credentials are supplied by the
// task role (IRSA on EKS, task role on ECS), which is why no key appears below.
//
// `scheduledRefreshTimer` is what keeps the pre-aggregations warm. Without it the first
// dashboard request after each refresh window pays the full rebuild cost, which users
// experience as "the dashboard is slow in the mornings".

module.exports = {
  // Pre-aggregations live in the ElastiCache instance this module provisions; `cubestore` is
  // the only driver that survives a pod restart without a cold rebuild.
  externalDriverFactory: () => ({
    type: 'cubestore',
    host: process.env.CUBEJS_CUBESTORE_HOST,
    port: process.env.CUBEJS_CUBESTORE_PORT,
  }),

  scheduledRefreshTimer: 60,

  // Multi-tenant by security context. Returning a constant here would let any caller read
  // every tenant's rollups out of one shared cache -- the cache key must carry the tenant.
  contextToAppId: ({ securityContext }) =>
    `CUBE_APP_${securityContext.tenantId || 'default'}`,

  // Row-level filtering is applied here so it cannot be bypassed by a caller crafting their
  // own query. This mirrors the Lake Formation row filters in modules/governance-lakeformation;
  // both must agree, and Lake Formation is the one that is authoritative.
  queryRewrite: (query, { securityContext }) => {
    if (securityContext.country) {
      query.filters.push({
        member: 'CustomerEvents.country',
        operator: 'equals',
        values: [securityContext.country],
      });
    }
    return query;
  },
};
