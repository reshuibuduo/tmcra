#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: publish-windows-desktop-release.sh STAGING_DIR DOWNLOADS_DIR VERIFIER" >&2
  exit 64
fi

stage="$(realpath -e "$1")"
downloads="$(realpath -e "$2")"
verifier="$(realpath -e "$3")"

case "$stage" in
  /opt/tmcra-release/shared/downloads-staging/desktop-windows-*) ;;
  *) echo "staging directory is outside the desktop release boundary" >&2; exit 65 ;;
esac

if [[ "$downloads" != "/opt/tmcra-release/shared/downloads" ]]; then
  echo "downloads directory is not the production release boundary" >&2
  exit 65
fi

case "$verifier" in
  /opt/tmcra-release/releases/*/deploy/gpuhome/verify_desktop_release.py) ;;
  *) echo "desktop verifier is outside an immutable site release" >&2; exit 65 ;;
esac

[[ -f "$verifier" && ! -L "$verifier" ]] || {
  echo "desktop verifier is not a regular file" >&2
  exit 66
}

installer_name=TMCRA-Memory-Setup-latest.exe
manifest="$stage/tmcra-memory-desktop-release.json"
checksum="$stage/$installer_name.sha256"
installer="$stage/$installer_name"
update_source="$stage/desktop/windows/x64"

for file in "$manifest" "$checksum" "$installer"; do
  [[ -s "$file" && ! -L "$file" ]] || {
    echo "required release file is missing or unsafe: $file" >&2
    exit 66
  }
done
[[ -d "$update_source" && ! -L "$update_source" ]] || {
  echo "desktop updater directory is missing or unsafe" >&2
  exit 66
}

python3 "$verifier" \
  "$manifest" "$checksum" "$installer_name" "$installer" \
  --update-dir "$update_source" >/dev/null

installer_final="$downloads/$installer_name"
checksum_final="$downloads/$installer_name.sha256"
manifest_final="$downloads/tmcra-memory-desktop-release.json"
update_final="$downloads/desktop/windows/x64"
transaction="$downloads/.desktop-windows-publish-$$"
backup="$transaction/backup"
new_update="$transaction/new-update"
mkdir -p "$backup"
chmod 0755 "$transaction" "$backup"

installer_had_previous=0
checksum_had_previous=0
manifest_had_previous=0
installer_activated=0
checksum_activated=0
manifest_activated=0
update_had_previous=0
update_activated=0
complete=0

backup_file() {
  local source=$1
  local destination=$2
  if [[ -e "$source" || -L "$source" ]]; then
    [[ -f "$source" && ! -L "$source" ]] || {
      echo "refusing to replace a non-regular release file: $source" >&2
      return 1
    }
    ln -- "$source" "$destination"
    return 0
  fi
  return 2
}

publish_file() {
  local source=$1
  local destination=$2
  local temporary="${destination}.publish-$$"
  install -m 0644 -- "$source" "$temporary"
  mv -f -- "$temporary" "$destination"
}

rollback_file() {
  local destination=$1
  local saved=$2
  local activated=$3
  local had_previous=$4
  [[ "$activated" -eq 1 ]] || return 0
  if [[ "$had_previous" -eq 1 ]]; then
    mv -f -- "$saved" "$destination"
  else
    rm -f -- "$destination"
  fi
}

cleanup_transaction() {
  case "$transaction" in
    "$downloads"/.desktop-windows-publish-*) rm -rf -- "$transaction" ;;
    *) echo "refusing to remove unexpected transaction path" >&2; return 1 ;;
  esac
}

rollback() {
  local code=${1:-1}
  trap - ERR INT TERM
  set +e
  rollback_file "$manifest_final" "$backup/manifest" "$manifest_activated" "$manifest_had_previous"
  rollback_file "$checksum_final" "$backup/checksum" "$checksum_activated" "$checksum_had_previous"
  rollback_file "$installer_final" "$backup/installer" "$installer_activated" "$installer_had_previous"
  if [[ "$update_activated" -eq 1 ]]; then
    rm -rf -- "$update_final"
    if [[ "$update_had_previous" -eq 1 ]]; then
      mv -- "$backup/update" "$update_final"
    fi
  fi
  cleanup_transaction || true
  echo "DESKTOP_PUBLISH_FAILED_ROLLED_BACK exit_code=$code" >&2
  exit "$code"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

mkdir -p "$downloads/desktop/windows"
mv -- "$update_source" "$new_update"
if [[ -e "$update_final" || -L "$update_final" ]]; then
  [[ -d "$update_final" && ! -L "$update_final" ]] || {
    echo "refusing to replace an unsafe updater path" >&2
    false
  }
  mv -- "$update_final" "$backup/update"
  update_had_previous=1
fi
mv -- "$new_update" "$update_final"
update_activated=1

if backup_file "$installer_final" "$backup/installer"; then installer_had_previous=1; else [[ $? -eq 2 ]]; fi
publish_file "$installer" "$installer_final"
installer_activated=1

if backup_file "$checksum_final" "$backup/checksum"; then checksum_had_previous=1; else [[ $? -eq 2 ]]; fi
publish_file "$checksum" "$checksum_final"
checksum_activated=1

if backup_file "$manifest_final" "$backup/manifest"; then manifest_had_previous=1; else [[ $? -eq 2 ]]; fi
publish_file "$manifest" "$manifest_final"
manifest_activated=1

python3 "$verifier" \
  "$manifest_final" "$checksum_final" "$installer_name" "$installer_final" \
  --update-dir "$update_final" >/dev/null

complete=1
trap - ERR INT TERM
cleanup_transaction
case "$stage" in
  /opt/tmcra-release/shared/downloads-staging/desktop-windows-*) rm -rf -- "$stage" ;;
esac
echo "DESKTOP_PUBLISH_COMPLETE"
