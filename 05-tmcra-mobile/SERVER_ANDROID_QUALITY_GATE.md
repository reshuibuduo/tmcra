# TMCRA Android server quality gate

The server emulator is a development gate. It runs before any APK is installed on a physical phone.

## Covered automatically

- final APK DEX and ABI inspection;
- clean Android install;
- a lightweight Android instrumentation/context smoke test;
- two-speaker, four-speaker, and overlapping-speech fixture probes;
- automatic voiceprint enrollment;
- voiceprint reuse after engine reopen;
- voiceprint reuse after Android process restart;
- rejection of overlap spans from voiceprint enrollment;
- phase-level probe telemetry;
- result JSON, failure diagnostics, and APK hashes retained per run.

The four-speaker fixture is not executed twice. Instrumentation only verifies
that the target application context is installable and addressable. The
subsequent fixture probes own the expensive model execution and emit the
identity lifecycle evidence used by the gate.

## Environment boundary

The current GPU server is an x86_64 cloud container. Its CPU exposes virtualization flags, but the container cannot open `/dev/kvm`. The gate therefore uses an x86_64-only internal APK and Android emulator software acceleration. Production APKs remain ARM64-only.

The default virtual device is Android 11 / API 30 using the AOSP Automated Test Device (`aosp_atd`) image. This image is intended for automated tests and removes applications, services, and rendering work that do not contribute to the functional gate. An Android 16 / API 36 Google APIs image was rejected on this no-KVM host: software emulation repeatedly stalled `system_server` while opening the lock-settings SQLite database and triggered the watchdog after about 62 seconds. Increasing the boot timeout does not fix that failure mode.

The server gate cannot validate microphone acoustics, Bluetooth routing, OEM power management, or lock-screen recording. Those remain physical-phone release checks after this gate passes.

## Invocation

```bash
export TMCRA_ANDROID_CI_ROOT=/opt/tmcra-data/tmcra-android-ci
bash android/ci/server-emulator-gate.sh
```

The default AVD contract is:

```text
name:         tmcra-ci-atd-api30
system image: system-images;android-30;aosp_atd;x86_64
acceleration: software (`-accel off`)
boot policy:  clean data image on every gate run
```

`sys.boot_completed=1` is necessary but not sufficient on a software-emulated
ATD. The gate also requires the Android package, settings, and activity
services plus one unchanged `system_server` PID for seven checks over 60 seconds
before installing the application.

The headless gate does not change display animation or power settings. The
probe activity owns its wake lock, and display cosmetics are outside this
functional test contract.

Each real-audio probe has a one-hour hard limit by default because x86 Android
is running through software CPU emulation on this host. The activity writes a
progress record at diarization and identity lifecycle boundaries. The gate logs
only changed progress records and captures logcat, process state, system
properties, and the latest progress record before an error shuts the emulator
down. Override the limit with `TMCRA_PROBE_TIMEOUT_SECONDS` only for a deliberate
diagnostic run.

The server SDK must also provide Android Build Tools 36.0.0. The gate uses its
packaging metadata while inspecting the final APK and fails before emulator
startup when that dependency is absent.

The x86_64 APK must be built explicitly:

```bash
./gradlew -PtmcraTestAbi=x86_64 testDebugUnitTest assembleDebug assembleDebugAndroidTest
```

Omitting `tmcraTestAbi` keeps the production development default at `arm64-v8a`.
