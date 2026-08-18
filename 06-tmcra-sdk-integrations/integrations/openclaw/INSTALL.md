# Install and Configure

## Local checkout

From the OpenClaw host, install this directory as a linked plugin:

```bash
openclaw plugins install --link /path/to/integrations/openclaw
```

Enable it if the host did not enable it during installation, then restart the
Gateway:

```bash
openclaw plugins enable tmcra-openclaw
openclaw gateway restart
```

OpenClaw requires conversation access for hooks that receive prompt and
assistant content. Add `hooks.allowConversationAccess: true` to the plugin entry
as shown in the README. Keep the TMCRA key and identity secret in the Gateway
service environment, never in OpenClaw user-facing prompts or model-visible
configuration.

## Runtime verification

Use the official runtime inspection command after the restart:

```bash
openclaw plugins inspect tmcra-openclaw --runtime --json
```

The report should classify the plugin as hook-only and list
`before_prompt_build`, `agent_end`, `gateway_start`, and `gateway_stop`.

## Production notes

Use a queue path outside the workspace, owned by the Gateway service account.
The plugin creates the parent directory with mode `0700` and the queue file
with mode `0600`. Back up or monitor this file as local durable state; it may
contain pending user and assistant turn content while TMCRA is unavailable.

The TMCRA service requires `Authorization: Bearer ...`, an `Idempotency-Key`
for ingest, and opaque tenant/scope isolation. This plugin supplies those
headers and fields internally. It does not expose an end-user API key or add a
model-visible tool for TMCRA.
