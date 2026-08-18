# Model Provenance

Verified on 2026-08-18 from public upstream project pages. This release
candidate redistributes the small TMCRA service reranker checkpoint documented
in `02-tmcra-memory-api/models/`. It does not redistribute third-party model
weights. It includes source code, manifests, checksums, and the small audio
fixtures listed in this component.

## Runtime model references

| Use | Upstream artifact | Current public license signal | Redistribution status |
|---|---|---|---|
| Service embedding | `BAAI/bge-m3` | Hugging Face model card metadata: MIT | Not redistributed here |
| Service reranker | `BAAI/bge-reranker-v2-m3` | Hugging Face model card metadata: Apache-2.0 | Not redistributed here |
| Service runtime reranker | `02-tmcra-memory-api/models/tmcra_v3_reranker.pt` | Apache-2.0, declared by this repository | Included; see the asset README for checksum |
| Mobile / service remote ASR | `Qwen/Qwen3-ASR-0.6B` and `Qwen/Qwen3-ASR-0.6B-hf` | Hugging Face model card metadata: Apache-2.0 | Not redistributed here |
| Mobile runtime | `k2-fsa/sherpa-onnx` | GitHub repository license: Apache-2.0 | Runtime dependency only |
| Mobile streaming ASR | `csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01` | Upstream page links to sherpa-onnx docs/release; model card has no explicit license metadata | Download reference only; confirm before redistributing weights |
| Mobile speaker segmentation | `csukuangfj/sherpa-onnx-pyannote-segmentation-3-0` | Model card says files are converted from `pyannote/segmentation-3.0`; no explicit license metadata on the converted repo page | Download reference only; confirm before redistributing weights |
| Mobile speaker identity | `iic/speech_eres2netv2_sv_zh-cn_16k-common` / 3D-Speaker | 3D-Speaker GitHub repository is Apache-2.0; ModelScope page must be reviewed before redistributing the exact model artifact | Download reference only; confirm before redistributing weights |

## Fixture boundary

The `fixtures/` WAV files are public sample audio files copied from the official
sherpa-onnx `speaker-segmentation-models` release and are retained for local
smoke tests. They are not TMCRA user recordings. Downstream redistributors must
retain the documented upstream attribution and comply with the upstream release
terms.

## Upstream URLs

- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/BAAI/bge-reranker-v2-m3
- https://huggingface.co/Qwen/Qwen3-ASR-0.6B
- https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf
- https://github.com/k2-fsa/sherpa-onnx
- https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models
- https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01
- https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0
- https://github.com/modelscope/3D-Speaker
- https://www.modelscope.cn/models/iic/speech_eres2netv2_sv_zh-cn_16k-common
