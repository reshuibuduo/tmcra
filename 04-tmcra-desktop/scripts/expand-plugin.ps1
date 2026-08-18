[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [Parameter(Mandatory = $true)]
    [string]$DestinationPath
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
    throw "The TMCRA plugin archive does not exist."
}
if (Test-Path -LiteralPath $DestinationPath) {
    throw "The temporary extraction directory already exists."
}

Expand-Archive -LiteralPath $ArchivePath -DestinationPath $DestinationPath -Force
