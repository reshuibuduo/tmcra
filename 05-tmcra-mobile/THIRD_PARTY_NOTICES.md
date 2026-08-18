# TMCRA Mobile third-party review

This file records the current engineering review. A release build must include
the complete applicable license and NOTICE texts before distribution.

## sherpa-onnx 1.13.4

- Project: <https://github.com/k2-fsa/sherpa-onnx>
- License: Apache License 2.0
- Use: Android native runtime for streaming ASR and speaker embedding inference

## 3D-Speaker / ERes2NetV2

- Project: <https://github.com/modelscope/3D-Speaker>
- Model ID: `iic/speech_eres2netv2_sv_zh-cn_16k-common@v1.0.1`
- Project and published ModelScope model license: Apache License 2.0
- Use: on-device speaker embedding

## 2025 small CTC Zipformer evaluation weights

- Model: <https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01>
- Use: internal Android streaming-ASR evaluation
- Release status: blocked. The model card does not currently declare an
  explicit weight license. Do not distribute these weights in a commercial
  release until the author or repository supplies a clear license.

## Apache-2.0 ASR fallback candidate

- Model: <https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23>
- License metadata: Apache License 2.0
- Status: candidate for the commercial model profile after device A/B testing

