# TMCRA model & data assets

This component does **not** redistribute model weights or benchmark datasets.
Until redistribution rights are confirmed, it ships:

- `MANIFEST.md` — every model/dataset artifact known to the build host with
  SHA-256 checksums and sources.
- `fixtures/` — public audio fixtures from the official sherpa-onnx
  `speaker-segmentation-models` release (MIT/Apache-2.0 redistributable):
  - `0-four-speakers-zh.wav` (57 s, 4 speakers)
  - `1-two-speakers-en.wav` (16 s, 2 speakers)
  - `2-two-speakers-en.wav` (34 s, 2 speakers, overlapping)

## How to obtain the production models

The mobile app fetches its models at build time with pinned SHA-256 checksums
(see component 05 `android/app/build.gradle`); the memory service loads its
embedding/reranker/cross-encoder models from paths configured by environment
variables (component 02). Consult each model's own license before use.

The production text-memory route maps those artifacts as follows:

- `TMCRA_EMBEDDING_MODEL` -> `BAAI/bge-m3` for 1,024-dimensional dense recall;
- `TMCRA_CROSS_MODEL` -> `BAAI/bge-reranker-v2-m3` for query/evidence cross encoding;
- `TMCRA_CHECKPOINT` -> the bundled `tmcra_v3_reranker.pt` for local TMCRA ranking signals; and
- Writer, reviewer, slow-graph, recall-planner, and outer-agent models remain
  separate configured roles rather than being hidden inside the embedding path.

See [the complete production model stack](../docs/PRODUCTION_MODEL_STACK.md)
before downloading or replacing a model.
