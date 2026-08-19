#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$REPO_ROOT/02-tmcra-memory-api/deploy/install-tmcra.sh" "$@"
