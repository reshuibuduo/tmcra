[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CodexPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedMarketplaceRoot
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CodexPath -PathType Leaf)) {
    throw "The verified Codex executable is no longer available."
}
if ($ExpectedVersion -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "The expected TMCRA plugin version is invalid."
}
if (-not (Test-Path -LiteralPath $ExpectedMarketplaceRoot -PathType Container)) {
    throw "The expected TMCRA marketplace directory is missing."
}

$listJson = & $CodexPath plugin list --json
if ($LASTEXITCODE -ne 0) {
    throw "Codex could not report installed plugins."
}
$list = ($listJson | Out-String | ConvertFrom-Json)
$installed = @($list.installed) |
    Where-Object { $_.pluginId -eq 'tmcra-memory@tmcra-local' } |
    Select-Object -First 1
if (-not $installed -or $installed.version -ne $ExpectedVersion) {
    throw "The expected TMCRA Memory plugin is not installed in Codex."
}

$source = ([string]$installed.marketplaceSource.source) -replace '^\\\\\?\\', ''
$actualRoot = [System.IO.Path]::GetFullPath($source).TrimEnd('\')
$expectedRoot = [System.IO.Path]::GetFullPath($ExpectedMarketplaceRoot).TrimEnd('\')
if (-not $actualRoot.Equals($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Codex is using TMCRA Memory from a different marketplace directory."
}

exit 0
