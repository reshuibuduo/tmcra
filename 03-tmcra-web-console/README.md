# TMCRA Commercial Console

The customer console runs on [vinext](https://github.com/cloudflare/vinext)
and Cloudflare Workers. D1 owns account profiles, isolated personal memory-space
bindings, enterprise organizations, agents, memberships, API-key metadata, and
control-plane events. Production memory snapshots remain in the TMCRA Memory
API.

The public control surfaces are deliberately separate:

- `/personal` and `/api/personal/*` operate one personal memory space.
- `/enterprise` and `/api/enterprise/*` operate organization resources.
- `/internal` and `/api/internal` are the TMCRA staff control plane.
- `/console` is a compatibility dispatcher; it does not own a data model.

An unclassified login is sent to `/account-setup` and does not automatically
create an organization or personal memory space.

## Prerequisites

- Node.js `>=22.13.0`

## Quick Start

```bash
npm install
npm run dev
npm run build
```

This starter does not use `wrangler.jsonc`.

## Production memory graph

The enterprise Memory view uses `/api/enterprise/graph`; the personal Memory
view uses `/api/personal/graph`. The browser
never receives a TMCRA API key or chooses a production scope directly. The BFF
first verifies the signed-in account domain, then resolves either the D1
organization/agent pair or the D1 personal-space identity through a separate
server-owned binding.

Configure these server secrets in the hosting environment:

- `TMCRA_MEMORY_API_BASE_URL`: HTTPS origin of the production Memory API.
- `TMCRA_MEMORY_API_CONTROL_BASE_URL`: optional server-to-server API origin. On a co-located GPUHome deployment, use `http://127.0.0.1:2009` together with `TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK=1`; clients still receive `TMCRA_MEMORY_API_BASE_URL`.
- `TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK`: accepts exactly `1` to permit HTTP only for a literal `127.0.0.1` control URL. The GPUHome runtime materializes a port-scoped service binding for that address; Sites builds do not receive the binding.
- `TMCRA_MEMORY_API_TENANT_BINDINGS`: JSON object keyed by the D1 organization
  ID. Each value contains a scoped `apiKey` and either `defaultScope` or
  `scopeByAgent` entries keyed by the D1 agent ID or slug.
- `TMCRA_MEMORY_API_PERSONAL_BINDINGS`: JSON object keyed by the D1 personal
  memory-space ID. Each value contains a scoped `apiKey` and optional
  `defaultScope`, which must match the D1 personal scope exactly.

Example shape, using placeholders only:

```json
{
  "org_example": {
    "apiKey": "tmcra_replace_with_scoped_key",
    "defaultScope": "customer-example",
    "scopeByAgent": {
      "agt_example": "customer-example-agent"
    }
  }
}
```

The initial graph request returns slow-memory summaries only. Fast-memory and
Source nodes are fetched by explicit neighbor expansion, and verbatim Source
text is returned only by the evidence endpoint. All responses are `no-store`.
The UI provides semantic, timeline, and table views over the same projection.

Sigma.js and Graphology power the semantic canvas. Both are MIT licensed; the
required notices are in `THIRD_PARTY_NOTICES.md`.

## Included Shape

- edit site code under `app/`
- `.openai/hosting.json` declares optional Sites D1 and R2 bindings
- `vite.config.ts` simulates declared bindings for local development
- `db/schema.ts` starts intentionally empty
- `examples/d1/` contains an optional D1 example surface
- `drizzle.config.ts` supports local migration generation when needed

## Account identity headers

Application code reads the current account email from
`oai-authenticated-user-email`. The trusted hosting boundary owns this header:
OpenAI Sites supplies it on the backup deployment, while the GPUHome gateway
supplies it from its native TMCRA account session on the primary deployment.
The gateway removes every caller-supplied `oai-authenticated-*` header before
adding the authenticated identity.

SIWC-authenticated workspace sites may also receive
`oai-authenticated-user-full-name` when the user's SIWC profile has a non-empty
`name` claim. The full-name value is percent-encoded UTF-8 and is accompanied by
`oai-authenticated-user-full-name-encoding: percent-encoded-utf-8`.

Treat the full name as optional and fall back to email when it is absent:

```tsx
import { headers } from "next/headers";

export default async function Home() {
  const requestHeaders = await headers();
  const email = requestHeaders.get("oai-authenticated-user-email");
  const encodedFullName = requestHeaders.get("oai-authenticated-user-full-name");
  const fullName =
    encodedFullName &&
    requestHeaders.get("oai-authenticated-user-full-name-encoding") ===
      "percent-encoded-utf-8"
      ? decodeURIComponent(encodedFullName)
      : null;

  const displayName = fullName ?? email;
  // ...
}
```

## Compatible account sign-in paths

Import the ready-to-use helpers from `app/chatgpt-auth.ts` when the site needs
optional or required ChatGPT sign-in:

- Use `getChatGPTUser()` for optional signed-in UI.
- Use `requireChatGPTUser(returnTo)` for server-rendered pages that should send
  anonymous visitors through Sign in with ChatGPT.
- Use `chatGPTSignInPath(returnTo)` and `chatGPTSignOutPath(returnTo)` for
  browser links or actions.
- Pass a same-origin relative `returnTo` path for the destination after sign-in
  or sign-out. The helper validates and safely encodes it.
- Mark protected pages with `export const dynamic = "force-dynamic"` because
  they depend on per-request identity headers.

The application deliberately keeps the compatibility paths
`/signin-with-chatgpt` and `/signout-with-chatgpt`. On OpenAI Sites, Dispatch
owns those paths. On the primary GPUHome deployment, `deploy/gpuhome/proxy.py`
serves the TMCRA Account register/sign-in pages and owns the session cookie.
Application routes must not implement those reserved paths. Routes that do not
call the helper remain anonymous-compatible.

On the OpenAI Sites backup, SIWC establishes identity only; it does not prove
workspace membership. Use that hosting platform's access policy controls for
workspace-wide restrictions, or enforce explicit server-side membership or
allowlist checks. The primary GPUHome deployment instead uses the native TMCRA
Account session described below. Public marketing content remains anonymous on
both deployments.

## GPUHome native account gateway

The public gateway forwards `/console`, `/personal`, `/enterprise`,
`/account-setup`, and their customer APIs to the local vinext process. A valid
TMCRA Account session is required before it injects identity headers. The two
Codex device-flow endpoints `/api/device/v1/authorizations` and
`/api/device/v1/token` remain anonymous so a new installation can start and
poll authorization; approval and connection management remain authenticated.
`/internal` and `/api/internal` use the same verified TMCRA Account session as
the customer console, then enforce a separate internal RBAC policy. They also
fail closed unless the effective client IP matches `TMCRA_INTERNAL_ALLOWED_IPS`.
The gateway accepts its dedicated client-IP header only from
`TMCRA_TRUSTED_PROXY_IPS`; the production Nginx proxy overwrites that header.

Copy `deploy/gpuhome/deployment.env.example` to the shared deployment env and
set every blank secret. `TMCRA_SESSION_SECRET` must be at least 32 bytes and
should be generated independently of every other key. The default persistent
account database is
`/opt/tmcra/tmcra-official/shared/auth/accounts.sqlite3`; releases only link to it,
so account and session state survives release switches and rollback. The
gateway stores PBKDF2-SHA256 password hashes with per-account random salts,
HMAC-hashed opaque sessions, and persistent rate-limit counters. Production
startup refuses a PBKDF2 work factor below 310,000.

`TMCRA_PUBLIC_HOSTS` is an exact comma-separated authority allowlist. Include
the port when the public URL uses a non-default port (for example,
`euvbyqa1jpvdm7yq-2000.sc01-webservice.gpuhome.cc:8443`). Requests with any
other Host or port are rejected before authentication or proxying.

When the website VM reaches a separate GPUHome API through
`tmcra-api-tunnel.service`, keep `TMCRA_MEMORY_API_BASE_URL=https://api.tmcra.com`
for clients, but set the website deployment environment to
`TMCRA_MEMORY_API_CONTROL_BASE_URL=http://127.0.0.1:22009` and
`TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK=1`. Copy
`deploy/vm/api-tunnel.env.example` to
`/opt/tmcra-release/shared/api-tunnel.env`, update the assigned GPUHome SSH
port there, and restart both `tmcra-api-tunnel.service` and
`tmcra-site.service`. The site must be rebuilt after changing these Worker
variables because the Vite/Cloudflare runtime snapshots them during release
construction.

Set `TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL` to the normalized email address of
the initial platform owner. Register and verify that address through the normal
TMCRA Account flow. The first successful internal request creates the locked
owner record; later staff access is controlled by internal RBAC.

Desktop installers are deployed as separate artifacts so they never enter
the Workers static-asset bundle. Upload them to temporary absolute paths and
pass `TMCRA_INSTALLER` plus `TMCRA_INSTALLER_SHA256` alongside the normal
`TMCRA_RELEASE`, `TMCRA_ARCHIVE`, and `TMCRA_ARCHIVE_SHA256` variables. The
release script also requires `TMCRA_DESKTOP_UPDATE_DIR` when the desktop
version changes. Point it at the generated
`.release-assets/desktop/windows/x64` directory containing `latest.yml`, the
versioned installer, and its blockmap. All updater artifacts are checked
against the public release manifest before the script switches the site,
latest installer, and update feed with rollback on errors or termination
signals.

macOS releases use separate `x64` and `arm64` channels. Build them on macOS
with `npm run dist:mac:x64` and `npm run dist:mac:arm64`; production publishing
requires a verified Developer ID signature and Apple notarization. After that
verification, `TMCRA_MAC_RELEASE_VERIFIED=1 npm run publish:mac` writes the two
DMG aliases, SHA-256 files, architecture-specific release manifests, and
`.release-assets/desktop/macos/{x64,arm64}` update feeds. Unsigned preview
publishing is permitted only with `TMCRA_ALLOW_UNSIGNED_MAC_PREVIEW=1`; its
manifest disables automatic updates and must be labeled as preview software.

## Useful Commands

- `npm run dev`: start local development
- `npm run build`: verify the vinext build output
- `npm test`: build the console and verify security and graph contracts
- `npm run test:security`: start an isolated local Worker, run active internal-control-plane probes, then verify the GPUHome gateway security suite
- `npm run lint`: run the application linter
- `npm run db:generate`: generate Drizzle migrations after schema changes

## Learn More

- [vinext Documentation](https://github.com/cloudflare/vinext)
- [Drizzle D1 Guide](https://orm.drizzle.team/docs/get-started/d1-new)
