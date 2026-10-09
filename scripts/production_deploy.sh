#!/usr/bin/env bash
set -euo pipefail

readonly DEPLOY_SHA="${1:-}"
readonly DEPLOY_DIR="${2:-}"
readonly PUBLIC_HEALTH_URL="${3:-}"
readonly OUTER_LOCK_DIR="/run/lock/axisai-production"
readonly OUTER_LOCK_PATH="$OUTER_LOCK_DIR/production.lock"
readonly OUTER_LOCK_CAPABILITY_FD="${AXISAI_OUTER_LOCK_FD:-}"
# R6-03A: everything after the proofs below is the exact-SHA blue/green
# transaction in this file's sibling, run from the git-archive context of
# DEPLOY_SHA (never from the mutable checkout).
readonly TRANSACTION_ENGINE="scripts/r6_deploy_transaction.py"

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
require_timeout_value HOST_GIT_PREPARATION_SECONDS
require_timeout_value HOST_RELEASE_FORWARD_SECONDS
require_timeout_value HOST_DIAGNOSTICS_SECONDS
require_timeout_value HOST_RELEASE_ROLLBACK_SECONDS
require_timeout_value HOST_RETIREMENT_SECONDS
require_timeout_value HOST_CLEANUP_SECONDS

readonly LOCK_WAIT_SECONDS="$HOST_LOCK_ACQUISITION_SECONDS"
readonly CLOCK_START_TIMEOUT_SECONDS=2
readonly CLOCK_STATE_SETUP_TIMEOUT_SECONDS=4
readonly PREFLIGHT_PHASE_SECONDS="$HOST_GIT_PREPARATION_SECONDS"
readonly FAILURE_TAIL_SECONDS=$((HOST_DIAGNOSTICS_SECONDS + HOST_RELEASE_ROLLBACK_SECONDS))
readonly SUCCESS_TAIL_SECONDS="$HOST_RETIREMENT_SECONDS"
if ((FAILURE_TAIL_SECONDS > SUCCESS_TAIL_SECONDS)); then
  readonly TAIL_SECONDS="$FAILURE_TAIL_SECONDS"
else
  readonly TAIL_SECONDS="$SUCCESS_TAIL_SECONDS"
fi
# The transaction engine owns its own phase deadlines (forward, then exactly
# one tail). This outer bound is only the backstop for the engine process.
readonly TRANSACTION_PHASE_SECONDS=$((HOST_RELEASE_FORWARD_SECONDS + TAIL_SECONDS))
readonly POST_LOCK_BUDGET_SECONDS=$((PREFLIGHT_PHASE_SECONDS + TRANSACTION_PHASE_SECONDS))
readonly COMMAND_KILL_GRACE_SECONDS=2
readonly CLEANUP_TIMEOUT_SECONDS=$((HOST_CLEANUP_SECONDS - COMMAND_KILL_GRACE_SECONDS))
readonly CLOCK_START_MAX_SECONDS=$((CLOCK_START_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly CLOCK_STATE_SETUP_MAX_SECONDS=$((CLOCK_STATE_SETUP_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly CLEANUP_MAX_SECONDS=$((CLEANUP_TIMEOUT_SECONDS + COMMAND_KILL_GRACE_SECONDS))
readonly MONOTONIC_CLOCK_CODE='import time; print(time.monotonic_ns() // 1_000_000_000)'

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
  remaining=$((CURRENT_PHASE_DEADLINE - now - COMMAND_KILL_GRACE_SECONDS))
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

if [[ "$#" -ne 3 ]]; then
  echo "usage: production_deploy.sh DEPLOY_SHA DEPLOY_DIR PUBLIC_HEALTH_URL" >&2
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
if [[ -z "$PUBLIC_HEALTH_URL" ]]; then
  # The blue/green transaction proves public health and the anonymous mobile
  # ingress envelope through nginx after the switch; without an origin it has
  # no post-switch proof, so it refuses before any mutation.
  echo "PUBLIC_HEALTH_URL is required for the post-switch proof" >&2
  exit 64
fi

if ((HOST_ROOT_BOOTSTRAP_SECONDS + HOST_LOCK_ACQUISITION_SECONDS +
      HOST_AUTHORITY_AND_STALE_PROOF_SECONDS + HOST_CLOCK_SETUP_SECONDS +
      HOST_GIT_PREPARATION_SECONDS + HOST_RELEASE_FORWARD_SECONDS +
      TAIL_SECONDS + HOST_CLEANUP_SECONDS != HOST_WORST_CASE_SECONDS ||
      SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS != SSM_EXECUTION_MARGIN_SECONDS ||
      SSM_EXECUTION_MARGIN_SECONDS <= 0 ||
      CLOCK_START_MAX_SECONDS + CLOCK_STATE_SETUP_MAX_SECONDS != HOST_CLOCK_SETUP_SECONDS ||
      PREFLIGHT_PHASE_SECONDS + TRANSACTION_PHASE_SECONDS != POST_LOCK_BUDGET_SECONDS ||
      TRANSACTION_PHASE_SECONDS < HOST_RELEASE_FORWARD_SECONDS + FAILURE_TAIL_SECONDS ||
      TRANSACTION_PHASE_SECONDS < HOST_RELEASE_FORWARD_SECONDS + SUCCESS_TAIL_SECONDS)); then
  echo "invalid host transaction budget" >&2
  exit 70
fi

MONOTONIC_STATE_FILE=""
BUILD_CONTEXT_DIR=""
BUILD_ARCHIVE=""
WORK_DIR=""
cleanup() {
  # One cleanup command shares the entire reserved phase, including kill grace.
  # rm -r never follows the engine's .env symlinks into the checkout.
  # Root owns and removes the monotonic state after this child terminates.
  local -a cleanup_paths=()
  if [[ -n "$BUILD_ARCHIVE" ]]; then cleanup_paths+=("$BUILD_ARCHIVE"); fi
  if [[ -n "$BUILD_CONTEXT_DIR" ]]; then cleanup_paths+=("$BUILD_CONTEXT_DIR"); fi
  if [[ -n "$WORK_DIR" ]]; then cleanup_paths+=("$WORK_DIR"); fi
  if ((${#cleanup_paths[@]} > 0)); then
    timeout --signal=TERM --kill-after="${COMMAND_KILL_GRACE_SECONDS}s" \
      "${CLEANUP_TIMEOUT_SECONDS}s" rm -r -- "${cleanup_paths[@]}" || true
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
readonly TRANSACTION_CUTOFF=$((PREFLIGHT_DEADLINE + TRANSACTION_PHASE_SECONDS))
enter_phase preflight "$PREFLIGHT_DEADLINE"
echo "host transaction budget: execution=$SSM_EXECUTION_TIMEOUT_SECONDS worst_case=$HOST_WORST_CASE_SECONDS margin=$SSM_EXECUTION_MARGIN_SECONDS lock=$LOCK_WAIT_SECONDS clock=$CLOCK_START_MAX_SECONDS clock_state=$CLOCK_STATE_SETUP_MAX_SECONDS preflight=$PREFLIGHT_PHASE_SECONDS forward=$HOST_RELEASE_FORWARD_SECONDS failure_tail=$FAILURE_TAIL_SECONDS success_tail=$SUCCESS_TAIL_SECONDS transaction=$TRANSACTION_PHASE_SECONDS post_lock=$POST_LOCK_BUDGET_SECONDS timeout_grace=$COMMAND_KILL_GRACE_SECONDS cleanup=$CLEANUP_MAX_SECONDS" >&2

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

# The exact candidate tree, from git objects only: untracked or ignored host
# files can never enter the image build or the transaction engine. The
# checkout itself is not touched here; the engine moves it to DEPLOY_SHA only
# once the new route, public/mobile proof and worker are all accepted.
BUILD_CONTEXT_DIR="$(run_external mktemp -d "$DEPLOY_DIR/.axisai-build-context.XXXXXX")"
BUILD_ARCHIVE="$(run_external mktemp "$DEPLOY_DIR/.axisai-build-archive.XXXXXX.tar")"
WORK_DIR="$(run_external mktemp -d "$DEPLOY_DIR/.axisai-r6-work.XXXXXX")"
readonly BUILD_CONTEXT_DIR BUILD_ARCHIVE WORK_DIR
run_external git archive --format=tar "$DEPLOY_SHA" -o "$BUILD_ARCHIVE"
run_external tar -xf "$BUILD_ARCHIVE" -C "$BUILD_CONTEXT_DIR"
if [[ ! -f "$BUILD_CONTEXT_DIR/$TRANSACTION_ENGINE" ]]; then
  echo "candidate has no blue/green transaction engine" >&2
  exit 1
fi

enter_phase transaction "$TRANSACTION_CUTOFF"
echo "starting blue/green transaction for $DEPLOY_SHA (previous $PREV_COMMIT)" >&2
run_external python3 -I -B "$BUILD_CONTEXT_DIR/$TRANSACTION_ENGINE" run \
  --deploy-sha "$DEPLOY_SHA" \
  --previous-commit "$PREV_COMMIT" \
  --deploy-dir "$DEPLOY_DIR" \
  --context "$BUILD_CONTEXT_DIR" \
  --work-dir "$WORK_DIR" \
  --public-health-url "$PUBLIC_HEALTH_URL" \
  --transaction-epoch "$TRANSACTION_EPOCH"
echo "deployment verified at $DEPLOY_SHA" >&2
