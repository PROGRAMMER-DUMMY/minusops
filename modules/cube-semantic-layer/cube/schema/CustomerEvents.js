// Cube schema for the Gold customer-events table.
//
// The pre-aggregation at the bottom is the reason to run Cube at all. Without it every
// dashboard refresh issues an Athena query against the lake and you pay per byte scanned,
// repeatedly, for numbers that changed once an hour at most.
//
// Two things are load-bearing and easy to get wrong:
//
//   * `partitionGranularity` must match the partition key the Athena table projects on
//     (event_date, see modules/query-athena). Mismatched, the rollup rebuild scans the whole
//     table every refresh and costs more than having no cache.
//   * `refreshKey.every` is a data-freshness commitment. Dashboards will report numbers up
//     to that stale. It belongs in a conversation with the business, not in a perf tuning
//     pass.

cube(`CustomerEvents`, {
  sql_table: `gold.customer_events`,

  joins: {},

  dimensions: {
    customerId: {
      sql: `customer_id`,
      type: `string`,
      primaryKey: true,
    },
    eventDate: {
      sql: `event_date`,
      type: `time`,
    },
    channel: {
      sql: `channel`,
      type: `string`,
    },
    country: {
      sql: `country`,
      type: `string`,
    },
  },

  measures: {
    eventCount: {
      type: `count`,
      description: `Interactions, not unique customers.`,
    },
    revenueAmount: {
      sql: `revenue`,
      type: `sum`,
      description: `Gross revenue in account currency, before refunds.`,
    },
    refundAmount: {
      sql: `refunds`,
      type: `sum`,
    },
    netRevenue: {
      sql: `${revenueAmount} - ${refundAmount}`,
      type: `number`,
      description: `Must match net_revenue in the dbt semantic layer. Two definitions of one metric is the problem a semantic layer exists to remove.`,
    },
  },

  preAggregations: {
    dailyByChannel: {
      measures: [CustomerEvents.eventCount, CustomerEvents.revenueAmount, CustomerEvents.refundAmount],
      dimensions: [CustomerEvents.channel, CustomerEvents.country],
      timeDimension: CustomerEvents.eventDate,
      granularity: `day`,
      partitionGranularity: `month`,
      refreshKey: {
        every: `1 hour`,
      },
    },
  },
});
