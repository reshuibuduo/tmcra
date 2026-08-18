#!/usr/bin/env bash
set -Eeuo pipefail

CI_ROOT="${TMCRA_ANDROID_CI_ROOT:-/opt/tmcra-data/tmcra-android-ci}"
SDK_ROOT="${ANDROID_SDK_ROOT:-$CI_ROOT/sdk}"
APP_APK="${TMCRA_APP_APK:-$CI_ROOT/artifacts/TMCRA-server-ci-x86_64-0.2.0.apk}"
TEST_APK="${TMCRA_TEST_APK:-$CI_ROOT/artifacts/TMCRA-server-ci-x86_64-0.2.0-androidTest.apk}"
FIXTURE_DIR="${TMCRA_FIXTURE_DIR:-$CI_ROOT/fixtures}"
REPORT_ROOT="${TMCRA_REPORT_ROOT:-$CI_ROOT/reports}"
AVD_NAME="${TMCRA_AVD_NAME:-tmcra-ci-atd-api30}"
SYSTEM_IMAGE="${TMCRA_SYSTEM_IMAGE:-system-images;android-30;aosp_atd;x86_64}"
BUILD_TOOLS_VERSION="${TMCRA_BUILD_TOOLS_VERSION:-36.0.0}"
PACKAGE="com.tmcra.memory.mobile.debug"
PROBE_ACTIVITY="$PACKAGE/com.tmcra.memory.mobile.audio.DiarizationProbeActivity"
APP_CONTEXT_TEST="com.getcapacitor.myapp.ExampleInstrumentedTest"
RESULT_PATH="files/fixtures/last-diarization-result.json"
PROGRESS_PATH="files/fixtures/last-diarization-progress.json"
PROBE_TIMEOUT_SECONDS="${TMCRA_PROBE_TIMEOUT_SECONDS:-3600}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="$REPORT_ROOT/$RUN_ID"
EMULATOR_PID=""
CURRENT_PROBE="gate"
DIAGNOSTICS_CAPTURED=false

export ANDROID_SDK_ROOT="$SDK_ROOT"
export ANDROID_HOME="$SDK_ROOT"
export PATH="$SDK_ROOT/cmdline-tools/latest/bin:$SDK_ROOT/platform-tools:$SDK_ROOT/emulator:$PATH"

mkdir -p "$REPORT_DIR"

log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"
}

capture_android_diagnostics() {
  [[ "$DIAGNOSTICS_CAPTURED" == "false" ]] || return 0
  DIAGNOSTICS_CAPTURED=true
  command -v adb >/dev/null 2>&1 || return 0
  command -v timeout >/dev/null 2>&1 || return 0
  timeout --kill-after=2 10 adb -e get-state </dev/null 2>/dev/null \
    | grep -Fq device || return 0
  timeout --kill-after=5 30 adb -e logcat -d -b all -v threadtime \
    </dev/null >"$REPORT_DIR/${CURRENT_PROBE}-logcat.txt" 2>&1 || true
  timeout --kill-after=5 30 adb -e shell ps -A \
    </dev/null >"$REPORT_DIR/${CURRENT_PROBE}-processes.txt" 2>&1 || true
  timeout --kill-after=5 30 adb -e shell getprop \
    </dev/null >"$REPORT_DIR/${CURRENT_PROBE}-getprop.txt" 2>&1 || true
  timeout --kill-after=5 30 adb -e exec-out run-as \
    "$PACKAGE" cat "$PROGRESS_PATH" \
    </dev/null >"$REPORT_DIR/${CURRENT_PROBE}-progress-at-failure.json" 2>/dev/null || true
}

fail() {
  log "FAIL: $*"
  capture_android_diagnostics
  exit 1
}

cleanup() {
  trap - ERR
  set +e
  timeout --kill-after=2 15 adb -e emu kill </dev/null >/dev/null 2>&1
  if [[ -n "$EMULATOR_PID" ]]; then
    kill "$EMULATOR_PID" >/dev/null 2>&1
    wait "$EMULATOR_PID" >/dev/null 2>&1
  fi
}
trap cleanup EXIT

on_error() {
  local status=$?
  log "ERROR: status=$status line=${BASH_LINENO[0]} command=$BASH_COMMAND"
  capture_android_diagnostics
}
trap on_error ERR

for command in adb emulator avdmanager apkanalyzer python3 sha256sum timeout unzip; do
  command -v "$command" >/dev/null || fail "missing command: $command"
done
[[ "$PROBE_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] \
  && (( PROBE_TIMEOUT_SECONDS >= 60 )) \
  || fail "TMCRA_PROBE_TIMEOUT_SECONDS must be an integer of at least 60"
[[ -x "$SDK_ROOT/build-tools/$BUILD_TOOLS_VERSION/aapt2" ]] \
  || fail "missing Android Build Tools $BUILD_TOOLS_VERSION"
[[ -f "$APP_APK" ]] || fail "missing app APK: $APP_APK"
[[ -f "$TEST_APK" ]] || fail "missing test APK: $TEST_APK"
for fixture in 0-four-speakers-zh.wav 1-two-speakers-en.wav 2-two-speakers-en.wav; do
  [[ -f "$FIXTURE_DIR/$fixture" ]] || fail "missing fixture: $fixture"
done

log "Static APK gate"
mapfile -t packaged_abis < <(
  unzip -Z1 "$APP_APK" 'lib/*/*.so' | awk -F/ '{print $2}' | sort -u
)
[[ "${packaged_abis[*]:-}" == "x86_64" ]] \
  || fail "server APK must contain only x86_64, got: ${packaged_abis[*]:-none}"
constructor_count="$(
  timeout --kill-after=5 120 apkanalyzer dex packages --defined-only "$APP_APK" \
    | grep -F 'com.tmcra.memory.mobile.data.AudioMemoryStore <init>' \
    | wc -l
)"
[[ "$constructor_count" -eq 2 ]] \
  || fail "final DEX has $constructor_count AudioMemoryStore constructors; expected 2"
sha256sum "$APP_APK" "$TEST_APK" >"$REPORT_DIR/apk-sha256.txt"

if ! timeout --kill-after=5 60 avdmanager list avd | grep -Fq "Name: $AVD_NAME"; then
  log "Create AVD $AVD_NAME"
  printf 'no\n' | timeout --kill-after=5 120 avdmanager create avd \
    --force \
    --name "$AVD_NAME" \
    --package "$SYSTEM_IMAGE" \
    --device pixel_2
fi

log "Boot clean emulator without KVM acceleration"
rm -f "$REPORT_DIR/emulator.log"
emulator \
  -avd "$AVD_NAME" \
  -wipe-data \
  -no-window \
  -no-audio \
  -no-boot-anim \
  -no-snapshot \
  -gpu swiftshader_indirect \
  -accel off \
  -memory 4096 \
  -cores 4 \
  >"$REPORT_DIR/emulator.log" 2>&1 &
EMULATOR_PID=$!

android_ready() {
  local completed package_service settings_service activity_service system_server_pid
  completed="$(
    timeout --kill-after=2 10 adb -e shell getprop sys.boot_completed \
      </dev/null 2>/dev/null || true
  )"
  [[ "$completed" == "1" ]] || return 1
  package_service="$(
    timeout --kill-after=2 10 adb -e shell service check package \
      </dev/null 2>/dev/null || true
  )"
  settings_service="$(
    timeout --kill-after=2 10 adb -e shell service check settings \
      </dev/null 2>/dev/null || true
  )"
  activity_service="$(
    timeout --kill-after=2 10 adb -e shell service check activity \
      </dev/null 2>/dev/null || true
  )"
  system_server_pid="$(
    timeout --kill-after=2 10 adb -e shell pidof system_server \
      </dev/null 2>/dev/null || true
  )"
  [[ "$package_service" == *"found"* \
    && "$settings_service" == *"found"* \
    && "$activity_service" == *"found"* \
    && -n "$system_server_pid" ]]
}

boot_deadline=$((SECONDS + 1200))
stable_checks=0
stable_system_server_pid=""
while (( stable_checks < 7 )); do
  if android_ready; then
    current_system_server_pid="$(
      timeout --kill-after=2 10 adb -e shell pidof system_server \
        </dev/null 2>/dev/null || true
    )"
    if [[ -n "$current_system_server_pid" \
      && "$current_system_server_pid" == "$stable_system_server_pid" ]]; then
      stable_checks=$((stable_checks + 1))
    else
      stable_system_server_pid="$current_system_server_pid"
      stable_checks=1
    fi
  else
    stable_checks=0
    stable_system_server_pid=""
  fi
  (( SECONDS < boot_deadline )) || fail "emulator boot timeout"
  kill -0 "$EMULATOR_PID" 2>/dev/null || fail "emulator exited during boot"
  (( stable_checks >= 7 )) || sleep 10
done
log "Android services and system_server remained stable for 60 seconds"

log "Clean install final server APK"
timeout --kill-after=10 180 adb -e uninstall "$PACKAGE" </dev/null >/dev/null 2>&1 || true
timeout --kill-after=10 600 adb -e install -t "$APP_APK" </dev/null \
  | tee "$REPORT_DIR/app-install.txt"
timeout --kill-after=10 300 adb -e install -t "$TEST_APK" </dev/null \
  | tee "$REPORT_DIR/test-install.txt"

adb -e shell "run-as $PACKAGE mkdir -p files/fixtures" </dev/null
for fixture in 0-four-speakers-zh.wav 1-two-speakers-en.wav 2-two-speakers-en.wav; do
  timeout --kill-after=5 120 adb -e push \
    "$FIXTURE_DIR/$fixture" "/data/local/tmp/$fixture" </dev/null >/dev/null
  timeout --kill-after=5 60 adb -e shell \
    "run-as $PACKAGE cp '/data/local/tmp/$fixture' 'files/fixtures/$fixture'" </dev/null
done

log "Run Android instrumentation smoke"
timeout --kill-after=10 300 adb -e shell am instrument -w \
  -e class "$APP_CONTEXT_TEST" \
  "$PACKAGE.test/androidx.test.runner.AndroidJUnitRunner" \
  </dev/null \
  | tee "$REPORT_DIR/instrumentation.txt"
grep -Fq 'OK (' "$REPORT_DIR/instrumentation.txt" \
  || fail "instrumentation suite did not report OK"

run_probe() {
  local label="$1"
  local fixture="$2"
  local expected_speakers="$3"
  local database="$4"
  local reset="$5"
  local preserve="$6"
  local require_overlap="$7"
  local output="$REPORT_DIR/$label.json"
  local progress_output="$REPORT_DIR/$label-progress.json"
  local last_progress=""
  local progress_json progress_compact

  CURRENT_PROBE="$label"
  DIAGNOSTICS_CAPTURED=false
  log "Probe $label: $fixture"
  timeout --kill-after=5 60 adb -e shell \
    "run-as $PACKAGE rm -f '$RESULT_PATH' '$PROGRESS_PATH'" </dev/null
  timeout --kill-after=10 120 adb -e shell am start -W \
    -n "$PROBE_ACTIVITY" \
    --es fixture "$fixture" \
    --ez identity_probe true \
    --es identity_database "$database" \
    --ez identity_reset "$reset" \
    --ez identity_preserve "$preserve" \
    --ei num_clusters -1 \
    </dev/null \
    >"$REPORT_DIR/$label-start.txt"

  local deadline=$((SECONDS + PROBE_TIMEOUT_SECONDS))
  until timeout --kill-after=2 30 adb -e shell \
    "run-as $PACKAGE test -f '$RESULT_PATH'" </dev/null; do
    if timeout --kill-after=2 30 adb -e shell \
      "run-as $PACKAGE test -f '$PROGRESS_PATH'" </dev/null; then
      progress_json="$(
        timeout --kill-after=5 60 adb -e exec-out run-as \
          "$PACKAGE" cat "$PROGRESS_PATH" </dev/null 2>/dev/null || true
      )"
      progress_compact="$(
        python3 -c \
          'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, separators=(",", ":")))' \
          <<<"$progress_json" 2>/dev/null || true
      )"
      if [[ -n "$progress_compact" && "$progress_compact" != "$last_progress" ]]; then
        last_progress="$progress_compact"
        printf '%s\n' "$progress_json" >"$progress_output"
        log "Probe $label progress: $progress_compact"
      fi
    fi
    (( SECONDS < deadline )) || fail "probe timeout: $label"
    sleep 15
  done
  timeout --kill-after=5 60 adb -e exec-out run-as \
    "$PACKAGE" cat "$RESULT_PATH" </dev/null >"$output"
  timeout --kill-after=5 60 adb -e exec-out run-as \
    "$PACKAGE" cat "$PROGRESS_PATH" </dev/null >"$progress_output" 2>/dev/null || true

  python3 - "$output" "$expected_speakers" "$reset" "$require_overlap" <<'PY'
import json
import sys

path, expected_raw, reset_raw, overlap_raw = sys.argv[1:]
expected = int(expected_raw)
reset = reset_raw.lower() == "true"
require_overlap = overlap_raw.lower() == "true"
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
errors = []
if data.get("ok") is not True:
    errors.append(f"probe not ok: {data.get('error') or data.get('identity_error')}")
if data.get("speaker_count") != expected:
    errors.append(f"speaker_count={data.get('speaker_count')} expected={expected}")
if data.get("identity_lifecycle_ok") is not True:
    errors.append("identity_lifecycle_ok is false")
if data.get("persistent_voiceprint_count") != expected:
    errors.append(
        f"persistent_voiceprint_count={data.get('persistent_voiceprint_count')} expected={expected}"
    )
if data.get("reopened_voiceprint_count") != expected:
    errors.append(
        f"reopened_voiceprint_count={data.get('reopened_voiceprint_count')} expected={expected}"
    )
if data.get("stable_after_reopen") is not True:
    errors.append("stable_after_reopen is false")
if data.get("every_reopen_matched") is not True:
    errors.append("every_reopen_matched is false")
if reset:
    if data.get("profile_count_before") != 0:
        errors.append(f"profile_count_before={data.get('profile_count_before')} expected=0")
    if data.get("new_profiles_created") != expected:
        errors.append(
            f"new_profiles_created={data.get('new_profiles_created')} expected={expected}"
        )
else:
    if data.get("profile_count_before") != expected:
        errors.append(
            f"profile_count_before={data.get('profile_count_before')} expected={expected}"
        )
    if data.get("new_profiles_created") != 0:
        errors.append(f"new_profiles_created={data.get('new_profiles_created')} expected=0")
    if data.get("every_first_pass_matched") is not True:
        errors.append("every_first_pass_matched is false after process restart")
if require_overlap and int(data.get("excluded_overlap_turn_count") or 0) < 1:
    errors.append("overlap fixture did not exclude any overlap turn")
if errors:
    raise SystemExit("; ".join(errors))
PY
  CURRENT_PROBE="gate"
}

run_probe four-enroll 0-four-speakers-zh.wav 4 tmcra_ci_four.db true true false
timeout --kill-after=5 60 adb -e shell am force-stop "$PACKAGE" </dev/null
run_probe four-after-process-restart 0-four-speakers-zh.wav 4 tmcra_ci_four.db false false false

python3 - \
  "$REPORT_DIR/four-enroll.json" \
  "$REPORT_DIR/four-after-process-restart.json" <<'PY'
import json
import sys

def ids(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    # Diarizer cluster numbers are local labels and may be permuted between runs.
    return sorted(item["persistent_speaker_id"] for item in data["identity_mappings"])

before = ids(sys.argv[1])
after = ids(sys.argv[2])
if before != after:
    raise SystemExit(f"voiceprint IDs changed after process restart: {before} != {after}")
PY

run_probe two-clean 1-two-speakers-en.wav 2 tmcra_ci_two_clean.db true false false
run_probe two-overlap 2-two-speakers-en.wav 2 tmcra_ci_two_overlap.db true false true

python3 - "$REPORT_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
reports = []
for path in sorted(root.glob("*.json")):
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    reports.append({
        "name": path.stem,
        "ok": data.get("ok"),
        "speaker_count": data.get("speaker_count"),
        "voiceprints": data.get("persistent_voiceprint_count"),
        "process_ms": data.get("process_ms"),
        "rtf": data.get("rtf"),
        "overlap_turns_excluded": data.get("excluded_overlap_turn_count"),
    })
summary = {
    "ok": all(item["ok"] is True for item in reports),
    "reports": reports,
}
with (root / "summary.json").open("w", encoding="utf-8") as handle:
    json.dump(summary, handle, ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY

log "PASS: reports written to $REPORT_DIR"
