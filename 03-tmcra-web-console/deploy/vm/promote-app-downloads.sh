#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: promote-app-downloads.sh STAGING_DIR DOWNLOADS_DIR MAC_RELEASE_NAME" >&2
  exit 64
fi

stage="$(realpath -e "$1")"
downloads="$(realpath -e "$2")"
mac_release_name="$3"

case "$stage" in
  /opt/tmcra-release/shared/downloads-staging/*) ;;
  *) echo "staging directory is outside the release boundary" >&2; exit 65 ;;
esac

if [[ "$downloads" != "/opt/tmcra-release/shared/downloads" ]]; then
  echo "downloads directory is not the production release boundary" >&2
  exit 65
fi

if [[ ! "$mac_release_name" =~ ^[0-9A-Za-z.-]+$ ]]; then
  echo "macOS release name is invalid" >&2
  exit 65
fi

mac_release="$downloads/macos-releases/$mac_release_name"
[[ -d "$mac_release" ]] || { echo "macOS release directory is missing" >&2; exit 66; }
chmod -R a+rX "$mac_release"

publish_file() {
  local source="$1"
  local destination="$2"
  local temporary="${destination}.tmp.$$"
  [[ -s "$source" ]] || { echo "required release file is missing: $source" >&2; exit 66; }
  install -m 0644 "$source" "$temporary"
  mv -f "$temporary" "$destination"
}

mkdir -p "$downloads/mobile/android" "$downloads/mobile/ios"

publish_file "$stage/TMCRA-Memory-Setup-latest.exe" "$downloads/TMCRA-Memory-Setup-latest.exe"
publish_file "$stage/TMCRA-Memory-Setup-latest.exe.sha256" "$downloads/TMCRA-Memory-Setup-latest.exe.sha256"
publish_file "$stage/tmcra-memory-desktop-release.json" "$downloads/tmcra-memory-desktop-release.json"

publish_file "$stage/TMCRA-Memory-Mobile-latest.apk" "$downloads/TMCRA-Memory-Mobile-latest.apk"
publish_file "$stage/TMCRA-Memory-Mobile-latest.apk.sha256" "$downloads/TMCRA-Memory-Mobile-latest.apk.sha256"
publish_file "$stage/tmcra-memory-mobile-android-release.json" "$downloads/tmcra-memory-mobile-android-release.json"
publish_file "$stage/mobile/android/TMCRA-Memory-Mobile-0.3.0-rc2.apk" "$downloads/mobile/android/TMCRA-Memory-Mobile-0.3.0-rc2.apk"
publish_file "$stage/mobile/android/TMCRA-Memory-Mobile-0.3.0-rc2.aab" "$downloads/mobile/android/TMCRA-Memory-Mobile-0.3.0-rc2.aab"
publish_file "$stage/mobile/ios/TMCRA-Memory-Mobile-iOS-Xcode-0.3.0-rc2.zip" "$downloads/mobile/ios/TMCRA-Memory-Mobile-iOS-Xcode-0.3.0-rc2.zip"

mac_link="$downloads/.macos-current.$$"
ln -s "macos-releases/$mac_release_name" "$mac_link"
mv -Tf "$mac_link" "$downloads/macos-current"

release_marker="$downloads/.macos-current.release.$$"
printf '%s\n' "$mac_release_name" > "$release_marker"
chmod 0644 "$release_marker"
mv -f "$release_marker" "$downloads/macos-current.release"

echo "Published Windows, Android, iOS source, and macOS $mac_release_name"
