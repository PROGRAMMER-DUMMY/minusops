# Terraform & AWS CLI Setup Guide

This guide provides step-by-step instructions on how to install **Terraform CLI** and **AWS CLI** on Windows, configure your AWS credentials, and connect your local workspace to the AWS provider.

---

## 📋 Table of Contents
1. [Installation Guide](#1-installation-guide)
2. [AWS CLI Configuration](#2-aws-cli-configuration)
3. [Connecting Terraform to AWS](#3-connecting-terraform-to-aws)
4. [Useful Terraform Commands](#4-useful-terraform-commands)
5. [Troubleshooting & Tips](#5-troubleshooting--tips)

---

## 1. Installation Guide

On Windows, the easiest and safest way to install both tools is using the **Windows Package Manager (`winget`)**.

### Step 1: Install Terraform
Open your terminal (PowerShell or Command Prompt) and run:
```powershell
winget install --id Hashicorp.Terraform --accept-source-agreements --accept-package-agreements
```

### Step 2: Install AWS CLI
In the same terminal, run:
```powershell
winget install --id Amazon.AWSCLI --accept-source-agreements --accept-package-agreements
```
> ⚠️ **Note:** Watch out for a Windows User Account Control (UAC) pop-up asking for administrator permissions to install AWS CLI. Click **Yes** to allow it.

### Step 3: Refresh Your Terminal
To make sure your system loads the new environment variables (`PATH`), **restart your terminal or IDE** (like PyCharm/VS Code).
Alternatively, refresh the environment variables in your current PowerShell session with:
```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
```

### Step 4: Verify Installation
Verify both tools are working by checking their versions:
```powershell
terraform -version
aws --version
```

---

## 2. AWS CLI Configuration

To allow Terraform to build resources in your AWS account, you must configure your AWS credentials locally.

1. Generate your access keys in the **AWS Console**:
   * Go to **IAM** -> **Users** -> Click on your User -> **Security credentials** -> **Create access key**.
2. In your terminal, run:
   ```bash
   aws configure
   ```
3. Enter the requested information:
   * **AWS Access Key ID**: `YOUR_ACCESS_KEY`
   * **AWS Secret Access Key**: `YOUR_SECRET_KEY`
   * **Default region name**: `us-east-1` (or your preferred region)
   * **Default output format**: `json`

This creates a secure credentials file at `~/.aws/credentials` which Terraform automatically reads.

---

## 3. Connecting Terraform to AWS

In your Terraform files, configure the AWS provider to connect to your account.

Create a file named `terraform.tf` and declare the AWS provider:
```hcl
# 1. Terraform Settings Block
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.52.0" # Or current stable version
    }
  }
}

# 2. Provider Block
provider "aws" {
  region = "us-east-1" # Region to deploy resources in
}
```

Because you ran `aws configure`, Terraform will automatically use those credentials when running commands.

---

## 4. Useful Terraform Commands

Run these commands in your project directory:

* **`terraform init`**: Initializes the working directory, downloads the AWS provider, and loads modules.
* **`terraform fmt`**: Rewrites all configuration files in a clean, standard format.
* **`terraform validate`**: Checks whether your configuration is syntactically valid and internally consistent.
* **`terraform plan`**: Shows what changes Terraform will make to your infrastructure (Read-only, completely safe).
* **`terraform apply`**: Applies the changes to your AWS account (Creates/modifies resources).
* **`terraform destroy`**: Deletes all resources managed by this project (Best practice to prevent unwanted AWS charges).

---

## 5. Troubleshooting & Tips

### SSH Key Permissions on Windows
If you use a local key pair (e.g. `terra-key`) and Windows complains about "too open" permissions when running SSH:
```powershell
# Reset inherited permissions
icacls .\terra-key /reset

# Restrict access only to your user
icacls .\terra-key /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

### Allowlisting Your Public IP
For security groups, never expose SSH (port 22) to the whole internet (`0.0.0.0/0`). Find your public IP address (e.g., using `curl icanhazip.com`) and restrict access to:
```hcl
ingress {
  from_port   = 22
  to_port     = 22
  protocol    = "tcp"
  cidr_blocks = ["<YOUR_PUBLIC_IP>/32"]
}
```
