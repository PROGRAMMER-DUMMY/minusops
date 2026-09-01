package precinct.authz

import rego.v1

# Default deny
default allow := false

# High risk tools requiring step-up
high_risk_tools := {
    "terraform_apply",
    "terraform_destroy",
    "modify_patient_record",
    "delete_patient_record",
    "execute_payment",
    "drop_table",
    "modify_security_group"
}

# Read-only data tools
data_tools := {
    "query_domain_ard",
    "query_solution_ard",
    "search_knowledge_base",
    "get_schema_metadata",
    "get_lineage_graph",
    "read_audit_logs"
}

# Decision calculation
decision := "allow" if {
    allow_data_read
} else := "allow" if {
    allow_verified_step_up
} else := "step_up_required" if {
    input.tool_name in high_risk_tools
    not input.step_up_verified
} else := "deny"

# Rule: Allow data reads for valid analyst/operator agents
allow_data_read if {
    input.tool_name in data_tools
    glob.match("spiffe://enterprise.local/agents/*", [], input.caller_spiffe_id)
}

# Rule: Allow high risk operations if step up verified and role is operator/architect
allow_verified_step_up if {
    input.tool_name in high_risk_tools
    input.step_up_verified == true
    glob.match("spiffe://enterprise.local/agents/operator/*", [], input.caller_spiffe_id)
}
