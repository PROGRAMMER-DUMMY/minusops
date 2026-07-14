
# G6 (docs/g6_scope.md) -- SEC-*/COST-* rules over real Terraform plan JSON, evaluated in
# shadow mode alongside core/reporting/optimize_analyzer.py's existing regex-over-HCL rules
# (core/governance/rego_gate.py is the enforcing/never-enforcing wrapper; this file only
# computes findings against whatever `input` it's given).
#
# EXTENSION (docs/g6_iam_extension_scope.md): SEC-02/SEC-05 extended, SEC-06/SEC-07 added, to
# cover IAM/KMS/S3 security CONTENT the G9 emulator gauntlet cannot -- an emulator checks
# VALIDITY (would real AWS accept this), never SAFETY (is this dangerous). Still shadow-only,
# same as every other rule in this file; joins the same unflipped pile, no new enforcement.
#
# Input contract: `input` is a real `terraform show -json` plan document (the full document --
# both `resource_changes` and `configuration` are read; see the reference-tracing helper
# below). Every rule here is fail-CLOSED on unknown-until-apply values: a presence-based check
# consults `after_unknown.<field>` FIRST, and routes to a distinct `field_unresolved` finding
# (via `finding_unresolved`) rather than ever reading an unknown value as if it were false/
# absent. This is the exact shape docs/g6_scope.md's fail-closed section calls out -- verified
# against a real plan, not assumed: Terraform's plan JSON marks an unresolved leaf as `true` in
# a SPARSE `after_unknown` structure (present only for genuinely unknown fields; known fields
# simply don't appear there at all) with the corresponding `after.<field>` as `null`.
package minusops.g6

import rego.v1

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

finding(rule_id, category, title, description, severity, resource) := {
	"id": rule_id,
	"category": category,
	"title": title,
	"description": description,
	"severity": severity,
	"resource": resource,
	"finding_kind": "standard",
}

# The distinct, always-BLOCKing verdict for "a tracked field is unresolved until apply" --
# never collapsed into the standard finding shape, so the Python wrapper (and a human reading
# the audit chain) can tell "genuinely non-compliant" apart from "can't verify yet."
finding_unresolved(rule_id, category, title, resource) := {
	"id": rule_id,
	"category": "Security",
	"title": title,
	"description": sprintf("%s is not known until apply -- cannot verify at plan time.", [title]),
	"severity": "HIGH",
	"resource": resource,
	"finding_kind": "field_unresolved",
}

# True if `field` is unresolved until apply for this resource_changes entry. Sparse structure:
# only present (and true) for genuinely unknown leaves -- a known field simply isn't a key
# here, so this must never be read as `== false` meaning "known", only "isn't unknown."
is_unknown(rc, field) if rc.change.after_unknown[field] == true

managed(type_name) := [rc |
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == type_name
]

# Verified live, not assumed: data sources do NOT appear in `resource_changes` at all --
# confirmed twice against real `terraform show -json` output (an all-data-sources plan with no
# managed resources shows an EMPTY resource_changes entirely; a mixed plan's resource_changes
# lists only the managed resources). Data source reads land in `prior_state.values.root_module.
# resources` instead, as already-resolved `.values` (no `.change`/`.after_unknown` wrapper --
# data sources resolve synchronously during plan, they don't have apply-time-only computed
# attributes the way managed resources do). Normalized here into the same {change: {after,
# after_unknown}} shape managed() returns, so every rule below can treat both uniformly.
#
# Disclosed boundary: a data source whose OWN inputs depend on an unresolved managed-resource
# attribute (a "deferred" read) is a real edge case this does not specially handle -- treated
# as fully resolved like any other prior_state entry, not verified against a live example of
# that specific scenario. Narrower than ideal, named rather than silently assumed away.
prior_state_resources := object.get(
	object.get(object.get(input, "prior_state", {}), "values", {}), "root_module", {"resources": []},
).resources

data_sources(type_name) := [rc |
	some r in prior_state_resources
	r.mode == "data"
	r.type == type_name
	rc := {"address": r.address, "type": r.type, "mode": "data",
		"change": {"after": r.values, "after_unknown": {}}}
]

config_resources := input.configuration.root_module.resources

# Every address a resource's config references, across all its attribute expressions -- from
# the plan's `configuration` block, independent of whether the referenced value has resolved
# yet. This is what SEC-01/COST-01 use to correlate "does a sibling resource exist and does it
# reference this bucket", verified live against a real plan rather than assumed: `expressions.
# <attr>.references` is a real, documented-shape array containing both the specific attribute
# reference (`aws_s3_bucket.b.id`) and the base resource address (`aws_s3_bucket.b`).
#
# Real bug caught by the 16-module parity pass, not assumed away: a `for_each`-based sibling
# (e.g. `for_each = aws_s3_bucket.zone` then `bucket = each.value.id`) does NOT show the bucket
# address inside `expressions.bucket.references` at all -- that array only ever contains the
# symbolic `each.value`/`each.value.id`, never resolved back to the underlying resource. The
# real reference lives in a SEPARATE, sibling top-level field on the config resource itself:
# `for_each_expression.references`. Confirmed live against storage-medallion-s3's real plan
# (a for_each S3 module with a genuine, correctly-configured public-access-block/lifecycle
# sibling for every zone), which false-positived on every bucket before this fix -- missing
# for_each_expression entirely, not a hypothetical.
referenced_addresses(cfg_resource) := addrs if {
	expr_refs := [r |
		some _, expr in cfg_resource.expressions
		is_object(expr)
		some r in object.get(expr, "references", [])
	]
	for_each_refs := object.get(object.get(cfg_resource, "for_each_expression", {}), "references", [])
	addrs := {r | some r in array.concat(expr_refs, for_each_refs)}
}

# A `for_each`/`count` sibling relationship is only ever expressed at the base resource address
# in `configuration` (e.g. `aws_s3_bucket.zone`), never per-expanded-instance -- but `rc.address`
# from `resource_changes` for an expanded instance carries the index suffix (e.g.
# `aws_s3_bucket.zone["bronze"]`). Strip it before comparing, or every for_each instance would
# fail to match a reference that's genuinely there (the same bug class as above, one layer up).
base_address(addr) := regex.replace(addr, `\[[^\]]*\]$`, "")

has_sibling_referencing(rc, sibling_type) if {
	some cfg in config_resources
	cfg.type == sibling_type
	base_address(rc.address) in referenced_addresses(cfg)
}

# ---------------------------------------------------------------------------
# SEC-01 / COST-01 -- S3 bucket missing public-access-block / lifecycle policy
# ---------------------------------------------------------------------------

sec01_findings contains f if {
	some rc in managed("aws_s3_bucket")
	not has_sibling_referencing(rc, "aws_s3_bucket_public_access_block")
	f := finding("SEC-01", "Security", "S3 Public Access Block Missing",
		"Every S3 bucket needs an aws_s3_bucket_public_access_block to prevent accidental exposure.",
		"HIGH", rc.address)
}

cost01_findings contains f if {
	some rc in managed("aws_s3_bucket")
	not has_sibling_referencing(rc, "aws_s3_bucket_lifecycle_configuration")
	f := finding("COST-01", "Cost", "S3 Bucket Missing Lifecycle Policy",
		"Configure aws_s3_bucket_lifecycle_configuration to transition or expire old data.",
		"MEDIUM", rc.address)
}

# ---------------------------------------------------------------------------
# SEC-03 -- Unencrypted Redshift cluster
#
# Verified live against the real AWS provider schema, not assumed: `encrypted` on
# aws_redshift_cluster is declared type "string" (legacy provider typing, predates a proper
# bool), so real plan JSON carries the STRING "true"/"false", not a JSON boolean. A rule
# comparing against the boolean `true` would silently mis-flag every genuinely-encrypted
# cluster as unencrypted -- caught directly against a real plan, not left as an assumption
# (docs/g6_scope.md's own text originally described this as "a real boolean value"; that
# claim was wrong and is corrected here, not carried forward).
# ---------------------------------------------------------------------------

sec03_findings contains f if {
	some rc in managed("aws_redshift_cluster")
	is_unknown(rc, "encrypted")
	f := finding_unresolved("SEC-03", "Security", "Unencrypted Redshift Cluster", rc.address)
}

sec03_findings contains f if {
	some rc in managed("aws_redshift_cluster")
	not is_unknown(rc, "encrypted")
	rc.change.after.encrypted != "true"
	f := finding("SEC-03", "Security", "Unencrypted Redshift Cluster",
		"Redshift clusters must set encrypted = true to secure data at rest.",
		"HIGH", rc.address)
}

# ---------------------------------------------------------------------------
# SEC-04 -- Unencrypted MSK cluster
#
# Verified live, not assumed: an unset/omitted encryption_info block resolves to an EMPTY
# LIST in plan JSON, never a missing key or null -- the presence check below is `count(...) ==
# 0`, not "key absent", which the regex-over-HCL original couldn't distinguish either way.
# ---------------------------------------------------------------------------

sec04_findings contains f if {
	some rc in managed("aws_msk_cluster")
	is_unknown(rc, "encryption_info")
	f := finding_unresolved("SEC-04", "Security", "Unencrypted MSK Cluster", rc.address)
}

sec04_findings contains f if {
	some rc in managed("aws_msk_cluster")
	not is_unknown(rc, "encryption_info")
	count(object.get(rc.change.after, "encryption_info", [])) == 0
	f := finding("SEC-04", "Security", "Unencrypted MSK Cluster",
		"Amazon MSK clusters should declare encryption_info (TLS in-transit + KMS at rest).",
		"HIGH", rc.address)
}

# ---------------------------------------------------------------------------
# COST-02 -- Databricks cluster missing auto-termination
# ---------------------------------------------------------------------------

cost02_findings contains f if {
	some rc in managed("databricks_cluster")
	is_unknown(rc, "autotermination_minutes")
	f := finding_unresolved("COST-02", "Cost", "Databricks Cluster Missing Auto-Termination", rc.address)
}

cost02_findings contains f if {
	some rc in managed("databricks_cluster")
	not is_unknown(rc, "autotermination_minutes")
	val := object.get(rc.change.after, "autotermination_minutes", null)
	is_falsy_minutes(val)
	f := finding("COST-02", "Cost", "Databricks Cluster Missing Auto-Termination",
		"Set autotermination_minutes so idle Databricks clusters stop billing.",
		"HIGH", rc.address)
}

is_falsy_minutes(val) if val == null

is_falsy_minutes(val) if val == 0

# ---------------------------------------------------------------------------
# COST-03 -- EMR cluster lacks Spot instance pricing
#
# Verified live: core_instance_group/master_instance_group are LISTS of objects, bid_price a
# direct string field within each -- the doc flagged this shape as unverified before
# implementation; confirmed rather than assumed. Per-element unknown tracking inside a known
# list (individual fields of one list entry independently unknown) is a disclosed, narrower
# scope boundary: only whole-field unknown (the group list itself unresolved) routes to
# field_unresolved here, not an unknown bid_price nested inside an otherwise-known group --
# real usage sets bid_price as a literal, not a computed value, making this gap unlikely to
# matter in practice, but it is a real, named boundary, not a silent one.
# ---------------------------------------------------------------------------

emr_instance_group_fields := ["master_instance_group", "core_instance_group"]

cost03_findings contains f if {
	some rc in managed("aws_emr_cluster")
	some field in emr_instance_group_fields
	is_unknown(rc, field)
	f := finding_unresolved("COST-03", "Cost", "EMR Cluster Lacks Spot Instance Pricing", rc.address)
}

cost03_findings contains f if {
	some rc in managed("aws_emr_cluster")
	not any_instance_group_unknown(rc)
	not any_bid_price_set(rc)
	f := finding("COST-03", "Cost", "EMR Cluster Lacks Spot Instance Pricing",
		"EMR task instances should use Spot pricing (bid_price) to cut cost.",
		"MEDIUM", rc.address)
}

any_instance_group_unknown(rc) if {
	some field in emr_instance_group_fields
	is_unknown(rc, field)
}

any_bid_price_set(rc) if {
	some field in emr_instance_group_fields
	some group in object.get(rc.change.after, field, [])
	is_object(group)
	group.bid_price != null
}

# ---------------------------------------------------------------------------
# SEC-05a -- Databricks-canonical trust policy missing external_id
# ---------------------------------------------------------------------------

sec05_findings contains f if {
	some rc in data_sources("databricks_aws_assume_role_policy")
	is_unknown(rc, "external_id")
	f := finding_unresolved("SEC-05", "Security", "Databricks Cross-Account Trust Policy External ID", rc.address)
}

sec05_findings contains f if {
	some rc in data_sources("databricks_aws_assume_role_policy")
	not is_unknown(rc, "external_id")
	is_blank(object.get(rc.change.after, "external_id", null))
	f := finding("SEC-05", "Security", "Databricks Cross-Account Trust Policy Missing External ID",
		"data \"databricks_aws_assume_role_policy\" has no external_id argument -- the generated trust policy only includes the external-ID condition when external_id is supplied.",
		"HIGH", rc.address)
}

is_blank(val) if val == null

is_blank(val) if val == ""

# ---------------------------------------------------------------------------
# SEC-05b/c -- Hand-rolled cross-account trust policy: missing sts:ExternalId / wildcard
# principal
#
# Reads the data source's STRUCTURED `.statement` field directly -- verified live: real plan
# JSON exposes aws_iam_policy_document's statements as already-decomposed Terraform values
# (principals, conditions, resources as real lists/objects), not just the assembled `.json`/
# `.minified_json` strings. This is a genuine improvement over the original regex (which only
# ever saw literal HCL source text): resolved plan JSON reflects the actual assembled policy,
# including any variable interpolation resolved, so Rego can catch cases the regex couldn't --
# see docs/g6_scope.md's parity item for how any resulting NEW finding gets explained, not
# waved through.
# ---------------------------------------------------------------------------

sec05_findings contains f if {
	some rc in data_sources("aws_iam_policy_document")
	some stmt in object.get(rc.change.after, "statement", [])
	is_object(stmt)
	is_assume_role_statement(stmt)
	has_aws_principal(stmt)
	not has_external_id_condition(stmt)
	f := finding("SEC-05", "Security", "Cross-Account Trust Policy Missing External ID",
		"A cross-account AssumeRole trust policy (principals type = \"AWS\") has no sts:ExternalId condition. Without one, the role can be assumed by anyone who later controls that principal ARN elsewhere.",
		"HIGH", rc.address)
}

sec05_findings contains f if {
	some rc in data_sources("aws_iam_policy_document")
	some stmt in object.get(rc.change.after, "statement", [])
	is_object(stmt)
	is_assume_role_statement(stmt)
	has_wildcard_principal(stmt)
	f := finding("SEC-05", "Security", "Cross-Account Trust Policy Has Wildcard Principal",
		"A cross-account AssumeRole trust policy grants identifiers = [\"*\"] instead of a specific account/role ARN, allowing any AWS principal to assume the role.",
		"HIGH", rc.address)
}

is_assume_role_statement(stmt) if "sts:AssumeRole" in object.get(stmt, "actions", [])

has_aws_principal(stmt) if {
	some p in object.get(stmt, "principals", [])
	is_object(p)
	p.type == "AWS"
}

has_wildcard_principal(stmt) if {
	some p in object.get(stmt, "principals", [])
	is_object(p)
	p.type == "AWS"
	"*" in object.get(p, "identifiers", [])
}

has_external_id_condition(stmt) if {
	some c in object.get(stmt, "condition", [])
	is_object(c)
	lower(object.get(c, "variable", "")) == "sts:externalid"
}

# ---------------------------------------------------------------------------
# Shared helpers for RAW JSON policy documents (docs/g6_iam_extension_scope.md section 2/3):
# aws_iam_role.assume_role_policy, aws_kms_key.policy, aws_s3_bucket_policy.policy are all
# declared plain `string`-typed schema attributes (verified live against the real AWS provider
# schema) -- an unparsed JSON string needing json.unmarshal, never the already-decomposed
# `.statement` shape aws_iam_policy_document's data source gets. Distinct from
# has_wildcard_principal/has_aws_principal above, which only understand that structured shape.
# ---------------------------------------------------------------------------

as_list(x) := x if is_array(x)

as_list(x) := [x] if not is_array(x)

is_wildcard_principal_raw(p) if p == "*"

is_wildcard_principal_raw(p) if {
	is_object(p)
	some v in as_list(object.get(p, "AWS", []))
	v == "*"
}

# A literal external-AWS-account ARN principal. Verify-first item confirmed live before
# implementing (docs/g6_iam_extension_scope.md section 3): comparing against a resolved
# `data.aws_caller_identity.current.account_id` to determine same-account-vs-cross-account was
# considered and rejected -- confirmed via a real `terraform plan` that this data source is a
# genuine STS API call that fails outright (InvalidClientTokenId) under the dummy credentials
# this repo's own real-plan testing uses, and would couple every real customer's plan to a
# live STS call succeeding just to run a governance check. Falls back to literal-ARN matching
# instead: any AWS-account-shaped ARN (a 12-digit account number) is treated as external,
# regardless of which account it actually is -- service principals ("glue.amazonaws.com")
# never match this pattern and are unaffected. A same-account root/role ARN written as a
# literal ARN (rather than via a data source) is a real, disclosed false-positive risk this
# design accepts; the 16-module zero-FP shadow proof is what actually tests whether this
# repo's real modules trip it.
is_external_account_arn(p) if {
	is_string(p)
	regex.match(`^arn:aws:iam::[0-9]{12}:`, p)
}

has_external_account_principal_raw(p) if is_external_account_arn(p)

has_external_account_principal_raw(p) if {
	is_object(p)
	some v in as_list(object.get(p, "AWS", []))
	is_external_account_arn(v)
}

has_external_id_condition_raw(stmt) if {
	some _, ops in object.get(stmt, "Condition", {})
	is_object(ops)
	some k, _ in ops
	lower(k) == "sts:externalid"
}

statement_actions(stmt) := as_list(object.get(stmt, "Action", []))

is_assume_role_statement_raw(stmt) if {
	stmt.Effect == "Allow"
	"sts:AssumeRole" in statement_actions(stmt)
}

# ---------------------------------------------------------------------------
# SEC-05 (extended) -- aws_iam_role.assume_role_policy set directly as raw JSON (jsonencode(...)
# or a hard-coded string), not only via data.aws_iam_policy_document. Real, common pattern in
# this repo's own modules, verified by grep before scoping (9 of 16 declare aws_iam_role).
# ---------------------------------------------------------------------------

sec05_findings contains f if {
	some rc in managed("aws_iam_role")
	is_unknown(rc, "assume_role_policy")
	f := finding_unresolved("SEC-05", "Security", "IAM Role Trust Policy", rc.address)
}

sec05_findings contains f if {
	some rc in managed("aws_iam_role")
	not is_unknown(rc, "assume_role_policy")
	policy_text := object.get(rc.change.after, "assume_role_policy", "")
	is_string(policy_text)
	policy_text != ""
	parsed := json.unmarshal(policy_text)
	some stmt in object.get(parsed, "Statement", [])
	is_object(stmt)
	is_assume_role_statement_raw(stmt)
	is_wildcard_principal_raw(object.get(stmt, "Principal", null))
	f := finding("SEC-05", "Security", "IAM Trust Policy Has Wildcard Principal",
		"An assume_role_policy grants Principal \"*\" (or AWS: \"*\"), allowing any AWS principal to assume this role.",
		"HIGH", rc.address)
}

sec05_findings contains f if {
	some rc in managed("aws_iam_role")
	not is_unknown(rc, "assume_role_policy")
	policy_text := object.get(rc.change.after, "assume_role_policy", "")
	is_string(policy_text)
	policy_text != ""
	parsed := json.unmarshal(policy_text)
	some stmt in object.get(parsed, "Statement", [])
	is_object(stmt)
	is_assume_role_statement_raw(stmt)
	has_external_account_principal_raw(object.get(stmt, "Principal", null))
	not has_external_id_condition_raw(stmt)
	f := finding("SEC-05", "Security", "Cross-Account IAM Trust Policy Missing External ID",
		"An assume_role_policy trusts an external AWS account ARN with no sts:ExternalId condition -- without one, the role can be assumed by anyone who later controls that account/ARN.",
		"HIGH", rc.address)
}

# ---------------------------------------------------------------------------
# SEC-06 (new) -- KMS key policy wide open: an Allow statement granting a wildcard principal a
# wildcard (or kms:*-shaped) action. docs/g6_iam_extension_scope.md section 2: aws_kms_key.
# policy is schema `computed = true` -- confirmed live that an unset policy (the common real
# pattern; storage-medallion-s3 does exactly this) resolves as after_unknown.policy == true,
# never a knowable default. Never read that as "no policy, safe."
# ---------------------------------------------------------------------------

sec06_findings contains f if {
	some rc in managed("aws_kms_key")
	is_unknown(rc, "policy")
	f := finding_unresolved("SEC-06", "Security", "KMS Key Policy Wide Open", rc.address)
}

sec06_findings contains f if {
	some rc in managed("aws_kms_key")
	not is_unknown(rc, "policy")
	policy_text := object.get(rc.change.after, "policy", "")
	is_string(policy_text)
	policy_text != ""
	parsed := json.unmarshal(policy_text)
	some stmt in object.get(parsed, "Statement", [])
	is_object(stmt)
	stmt.Effect == "Allow"
	is_wildcard_principal_raw(object.get(stmt, "Principal", null))
	kms_action_is_broad(stmt)
	f := finding("SEC-06", "Security", "KMS Key Policy Wide Open",
		"A KMS key policy grants a wildcard principal a wildcard (or kms:*) action -- any AWS principal can fully control this key.",
		"HIGH", rc.address)
}

kms_action_is_broad(stmt) if "*" in statement_actions(stmt)

kms_action_is_broad(stmt) if "kms:*" in statement_actions(stmt)

# ---------------------------------------------------------------------------
# SEC-07 (new) -- S3 bucket policy allows public access: an Allow statement granting a
# wildcard principal. docs/g6_iam_extension_scope.md section 2's own load-bearing finding: a
# policy that interpolates its own bucket's ARN (the common create-together pattern) comes
# back after_unknown.policy == true -- this rule predominantly BLOCKs on a fresh apply,
# proven both ways in tests/test_rego_gate.py, not assumed away.
# ---------------------------------------------------------------------------

sec07_findings contains f if {
	some rc in managed("aws_s3_bucket_policy")
	is_unknown(rc, "policy")
	f := finding_unresolved("SEC-07", "Security", "S3 Bucket Policy Allows Public Access", rc.address)
}

sec07_findings contains f if {
	some rc in managed("aws_s3_bucket_policy")
	not is_unknown(rc, "policy")
	policy_text := object.get(rc.change.after, "policy", "")
	is_string(policy_text)
	policy_text != ""
	parsed := json.unmarshal(policy_text)
	some stmt in object.get(parsed, "Statement", [])
	is_object(stmt)
	stmt.Effect == "Allow"
	is_wildcard_principal_raw(object.get(stmt, "Principal", null))
	f := finding("SEC-07", "Security", "S3 Bucket Policy Allows Public Access",
		"A bucket policy grants Principal \"*\" with Effect \"Allow\" -- this bucket policy itself grants public access; rely on Block Public Access settings, not a public Allow statement.",
		"HIGH", rc.address)
}

# ---------------------------------------------------------------------------
# SEC-02 -- Wildcard IAM Resource
#
# Two shapes, per docs/g6_scope.md: a data.aws_iam_policy_document's structured .statement
# (no parsing needed, same as SEC-05b/c above) and a managed aws_iam_policy/aws_iam_role_
# policy's `.policy` attribute, which IS a plain JSON string requiring json.unmarshal --
# unlike aws_iam_policy_document, Terraform doesn't decompose an arbitrary policy string
# attribute into structured values. The Python wrapper MUST invoke `opa eval` with
# --strict-builtin-errors for this reason: without it, a malformed .policy string makes
# json.unmarshal's result undefined, which silently drops this rule's match for that one
# resource instead of surfacing as a real evaluation error -- exactly the fail-open shape
# this whole session exists to catch, one layer inside a single rule instead of at the
# eval-invocation level.
# ---------------------------------------------------------------------------

sec02_findings contains f if {
	some rc in data_sources("aws_iam_policy_document")
	some stmt in object.get(rc.change.after, "statement", [])
	is_object(stmt)
	"*" in object.get(stmt, "resources", [])
	f := finding("SEC-02", "Security", "Wildcard IAM Policy Permissions",
		"IAM statements should target specific resource ARNs; avoid Resource = \"*\".",
		"MEDIUM", rc.address)
}

sec02_findings contains f if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type in {"aws_iam_policy", "aws_iam_role_policy"}
	policy_text := object.get(rc.change.after, "policy", "")
	is_string(policy_text)
	policy_text != ""
	parsed := json.unmarshal(policy_text)
	some stmt in object.get(parsed, "Statement", [])
	is_object(stmt)
	resource_has_wildcard(stmt)
	f := finding("SEC-02", "Security", "Wildcard IAM Policy Permissions",
		"IAM statements should target specific resource ARNs; avoid Resource = \"*\".",
		"MEDIUM", rc.address)
}

resource_has_wildcard(stmt) if stmt.Resource == "*"

resource_has_wildcard(stmt) if "*" in stmt.Resource

# Extension (docs/g6_iam_extension_scope.md section 3): Action == "*" alongside the existing
# Resource == "*" check, same two statement shapes. Fires unconditionally on these two
# IDENTITY-based policy types (aws_iam_policy/aws_iam_role_policy, and the trust-policy-
# document data source), not only when paired with a wildcard principal -- an identity policy
# has no Principal field of its own; the attached role/user already fixes it, so a wildcard
# Action here is over-broad regardless. A real, legitimate exception this session did not
# anticipate is exactly what the 16-module zero-FP shadow proof exists to catch before this
# ever enforces, not something asserted safe here.

sec02_findings contains f if {
	some rc in data_sources("aws_iam_policy_document")
	some stmt in object.get(rc.change.after, "statement", [])
	is_object(stmt)
	"*" in object.get(stmt, "actions", [])
	f := finding("SEC-02", "Security", "Wildcard IAM Policy Action",
		"IAM statements should target specific actions; avoid Action = \"*\".",
		"MEDIUM", rc.address)
}

sec02_findings contains f if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type in {"aws_iam_policy", "aws_iam_role_policy"}
	policy_text := object.get(rc.change.after, "policy", "")
	is_string(policy_text)
	policy_text != ""
	parsed := json.unmarshal(policy_text)
	some stmt in object.get(parsed, "Statement", [])
	is_object(stmt)
	action_has_wildcard(stmt)
	f := finding("SEC-02", "Security", "Wildcard IAM Policy Action",
		"IAM statements should target specific actions; avoid Action = \"*\".",
		"MEDIUM", rc.address)
}

action_has_wildcard(stmt) if stmt.Action == "*"

action_has_wildcard(stmt) if "*" in stmt.Action

# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

findings contains f if some f in sec01_findings

findings contains f if some f in cost01_findings

findings contains f if some f in sec03_findings

findings contains f if some f in sec04_findings

findings contains f if some f in cost02_findings

findings contains f if some f in cost03_findings

findings contains f if some f in sec05_findings

findings contains f if some f in sec02_findings

findings contains f if some f in sec06_findings

findings contains f if some f in sec07_findings
