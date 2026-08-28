#!/usr/bin/env bash
set -euo pipefail

readonly DEPLOY_SHA="${1:-}"
readonly DEPLOY_DIR="${2:-}"
readonly PUBLIC_HEALTH_URL="${3:-}"
readonly OUTER_LOCK_DIR="/run/lock/axisai-production"
readonly OUTER_LOCK_PATH="$OUTER_LOCK_DIR/production.lock"
readonly OUTER_LOCK_CAPABILITY_FD="${AXISAI_OUTER_LOCK_FD:-}"

require_timeout_value() {
  local name="$1" value="${!1-}"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "missing or invalid canonical timeout value: $name" >&2
    exit 70
  fi
  readonly "$name=$value"
}

require_timeout_value SSM_EXECUTION_TIMEOUT_SECONDS
require_timeout_value HOST_WORST_CASE_SECONDS
require_timeout_value SSM_EXECUTION_MARGIN_SECONDS
require_timeout_value HOST_ROOT_BOOTSTRAP_SECONDS
require_timeout_value HOST_LOCK_ACQUISITION_SECONDS
require_timeout_value HOST_AUTHORITY_AND_STALE_PROOF_SECONDS
require_timeout_value HOST_CLOCK_SETUP_SECONDS
require_timeout_value HOST_GIT_FETCH_CHECKOUT_SECONDS
require_timeout_value HOST_CANDIDATE_BUILD_START_SECONDS
require_timeout_value HOST_CANDIDATE_REVISION_HEALTH_SECONDS
require_timeout_value HOST_DIAGNOSTICS_SECONDS
require_timeout_value HOST_ROLLBACK_BUILD_START_SECONDS
require_timeout_value HOST_ROLLBACK_REVISION_HEALTH_SECONDS
require_timeout_value HOST_CLEANUP_SECONDS

readonly LOCK_WAIT_SECONDS="$HOST_LOCK_ACQUISITION_SECONDS"
readonly CLOCK_START_TIMEOUT_SECONDS=2
readonly CLOCK_STATE_SETUP_TIMEOUT_SECONDS=4
readonly PREFLIGHT_PHASE_SECONDS="$HOST_GIT_FETCH_CHECKOUT_SECONDS"
readonly CANDIDATE_PHASE_SECONDS=$((
  HOST_CANDIDATE_BUILD_START_SECONDS + HOST_CANDIDATE_REVISION_HEALTH_SECONDS
))
readonly DIAGNOSTIC_PHASE_SECONDS="$HOST_DIAGNOSTICS_SECONDS"
readonly ROLLBACK_PHASE_SECONDS=$((
  HOST_ROLLBACK_BUILD_START_SECONDS + HOST_ROLLBACK_REVISION_HEALTH_SECONDS
))
readonly POST_LOCK_BUDGET_SECONDS=$((
  PREFLIGHT_PHASE_SECONDS + CANDIDATE_PHASE_SECONDS +
  DIAGNOSTIC_PHASE_SECONDS + ROLLBACK_PHASE_SECONDS
))
readonly ROLLBACK_RESET_TIMEOUT_SECONDS=5
readonly COMMAND_KILL_GRACE_SECONDS=2
readonly CLEANUP_TIMEOUT_SECONDS=$((HOST_CLEANUP_SECONDS - COMMAND_KILL_GRACE_SECONDS))
readonly CLOCK_START_MAX_SECONDS=$((CLOCK_START_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly CLOCK_STATE_SETUP_MAX_SECONDS=$((CLOCK_STATE_SETUP_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly ROLLBACK_RESET_MAX_SECONDS=$((ROLLBACK_RESET_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly CLEANUP_MAX_SECONDS=$((CLEANUP_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly MONOTONIC_CLOCK_CODE='import time; print(time.monotonic_ns() // 1_000_000_000)'
readonly INTERNAL_HEALTH_ATTEMPTS=30
readonly PUBLIC_HEALTH_ATTEMPTS=12
readonly HEALTH_CONNECT_TIMEOUT_SECONDS=2
readonly HEALTH_MAX_TIME_SECONDS=5
readonly HEALTH_RETRY_DELAY_SECONDS=5

clock_now() {
  local destination="$1" reading previous
  if ! reading="$(timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
    "${CLOCK_START_TIMEOUT_SECONDS}s" python3 -c "$MONOTONIC_CLOCK_CODE")"; then
    echo "bounded monotonic clock helper failed" >&2
    return 1
  fi
  if [[ ! "$reading" =~ ^[0-9]+$ ]]; then
    echo "monotonic clock returned an invalid reading" >&2
    return 1
  fi
  if [[ -s "$MONOTONIC_STATE_FILE" ]]; then
    if ! IFS= read -r previous < "$MONOTONIC_STATE_FILE" ||
       [[ ! "$previous" =~ ^[0-9]+$ ]]; then
      echo "monotonic clock state is invalid" >&2
      return 1
    fi
    if ((reading < previous)); then
      echo "monotonic clock moved backward" >&2
      return 1
    fi
  fi
  if ! printf '%s\n' "$reading" > "$MONOTONIC_STATE_FILE"; then
    echo "monotonic clock state could not be recorded" >&2
    return 1
  fi
  printf -v "$destination" '%s' "$reading"
}

enter_phase() {
  CURRENT_PHASE="$1"
  CURRENT_PHASE_DEADLINE="$2"
  export CURRENT_PHASE
}

run_external() {
  local now remaining status
  clock_now now || return 1
  remaining=$((CURRENT_PHASE_DEADLINE - now))
  if ((remaining <= 0)); then
    echo "$CURRENT_PHASE phase deadline exhausted" >&2
    return 124
  fi
  if timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
    "${remaining}s" "$@"; then
    return 0
  else
    status="$?"
    return "$status"
  fi
}

# The root wrapper passes descriptor 7 for the exact locked open-file
# description.  The path probe proves some holder exists; flocking fd 7 proves
# this process inherited that same locked OFD.  Both checks are required, so an
# unrelated holder plus a caller-opened unlocked descriptor cannot forge proof.
if [[ "$OUTER_LOCK_CAPABILITY_FD" != 7 ]]; then
  echo "outer deployment lock is unavailable or unsafe" >&2
  exit 73
fi
if ! outer_dir_metadata="$(
       LC_ALL=C stat -c '%u:%F:%a' -- "$OUTER_LOCK_DIR"
     )" ||
   ! outer_file_metadata="$(
       LC_ALL=C stat -c '%d:%i:%u:%F:%a:%h' -- "$OUTER_LOCK_PATH"
     )" ||
   [[ "$outer_dir_metadata" != "0:directory:755" ]] ||
   [[ "$outer_file_metadata" != *":0:regular file:644:1" &&
      "$outer_file_metadata" != *":0:regular empty file:644:1" ]]; then
  echo "outer deployment lock is unavailable or unsafe" >&2
  exit 73
fi

if ! outer_fd_metadata="$(
       LC_ALL=C stat -L -c '%d:%i:%u:%F:%a:%h' -- /proc/self/fd/7
     )" || [[ "$outer_fd_metadata" != "$outer_file_metadata" ]]; then
  echo "outer deployment lock capability is unavailable or unsafe" >&2
  exit 73
fi

set +e
flock -n -E 73 "$OUTER_LOCK_PATH" true
outer_probe_status="$?"
set -e
if [[ "$outer_probe_status" != 73 ]]; then
  echo "outer deployment lock is not held by the deployment wrapper" >&2
  exit 73
fi
if ! flock -n -E 73 7; then
  echo "outer deployment lock capability is not held" >&2
  exit 73
fi

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "usage: production_deploy.sh DEPLOY_SHA DEPLOY_DIR [PUBLIC_HEALTH_URL]" >&2
  exit 64
fi
if [[ ! "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "DEPLOY_SHA must be lowercase 40-hex" >&2
  exit 64
fi
if [[ "$DEPLOY_DIR" != /* || "$DEPLOY_DIR" == *$'\n'* || "$DEPLOY_DIR" == *$'\r'* ]]; then
  echo "DEPLOY_DIR must be one absolute path" >&2
  exit 64
fi

if ((HOST_ROOT_BOOTSTRAP_SECONDS + HOST_LOCK_ACQUISITION_SECONDS +
      HOST_AUTHORITY_AND_STALE_PROOF_SECONDS + HOST_CLOCK_SETUP_SECONDS +
      HOST_GIT_FETCH_CHECKOUT_SECONDS + HOST_CANDIDATE_BUILD_START_SECONDS +
      HOST_CANDIDATE_REVISION_HEALTH_SECONDS + HOST_DIAGNOSTICS_SECONDS +
      HOST_ROLLBACK_BUILD_START_SECONDS + HOST_ROLLBACK_REVISION_HEALTH_SECONDS +
      HOST_CLEANUP_SECONDS != HOST_WORST_CASE_SECONDS ||
      SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS != SSM_EXECUTION_MARGIN_SECONDS ||
      CLOCK_START_MAX_SECONDS + CLOCK_STATE_SETUP_MAX_SECONDS != HOST_CLOCK_SETUP_SECONDS ||
      PREFLIGHT_PHASE_SECONDS + CANDIDATE_PHASE_SECONDS +
      DIAGNOSTIC_PHASE_SECONDS + ROLLBACK_PHASE_SECONDS != POST_LOCK_BUDGET_SECONDS ||
      ROLLBACK_RESET_MAX_SECONDS +
        INTERNAL_HEALTH_ATTEMPTS * HEALTH_MAX_TIME_SECONDS +
        (INTERNAL_HEALTH_ATTEMPTS - 1) * HEALTH_RETRY_DELAY_SECONDS > ROLLBACK_PHASE_SECONDS ||
      CANDIDATE_PHASE_SECONDS < HOST_CANDIDATE_BUILD_START_SECONDS +
        HOST_CANDIDATE_REVISION_HEALTH_SECONDS)); then
  echo "invalid host transaction budget" >&2
  exit 70
fi

OVERRIDE_FILE=""
HEALTH_BODY=""
MONOTONIC_STATE_FILE=""
BUILD_CONTEXT_DIR=""
BUILD_ARCHIVE=""
declare -a BUILD_CONTEXT_DIRS=()
declare -a BUILD_ARCHIVES=()
cleanup() {
  local -a cleanup_files=()
  if [[ -n "$OVERRIDE_FILE" ]]; then
    cleanup_files+=("$OVERRIDE_FILE")
  fi
  if [[ -n "$HEALTH_BODY" ]]; then
    cleanup_files+=("$HEALTH_BODY")
  fi
  # The clock state lives in the root-owned runtime directory, which this
  # unprivileged process cannot unlink from.  Root removes it after the child
  # terminates; listing it here would make one guaranteed `rm` failure abandon
  # the checkout files that this process really does own.
  if ((${#cleanup_files[@]} > 0)); then
    timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
      "${CLEANUP_TIMEOUT_SECONDS}s" rm -f -- "${cleanup_files[@]}" || true
  fi
  if ((${#BUILD_ARCHIVES[@]} > 0)); then
    timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
      "${CLEANUP_TIMEOUT_SECONDS}s" rm -f -- "${BUILD_ARCHIVES[@]}" || true
  fi
  if ((${#BUILD_CONTEXT_DIRS[@]} > 0)); then
    timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
      "${CLEANUP_TIMEOUT_SECONDS}s" rm -r -- "${BUILD_CONTEXT_DIRS[@]}" || true
  fi
}
trap cleanup EXIT

# Transaction scratch state belongs to the root-controlled runtime area, never
# to the production checkout.  The root bootstrap provisions this file after its
# authority and staleness proofs pass, and hands the path down at privilege
# drop; a stale command therefore never creates it at all.
MONOTONIC_STATE_FILE="${AXISAI_MONOTONIC_STATE:-}"
if [[ "$MONOTONIC_STATE_FILE" != /* ]] ||
   [[ "$MONOTONIC_STATE_FILE" == *$'\n'* ]] ||
   [[ "$MONOTONIC_STATE_FILE" == *$'\r'* ]] ||
   ! clock_state_metadata="$(
       LC_ALL=C stat -c '%u:%a:%h:%F' -- "$MONOTONIC_STATE_FILE"
     )" ||
   [[ "$clock_state_metadata" != "$EUID:600:1:regular file" &&
      "$clock_state_metadata" != "$EUID:600:1:regular empty file" ]]; then
  echo "monotonic clock state unavailable before deployment mutation" >&2
  exit 70
fi
readonly MONOTONIC_STATE_FILE

if ! clock_now TRANSACTION_EPOCH; then
  echo "monotonic clock unavailable before deployment mutation" >&2
  exit 70
fi
readonly TRANSACTION_EPOCH
readonly PREFLIGHT_DEADLINE=$((TRANSACTION_EPOCH + PREFLIGHT_PHASE_SECONDS))
readonly CANDIDATE_CUTOFF=$((PREFLIGHT_DEADLINE + CANDIDATE_PHASE_SECONDS))
readonly DIAGNOSTIC_CUTOFF=$((CANDIDATE_CUTOFF + DIAGNOSTIC_PHASE_SECONDS))
readonly ROLLBACK_CUTOFF=$((DIAGNOSTIC_CUTOFF + ROLLBACK_PHASE_SECONDS))
enter_phase preflight "$PREFLIGHT_DEADLINE"
echo "host transaction budget: execution=$SSM_EXECUTION_TIMEOUT_SECONDS worst_case=$HOST_WORST_CASE_SECONDS margin=$SSM_EXECUTION_MARGIN_SECONDS lock=$LOCK_WAIT_SECONDS clock=$CLOCK_START_MAX_SECONDS clock_state=$CLOCK_STATE_SETUP_MAX_SECONDS rollback_reset=$ROLLBACK_RESET_MAX_SECONDS preflight=$PREFLIGHT_PHASE_SECONDS candidate=$CANDIDATE_PHASE_SECONDS diagnostics=$DIAGNOSTIC_PHASE_SECONDS rollback=$ROLLBACK_PHASE_SECONDS post_lock=$POST_LOCK_BUDGET_SECONDS timeout_grace=$COMMAND_KILL_GRACE_SECONDS cleanup=$CLEANUP_MAX_SECONDS" >&2

if [[ -n "$PUBLIC_HEALTH_URL" ]]; then
  run_external python3 - "$PUBLIC_HEALTH_URL" <<'PY'
import sys
from urllib.parse import urlsplit

value = sys.argv[1]
parsed = urlsplit(value)
valid = (
    value == value.strip()
    and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    and parsed.scheme == "https"
    and bool(parsed.hostname)
    and parsed.username is None
    and parsed.password is None
)
if not valid:
    raise SystemExit("PUBLIC_HEALTH_URL must be HTTPS without credentials or controls")
PY
fi

cd -- "$DEPLOY_DIR"

PREV_COMMIT="$(run_external git rev-parse --verify HEAD^{commit})"
readonly PREV_COMMIT
if [[ ! "$PREV_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "current production revision is invalid" >&2
  exit 1
fi

echo "validating deployment candidate $DEPLOY_SHA" >&2
run_external git fetch origin main --prune
run_external git cat-file -e "$PREV_COMMIT^{commit}"
run_external git cat-file -e "$DEPLOY_SHA^{commit}"
ORIGIN_MAIN="$(run_external git rev-parse --verify refs/remotes/origin/main)"
readonly ORIGIN_MAIN
if [[ "$ORIGIN_MAIN" != "$DEPLOY_SHA" ]]; then
  echo "deployment candidate is stale: origin/main differs from DEPLOY_SHA" >&2
  exit 1
fi
if ! run_external git merge-base --is-ancestor "$PREV_COMMIT" "$DEPLOY_SHA"; then
  echo "deployment candidate is older than or divergent from production" >&2
  exit 1
fi
PREV_DEPLOY_MARKER="$(run_external git ls-tree --name-only \
  "$PREV_COMMIT" -- scripts/production_deploy.sh)"
readonly PREV_DEPLOY_MARKER
if [[ "$PREV_DEPLOY_MARKER" == "scripts/production_deploy.sh" ]]; then
  LEGACY_ROLLBACK_ALLOWED=0
else
  # The host helper itself is the durable revision-health contract marker.
  # It enters production with the revision-aware health contract, so only its
  # immediate predecessor can use the missing-revision compatibility proof.
  LEGACY_ROLLBACK_ALLOWED=1
fi
readonly LEGACY_ROLLBACK_ALLOWED

clock_now CANDIDATE_STARTED_AT
readonly CANDIDATE_STARTED_AT
CANDIDATE_PHASE_DEADLINE=$((CANDIDATE_STARTED_AT + CANDIDATE_PHASE_SECONDS))
if ((CANDIDATE_PHASE_DEADLINE > CANDIDATE_CUTOFF)); then
  CANDIDATE_PHASE_DEADLINE="$CANDIDATE_CUTOFF"
fi
readonly CANDIDATE_PHASE_DEADLINE
enter_phase candidate "$CANDIDATE_PHASE_DEADLINE"
OVERRIDE_FILE="$(run_external mktemp "$DEPLOY_DIR/.axisai-compose-override.XXXXXX.yml")"
HEALTH_BODY="$(run_external mktemp "$DEPLOY_DIR/.axisai-health.XXXXXX.json")"
readonly OVERRIDE_FILE HEALTH_BODY
readonly -a COMPOSE_FILES=(-f "$DEPLOY_DIR/docker-compose.yml" -f "$OVERRIDE_FILE")

materialize_build_context() {
  local revision="$1"
  BUILD_CONTEXT_DIR="$(run_external mktemp -d "$DEPLOY_DIR/.axisai-build-context.XXXXXX")" || return 1
  BUILD_ARCHIVE="$(run_external mktemp "$DEPLOY_DIR/.axisai-build-archive.XXXXXX.tar")" || return 1
  BUILD_CONTEXT_DIRS+=("$BUILD_CONTEXT_DIR")
  BUILD_ARCHIVES+=("$BUILD_ARCHIVE")
  run_external git archive --format=tar "$revision" -o "$BUILD_ARCHIVE" || return 1
  run_external tar -xf "$BUILD_ARCHIVE" -C "$BUILD_CONTEXT_DIR" || return 1
}

write_override() {
  local revision="$1"
  local build_context="$2"
  umask 077
  printf '%s\n' \
    'services:' \
    '  web:' \
    '    build:' \
    "      context: '$build_context'" \
    '      args:' \
    "        BUILD_REVISION: '$revision'" \
    '    environment:' \
    "      APP_REVISION: '$revision'" \
    '  worker:' \
    '    build:' \
    "      context: '$build_context'" \
    '      args:' \
    "        BUILD_REVISION: '$revision'" \
    '    environment:' \
    "      APP_REVISION: '$revision'" > "$OVERRIDE_FILE" || return 1
}

probe_internal_health_once() {
  local expected_revision="$1"
  local allow_missing_revision="$2"
  local health_fields health_status has_revision health_revision

  health_fields="$(run_external docker compose "${COMPOSE_FILES[@]}" \
    exec -T web python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen(
    'http://127.0.0.1:5000/health?deep=1', timeout=5
) as response:
    if response.status != 200:
        raise SystemExit(f"deep health returned HTTP {response.status}")
    payload = json.load(response)
if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
    raise SystemExit("deep health JSON has no string status")
revision_present = "revision" in payload
revision = payload.get("revision", "")
if revision_present and not isinstance(revision, str):
    raise SystemExit("deep health JSON revision is not a string")
print(f"{payload['status']}\t{int(revision_present)}\t{revision}")
PY
)" || return 1
  IFS=$'\t' read -r health_status has_revision health_revision <<< "$health_fields"
  if [[ "$health_status" != "ok" ]]; then
    echo "deep health status is not ok" >&2
    return 1
  fi
  if [[ "$has_revision" == "1" ]]; then
    if [[ "$health_revision" != "$expected_revision" ]]; then
      echo "deep health revision mismatch" >&2
      return 1
    fi
  elif [[ "$allow_missing_revision" == "1" ]]; then
    echo "rollback compatibility proof accepted: deep health has no revision" >&2
  else
    echo "deep health revision is missing" >&2
    return 1
  fi
}

probe_internal_health() {
  local expected_revision="$1"
  local allow_missing_revision="$2"
  local attempt

  for ((attempt = 1; attempt <= INTERNAL_HEALTH_ATTEMPTS; attempt++)); do
    if probe_internal_health_once "$expected_revision" "$allow_missing_revision"; then
      echo "deep health verified on attempt $attempt" >&2
      return 0
    fi
    echo "deep health not ready on attempt $attempt/$INTERNAL_HEALTH_ATTEMPTS" >&2
    if ((attempt < INTERNAL_HEALTH_ATTEMPTS)); then
      run_external sleep "$HEALTH_RETRY_DELAY_SECONDS" || return 1
    fi
  done
  echo "deep health readiness exhausted after $INTERNAL_HEALTH_ATTEMPTS attempts" >&2
  return 1
}

verify_public_health_once() {
  local health_code
  health_code="$(run_external curl --silent --show-error \
    --connect-timeout "$HEALTH_CONNECT_TIMEOUT_SECONDS" \
    --max-time "$HEALTH_MAX_TIME_SECONDS" \
    --output "$HEALTH_BODY" \
    --write-out '%{http_code}' \
    "$PUBLIC_HEALTH_URL")" || return 1
  if [[ "$health_code" != "200" ]]; then
    echo "public health returned HTTP $health_code" >&2
    return 1
  fi
}

verify_public_health() {
  local attempt

  for ((attempt = 1; attempt <= PUBLIC_HEALTH_ATTEMPTS; attempt++)); do
    if verify_public_health_once; then
      echo "public health verified on attempt $attempt" >&2
      return 0
    fi
    echo "public health not ready on attempt $attempt/$PUBLIC_HEALTH_ATTEMPTS" >&2
    if ((attempt < PUBLIC_HEALTH_ATTEMPTS)); then
      run_external sleep "$HEALTH_RETRY_DELAY_SECONDS" || return 1
    fi
  done
  echo "public health readiness exhausted after $PUBLIC_HEALTH_ATTEMPTS attempts" >&2
  return 1
}

start_and_verify_release() {
  local revision="$1"
  local allow_missing_health_revision="$2"
  local check_public="$3"
  local running_revision

  materialize_build_context "$revision" || return 1
  write_override "$revision" "$BUILD_CONTEXT_DIR" || return 1
  run_external docker compose "${COMPOSE_FILES[@]}" build || return 1
  run_external docker compose "${COMPOSE_FILES[@]}" up -d --remove-orphans || return 1
  run_external docker compose "${COMPOSE_FILES[@]}" ps || return 1
  running_revision="$(run_external docker compose "${COMPOSE_FILES[@]}" exec -T web cat /app/BUILD_REVISION)" || return 1
  if [[ "$running_revision" != "$revision" ]]; then
    echo "running web container revision mismatch" >&2
    return 1
  fi
  probe_internal_health "$revision" "$allow_missing_health_revision" || return 1
  if [[ "$check_public" == "1" && -n "$PUBLIC_HEALTH_URL" ]]; then
    verify_public_health || return 1
  fi
}

rollback_release() {
  local restored_head rollback_started_at rollback_phase_deadline status

  enter_phase rollback "$ROLLBACK_CUTOFF"
  echo "rolling back to $PREV_COMMIT" >&2
  if timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
    "${ROLLBACK_RESET_TIMEOUT_SECONDS}s" git reset --hard "$PREV_COMMIT"; then
    :
  else
    status="$?"
    echo "bounded exact rollback reset failed" >&2
    return "$status"
  fi
  if ! clock_now rollback_started_at; then
    echo "exact rollback reset attempted but monotonic verification clock is unavailable" >&2
    return 1
  fi
  rollback_phase_deadline=$((rollback_started_at + ROLLBACK_PHASE_SECONDS))
  if ((rollback_phase_deadline > ROLLBACK_CUTOFF)); then
    rollback_phase_deadline="$ROLLBACK_CUTOFF"
  fi
  enter_phase rollback "$rollback_phase_deadline"
  restored_head="$(run_external git rev-parse --verify HEAD^{commit})" || return 1
  if [[ "$restored_head" != "$PREV_COMMIT" ]]; then
    echo "rollback checkout revision mismatch" >&2
    return 1
  fi
  start_and_verify_release "$PREV_COMMIT" "$LEGACY_ROLLBACK_ALLOWED" 0 || return 1
  echo "rollback verified at $PREV_COMMIT" >&2
}

on_deploy_error() {
  local failure_status="$?" now diagnostic_deadline
  trap - ERR
  set +e
  if [[ "$failure_status" -eq 0 ]]; then
    failure_status=1
  fi
  if clock_now now; then
    diagnostic_deadline=$((now + DIAGNOSTIC_PHASE_SECONDS))
    if ((diagnostic_deadline > DIAGNOSTIC_CUTOFF)); then
      diagnostic_deadline="$DIAGNOSTIC_CUTOFF"
    fi
    enter_phase diagnostics "$diagnostic_deadline"
    echo "candidate deployment failed; collecting bounded diagnostics" >&2
    run_external docker compose "${COMPOSE_FILES[@]}" ps >&2
    run_external docker compose "${COMPOSE_FILES[@]}" logs --tail 100 web worker >&2
  else
    echo "candidate deployment failed; diagnostics skipped because bounded clock failed" >&2
  fi
  if ! rollback_release; then
    echo "rollback failed verification" >&2
  fi
  exit "$failure_status"
}

trap on_deploy_error ERR
echo "checking out exact candidate $DEPLOY_SHA" >&2
run_external git reset --hard "$DEPLOY_SHA"
CHECKED_OUT_HEAD="$(run_external git rev-parse --verify HEAD^{commit})"
readonly CHECKED_OUT_HEAD
if [[ "$CHECKED_OUT_HEAD" != "$DEPLOY_SHA" ]]; then
  echo "candidate checkout revision mismatch" >&2
  false
fi

start_and_verify_release "$DEPLOY_SHA" 0 1
run_external docker image prune -f
trap - ERR
echo "deployment verified at $DEPLOY_SHA" >&2
