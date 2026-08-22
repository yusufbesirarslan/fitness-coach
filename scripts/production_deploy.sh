#!/usr/bin/env bash
set -euo pipefail

readonly DEPLOY_SHA="${1:-}"
readonly DEPLOY_DIR="${2:-}"
readonly PUBLIC_HEALTH_URL="${3:-}"
readonly LOCK_PATH="$DEPLOY_DIR/.axisai-production-deploy.lock"
readonly INTERNAL_HEALTH_ATTEMPTS=30
readonly PUBLIC_HEALTH_ATTEMPTS=12
readonly HEALTH_CONNECT_TIMEOUT_SECONDS=2
readonly HEALTH_MAX_TIME_SECONDS=5
readonly HEALTH_RETRY_DELAY_SECONDS=5

exec 9>"$LOCK_PATH"
if ! flock -w 60 9; then
  echo "deployment lock unavailable after 60 seconds" >&2
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
if [[ -n "$PUBLIC_HEALTH_URL" ]]; then
  python - "$PUBLIC_HEALTH_URL" <<'PY'
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

PREV_COMMIT="$(git rev-parse --verify HEAD^{commit})"
readonly PREV_COMMIT
if [[ ! "$PREV_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "current production revision is invalid" >&2
  exit 1
fi

echo "validating deployment candidate $DEPLOY_SHA" >&2
git fetch origin main --prune
git cat-file -e "$PREV_COMMIT^{commit}"
git cat-file -e "$DEPLOY_SHA^{commit}"
ORIGIN_MAIN="$(git rev-parse --verify refs/remotes/origin/main)"
readonly ORIGIN_MAIN
if [[ "$ORIGIN_MAIN" != "$DEPLOY_SHA" ]]; then
  echo "deployment candidate is stale: origin/main differs from DEPLOY_SHA" >&2
  exit 1
fi
if ! git merge-base --is-ancestor "$PREV_COMMIT" "$DEPLOY_SHA"; then
  echo "deployment candidate is older than or divergent from production" >&2
  exit 1
fi
PREV_DEPLOY_MARKER="$(git ls-tree --name-only \
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

OVERRIDE_FILE=""
HEALTH_BODY=""
cleanup() {
  if [[ -n "$OVERRIDE_FILE" ]]; then
    rm -f -- "$OVERRIDE_FILE" || true
  fi
  if [[ -n "$HEALTH_BODY" ]]; then
    rm -f -- "$HEALTH_BODY" || true
  fi
}
trap cleanup EXIT

OVERRIDE_FILE="$(mktemp "$DEPLOY_DIR/.axisai-compose-override.XXXXXX.yml")"
HEALTH_BODY="$(mktemp "$DEPLOY_DIR/.axisai-health.XXXXXX.json")"
readonly OVERRIDE_FILE HEALTH_BODY
readonly -a COMPOSE_FILES=(-f "$DEPLOY_DIR/docker-compose.yml" -f "$OVERRIDE_FILE")

write_override() {
  local revision="$1"
  umask 077
  printf '%s\n' \
    'services:' \
    '  web:' \
    '    environment:' \
    "      APP_REVISION: '$revision'" \
    '  worker:' \
    '    environment:' \
    "      APP_REVISION: '$revision'" > "$OVERRIDE_FILE" || return 1
}

probe_internal_health_once() {
  local expected_revision="$1"
  local allow_missing_revision="$2"
  local health_code health_fields health_status has_revision health_revision

  health_code="$(curl --silent --show-error \
    --connect-timeout "$HEALTH_CONNECT_TIMEOUT_SECONDS" \
    --max-time "$HEALTH_MAX_TIME_SECONDS" \
    --output "$HEALTH_BODY" \
    --write-out '%{http_code}' \
    'http://127.0.0.1:5000/health?deep=1')" || return 1
  if [[ "$health_code" != "200" ]]; then
    echo "deep health returned HTTP $health_code" >&2
    return 1
  fi

  health_fields="$(python - "$HEALTH_BODY" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as health_file:
    payload = json.load(health_file)
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
      sleep "$HEALTH_RETRY_DELAY_SECONDS" || return 1
    fi
  done
  echo "deep health readiness exhausted after $INTERNAL_HEALTH_ATTEMPTS attempts" >&2
  return 1
}

verify_public_health_once() {
  local health_code
  health_code="$(curl --silent --show-error \
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
      sleep "$HEALTH_RETRY_DELAY_SECONDS" || return 1
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

  write_override "$revision" || return 1
  docker compose "${COMPOSE_FILES[@]}" build || return 1
  docker compose "${COMPOSE_FILES[@]}" up -d --remove-orphans || return 1
  docker compose "${COMPOSE_FILES[@]}" ps || return 1
  running_revision="$(docker compose "${COMPOSE_FILES[@]}" exec -T web printenv APP_REVISION)" || return 1
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
  local restored_head

  echo "rolling back to $PREV_COMMIT" >&2
  git reset --hard "$PREV_COMMIT" || return 1
  restored_head="$(git rev-parse --verify HEAD^{commit})" || return 1
  if [[ "$restored_head" != "$PREV_COMMIT" ]]; then
    echo "rollback checkout revision mismatch" >&2
    return 1
  fi
  start_and_verify_release "$PREV_COMMIT" "$LEGACY_ROLLBACK_ALLOWED" 0 || return 1
  echo "rollback verified at $PREV_COMMIT" >&2
}

on_deploy_error() {
  local failure_status="$?"
  trap - ERR
  set +e
  if [[ "$failure_status" -eq 0 ]]; then
    failure_status=1
  fi
  echo "candidate deployment failed; collecting bounded diagnostics" >&2
  docker compose "${COMPOSE_FILES[@]}" ps >&2
  docker compose "${COMPOSE_FILES[@]}" logs --tail 100 web worker >&2
  if ! rollback_release; then
    echo "rollback failed verification" >&2
  fi
  exit "$failure_status"
}

trap on_deploy_error ERR
echo "checking out exact candidate $DEPLOY_SHA" >&2
git reset --hard "$DEPLOY_SHA"
CHECKED_OUT_HEAD="$(git rev-parse --verify HEAD^{commit})"
readonly CHECKED_OUT_HEAD
if [[ "$CHECKED_OUT_HEAD" != "$DEPLOY_SHA" ]]; then
  echo "candidate checkout revision mismatch" >&2
  false
fi

start_and_verify_release "$DEPLOY_SHA" 0 1
docker image prune -f
trap - ERR
echo "deployment verified at $DEPLOY_SHA" >&2
