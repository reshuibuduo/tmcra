# TMCRA deployment guide

This is the supported public deployment profile for the Memory API. It is a
runbook for an operator's own infrastructure, not a hosted TMCRA service.

## Supported hardware boundary

The complete public release was developed on a **single NVIDIA RTX 5090** GPU.
Use that as the reference development configuration: one service process owns
one startup-preloaded GPU retrieval engine, while a resident Writer pool handles
durable writes.

For the published default profile, provision **at least 32 GB of GPU VRAM**.
This is a planning recommendation, not an SLA. Actual usable concurrency and
latency depend on embedding, reranking, and generation model versions; context
lengths; batching; provider configuration; host RAM; disk; and traffic shape.

The public release does not declare a supported multi-GPU topology. Operators
that require tensor/model parallelism, sharding, cross-device retrieval,
multi-process scheduling, or multi-GPU failover must engineer and validate it
themselves. Before exposing such a deployment, test model placement, GPU memory
pressure, request routing, index visibility, worker replacement, readiness,
rollback, and representative benchmark/production traffic.

## Production topology

```text
Client application or agent
        |
        | HTTPS, authenticated product identity
        v
Product BFF or trusted reverse proxy
        |
        | trusted internal hop
        v
TMCRA Memory API process (single-GPU reference profile)
  |- preloaded retrieval engine
  |- resident Writer process pool
  |- control SQLite: keys, jobs, leases, receipts, costs
  |- per-tenant/scope Source databases and immutable index generations
  `- operator-selected model/provider endpoints
```

The reverse proxy terminates TLS. Configure
`TMCRA_SERVICE_TLS_PROXY_MODE=trusted_proxy` only when the Memory API bind
address is protected by that trusted proxy. Do not expose the internal process
directly to the internet or pass tenant keys to a browser/mobile client.

For a Web Console deployment, the BFF verifies account identity and resolves a
server-owned tenant/scope binding before it calls the API. The browser does not
hold a production Memory API key.

## Single-GPU install

The deployable service is component 02. The exact model paths and environment
settings are described in `deploy/tmcra-service.env.example`.

```bash
git clone https://github.com/reshuibuduo/tmcra.git
cd tmcra/02-tmcra-memory-api

python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-tmcra-service.txt

# Download the public BGE embedding and cross-encoder models specified in
# component 10's MODEL_PROVENANCE.md. Copy the bundled TMCRA reranker into the
# configured models directory.

sudo install -d -m 700 /etc/tmcra
sudo cp deploy/tmcra-service.env.example /etc/tmcra/service.env
sudo cp deploy/writer.env.example /etc/tmcra/writer.env
sudoedit /etc/tmcra/service.env
sudoedit /etc/tmcra/writer.env

python ops/run_tmcra_service_preflight.py --env-file /etc/tmcra/service.env
```

`writer.env` contains the local-model route and credential boundary and must
stay outside Git. The default production generation model is
`Qwen3.6-35B-A3B` (35B total parameters, about 3B active parameters per token),
served through the loopback OpenAI-compatible endpoint. Set the public HTTPS
origin, state/model paths, CUDA device settings, and provider configuration in
`service.env`. Do not publish either file, the state directory, or any key file.

The exact Writer, reviewer, slow-graph, embedding, runtime-reranker,
cross-encoder, recall-planner, evidence-compiler, and outer-agent boundaries are
documented in [PRODUCTION_MODEL_STACK.md](PRODUCTION_MODEL_STACK.md). The
service template declares `TMCRA_EMBEDDING_MODEL`, `TMCRA_CROSS_MODEL`,
`TMCRA_CHECKPOINT`, and recall-pool GPU estimates explicitly; the Writer
template contains operator-owned path and key placeholders that must be set
before startup.

### Start the default Qwen3.6 model route

Place the operator-licensed `Qwen3.6-35B-A3B` GGUF and a CUDA build of
`llama-server` at the paths configured by `TMCRA_LOCAL_LLM_MODEL` and
`TMCRA_LLAMA_SERVER_BIN`. The repository does not redistribute Qwen weights.
The validated public profile uses the alias
`tmcra-qwen3.6-35b-a3b-iq3s`, loopback port `11435`, and at least 65,536 context
tokens per parallel slot.

Create one mode-restricted lane key at the path declared by
`TMCRA_LOCAL_WRITER_API_KEY_FILE` in `writer.env`:

```bash
sudo install -d -m 700 /opt/tmcra-data/local-llm/secrets
sudo install -m 600 /dev/null /opt/tmcra-data/local-llm/secrets/qwen36-api.key
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' \
  | sudo tee /opt/tmcra-data/local-llm/secrets/qwen36-api.key >/dev/null
```

Start and verify the local model before running the TMCRA service preflight:

```bash
export TMCRA_SERVICE_ENV_FILE=/etc/tmcra/service.env
bash 02-tmcra-memory-api/deploy/tmcra-local-llm-control.sh start
bash 02-tmcra-memory-api/deploy/tmcra-local-llm-control.sh status
python 02-tmcra-memory-api/ops/run_tmcra_service_preflight.py \
  --env-file /etc/tmcra/service.env
```

The control script loads both `service.env` and its declared `TMCRA_WRITER_ENV`,
validates the exact executable/model/alias/port/context contract, then probes
every configured authenticated lane.

Install `deploy/tmcra-memory-api.service` on a systemd host. When systemd is
not available, use `deploy/tmcra-memory-api-control.sh start`; its supervisor
validates the deployment and shared-core manifest before entering the restart
loop and writes durable PID files.

## Preflight and readiness

Production must use:

```text
TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=full
TMCRA_SERVICE_WRITER_EXECUTION_MODE=resident
TMCRA_SERVICE_WRITER_POOL_SIZE=4
TMCRA_SERVICE_WRITER_POOL_STARTUP_TIMEOUT_SECONDS=120
TMCRA_SERVICE_WRITER_POOL_REQUEST_TIMEOUT_SECONDS=900
```

Before the service becomes ready, full preflight checks shared algorithm
hashes, deployment path writes, SQLite integrity/locking, free disk, provider
configuration, active generation checksums, CUDA allocation, BGE-M3 and
cross-encoder inference, graph adapter availability, and every Writer
handshake. It makes no paid provider call. The report is atomically written to
`$TMCRA_SERVICE_STATE_DIR/startup_preflight.json`.

Use `/healthz` only for process liveness. Use `/readyz` for routing and rollout
decisions: it reads the cached preflight result plus current Writer-pool and
shared-core state without repeatedly loading models or hashing indexes. A
listening port is not proof of readiness.

## Capacity and runtime tuning

Start from the default single-GPU profile before increasing concurrency. Observe
the staff-only runtime endpoint from a server-to-server control plane, then
tune one variable at a time with representative traffic:

- GPU VRAM headroom and retrieval latency under the selected model set;
- resident Writer pool size and provider-key concurrency;
- tenant/global queue limits and request budgets;
- fast-index threshold (default: 16 messages or two seconds);
- slow-graph eligibility/cooldown (batched by design, not per message);
- request duration, job wait time, provider cost, and p95/p99 latency.

The runtime endpoint must use a distinct staff key kept only in server
environment. It reports bounded operational facts and generic failure
categories, not raw customer content or credentials.

## Upgrade, rollback, and backup discipline

Treat an update as a new deployment artifact:

1. Build a clean checkout and verify its release checksum.
2. Run the no-paid-call preflight with the target environment and model paths.
3. Start the target release and wait for `/readyz` before shifting traffic.
4. Set validated release metadata such as release ID, SHA-256, channel, canary
   percentage, and rollback release ID when using the staff monitoring view.
5. Keep a tested backup/restore procedure for the control SQLite database and
   the per-scope Source/index state before changing storage or model policy.

Do not enable `TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=off` in production. It
exists only for unit tests and local contract development. The supported public
default keeps `TMCRA_LEARNED_GRAPH_ENABLED=0`; enabling it requires separately
licensed node/path checkpoints and a separate validation plan.

## Multi-GPU operator checklist

Multi-GPU changes are an operator-owned extension. At minimum, document and
test all of the following before calling the deployment production-ready:

- exact mapping of each model and process to a GPU;
- who owns a tenant/scope request when engines are replicated;
- how immutable index generations become visible across processes/devices;
- worker-loss and restart behavior with an in-flight external Writer action;
- per-device memory limits, admission control, and overload rejection;
- readiness semantics when only part of a multi-GPU pool is available;
- benchmark parity, correctness under failover, and a rollback path to the
  supported single-GPU configuration.
