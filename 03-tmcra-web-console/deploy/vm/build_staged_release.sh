#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: build_staged_release.sh <project> <deployment-env> <node>" >&2
  exit 2
fi

project="$1"
deployment_env="$2"
node="$3"

case "$project:$deployment_env:$node" in
  /*:/*:/*) ;;
  *) echo "release build paths must be absolute" >&2; exit 2 ;;
esac
[ -d "$project" ] && [ ! -L "$project" ]
[ -f "$deployment_env" ] && [ ! -L "$deployment_env" ]
[ -x "$node" ]
[ -f "$project/node_modules/vinext/dist/cli.js" ]

# Vite snapshots this explicit allowlist into the local Workerd preview
# bundle. Parse name/value pairs without evaluating shell syntax in secrets.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ""|\#*) continue ;;
  esac
  name="${line%%=*}"
  value="${line#*=}"
  if [ "$name" = "$line" ]; then
    echo "invalid deployment environment line" >&2
    exit 2
  fi
  case "$name" in
    [A-Za-z_][A-Za-z0-9_]*) export "$name=$value" ;;
    *) echo "invalid deployment environment name" >&2; exit 2 ;;
  esac
done <"$deployment_env"

export TMCRA_PROJECT_ROOT="$project"
export PATH="$(dirname "$node"):$PATH"
cd "$project"
exec "$node" "$project/node_modules/vinext/dist/cli.js" build
