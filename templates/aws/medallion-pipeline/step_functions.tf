resource "aws_sfn_state_machine" "pipeline_orchestrator" {
  name     = "MedallionDataPipeline-${var.environment}"
  role_arn = aws_iam_role.sfn_role.arn

  definition = jsonencode({
    Comment = "Orchestrates the Medallion Data Pipeline (Bronze -> Silver -> Gold)"
    StartAt = "ProcessBronzeToSilver"
    States = {
      ProcessBronzeToSilver = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.bronze_to_silver.name
        }
        Next = "ProcessSilverToGold"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "PipelineFailed"
          }
        ]
      }
      ProcessSilverToGold = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.silver_to_gold.name
        }
        Next = "RunGoldCrawler"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "PipelineFailed"
          }
        ]
      }
      RunGoldCrawler = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:startCrawler"
        Parameters = {
          Name = aws_glue_crawler.gold_crawler.name
        }
        Next = "PipelineSucceeded"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "PipelineFailed"
          }
        ]
      }
      PipelineSucceeded = {
        Type = "Succeed"
      }
      PipelineFailed = {
        Type  = "Fail"
        Cause = "Pipeline failed due to a job execution or metadata crawler failure."
        Error = "PipelineExecutionError"
      }
    }
  })
}
