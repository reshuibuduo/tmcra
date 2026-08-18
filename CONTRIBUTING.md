# Contributing

Contributions are welcome. Please open an issue or discussion before making a
large architectural change, then submit a focused pull request with tests and
documentation for the affected component.

Before submitting changes:

1. Never commit credentials, private keys, customer data, production logs,
   databases, local caches, or generated build outputs.
2. Preserve the memory API shared-core manifest contract. If a pinned algorithm
   file changes, regenerate the manifest and run the service preflight before
   packaging.
3. Run the relevant component tests and document any deployment, compatibility,
   license, model, or dataset change.
4. State the license and provenance of every added third-party asset. Do not
   add a model checkpoint or audio sample without an explicit redistribution
   basis.
