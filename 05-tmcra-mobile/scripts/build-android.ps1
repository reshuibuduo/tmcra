param(
    [ValidateSet("assembleDebug", "assembleRelease", "bundleRelease")]
    [string]$Task = "assembleDebug"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CacheRoot = Join-Path $ProjectRoot ".build-cache"
$GradleHome = Join-Path $CacheRoot "gradle"
$TempRoot = Join-Path $CacheRoot "tmp"
$AndroidUserHome = Join-Path $CacheRoot "android-user"
$BundledJdkRoot = Join-Path $CacheRoot "jdk-21"
$SigningProperties = Join-Path $CacheRoot "release\android-signing.properties"

New-Item -ItemType Directory -Force -Path $GradleHome, $TempRoot, $AndroidUserHome | Out-Null
$env:GRADLE_USER_HOME = $GradleHome
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:ANDROID_USER_HOME = $AndroidUserHome

$BundledJava = Get-ChildItem -Path $BundledJdkRoot -Filter java.exe -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '[\\/]bin[\\/]java\.exe$' } |
    Select-Object -First 1
if ($BundledJava) {
    $env:JAVA_HOME = Split-Path (Split-Path $BundledJava.FullName -Parent) -Parent
} elseif ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe"))) {
    # Respect a compatible caller-provided JDK when the project cache is absent.
} else {
    $JavaCommand = Get-Command java -ErrorAction Stop
    $env:JAVA_HOME = Split-Path (Split-Path $JavaCommand.Source -Parent) -Parent
}

$JavaReleaseFile = Join-Path $env:JAVA_HOME "release"
$JavaVersionLine = Get-Content $JavaReleaseFile -ErrorAction Stop |
    Where-Object { $_ -match '^JAVA_VERSION=' } |
    Select-Object -First 1
if ($JavaVersionLine -notmatch '^JAVA_VERSION="(\d+)') {
    throw "Unable to determine the Java version from JAVA_HOME=$env:JAVA_HOME"
}
$JavaMajor = [int]$Matches[1]
if ($JavaMajor -lt 21 -or $JavaMajor -gt 24) {
    throw "Android build requires JDK 21-24; found JDK $JavaMajor at $env:JAVA_HOME"
}
Write-Host "Using JDK $JavaMajor from $env:JAVA_HOME"

if ($Task -match 'Release$') {
    if (-not (Test-Path $SigningProperties)) {
        throw "Release signing configuration is missing: $SigningProperties"
    }
    Get-Content $SigningProperties | ForEach-Object {
        if ($_ -match '^([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
}

$AndroidRoot = Join-Path $ProjectRoot "android"
$Gradle = Join-Path $AndroidRoot "gradlew.bat"
Push-Location $AndroidRoot
try {
    & $Gradle $Task --no-daemon
    if ($LASTEXITCODE -ne 0) {
        throw "Android Gradle task failed: $Task"
    }
} finally {
    Pop-Location
}
