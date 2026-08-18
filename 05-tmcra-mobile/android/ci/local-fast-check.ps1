# TMCRA Android local fast check.
# Replaces the heavy server-emulator-gate.sh for local development: build the
# debug APK + androidTest APK, install both on the local WHPX emulator, and run
# the fast instrumented gates (context smoke + voiceprint identity lifecycle).
# No one-hour probes, no static DEX checks, no boot-stability windows.
#
# Usage:
#   powershell -File android/ci/local-fast-check.ps1 [-Abi x86_64] [-Avd tmcra-dev]

param(
    [string]$Abi = "x86_64",
    [string]$Avd = "tmcra-dev"
)

$ErrorActionPreference = "Stop"
$Sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Android\Sdk" } else { throw "Set ANDROID_HOME to your Android SDK directory." }
$env:ANDROID_HOME = $Sdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:ANDROID_AVD_HOME = if ($env:ANDROID_AVD_HOME) { $env:ANDROID_AVD_HOME } else { "E:\.android-avd" }
$Adb = "$Sdk\platform-tools\adb.exe"
$Emulator = "$Sdk\emulator\emulator.exe"
$Project = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Android = Split-Path -Parent $PSScriptRoot
$AppApk = Join-Path $Android "app\build\outputs\apk\debug\app-debug.apk"
$TestApk = Join-Path $Android "app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk"
$Package = "com.tmcra.memory.mobile.debug"
$TestPackage = "$Package.test"
$Runner = "androidx.test.runner.AndroidJUnitRunner"

function Step([string]$message) { Write-Host "`n==> $message" -ForegroundColor Cyan }

Step "Build debug + androidTest APKs (abi=$Abi)"
Push-Location $Android
try {
    & .\gradlew.bat "-PtmcraTestAbi=$Abi" assembleDebug assembleDebugAndroidTest --console=plain
    if ($LASTEXITCODE -ne 0) { throw "gradle build failed ($LASTEXITCODE)" }
} finally { Pop-Location }
if (-not (Test-Path $AppApk)) { throw "app APK missing: $AppApk" }
if (-not (Test-Path $TestApk)) { throw "test APK missing: $TestApk" }

Step "Wait for emulator $Avd"
& $Adb start-server | Out-Null
& $Emulator -list-avds | Select-String -SimpleMatch $Avd | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (& $Emulator -list-avds | Select-String -SimpleMatch $Avd)) {
    throw "AVD '$Avd' not found"
}
$deadline = (Get-Date).AddMinutes(10)
$booted = $false
while ((Get-Date) -lt $deadline) {
    $state = (& $Adb -e get-state 2>$null)
    if ($state -match "device") {
        $completed = (& $Adb -e shell getprop sys.boot_completed 2>$null).Trim()
        if ($completed -eq "1") { $booted = $true; break }
    }
    Start-Sleep -Seconds 5
}
if (-not $booted) { throw "emulator did not finish booting in 10 minutes" }
Step "Emulator booted"

Step "Install APKs"
& $Adb -e install -r -t $AppApk
if ($LASTEXITCODE -ne 0) { throw "app install failed" }
& $Adb -e install -r -t $TestApk
if ($LASTEXITCODE -ne 0) { throw "test install failed" }

Step "Run fast instrumented gates"
$classes = @(
    "com.getcapacitor.myapp.ExampleInstrumentedTest",
    "com.tmcra.memory.mobile.audio.VoiceprintLifecycleInstrumentedTest"
)
foreach ($class in $classes) {
    Write-Host "--- $class"
    $out = & $Adb -e shell am instrument -w -e class $class "$TestPackage/$Runner" 2>&1
    $out | Out-String | Write-Host
    if (($out -join "`n") -notmatch "OK \(\d+ test") { throw "instrumentation failed: $class" }
}

Step "PASS: local fast check green"
