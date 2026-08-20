# Clean-room deployment validation

Validation date: 2026-08-20

## Result

The public `v0.3.0-rc1` source archive was deployed into a dedicated directory on a single NVIDIA RTX 5090 (32 GB). The full production model geometry and default API pools reached `ready`, and the application, provenance, knowledge-graph, personal-knowledge-base, and cross-agent SDK paths passed after applying the release-candidate fixes listed below.

The immutable `v0.3.0-rc1` tag does not contain these fixes. Publish them as a new release candidate; do not move the existing tag.

## Isolation and runtime

- The validation used a dedicated code, configuration, data, state, model, key, log, and report tree.
- No production data directory was mounted into the clean room.
- A pre-existing `llama-server` binary was reused read-only. The Qwen, BGE embedding, and BGE reranker artifacts were downloaded into the clean room and verified there.
- The public source archive SHA-256 was `26c33bb5bef0c83f1fc5e1b363c0205edc9242fa29252f3267f1016e539ed30a`.
- Qwen ran with the public default geometry: 3 parallel slots, 65,536 tokens per slot, and 196,608 total context tokens.
- The API ran with the public defaults of 4 Writer workers and 2 recall replicas.
- Peak GPU memory use was about 25.2 GB including an unrelated, pre-existing workload of about 2.0 GB. About 6.9 GB remained free.
- The API, local model, and temporary TLS proxy were stopped after validation. The unrelated workload remained healthy.

## Verified paths

| Path | Result |
| --- | --- |
| Release checksum and installer preparation | Passed |
| Qwen model identity and real generation | Passed |
| Full service preflight | 12/12 checks passed |
| Health and readiness | `ok` / `ready` |
| Ingest and read-your-writes recall | Passed |
| Memory graph overview, neighbors, evidence, and recall trace | Passed |
| Unauthenticated API rejection | HTTP 401 |
| Source provenance | Source IDs, source journal, source commits, origin job, and ingest metadata linked |
| Python SDK cross-agent recall | Agent B recalled Agent A's committed memory before writing its own turn |
| Personal knowledge base | Ready in 31.868 seconds |
| Personal knowledge output | 1 domain, 2 pages, 6 claims, 7 evidence records |
| Personal knowledge generator | Local personal-knowledge agent using the configured Qwen model |
| Temporary validation credentials | Revoked after every test |

The provenance result means every tested memory could be traced through the audit chain to its source record and ingest operation. Derived prompt rows do not repeat every timestamp and application field inline; those fields remain available through the durable source journal and operation metadata.

## Release-candidate fixes required

1. Bound `huggingface_hub` below 1.0 to remain compatible with the current Transformers dependency.
2. Download only runtime-required BGE files, disable Xet by default for portable downloads, and verify the main model artifacts by SHA-256.
3. Ship and install the reranker `TMCRA_MODEL_MANIFEST.json` required by runtime verification.
4. Resolve custom root and Python paths after loading `service.env` in the API and maintenance controls.
5. Make the preflight script executable directly from any working directory.
6. Point `TMCRA_INTEGRATED_REPO` at the installed source tree so graph code is available.
7. Allow the valid `dedicated-local-slot` value in the projection progress API response model.
8. Keep the Python-SDK launch smoke out of the standalone service archive; the self-contained commercial API smoke remains included.

## Host-specific observations

- The minimized Ubuntu image did not include `ensurepip` or the `python3-venv` package. Validation used the already installed `uv` runtime to create the isolated environment without changing host packages.
- Direct GitHub and Hugging Face access timed out from this host. Release and model downloads required reachable mirrors. This is a network condition operators should account for with the documented mirror overrides.
