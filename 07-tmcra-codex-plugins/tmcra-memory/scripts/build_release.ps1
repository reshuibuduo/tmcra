[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$pluginRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent (Split-Path -Parent $pluginRoot)
$downloadsRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $repoRoot "tmcra-commercial-site\TMCRA\public\downloads")
)
if (-not $OutputPath) {
    $OutputPath = Join-Path $downloadsRoot "tmcra-codex-latest.zip"
}

$resolvedRepo = [System.IO.Path]::GetFullPath($repoRoot)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$allowedOutputPrefix = $downloadsRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $resolvedOutput.StartsWith($allowedOutputPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath must stay inside the website public/downloads directory."
}
if ([System.IO.Path]::GetDirectoryName($resolvedOutput) -ne $downloadsRoot) {
    throw "OutputPath must be a direct child of the website public/downloads directory."
}
if ([System.IO.Path]::GetExtension($resolvedOutput) -ne '.zip') {
    throw "OutputPath must use the .zip extension."
}

$pluginManifestPath = Join-Path $pluginRoot ".codex-plugin\plugin.json"
$pluginManifest = Get-Content -Raw -LiteralPath $pluginManifestPath | ConvertFrom-Json
$pluginVersion = [string]$pluginManifest.version
if ($pluginVersion -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$') {
    throw "Plugin version must be a release-orderable semantic version without build metadata."
}
$versionedOutput = Join-Path $downloadsRoot "tmcra-codex-$pluginVersion.zip"
$releaseManifestPath = Join-Path $downloadsRoot "tmcra-codex-release.json"
if ($resolvedOutput.Equals($versionedOutput, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath must be an alias such as tmcra-codex-latest.zip, not the versioned archive path."
}
$versionedSha256Path = "$versionedOutput.sha256"
$aliasSha256Path = "$resolvedOutput.sha256"

$runtimeFiles = @(
    ".agents/plugins/marketplace.json",
    "plugins/tmcra-memory/.codex-plugin/plugin.json",
    "plugins/tmcra-memory/.mcp.json",
    "plugins/tmcra-memory/README.md",
    "plugins/tmcra-memory/hooks/hook_common.mjs",
    "plugins/tmcra-memory/hooks/hooks.json",
    "plugins/tmcra-memory/hooks/post_compact.mjs",
    "plugins/tmcra-memory/hooks/post_tool_use.mjs",
    "plugins/tmcra-memory/hooks/pre_compact.mjs",
    "plugins/tmcra-memory/hooks/run_hook.mjs",
    "plugins/tmcra-memory/hooks/session_start.mjs",
    "plugins/tmcra-memory/hooks/stop.mjs",
    "plugins/tmcra-memory/hooks/subagent_start.mjs",
    "plugins/tmcra-memory/hooks/subagent_stop.mjs",
    "plugins/tmcra-memory/hooks/user_prompt_submit.mjs",
    "plugins/tmcra-memory/scripts/check_config.mjs",
    "plugins/tmcra-memory/scripts/configure.mjs",
    "plugins/tmcra-memory/scripts/device_login.mjs",
    "plugins/tmcra-memory/scripts/drain_outbox.mjs",
    "plugins/tmcra-memory/scripts/history_import.mjs",
    "plugins/tmcra-memory/scripts/install.ps1",
    "plugins/tmcra-memory/scripts/install.sh",
    "plugins/tmcra-memory/scripts/mcp_server.mjs",
    "plugins/tmcra-memory/scripts/project_bootstrap.mjs",
    "plugins/tmcra-memory/scripts/project_init.mjs",
    "plugins/tmcra-memory/scripts/tmcra_client.mjs",
    "plugins/tmcra-memory/skills/manage-tmcra-memory/agents/openai.yaml",
    "plugins/tmcra-memory/skills/manage-tmcra-memory/SKILL.md",
    "INSTALL-TMCRA-CODEX.md",
    "Install-TMCRA.ps1",
    "install.sh"
)
foreach ($entry in $runtimeFiles) {
    $sourcePath = Join-Path $resolvedRepo ($entry.Replace("/", [System.IO.Path]::DirectorySeparatorChar))
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Release source is missing $entry"
    }
}

New-Item -ItemType Directory -Force -Path $downloadsRoot | Out-Null
$temporaryArchive = Join-Path $downloadsRoot ".tmcra-codex-$([guid]::NewGuid().ToString('N')).zip"
$temporaryAlias = Join-Path $downloadsRoot ".tmcra-codex-alias-$([guid]::NewGuid().ToString('N')).zip"
$temporaryManifest = Join-Path $downloadsRoot ".tmcra-codex-release-$([guid]::NewGuid().ToString('N')).json"
$temporaryVersionedSha256 = Join-Path $downloadsRoot ".tmcra-codex-versioned-$([guid]::NewGuid().ToString('N')).sha256"
$temporaryAliasSha256 = Join-Path $downloadsRoot ".tmcra-codex-alias-$([guid]::NewGuid().ToString('N')).sha256"

function Write-Utf8NoBom([string]$Path, [string]$Value) {
    [System.IO.File]::WriteAllText($Path, $Value, (New-Object System.Text.UTF8Encoding($false)))
}

function Publish-Atomic([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        $backup = "$Destination.$([guid]::NewGuid().ToString('N')).bak"
        try {
            [System.IO.File]::Replace($Source, $Destination, $backup, $true)
        }
        finally {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
    else {
        [System.IO.File]::Move($Source, $Destination)
    }
}

try {
    Push-Location $resolvedRepo
    try {
        & tar -a -cf $temporaryArchive @runtimeFiles
        if ($LASTEXITCODE -ne 0) { throw "Could not create the TMCRA Codex release archive." }
    }
    finally {
        Pop-Location
    }

    $archiveFiles = @(& tar -tf $temporaryArchive | Where-Object { -not $_.EndsWith("/") })
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the TMCRA Codex release archive." }
    $unexpected = @($archiveFiles | Where-Object { $_ -notin $runtimeFiles })
    $missing = @($runtimeFiles | Where-Object { $_ -notin $archiveFiles })
    if ($unexpected.Count -gt 0) {
        throw "Release archive contains unexpected files: $($unexpected -join ', ')"
    }
    if ($missing.Count -gt 0) {
        throw "Release archive is missing files: $($missing -join ', ')"
    }

    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $temporaryArchive
    $bytes = (Get-Item -LiteralPath $temporaryArchive).Length
    Copy-Item -LiteralPath $temporaryArchive -Destination $temporaryAlias
    Write-Utf8NoBom $temporaryVersionedSha256 "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($versionedOutput))`n"
    Write-Utf8NoBom $temporaryAliasSha256 "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($resolvedOutput))`n"

    $releaseManifest = [ordered]@{
        schemaVersion = 1
        plugin = [ordered]@{
            name = [string]$pluginManifest.name
            version = $pluginVersion
        }
        archive = [ordered]@{
            latest = [System.IO.Path]::GetFileName($resolvedOutput)
            versioned = [System.IO.Path]::GetFileName($versionedOutput)
            latestSha256 = [System.IO.Path]::GetFileName($aliasSha256Path)
            versionedSha256 = [System.IO.Path]::GetFileName($versionedSha256Path)
            bytes = $bytes
            sha256 = $hash.Hash.ToLowerInvariant()
            entryCount = $archiveFiles.Count
        }
        install = [ordered]@{
            windows = ".\Install-TMCRA.ps1"
            macosLinux = "sh ./install.sh"
        }
        requirements = [ordered]@{
            node = ">=18"
            codexPluginCli = $true
        }
        generatedAtUtc = [DateTime]::UtcNow.ToString("o")
    }
    Write-Utf8NoBom $temporaryManifest "$(($releaseManifest | ConvertTo-Json -Depth 5))`n"

    Publish-Atomic $temporaryArchive $versionedOutput
    Publish-Atomic $temporaryAlias $resolvedOutput
    Publish-Atomic $temporaryVersionedSha256 $versionedSha256Path
    Publish-Atomic $temporaryAliasSha256 $aliasSha256Path
    Publish-Atomic $temporaryManifest $releaseManifestPath

    [pscustomobject]@{
        OutputPath = $resolvedOutput
        VersionedOutputPath = $versionedOutput
        AliasSha256Path = $aliasSha256Path
        VersionedSha256Path = $versionedSha256Path
        ReleaseManifestPath = $releaseManifestPath
        Version = $pluginVersion
        Bytes = $bytes
        Sha256 = $hash.Hash.ToLowerInvariant()
        EntryCount = $archiveFiles.Count
    }
}
finally {
    Remove-Item -LiteralPath $temporaryArchive -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryAlias -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryManifest -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryVersionedSha256 -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryAliasSha256 -Force -ErrorAction SilentlyContinue
}
