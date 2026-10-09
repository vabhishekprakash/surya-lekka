<#
.SYNOPSIS
Check a deployed Surya Lekka stack from the outside.

.DESCRIPTION
Prints the AWS account and Region for -Profile first and stops unless the
account is -ExpectedAccount. Then it reads the stack's outputs and its
ReadingEngine parameter and runs scripts\smoke_test.py, which checks a sample
(saved reading), typed-in numbers (S2's), the reading-off refusal or, with
reading on, one synthetic page through the job flow, and the web app when
hosting is on. It prints PASS or FAIL for each step and never prints document
text. With reading on, the job step makes one paid model call.

.EXAMPLE
.\scripts\smoke_test.ps1 -Profile default -Region ap-south-1 -ExpectedAccount 656446902316
#>
param(
    [Alias("Profile")][string]$AwsProfile = "default",
    [ValidateSet("ap-south-1", "ap-southeast-2")][string]$Region = "ap-south-1",
    [ValidatePattern('^\d{12}$')][string]$ExpectedAccount = "656446902316",
    [string]$StackName = "surya-lekka"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "aws_target.ps1")
Confirm-AwsTarget -AwsProfile $AwsProfile -Region $Region -ExpectedAccount $ExpectedAccount

Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"

$stackJson = aws cloudformation describe-stacks --stack-name $StackName --profile $AwsProfile --region $Region `
    --query "Stacks[0]" --output json
if ($LASTEXITCODE -ne 0) { throw "Reading stack $StackName failed (exit code $LASTEXITCODE)" }
$stack = $stackJson | ConvertFrom-Json
$apiUrl = ($stack.Outputs | Where-Object { $_.OutputKey -eq "ApiUrl" }).OutputValue
$siteUrl = ($stack.Outputs | Where-Object { $_.OutputKey -eq "SiteUrl" }).OutputValue
$engine = ($stack.Parameters | Where-Object { $_.ParameterKey -eq "ReadingEngine" }).ParameterValue
if (-not $apiUrl) { throw "Stack $StackName has no ApiUrl output." }
if (-not $engine) { $engine = "none" }

$smokeArgs = @("scripts\smoke_test.py", "--api", $apiUrl, "--reading-engine", $engine)
if ($siteUrl) { $smokeArgs += @("--site", $siteUrl) }
Write-Host "== Smoke test (reading engine: $engine)"
& $python @smokeArgs
exit $LASTEXITCODE
