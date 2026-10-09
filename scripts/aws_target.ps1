# Shared by deploy.ps1 and smoke_test.ps1. Prints the AWS account and Region a
# profile points at, and stops unless the account is the expected one.
function Confirm-AwsTarget([string]$AwsProfile, [string]$Region, [string]$ExpectedAccount) {
    $account = aws sts get-caller-identity --profile $AwsProfile --region $Region --query Account --output text
    if ($LASTEXITCODE -ne 0 -or -not $account) { throw "Couldn't read the AWS account for profile '$AwsProfile'." }
    $account = "$account".Trim()
    Write-Host "AWS account: $account"
    Write-Host "Region:      $Region"
    Write-Host "Profile:     $AwsProfile"
    if ($account -ne $ExpectedAccount) {
        throw "Profile '$AwsProfile' is for account $account, not $ExpectedAccount. Stopping before any change."
    }
}
