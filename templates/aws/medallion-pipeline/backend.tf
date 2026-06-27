# Remote state — S3 with native locking (use_lockfile, Terraform >= 1.10).
# Per-client values (bucket / key / region) are PARTIAL config supplied at init:
#
#   terraform init -backend-config=backend.hcl
#
# See backend.hcl.example. The state bucket is created by bootstrap/aws/.
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
