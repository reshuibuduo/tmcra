# Changelog

## 1.0.0-rc.1 - 2026-09-06

- Discover the shared private local installation without a TMCRA account. Explicit local identities allow numeric loopback HTTP, ignore inherited cloud credentials/proxies, and block stale cloud requests after selection.

- Add session memory controls, task continuation, bounded evidence selection, and generation-aware durable replay.
- Add targeted feedback with exact-source previews and interactive MCP elicitation confirmation; rejection or unsupported confirmation leaves memory unchanged.
- Keep correction discussions out of automatic writeback; effective correction requires the matching Memory API update.
- Publish nine tools in the runtime and MCPB manifest.

- Normalize repository, release, and companion Codex plugin links after the standalone repository renames.

## 0.5.1 - 2026-08-16

- Make the Windows setup test robust to short-path and long-path normalization on GitHub runners.
- Shorten the public server description to the official MCP Registry limit.

## 0.5.0 - 2026-08-16

- Publish the TMCRA MCP Server as an independent Apache-2.0 repository.
- Add an MCPB package for cross-platform `uv` installation.
- Preserve seven tested recall, ingest, lifecycle, reconciliation, and job tools.
- Add public build metadata, security policy, bilingual documentation, and MCP Registry metadata.
