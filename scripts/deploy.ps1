<#
.SYNOPSIS
Build and deploy Surya Lekka with AWS SAM, then upload the sample page images.

.DESCRIPTION
Run from anywhere; the script works from the repository root. It renders the
synthetic sample quotes to page JPEGs, runs sam build and sam deploy (guided
the first time, when there is no samconfig.toml yet), uploads the sample pages
to s3://<bucket>/samples/ and prints the API URL.

This deploys real AWS resources and Bedrock calls cost money when the app is used.

.EXAMPLE
.\scripts\deploy.ps1 -SiteOrigin https://example.github.io
#>
param(
    [Parameter(Mandatory = $true)][string]$SiteOrigin,
    [string]$StackName = "surya-lekka",
    [string]$Region = "ap-south-1"
)

$ErrorActionPreference = "Stop"
if ($StackName -cnotmatch '^[a-z0-9][a-z0-9-]{1,30}$') {
    throw "The stack name also starts the bucket name: use 2 to 31 lower-case letters, digits or hyphens."
}
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"

function Invoke-Step([string]$Description, [scriptblock]$Command) {
    Write-Host "== $Description"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)" }
}

Invoke-Step "Generate the sample quotes and render their pages" {
    & $python scripts\render_samples.py --out .build\samples
}
Invoke-Step "Lint the template" { & $python scripts\lint_template.py template.yaml }
Invoke-Step "Build" { sam build --template-file template.yaml }

$deployArgs = @(
    "--stack-name", $StackName,
    "--region", $Region,
    "--capabilities", "CAPABILITY_IAM",
    "--parameter-overrides", "SiteOrigin=$SiteOrigin StackPrefix=$StackName"
)
if (Test-Path samconfig.toml) {
    Invoke-Step "Deploy" { sam deploy @deployArgs --no-fail-on-empty-changeset }
} else {
    Invoke-Step "Deploy (guided, first time)" { sam deploy --guided @deployArgs }
}

$outputsJson = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
    --query "Stacks[0].Outputs" --output json
if ($LASTEXITCODE -ne 0) { throw "Reading the stack outputs failed (exit code $LASTEXITCODE)" }
$outputs = $outputsJson | ConvertFrom-Json
$bucket = ($outputs | Where-Object { $_.OutputKey -eq "BucketName" }).OutputValue
$apiUrl = ($outputs | Where-Object { $_.OutputKey -eq "ApiUrl" }).OutputValue
if (-not $bucket -or -not $apiUrl) { throw "The stack outputs have no BucketName or ApiUrl." }

Invoke-Step "Upload the sample pages" {
    aws s3 cp .build\samples "s3://$bucket/samples/" --recursive --region $Region
}

Write-Host ""
Write-Host "API URL: $apiUrl"
