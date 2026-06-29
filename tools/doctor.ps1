# Windows Environment Doctor Script for Terraform & AWS CLI
# Run this script using: powershell -ExecutionPolicy Bypass -File .\doctor.ps1

$ErrorActionPreference = "Stop"

function Write-Header ($text) {
    Write-Host "`n=== $text ===" -ForegroundColor Cyan
}

function Write-Success ($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function Write-WarningMsg ($text) {
    Write-Host "[WARN] $text" -ForegroundColor Yellow
}

function Write-Failure ($text, $suggestion) {
    Write-Host "[ERR] $text" -ForegroundColor Red
    if ($suggestion) {
        Write-Host "    Suggestion: $suggestion" -ForegroundColor Gray
    }
}

Write-Header "Starting Environment Diagnostics"
Write-Host "Local Time: $(Get-Date)"
Write-Host "OS: $((Get-CimInstance Win32_OperatingSystem).Caption)"

# -------------------------------------------------------------
# 1. Check Terraform Installation
# -------------------------------------------------------------
Write-Header "Checking Terraform CLI"
$tfCommand = Get-Command terraform -ErrorAction SilentlyContinue
if ($tfCommand) {
    $tfVersionInfo = & terraform -version | Out-String
    $tfVersion = ($tfVersionInfo -split "`n")[0].Trim()
    Write-Success "Terraform is installed and recognized."
    Write-Host "    Version: $tfVersion" -ForegroundColor Gray
    Write-Host "    Path: $($tfCommand.Source)" -ForegroundColor Gray
} else {
    $wingetPath = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
    $tfSearch = Get-ChildItem -Path $wingetPath -Filter "*Terraform*" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    
    if ($tfSearch) {
        Write-WarningMsg "Terraform is installed in '$($tfSearch.DirectoryName)' but not recognized in your current session's PATH."
        Write-Host "    Suggestion: Restart your terminal/IDE or refresh your PATH environment variable." -ForegroundColor Gray
    } else {
        Write-Failure "Terraform CLI is not installed or not in PATH." "Run: winget install --id Hashicorp.Terraform"
    }
}

# -------------------------------------------------------------
# 2. Check AWS CLI Installation
# -------------------------------------------------------------
Write-Header "Checking AWS CLI"
$awsCommand = Get-Command aws -ErrorAction SilentlyContinue
if ($awsCommand) {
    $awsVersion = & aws --version 2>&1 | Out-String
    Write-Success "AWS CLI is installed and recognized."
    Write-Host "    Version: $($awsVersion.Trim())" -ForegroundColor Gray
    Write-Host "    Path: $($awsCommand.Source)" -ForegroundColor Gray
} else {
    $awsStandardPath = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
    if (Test-Path $awsStandardPath) {
        Write-WarningMsg "AWS CLI is installed at '$awsStandardPath' but not recognized in your current session's PATH."
        Write-Host "    Suggestion: Restart your terminal/IDE or refresh your PATH." -ForegroundColor Gray
    } else {
        Write-Failure "AWS CLI is not installed or not in PATH." "Run: winget install --id Amazon.AWSCLI"
    }
}

# -------------------------------------------------------------
# 3. Check AWS Connection & Credentials
# -------------------------------------------------------------
Write-Header "Checking AWS Provider Credentials"
if ($awsCommand) {
    try {
        $identity = & aws sts get-caller-identity --query "[Account, Arn]" --output text 2>$null
        if ($LASTEXITCODE -eq 0) {
            $parts = $identity -split "`t"
            Write-Success "AWS credentials are configured and valid."
            Write-Host "    Account ID: $($parts[0].Trim())" -ForegroundColor Gray
            Write-Host "    User/Role ARN: $($parts[1].Trim())" -ForegroundColor Gray
        } else {
            Write-WarningMsg "AWS CLI is installed, but no valid credentials could be found or they have expired."
            Write-Host "    Suggestion: Run 'aws configure' to set your credentials." -ForegroundColor Gray
        }
    } catch {
        Write-WarningMsg "Could not query AWS STS. Check your internet connection or credentials."
    }
} else {
    Write-WarningMsg "Skipped AWS credential verification because AWS CLI is not available."
}

# -------------------------------------------------------------
# 4. Check Python (the engine runtime)
# -------------------------------------------------------------
Write-Header "Checking Python Runtime"
$pyCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCommand) { $pyCommand = Get-Command python3 -ErrorAction SilentlyContinue }
if ($pyCommand) {
    $pyVersion = & $pyCommand.Source --version 2>&1 | Out-String
    Write-Success "Python is installed and recognized."
    Write-Host "    Version: $($pyVersion.Trim())" -ForegroundColor Gray
    Write-Host "    Path: $($pyCommand.Source)" -ForegroundColor Gray
} else {
    Write-Failure "Python is not installed or not in PATH." "Install Python 3.10+ from https://www.python.org/downloads/"
}

Write-Header "Diagnostics Complete"
