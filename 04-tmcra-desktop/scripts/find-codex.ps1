[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$candidates = @()

$command = Get-Command codex -ErrorAction SilentlyContinue
if ($command -and $command.Source -and $command.Source -notlike "*WindowsApps*") {
    $candidates += $command.Source
}

$desktopBin = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
if (Test-Path -LiteralPath $desktopBin -PathType Container) {
    $candidates += @(
        Get-ChildItem -LiteralPath $desktopBin -Recurse -Filter codex.exe -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    ).FullName
}

foreach ($path in @($candidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $path plugin marketplace --help *> $null
        $marketplaceExitCode = $LASTEXITCODE
        & $path plugin add --help *> $null
        $pluginExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($marketplaceExitCode -eq 0 -and $pluginExitCode -eq 0) {
        [Console]::Out.WriteLine([System.IO.Path]::GetFullPath($path))
        exit 0
    }
}

[Console]::Error.WriteLine("A Codex installation with plugin support was not found.")
exit 2
