# Module: orchestrator-stepfunctions
# Serverless orchestration via a Step Functions state machine + a least-privilege role.
# The caller passes the state-machine definition and the task role ARNs it may invoke.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "definition_json" {
  type        = string
  description = "Amazon States Language JSON for the workflow."
}

variable "task_role_arns" {
  type        = list(string)
  default     = []
  description = "Resource ARNs the state machine may act on (e.g. Glue job ARNs)."
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-sfn-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "InvokeGlue"
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
    resources = length(var.task_role_arns) > 0 ? var.task_role_arns : ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${var.name_prefix}-sfn"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "this" {
  name       = "${var.name_prefix}-workflow"
  role_arn   = aws_iam_role.sfn.arn
  definition = var.definition_json
  tags       = var.tags
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.this.arn
}

output "role_arn" {
  value = aws_iam_role.sfn.arn
}
