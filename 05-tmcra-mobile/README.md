# TMCRA Mobile Audio Memory

Native Android MVP for a phone-and-earpiece personal memory loop. The app keeps
microphone capture, VAD, live ASR, speaker embeddings, and speaker matching on
the phone. TMCRA receives text records plus an opaque local speaker ID; it does
not receive speaker embeddings or raw audio during the normal path.

## Implemented pipeline

```text
AudioRecord 16 kHz mono
  -> adaptive VAD and bounded local WAV cache
  -> live on-device Zipformer ASR (partial text while speaking)
  -> VAD utterance finalization
  -> on-device ERes2NetV2 speaker embedding and conservative matching
  -> local SQLite outbox
  -> TMCRA recall (account-global + current audio project)
  -> text-only TMCRA write with actor provenance
  -> optional local reminder and Android TTS
```

Speaker attribution and semantic retrieval are separate:

- The embedding only answers which local speaker cluster produced an utterance.
- TMCRA semantic retrieval indexes the transcript and speaker provenance text.
- Encrypted embedding templates remain in app-private storage.
- Low-score or ambiguous matches remain `unknown`; they are not forced onto a
  known person.
- When the user labels a speaker, the app synchronizes only the opaque local ID,
  label, relation, and revision. The mapping has an offline retry queue.

## Current model profile

| Function | Artifact | Packaged size | Runtime |
| --- | --- | ---: | --- |
| Chinese streaming ASR | `sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01` | 26,342,340 bytes | sherpa-onnx 1.13.4 / CPU |
| Speaker identity | `iic/speech_eres2netv2_sv_zh-cn_16k-common@v1.0.1` | 71,441,526 bytes | sherpa-onnx 1.13.4 / CPU |

Model assets are checksum-pinned in `android/app/build.gradle`. Build-time path
overrides are available through `TMCRA_ASR_MODEL_PATH`,
`TMCRA_ASR_TOKENS_PATH`, and `TMCRA_SPEAKER_MODEL_PATH`.

The 2025 ASR model is selected for internal phone evaluation because it is a
small, genuine streaming CTC model. Its Hub card currently has no explicit
weight-license metadata. It must not enter a commercial release until the
weight license is confirmed. The older 14M Chinese Zipformer is the current
Apache-2.0 fallback candidate. See `docs/MODEL_SELECTION.zh-CN.md`.

## Privacy and network behavior

- Local WAV cache: app-private, maximum 24 hours or 256 MiB, oldest first.
- Voiceprints: Android-Keystore-encrypted local blobs; never accepted by the
  audio-memory API contract.
- Default ASR: local only.
- Remote ASR fallback: off by default and requires an explicit in-app opt-in.
- Remote memory: transcript, timestamp, duration, local speaker ID/label,
  attribution confidence, and ASR provenance.
- Non-owner speech is written as an observed sensor/tool record so it cannot be
  confused with a statement made by the user.

## Build and test

Requirements: Windows PowerShell, Android SDK, and JDK 21-24. The first hardware
validation package targets `arm64-v8a` and Android API 24+.

```powershell
cd mobile/tmcra-memory
npm run android:debug
```

Output:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Direct Gradle checks:

```powershell
cd android
.\gradlew.bat testDebugUnitTest assembleDebug --no-daemon
```

The debug APK is development-signed. Release tasks require the ignored signing
configuration consumed by `scripts/build-android.ps1`.

## Validation boundary

The build and deterministic unit tests run on the development workstation.
Phone-specific latency, sustained battery use, thermal throttling, Bluetooth
capture quality, noisy-scene word error rate, and speaker false-accept/false-
reject rates still require a real arm64 Android device. Those measurements are
release gates, not inferred from desktop benchmarks.

