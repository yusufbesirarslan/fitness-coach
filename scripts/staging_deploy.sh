#!/usr/bin/env bash
# AxisAI staging deploy — runs ON the staging host, usually through
# `aws ssm send-command`. See docs/STAGING.md.
#
# This is deliberately NOT scripts/production_deploy.sh with the environment
# swapped. That script carries production-specific hardening — the
# /run/lock/axisai-production capability lock, the phase-deadline monotonic
# clock budget, the origin/main staleness proof, the ancestor check against the
# currently deployed revision, and the automatic rollback-on-unhealthy path.
# Copying that machinery here would create a SECOND long-term deployment
# authority whose security-critical rules could drift from production's without
# anyone noticing (the failure mode §23 of the staging brief names). Staging
# needs none of it: there is no user data to protect, no concurrent deployer,
# and a bad staging revision is fixed by running this again.
#
# What staging DOES keep, because they are correctness rather than hardening:
#   * exact-SHA deployment — never a branch tip;
#   * BUILD_REVISION proof that the container actually runs that SHA;
#   * fail-closed target identity, so this can never run against production.
set -euo pipefail

readonly DEPLOY_SHA="${1:-}"
readonly STAGING_ROOT="${AXISAI_STAGING_ROOT:-/opt/axisai-staging}"
readonly REPO_DIR="$STAGING_ROOT/repo"
readonly ENVIRONMENT_MARKER="$STAGING_ROOT/ENVIRONMENT"
readonly INSTANCE_MARKER="$STAGING_ROOT/INSTANCE_ID"
readonly REVISION_OVERRIDE="$STAGING_ROOT/.axisai-staging-revision.yml"
# The staging overlay is installed on the HOST, not read out of the checkout,
# for the same reason the production helper is shipped with the SSM command
# rather than read from the deployed tree: the tooling must be able to deploy a
# revision that predates the tooling. b77a1dc — the revision Sprint 14 PR5 needs
# — contains no docker-compose.staging.yml, and reading the overlay from the
# checkout would make that revision undeployable.
readonly STAGING_OVERLAY="$STAGING_ROOT/docker-compose.staging.yml"

die() { echo "staging-deploy: $*" >&2; exit 1; }

# ── Fail-closed target identity ───────────────────────────────────────────────
# Three independent facts must agree before anything is mutated. Any one of them
# missing is a hard stop, never a skipped step: an unset variable must not be
# able to degrade into "deploy wherever this happens to be running".

assert_staging_environment() {
  [[ -f "$ENVIRONMENT_MARKER" ]] || die "no environment marker at $ENVIRONMENT_MARKER"
  local declared
  declared="$(cat "$ENVIRONMENT_MARKER")"
  [[ "$declared" == "staging" ]] || die "environment marker says '$declared', not 'staging'"
}

assert_staging_instance() {
  [[ -f "$INSTANCE_MARKER" ]] || die "no instance marker at $INSTANCE_MARKER"
  local expected actual token
  expected="$(cat "$INSTANCE_MARKER")"
  # IMDSv2. The host is launched with HttpTokens=required, so an unauthenticated
  # read returns 401 and `actual` would be empty — which fails the comparison
  # below rather than passing it.
  token="$(curl -fsS -X PUT 'http://169.254.169.254/latest/api/token' \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' --max-time 5)" \
    || die "instance metadata unavailable"
  actual="$(curl -fsS -H "X-aws-ec2-metadata-token: $token" \
    'http://169.254.169.254/latest/meta-data/instance-id' --max-time 5)" \
    || die "instance id unavailable"
  [[ -n "$actual" && "$actual" == "$expected" ]] \
    || die "running on '$actual' but this environment is recorded as '$expected'"
  # Supplied by the caller so the guard can refuse production by identity rather
  # than by trusting a label. Optional only because the production id is a
  # deployment secret the staging host has no business storing.
  if [[ -n "${AXISAI_PRODUCTION_INSTANCE_ID:-}" ]]; then
    [[ "$actual" != "$AXISAI_PRODUCTION_INSTANCE_ID" ]] \
      || die "target instance IS the production instance"
  fi
}

assert_staging_database() {
  local url count
  # Read the SAME assignment the application will. Compose's `env_file` takes
  # the LAST assignment of a key, so inspecting the first one would let a .env
  # carrying two DATABASE_URL lines pass this guard and then boot against the
  # other endpoint — and the app runs flask_migrate.upgrade() at boot, so that
  # is a write, not a read. Appending a corrected line instead of editing in
  # place is the ordinary way a .env acquires a duplicate, so refuse the
  # ambiguity outright rather than picking a side and being right by luck.
  count="$(grep -cE '^DATABASE_URL=' "$REPO_DIR/.env" || true)"
  [[ "$count" == "1" ]] \
    || die "expected exactly one DATABASE_URL line in staging .env, found $count"
  url="$(grep -E '^DATABASE_URL=' "$REPO_DIR/.env" | cut -d= -f2-)"
  [[ -n "$url" ]] || die "DATABASE_URL is empty"
  # The staging database is a container on this host, reached over the compose
  # network. An RDS endpoint here means the staging environment has been pointed
  # at a managed database — refuse rather than migrate someone else's schema.
  case "$url" in
    *rds.amazonaws.com*) die "DATABASE_URL points at an RDS endpoint" ;;
    *"@db:5432/"*) : ;;
    *) die "DATABASE_URL does not target the staging db container" ;;
  esac
}

# ── Preflight ─────────────────────────────────────────────────────────────────

[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || die "usage: staging_deploy.sh <40-hex commit sha> (branch names are refused)"
assert_staging_environment
assert_staging_instance
[[ -d "$REPO_DIR/.git" ]] || die "no repository checkout at $REPO_DIR"
[[ -f "$STAGING_OVERLAY" ]] || die "no staging overlay at $STAGING_OVERLAY"
[[ -f "$REPO_DIR/.env" ]] || die "no staging .env at $REPO_DIR/.env"
# Not a bare `chmod` under `set -e`: this script runs as the unprivileged
# owner of the checkout, so a .env left root-owned by a rebuild fails with
# EPERM and aborts the deploy with an error about permissions when the real
# problem is ownership. Say which.
chmod 600 "$REPO_DIR/.env" 2>/dev/null \
  || die "cannot secure $REPO_DIR/.env — it is owned by $(stat -c %U "$REPO_DIR/.env"), not $(id -un)"
assert_staging_database

cd "$REPO_DIR"
echo "staging-deploy: fetching $DEPLOY_SHA"
git fetch origin --prune --tags
git cat-file -e "${DEPLOY_SHA}^{commit}" \
  || die "commit $DEPLOY_SHA is not present after fetching origin"
git checkout --detach "$DEPLOY_SHA"
readonly HEAD_SHA="$(git rev-parse HEAD)"
[[ "$HEAD_SHA" == "$DEPLOY_SHA" ]] \
  || die "checkout landed on $HEAD_SHA, not $DEPLOY_SHA"

# ── Build and start ───────────────────────────────────────────────────────────
# BUILD_REVISION is baked into the image (see Dockerfile) and APP_REVISION is
# passed to the process, exactly as the production path does it. The override is
# generated per deploy rather than committed so it can never carry a stale SHA.
umask 077
cat > "$REVISION_OVERRIDE" <<YAML
services:
  web:
    build:
      args:
        BUILD_REVISION: '$DEPLOY_SHA'
    environment:
      APP_REVISION: '$DEPLOY_SHA'
  worker:
    build:
      args:
        BUILD_REVISION: '$DEPLOY_SHA'
    environment:
      APP_REVISION: '$DEPLOY_SHA'
YAML

COMPOSE=(docker compose -f docker-compose.yml -f "$STAGING_OVERLAY" -f "$REVISION_OVERRIDE")
echo "staging-deploy: building $DEPLOY_SHA"
"${COMPOSE[@]}" build
echo "staging-deploy: starting services"
"${COMPOSE[@]}" up -d --remove-orphans
"${COMPOSE[@]}" ps

# ── Prove what is actually running ────────────────────────────────────────────
# A green `up -d` only means containers started. These two checks are what make
# the deploy evidence rather than assertion: the bytes in the image carry the
# SHA, and the running process reports the same SHA back over its own health
# endpoint.
echo "staging-deploy: waiting for health"
for attempt in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T web python3 -c "
import urllib.request, sys
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).status == 200 else 1)
" 2>/dev/null; then
    break
  fi
  [[ "$attempt" -lt 30 ]] || die "service did not become healthy"
  sleep 5
done

readonly BAKED="$("${COMPOSE[@]}" exec -T web cat /app/BUILD_REVISION | tr -d '\r\n')"
[[ "$BAKED" == "$DEPLOY_SHA" ]] \
  || die "running image carries BUILD_REVISION $BAKED, not $DEPLOY_SHA"

"${COMPOSE[@]}" exec -T web python3 - "$DEPLOY_SHA" <<'PY'
import json, sys, urllib.request

expected = sys.argv[1]
with urllib.request.urlopen('http://127.0.0.1:5000/health?deep=1', timeout=10) as response:
    if response.status != 200:
        raise SystemExit(f'deep health returned HTTP {response.status}')
    payload = json.load(response)
revision = payload.get('revision')
if revision != expected:
    raise SystemExit(f'deep health reports revision {revision!r}, expected {expected!r}')
print(json.dumps(payload, indent=2, sort_keys=True))
PY

echo "staging-deploy: OK — staging is running $DEPLOY_SHA"
