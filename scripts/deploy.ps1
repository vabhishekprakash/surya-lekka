<#
.SYNOPSIS
Build and deploy Surya Lekka with AWS SAM, then upload the samples and the web app.

.DESCRIPTION
Run from anywhere; the script works from the repository root. It first prints
the AWS account and Region for -Profile and stops unless the account is
-ExpectedAccount. Then it renders the synthetic sample quotes, runs sam build
and sam deploy without prompts (SAM's own artifacts bucket, settings saved to
samconfig.toml) and uploads the sample pages and saved readings.

With hosting on (the default) it uploads web/, with config.js pointing at the
new API, to the site bucket and clears the CloudFront cache. With
-HostingEnabled false it prints the API URL and the command that serves web/
on this machine against it.

Reading is off unless -ReadingEngine nova. Then -ModelId is looked up: for an
inference profile, aws bedrock get-inference-profile lists the Regions the
worker may call; any other ID must be a model in -Region.

This creates real AWS resources. With reading on, each quote read is a paid Bedrock call.

.EXAMPLE
.\scripts\deploy.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id>
#>
param(
    [Alias("Profile")][string]$AwsProfile = "default",
    [ValidateSet("ap-south-1", "ap-southeast-2")][string]$Region = "ap-south-1",
    [Parameter(Mandatory = $true)][ValidatePattern('^\d{12}$')][string]$ExpectedAccount,
    [string]$StackName = "surya-lekka",
    [ValidateSet("true", "false")][string]$HostingEnabled = "true",
    [string]$SiteOrigin = "http://127.0.0.1:8000",
    [ValidateSet("none", "nova")][string]$ReadingEngine = "none",
    [string]$ModelId = "global.amazon.nova-2-lite-v1:0"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "aws_target.ps1")
Confirm-AwsTarget -AwsProfile $AwsProfile -Region $Region -ExpectedAccount $ExpectedAccount

if ($StackName -cnotmatch '^[a-z0-9][a-z0-9-]{1,30}$') {
    throw "The stack name also starts the bucket name: use 2 to 31 lower-case letters, digits or hyphens."
}
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$aws = @("--profile", $AwsProfile, "--region", $Region)

function Invoke-Step([string]$Description, [scriptblock]$Command) {
    Write-Host "== $Description"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)" }
}

function Get-ModelAccess {
    # A failed lookup writes to stderr, which is expected for a model ID that isn't a profile.
    $ErrorActionPreference = "Continue"
    $reply = aws bedrock get-inference-profile --inference-profile-identifier $ModelId @aws --output json 2>$null
    if ($LASTEXITCODE -eq 0) {
        $json = $reply | & $python scripts\model_access.py --region $Region --model-id $ModelId
    } else {
        aws bedrock get-foundation-model --model-identifier $ModelId @aws --output json 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "$ModelId is neither an inference profile nor a model this account can use in $Region."
        }
        $json = & $python scripts\model_access.py --region $Region --model-id $ModelId --in-region
    }
    if ($LASTEXITCODE -ne 0) { throw "Couldn't work out where $ModelId runs." }
    return ($json | ConvertFrom-Json)
}

$access = [pscustomobject]@{ ProfileModelArns = "none"; GlobalModelArns = "none"; CrossRegion = $false }
if ($ReadingEngine -ne "none") {
    Write-Host "== Look up $ModelId"
    $access = Get-ModelAccess
    Write-Host "Pages may be read outside ${Region}: $($access.CrossRegion)"
}

Invoke-Step "Generate the sample quotes and render their pages" {
    & $python scripts\render_samples.py --out .build\samples
}
Invoke-Step "Lint the template" { & $python scripts\lint_template.py template.yaml }
Invoke-Step "Build" { sam build --template-file template.yaml }

$deployArgs = @("--stack-name", $StackName, "--capabilities", "CAPABILITY_IAM") + $aws + @(
    "--parameter-overrides",
    "StackPrefix=$StackName", "HostingEnabled=$HostingEnabled", "SiteOrigin=$SiteOrigin",
    "ReadingEngine=$ReadingEngine", "ModelId=$ModelId",
    "ProfileModelArns=$($access.ProfileModelArns)", "GlobalModelArns=$($access.GlobalModelArns)"
)
# Unattended: SAM makes or reuses its own artifacts bucket and saves these settings to samconfig.toml.
Invoke-Step "Deploy" {
    sam deploy @deployArgs --resolve-s3 --save-params --no-confirm-changeset --no-fail-on-empty-changeset
}

$outputsJson = aws cloudformation describe-stacks --stack-name $StackName @aws --query "Stacks[0].Outputs" --output json
if ($LASTEXITCODE -ne 0) { throw "Reading the stack outputs failed (exit code $LASTEXITCODE)" }
$outputs = $outputsJson | ConvertFrom-Json
function Get-Output([string]$Key) { ($outputs | Where-Object { $_.OutputKey -eq $Key }).OutputValue }
$bucket = Get-Output "BucketName"
$apiUrl = Get-Output "ApiUrl"
if (-not $bucket -or -not $apiUrl) { throw "The stack outputs have no BucketName or ApiUrl." }

Invoke-Step "Upload the sample pages and saved readings" {
    aws s3 cp .build\samples "s3://$bucket/samples/" --recursive @aws
}

$siteArgs = @("--api", $apiUrl, "--region", $Region)
if ($access.CrossRegion) { $siteArgs += "--cross-region" }
if ($HostingEnabled -eq "true") {
    $siteBucket = Get-Output "SiteBucketName"
    $distribution = Get-Output "DistributionId"
    $siteUrl = Get-Output "SiteUrl"
    if (-not $siteBucket -or -not $distribution) { throw "The stack outputs have no SiteBucketName or DistributionId." }
    Write-Host "== Write the web app with config.js for this API"
    $files = & $python scripts\build_site.py @siteArgs --out .build\web
    if ($LASTEXITCODE -ne 0) { throw "Writing the web app failed (exit code $LASTEXITCODE)" }
    $types = @{ ".html" = "text/html; charset=utf-8"; ".js" = "text/javascript; charset=utf-8";
                ".css" = "text/css; charset=utf-8" }
    foreach ($name in $files) {
        $type = $types[[IO.Path]::GetExtension($name)]
        if (-not $type) { throw "No content type is set for $name." }
        Invoke-Step "Upload $name" {
            aws s3 cp (Join-Path .build\web $name) "s3://$siteBucket/$name" --content-type $type `
                --cache-control no-cache @aws
        }
    }
    Invoke-Step "Clear the CloudFront cache" {
        aws cloudfront create-invalidation --distribution-id $distribution --paths "/*" @aws `
            --query Invalidation.Id --output text
    }
}

Write-Host ""
Write-Host "API URL: $apiUrl"
if ($HostingEnabled -eq "true") {
    Write-Host "Site:    $siteUrl"
} else {
    $local = "  .venv\Scripts\python -m src.api.local_server --port $(([uri]$SiteOrigin).Port) $($siteArgs -join ' ')"
    Write-Host "Hosting is off. Serve the web app on this machine against the API with:"
    Write-Host $local
    Write-Host "Then open $SiteOrigin/ (the API accepts requests from that origin only)."
}
