# Product Requirements Document (PRD) — FinOps Unit Economics, Latency Physics & Data SLA Framework

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-FINOPS-2026-005 (FinOps & Latency Architecture Additions) |
| **Status** | APPROVED SPECIFICATION FOR IMPLEMENTATION |
| **Lead Reviewers** | Matt (Principal Cloud Architect), Ponytail (Senior Reviewer) |
| **Target Components** | `.agents/skills/grill-me/SKILL.md`, `core/cost/`, `core/architecture/`, `core/reporting/` |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Date** | 2026-08-21 |

---

## 1. Executive Summary & Problem Statement

Standard IaC generators treat cost and latency as static design-time inputs. In reality, **cloud infrastructure is usage-based and dynamic**, and over-designing upfront leads to premature cost optimization or brittle architectures.

This specification elevates MinusOps from basic budget checks to an **empirical FinOps and Latency Physics Framework**, incorporating real-world data from enterprise case studies (Boeing, Capital One, European Banking, Databricks, Bigeye, dbt Labs, and Google Cloud).

---

## 2. The 6 Context Dimensions of Cloud Budgeting & Reliability

MinusOps evaluates 6 organizational variables during the architecture interview:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6 CONTEXT VARIABLES INFLUENCING BUDGET & RELIABILITY                        │
├──────────────────────────┬──────────────────────────────────────────────────┤
│ Context Variable         │ Architectural Impact & Behavior                  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **1. Company Stage**     │ • Early Stage: "No surprise bills", build simple.│
│                          │ • Enterprise: Formal FinOps chargeback & audit.  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **2. Cost Philosophy**   │ • Gate-First: Strict pre-merge budget ceilings.  │
│                          │ • Build-Then-Optimize: Instrument unit metrics.  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **3. Ownership Model**   │ • Central IT: Global pool cost governance.       │
│                          │ • Decentralized: Tag-based team chargeback.      │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **4. Criticality Tier**  │ • Tier 1 (Revenue): Multi-AZ, 99.9% SLA, P1 page.│
│                          │ • Tier 3 (Internal): Single-AZ, best-effort.     │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **5. Regulatory Load**   │ • HIPAA / SOC2 / SEC 17a-4: WORM, audit logging. │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **6. Growth Scale**      │ • Flat vs Hypergrowth (requires scale curves).   │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

---

## 3. Hardware & Streaming Latency Physics Hierarchy

All streaming and pipeline SLA commitments are constrained by physical hardware limits:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ HARDWARE & NETWORK LATENCY FLOORS (PHYSICAL LIMITS)                         │
├──────────────────────────────┬────────────────┬─────────────────────────────┤
│ Operation / Boundary         │ Latency Range  │ Architectural Implication   │
├──────────────────────────────┼────────────────┼─────────────────────────────┤
│ **L1 CPU Cache Reference**   │ ~1 ns          │ In-memory compute cycle     │
│ **RAM Access (In-Memory)**   │ ~100 ns        │ Flink / Spark state storage │
│ **NVMe Solid-State `fsync`** │ 0.05 – 1.0 ms  │ Write-ahead log / Bookies   │
│ **Intra-AZ Network RTT**     │ 0.2 – 1.0 ms   │ Single-AZ clustered brokers │
│ **Inter-AZ Network RTT**     │ 1.0 – 4.0 ms   │ Multi-AZ sync replication   │
│ **Schema Registry Lookup**   │ 1.0 – 5.0 ms   │ Cached message validation   │
│ **HDD Seek & `fsync`**       │ 5.0 – 20.0 ms  │ Destroys low-latency budget │
│ **Cross-Region Network RTT** │ 30 – 200+ ms   │ Eliminates sub-100ms sync   │
└──────────────────────────────┴────────────────┴─────────────────────────────┘
```

> **The Cross-Region Law:** Cross-region fiber RTT is $30\text{–}200\text{ ms}$. Therefore, **synchronous sub-100ms cross-region writes are physically impossible**. Cross-region DR must always use asynchronous replication (e.g. S3 RTC).

---

## 4. Layered Reliability Model & Error Budget Governance

```
       [ Real System Telemetry (SLI) ]
                     │
         Measured against internal goal
                     ▼
       [ Internal Target: SLO = 99.5% ]
                     │  Safety Margin / Error Budget Buffer
                     ▼
       [ External Contract: SLA = 98.0% ] ──► Financial penalty if breached
```

### 4.1 Error Budget Calculations (30-Day Rolling Window)
$$\text{Error Budget} = 100\% - \text{SLO Target}$$

* **99.0% SLO:** Error Budget = **7.2 hours** (432 minutes) of downtime/delay.
* **99.5% SLO:** Error Budget = **3.6 hours** (216 minutes) of downtime/delay.
* **99.9% SLO:** Error Budget = **43.2 minutes** of downtime/delay.
* **99.99% SLO:** Error Budget = **4.32 minutes** of downtime/delay.

### 4.2 Error Budget Burn Rate Policy
* **Healthy Budget (> 50% remaining):** Teams innovate and refactor freely.
* **Burn Alert (> 10% burned in 24h):** Automated notification to investigate upstream schema drift.
* **Depleted Budget (< 0% remaining):** **Hard Feature Freeze**. Engineering capacity is strictly redirected to pipeline reliability and data contracts.

### 4.3 Tail Latency Amplification in Fan-Out Data Systems
In sharded systems where a query issues $N$ parallel sub-queries:
$$P(\text{System Latency} > T) = 1 - (1 - P(\text{Single Service Latency} > T))^N$$
For $N = 50$ parallel partitions, a $1\%$ single-task tail delay amplifies to **$39.5\%$ end-to-end delay**.

---

## 5. The 7 Data Quality SLA Dimensions & Medallion Mapping

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 7 DATA QUALITY SLA DIMENSIONS ACROSS MEDALLION LAYERS                       │
├────────────────────┬──────────────────────────────────┬─────────────────────┤
│ Dimension          │ Quantitative Measurement         │ Medallion Layer     │
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **1. Freshness**   │ Delay between source commit &    │ **Gold Layer**      │
│                    │ warehouse availability           │ (Daily by 06:00 AM) │
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **2. Completeness**│ Null percentage on key columns   │ **Silver Layer**    │
│                    │ (e.g. `user_id` has 0% nulls)    │ (Great Expectations)│
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **3. Validity**    │ Format/regex matching rate       │ **Silver Layer**    │
│                    │ (e.g. 100% valid ISO dates)      │ (Contract Validator)│
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **4. Uniqueness**  │ Duplicate primary key count      │ **Silver / Gold**   │
│                    │ (0 duplicate transaction IDs)    │ (Deduplication)     │
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **5. Volume**      │ Row count vs 14-day median       │ **Bronze Layer**    │
│                    │ (Bound within $\pm 20\%$)        │ (Dead-Letter Guard) │
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **6. Schema**      │ Unannounced structural breaks    │ **Ingestion Layer** │
│    **Stability**   │ (Additive-only schema evolution) │ (Schema Registry)   │
├────────────────────┼──────────────────────────────────┼─────────────────────┤
│ **7. Distribution**│ Statistical mean/variance drift  │ **Gold Feature Store│
│    **Drift**       │ (Within 3 standard deviations)   │ (ML Data Marts)     │
└────────────────────┴──────────────────────────────────┴─────────────────────┘
```

---

## 6. Data Platform Unit Economics & FinOps Governance

$$\text{Pipeline Unit Cost} = \frac{\text{Compute (Glue/EMR)} + \text{Storage (S3/Glacier)} + \text{Networking (VPC Endpoints)}}{\text{Delivered Business Units}}$$

### 6.1 Key Unit Economics Metrics:
* **Cost per GB Processed ($\$/\text{GB}$):** Evaluates transformation engine efficiency (Glue vs dbt-on-Athena).
* **Cost per Pipeline Run ($\$/\text{Run}$):** Detects cluster sizing regressions.
* **Cost per Analytical Query ($\$/\text{Query}$):** Governs data warehouse usage.

### 6.2 The 4 Classic Cloud Bill Traps Mitigated by MinusOps:
1. **Cross-Region Egress Trap ($12.8K/mo):** Compute in Oregon querying databases in Virginia. *(Mitigated by region co-location check).*
2. **Auto-Scaling Churn Death Loop ($18.4K/mo):** Memory leaks restarting instances in 10-minute billable increments. *(Mitigated by hard max-instance caps and restart alarms).*
3. **Viral Asset Egress Shock ($2.6K overnight):** Direct S3 downloads without a CDN. *(Mitigated by CloudFront / PrivateLink routing).*
4. **Forgotten Dev Environments ($8.9K/mo):** Unused 24/7 staging clusters. *(Mitigated by `AutoShutdown` tags and off-hours schedulers).*

---

## 7. Grilling Engine Refinement (`.agents/skills/grill-me/SKILL.md`)

The `grill-me` skill is updated with the **Fast-Path vs Fallback Model**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ REFINED ARCHITECTURAL INTERROGATION FLOW                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ FAST-PATH INTERROGATION:                                                    │
│ "Is there an expected monthly cost target (ceiling, target, or undefined)?  │
│  And what latency is required, and what specific business action depends on │
│  that latency?"                                                             │
│                                                                             │
│ Standard Latency Archetypes:                                                │
│ • Daily / Weekly Batch    ──► S3 + dbt/Athena/Glue (Lowest cost, $0 idle)   │
│ • Hourly Scheduled Sync   ──► Step Functions + Glue Spark (Standard lake)   │
│ • Micro-Batch (5–15 mins) ──► EventBridge + S3 trigger + EMR Serverless     │
│ • Sub-Second Streaming    ──► Kinesis/MSK + Flink (24/7 dedicated compute) │
│                                                                             │
│ FALLBACK POLICY (When budget/latency is unknown):                           │
│ "Default to the cheapest serverless same-day architecture ($0 idle cost),   │
│  instrument unit metrics ($/GB) on Day 1, and pin hard circuit breakers     │
│  (120m Glue timeout, 10 GiB Athena cutoff)."                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Functional Requirements (FR) for Coding Agent

* **FR-17 (FinOps Unit Economics Estimator):** `core/cost/budget_calculator.py` computes estimated `$/GB` and `$/Run` metrics in addition to monthly totals.
* **FR-18 (Grilling Skill Graded Latency Trees):** `.agents/skills/grill-me/SKILL.md` incorporates the 6 context variables, the "Why" latency probe, and the Fallback policy.
* **FR-19 (Error Budget Burn Calculator):** `core/reporting/finops_agent.py` supports calculating monthly error budget burn based on pipeline run success rates.
* **FR-20 (Anti-Egress Architecture Rules):** `core/reporting/optimize_analyzer.py` scans for cross-region data transfer risks (`COST-04`) and un-endpointed S3 traffic (`COST-05`).
* **FR-21 (Data Quality SLA Assertion Suite):** Test harness validating the 7 data quality dimensions across Medallion zones.

---

## 9. Verification & Acceptance Criteria

* [ ] `tests/test_finops_unit_economics.py`: Validates calculation of `$/GB` and `$/Run` under 1x, 5x, and 10x scale curves.
* [ ] `tests/test_latency_physics_rules.py`: Asserts fail-closed linting on any architecture proposing synchronous sub-100ms cross-region writes.
* [ ] `tests/test_grill_me_finops_expansion.py`: Validates `grill-me` skill documentation formatting, contiguous pillar numbering, and fallback logic.
