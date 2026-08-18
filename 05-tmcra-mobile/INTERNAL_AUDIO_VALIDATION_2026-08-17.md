# TMCRA Mobile Audio Internal Validation — 2026-08-17

Internal engineering evidence. This file is not a public benchmark claim.

## Target

- Phone: Redmi `24117RK2CC`, Android 16, arm64
- Local ASR: `sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01`
- Speaker segmentation: `pyannote/segmentation-3.0-int8@sherpa-onnx-v1.13.4`
- Persistent voiceprint model: `iic/speech_eres2netv2_sv_zh-cn_16k-common@v1.0.1`
- Remote review: `Qwen3-ASR-0.6B-bf16` on the TMCRA GPU worker

## Public fixtures

All fixtures came from the official sherpa-onnx `speaker-segmentation-models` release:

- `0-four-speakers-zh.wav`, 56.8607 s, SHA-256 `BEDF036CAED208386C67B4EF4B11F83D74DD0D420B102163A1C33CD09CDE7010`
- `1-two-speakers-en.wav`, 16 s, SHA-256 `F1C877DC01595E28BE7147BF2FE38E5268147A868BF3FDB5C37B97F5940E21F3`
- `2-two-speakers-en.wav`, 34 s, SHA-256 `EE9C33D34E8F0FDA4B78277F609944A1565AA16E6E2146F4CB8F0EFB0D70030B`

Source: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models>

## On-device diarization calibration

Unknown speaker count was used for every calibration run.

| Fixture | Known speakers | Threshold | Detected | Processing | Result |
|---|---:|---:|---:|---:|---|
| 4-speaker Chinese | 4 | 0.90 | 3 | 41.2 s | fail: merged speakers |
| 4-speaker Chinese | 4 | 0.85 | 3 | 41.7 s | fail: merged speakers |
| 4-speaker Chinese | 4 | 0.70 | 4 | 35.8 s | pass |
| 2-speaker English #1 | 2 | 0.70 | 2 | 5.7 s | pass |
| 2-speaker English #2, overlap | 2 | 0.70 | 2 | 30.8 s | pass |

The production default is therefore `0.70`. This is a device-and-fixture calibration, not a general diarization accuracy claim.

The overlap fixture produced 14 raw diarization turns. The exclusive-timeline materializer turns intersecting tracks into one `speaker=-1 / overlap=true` span, so mixed PCM is never enrolled into either person's voiceprint and is not transcribed twice.

## Remote ASR worker

The 16-second official English fixture was submitted directly to the internal GPU worker using its protected loopback API:

- HTTP 200
- Provider: `tmcra-qwen3-asr`
- Model: `Qwen3-ASR-0.6B-bf16`
- Elapsed wall time: 2 s
- Returned text length: 169 characters
- Usage object present: yes

The worker reported ready on GPU before the request. The fixture and response body were removed after the probe.

## Website BFF deployment

The authenticated personal-audio routes are active on `tmcra.com`:

- `/api/personal/audio-memory/transcribe`
- `/api/personal/audio-memory/events`
- `/api/personal/audio-memory/speakers`
- `/api/personal/audio-memory/delete`

Anonymous requests return 401. Homepage and download page return 200. The deployed download-directory manifest hash remained `391ea3eace410c2fa753f96f863cb244600080644d85e74e84dc41039480fb2c`, identical to the previous release, so no new mobile or desktop installer was published.

## Code validation

- Android `assembleDebug`: pass
- Android `assembleDebugAndroidTest`: pass
- Android `lintDebug`: pass
- JVM unit tests, including exclusive overlap materialization: pass
- Website audio-memory contract tests: 11/11 pass
- Website production build: pass

On this Windows checkout, JVM tests must run through an ASCII drive mapping because the Gradle test worker corrupts the non-ASCII checkout path. Application and instrumentation builds do not have this limitation.

## Remaining phone gate

The debug app's ordinary-user session had expired. A clean debug reinstall is waiting for the phone's MIUI “install via USB” confirmation. After installation, log in once in the debug app, then run:

1. authenticated phone → website BFF → GPU ASR → write → recall → exact-delete probe;
2. acoustic replay through the production microphone service;
3. assertion that the public two-speaker fixture creates at least two persistent local voiceprints;
4. automatic deletion of temporary remote memories, local segments, WAV cache entries, and test-only voiceprints.

An Android emulator was deliberately not installed on the server. It would not validate this phone's arm64 native models, microphone path, MIUI background behavior, or acoustic speaker separation; the existing real-phone evidence is stronger.
