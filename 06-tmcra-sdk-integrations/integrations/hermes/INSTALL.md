# Install and Configure

Hermes documents standalone memory providers as plugins installed under the
active `$HERMES_HOME/plugins/` directory. Copy or link this directory there:

```bash
cp -R /path/to/integrations/hermes "$HERMES_HOME/plugins/tmcra-hermes"
```

The same directory can be installed as a Python package for Hermes builds that
use the `hermes_agent.plugins` entry-point group:

```bash
python -m pip install /path/to/integrations/hermes
```

Use the Hermes provider setup command, or set the required environment
variables in the service account that runs Hermes. Do not put the API key in a
Hermes prompt, plugin config object, repository file, or model-visible tool.

Select the provider in Hermes configuration:

```yaml
memory:
  provider: tmcra-hermes
```

Then verify discovery and activation with the Hermes memory/plugin status
commands available in the installed release. The provider must report as
available only when all required environment variables pass validation.

## Operational checks

1. Confirm `TMCRA_BASE_URL` is an `https://` URL and the API key has TMCRA
   `memory:read` and `memory:write` permissions.
2. Confirm the queue path is absolute and writable by the Hermes service
   account. The plugin writes it with owner-only permissions where supported.
3. Send one test turn and check the TMCRA service for one recall request and
   one ingest request. Replaying the same completed turn must reuse the same
   idempotency key.
4. Stop TMCRA temporarily, complete a turn, then restore it. The queue should
   drain without creating a second copy of the turn.

For Hermes releases whose memory-provider discovery predates user-plugin
scanning, use the release's documented compatibility path for a memory
provider (or install the wheel through the entry-point path above); do not edit
Hermes core files to load this integration.
