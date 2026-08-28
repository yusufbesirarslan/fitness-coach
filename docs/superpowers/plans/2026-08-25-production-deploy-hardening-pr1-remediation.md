# Production Deploy Hardening PR1 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate all eight Important production-deployment certification findings in new commits without shipping or mutating remote production/governance state.

**Architecture:** Preserve the existing CI controller → SSM root bootstrap → deploy-user host transaction, but move final freshness and stale authority to their actual mutation boundaries. Centralize time budgets, bake actual revision truth into the image, execute a root-controlled verified helper object, and make external dependencies and ownership surfaces immutable and reviewable.

**Tech Stack:** Python 3.11, Bash, pytest, GitHub Actions YAML, Dockerfile, Docker Compose, AWS CLI/SSM, Linux `flock`/`openat` primitives.

**Spec:** `docs/superpowers/specs/2026-08-24-production-deploy-hardening-pr1-remediation-design.md`

## Global Constraints

- Work only on `ops/deploy-hardening-pr1` in `C:\Users\yusuf\fitness-coach\.worktrees\deploy-hardening-pr1`.
- Preserve history: no rebase, amend, squash, branch reset, force-push, push, PR, merge, deployment, SendCommand, AWS mutation, GitHub-settings mutation, production `.env` change, or feature-flag change.
- Write and observe a correctly failing regression before each production behavior/configuration change.
- Use new normal commits at coherent security boundaries.
- SSM heartbeat maximum is 360 seconds at the send authority boundary.
- Host worst case is 1,580 seconds; SSM execution timeout is 1,800 seconds; minimum margin is 220 seconds.
- Linux-only proof is wired into CI and reported locally as `PENDING EXACT-SHA UBUNTU CI`.
- A clean local result still requires fresh independent certification.

## File structure

- Create `scripts/deploy_contract.py`: canonical heartbeat, host phase, SSM, polling, and controller authority constants plus derived algebra.
- Create `app/build_revision.py`: fixed-path immutable build revision loader used by application startup.
- Modify `scripts/deploy_control.py`: consume the contract, perform send-time freshness, render post-lock authority/stale gates, and create/validate the stable helper object.
- Modify `scripts/production_deploy.sh`: consume injected canonical phase ceilings, keep all repository/Docker mutation after the root gate, pass exact build revision, and verify the baked artifact.
- Modify `Dockerfile`: require and bake `/app/BUILD_REVISION`.
- Modify `app/__init__.py` and `app/config.py`: expose deep-health revision from the baked artifact, leaving `APP_REVISION` diagnostic-only.
- Modify `.github/workflows/ci.yml` and `.github/workflows/deploy.yml`: immutable Action pins and authoritative Linux helper-race job coverage.
- Modify `docker-compose.yml`: immutable Redis 7.4.11 Alpine reference.
- Modify `.github/CODEOWNERS`: explicit production-authority ownership.
- Modify `docs/DEPLOYMENT.md`: exact revised contract and external governance gates.
- Modify `tests/test_deploy_control.py`, `tests/test_production_deploy_script.py`, `tests/test_deploy_workflow.py`, `tests/test_health.py`, `tests/test_compose_config.py`, and `tests/test_redis_compose_security.py`: behavioral and configuration regressions.

---

### Task 1: Canonical timeout algebra and send-time SSM freshness

**Files:**
- Create: `scripts/deploy_contract.py`
- Modify: `scripts/deploy_control.py`
- Modify: `tests/test_deploy_control.py`
- Modify: `tests/test_production_deploy_script.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: `SSM_HEARTBEAT_MAX_AGE_SECONDS: int = 360`, `SSM_EXECUTION_TIMEOUT_SECONDS: int = 1800`, `HOST_PHASE_SECONDS: Mapping[str, int]`, `HOST_WORST_CASE_SECONDS: int = 1580`, `SSM_EXECUTION_MARGIN_SECONDS: int = 220`, `CONTROLLER_REQUIRED_SECONDS: int = 2490`, and `host_timeout_environment() -> dict[str, str]`.
- Produces: `require_fresh_ssm_target(config, aws, utc_now) -> ManagedInstance`.
- Consumes later: bootstrap rendering imports `host_timeout_environment`; host helper receives those values as fixed environment.

- [ ] **Step 1: Add failing freshness and algebra tests**

Add independently derived literal assertions and a call-order fake:

```python
def test_send_rechecks_online_heartbeat_at_actual_send_boundary(config):
    clock = iter((NOW, NOW + timedelta(seconds=61)))
    calls = []

    def aws(args):
        calls.append(args)
        if args[:2] == ["ssm", "describe-instance-information"]:
            sampled = next(clock)
            return managed_instance_response(sampled - timedelta(seconds=360))
        if args[:2] == ["ssm", "send-command"]:
            return {"Command": {"CommandId": COMMAND_ID}}
        raise AssertionError(args)

    assert send_command(config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW) == COMMAND_ID
    assert [call[1] for call in calls] == ["describe-instance-information", "send-command"]


def test_send_rejects_heartbeat_that_became_stale_after_preflight(config):
    aws = fresh_then_stale_ssm_runner(age_seconds=361)
    with pytest.raises(PreflightError, match="stale"):
        send_command(config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW)
    assert not aws.sent


def test_canonical_host_budget_preserves_literal_220_second_margin():
    assert list(HOST_PHASE_SECONDS.values()) == [10, 60, 80, 10, 70, 620, 160, 30, 440, 80, 20]
    assert HOST_WORST_CASE_SECONDS == 1580
    assert SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS == 220
    assert CONTROLLER_REQUIRED_SECONDS == 2490 < 46 * 60
```

Parameterized validation also covers `PingStatus` values `ConnectionLost` and
`Inactive`, age 360 accepted, age 361 rejected, malformed/future timestamps,
and a fake forbidden operation inserted between describe and send.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
python -m pytest -q tests/test_deploy_control.py -k "send_rechecks or became_stale or canonical_host_budget"
python -m pytest -q tests/test_production_deploy_script.py -k "host_transaction_budget"
```

Expected: failures because `send_command` has no clock/final freshness call and timeout constants are duplicated/inconsistent.

- [ ] **Step 3: Implement the canonical contract and boundary check**

Create the contract with immutable mappings and import it from the controller:

```python
from types import MappingProxyType

SSM_HEARTBEAT_MAX_AGE_SECONDS = 360
SSM_EXECUTION_TIMEOUT_SECONDS = 1800
HOST_PHASE_SECONDS = MappingProxyType({
    "root_bootstrap": 10,
    "lock_acquisition": 60,
    "authority_and_stale_proof": 80,
    "clock_setup": 10,
    "git_fetch_checkout": 70,
    "candidate_build_start": 620,
    "candidate_revision_health": 160,
    "diagnostics": 30,
    "rollback_build_start": 440,
    "rollback_revision_health": 80,
    "cleanup": 20,
})
HOST_WORST_CASE_SECONDS = sum(HOST_PHASE_SECONDS.values())
SSM_EXECUTION_MARGIN_SECONDS = SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS
CONTROLLER_REQUIRED_SECONDS = 300 + 2100 + 30 + 60

if HOST_WORST_CASE_SECONDS != 1580 or SSM_EXECUTION_MARGIN_SECONDS < 220:
    raise RuntimeError("invalid host timeout contract")
if CONTROLLER_REQUIRED_SECONDS >= 46 * 60:
    raise RuntimeError("invalid controller timeout contract")
```

Change `send_command` to accept `utc_now`, invoke
`require_fresh_ssm_target(config, aws, utc_now)` as its last operation before
constructing and submitting the already-prepared `send-command` argument list,
and update `run_deploy` to pass its injected clock. Precompute remote command
and JSON parameters before the freshness call so only list assembly remains.

Render the contract environment from `host_timeout_environment()` into the
privilege-drop `execve` environment. Make `production_deploy.sh` require and
validate those values instead of declaring independent authority constants.

- [ ] **Step 4: Run focused and adjacent tests GREEN**

Run:

```powershell
python -m pytest -q tests/test_deploy_control.py
python -m pytest -q tests/test_production_deploy_script.py -k "budget or timeout or deadline or health"
```

Expected: all selected tests pass; logs report `worst_case=1580`, `execution=1800`, and `margin=220`.

- [ ] **Step 5: Commit**

```powershell
git add scripts/deploy_contract.py scripts/deploy_control.py scripts/production_deploy.sh tests/test_deploy_control.py tests/test_production_deploy_script.py docs/DEPLOYMENT.md
git diff --cached --check
git commit -m "fix(deploy): tighten SSM freshness and timeout bounds"
```

---

### Task 2: Pin all trusted Actions and require Linux security proof

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_deploy_workflow.py`

**Interfaces:**
- Consumes: GitHub workflow `uses:` entries.
- Produces: `trusted_action_references(paths) -> iterable[(path, reference)]` test helper enforcing full SHAs or explicit local paths.

- [ ] **Step 1: Add the failing all-workflow pin test**

```python
@pytest.mark.parametrize("workflow_path", [Path(".github/workflows/ci.yml"), Path(".github/workflows/deploy.yml")])
def test_every_trusted_action_reference_is_immutable(workflow_path):
    violations = []
    for line_number, line in enumerate(workflow_path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.search(r"\buses:\s*([^\s#]+)", line)
        if not match:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference):
            violations.append((line_number, reference))
    assert violations == []
```

Extend the CI contract test so the authoritative Linux job runs both the OFD
lock cases and the helper-object replacement/O_NOFOLLOW marker cases.

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
python -m pytest -q tests/test_deploy_workflow.py -k "every_trusted_action or mandatory_root_linux"
```

Expected: the six `actions/checkout@v7` / `actions/setup-python@v7` references in
CI are reported as mutable, and Linux helper race coverage is absent.

- [ ] **Step 3: Apply authoritative immutable pins**

Use these already upstream-resolved immutable releases consistently:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
```

Retain the deploy workflow's existing immutable credential action:

```yaml
uses: aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c # v6.2.3
```

Expand the Linux job pytest selection to include the new `linux_helper_identity`
marker introduced in Task 5. Do not add mutable installer actions.

- [ ] **Step 4: Run workflow tests GREEN and parse YAML**

```powershell
python -m pytest -q tests/test_deploy_workflow.py
python -c "import pathlib,yaml; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github/workflows').glob('*.yml')]"
```

- [ ] **Step 5: Commit**

```powershell
git add .github/workflows/ci.yml .github/workflows/deploy.yml tests/test_deploy_workflow.py
git diff --cached --check
git commit -m "fix(ci): pin trusted deployment actions"
```

---

### Task 3: Establish immutable built and serving revision authority

**Files:**
- Create: `app/build_revision.py`
- Modify: `Dockerfile`
- Modify: `app/__init__.py`
- Modify: `app/config.py`
- Modify: `scripts/production_deploy.sh`
- Modify: `tests/test_health.py`
- Modify: `tests/test_production_deploy_script.py`
- Modify: `tests/test_deploy_workflow.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: `BUILD_REVISION_PATH = Path("/app/BUILD_REVISION")` and `load_build_revision(path: Path = BUILD_REVISION_PATH) -> str` returning a lowercase SHA or `unknown` outside a built image.
- Consumes: Docker build argument `BUILD_REVISION` from the exact materialized Git archive.
- Produces: deep-health `revision` from `app.config["BUILD_REVISION"]`; `APP_REVISION` remains non-authoritative metadata.

- [ ] **Step 1: Add failing independent-revision tests**

```python
def test_deep_health_revision_cannot_be_spoofed_by_app_revision(monkeypatch, tmp_path):
    artifact = tmp_path / "BUILD_REVISION"
    artifact.write_text("b" * 40 + "\n", encoding="ascii")
    monkeypatch.setattr(build_revision, "BUILD_REVISION_PATH", artifact)
    monkeypatch.setenv("APP_REVISION", "a" * 40)
    app = create_app_for_health_test()
    assert app.test_client().get("/health?deep=1").get_json()["revision"] == "b" * 40


def test_wrong_baked_candidate_revision_forces_rollback(bash_executable, host_fixture):
    fixture = host_fixture(baked_revision="c" * 40)
    result = fixture.run(bash_executable)
    assert result.returncode != 0
    assert f"git reset --hard {fixture.prev_commit}" in fixture.trace_text()
```

Add Dockerfile/host-contract tests that fail unless the Docker build receives
`BUILD_REVISION` from the same literal revision passed to `git archive`, and a
rollback trace exposes `PREV_COMMIT` as its baked and health revision.

- [ ] **Step 2: Run and confirm RED**

```powershell
python -m pytest -q tests/test_health.py tests/test_production_deploy_script.py -k "spoofed or baked or rollback"
```

Expected: deep health still returns `APP_REVISION`; no baked artifact is built or inspected.

- [ ] **Step 3: Bake and load the immutable artifact**

Add a Docker build step before ownership is finalized:

```dockerfile
ARG BUILD_REVISION
RUN case "$BUILD_REVISION" in \
      *[!0-9a-f]* | "" ) echo "BUILD_REVISION must be lowercase 40-hex" >&2; exit 64 ;; \
    esac && test "${#BUILD_REVISION}" -eq 40 && \
    printf '%s\n' "$BUILD_REVISION" > /app/BUILD_REVISION && \
    chown root:root /app/BUILD_REVISION && chmod 0444 /app/BUILD_REVISION
```

Implement strict loading:

```python
SHA_RE = re.compile(r"[0-9a-f]{40}")
BUILD_REVISION_PATH = Path("/app/BUILD_REVISION")

def load_build_revision(path: Path | None = None) -> str:
    target = BUILD_REVISION_PATH if path is None else path
    try:
        value = target.read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"
    return value if SHA_RE.fullmatch(value) else "unknown"
```

Set `app.config["BUILD_REVISION"]` once during factory startup and change deep
health to use it. In Compose override generation, add
`build.args.BUILD_REVISION: '$revision'`. Replace `printenv APP_REVISION` as the
actual proof with `cat /app/BUILD_REVISION`, while retaining `APP_REVISION` only
as labeled expected metadata if useful for diagnostics.

- [ ] **Step 4: Run focused and adjacent tests GREEN**

```powershell
python -m pytest -q tests/test_health.py tests/test_production_deploy_script.py tests/test_deploy_workflow.py
```

- [ ] **Step 5: Commit**

```powershell
git add app/build_revision.py app/__init__.py app/config.py Dockerfile scripts/production_deploy.sh tests/test_health.py tests/test_production_deploy_script.py tests/test_deploy_workflow.py docs/DEPLOYMENT.md
git diff --cached --check
git commit -m "fix(deploy): establish immutable build revision authority"
```

---

### Task 4: Reject stale commands before every production mutation

**Files:**
- Modify: `scripts/deploy_control.py`
- Modify: `scripts/production_deploy.sh`
- Modify: `tests/test_deploy_control.py`
- Modify: `tests/test_production_deploy_script.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: root-bootstrap order `lock -> command parameter proof -> deploy-user git ls-remote proof -> mutation gate`.
- Consumes: exact remote output `<DEPLOY_SHA>\trefs/heads/main` and controller authority value `<DEPLOY_SHA>:<canonical-command-uuid>`.
- Guarantees: stale exit status 75 and no `.env`, nginx, repository-ref/checkout, Docker, migration/startup, or revision mutation.

- [ ] **Step 1: Add failing zero-mutation tests**

Extend the real bootstrap harness with byte snapshots and command traces:

```python
def test_stale_post_lock_command_preserves_all_production_state(bootstrap_harness):
    before = bootstrap_harness.production_snapshot()
    result = bootstrap_harness.run(remote_main="f" * 40)
    after = bootstrap_harness.production_snapshot()
    assert result.returncode == 75
    assert after == before
    assert bootstrap_harness.mutation_trace() == []


def test_stale_command_never_chmods_env_or_reloads_nginx(bootstrap_harness):
    result = bootstrap_harness.run(remote_main="f" * 40)
    assert result.returncode == 75
    assert "fchmod:.env" not in bootstrap_harness.trace_text()
    assert "systemctl" not in bootstrap_harness.trace_text()
```

The snapshot includes `.env` bytes/mode/inode, nginx-managed bytes and service
trace, `HEAD`, refs/status, Docker trace, and revision marker bytes. Add a test
that a local `git fetch` before the remote proof is considered mutation and
fails ordering.

- [ ] **Step 2: Run and confirm RED**

```powershell
python -m pytest -q tests/test_deploy_control.py tests/test_production_deploy_script.py -k "stale_post_lock or stale_command_never"
```

Expected: current bootstrap changes `.env` mode and nginx state before host stale rejection.

- [ ] **Step 3: Move the final authority gate behind the lock and before mutation**

Move the SSM parameter loop inside the root-lock child. Run the read-only stale
proof as the configured deploy user with bounded `runuser` and exact parsing:

```sh
remote_main="$(root_external runuser -u "$deploy_user" -- \
  git -C "$deploy_dir" ls-remote --exit-code origin refs/heads/main)"
if [ "$remote_main" != "$deploy_sha$(printf '\t')refs/heads/main" ]; then
  echo 'deployment candidate is stale at host mutation gate' >&2
  exit 75
fi
```

Only after that comparison may the existing `.env` permission repair, nginx
validation/reload, helper creation, and host mutation flow run. Move monotonic
temporary state out of `DEPLOY_DIR` into the verified runtime directory. Keep
the host's post-gate fetch and exact-object checks as defense in depth.

- [ ] **Step 4: Run focused bootstrap/host tests GREEN**

```powershell
python -m pytest -q tests/test_deploy_control.py -k "bootstrap or authority or stale"
python -m pytest -q tests/test_production_deploy_script.py -k "stale or mutation or preflight"
```

- [ ] **Step 5: Commit**

```powershell
git add scripts/deploy_control.py scripts/production_deploy.sh tests/test_deploy_control.py tests/test_production_deploy_script.py docs/DEPLOYMENT.md
git diff --cached --check
git commit -m "fix(deploy): reject stale commands before production mutation"
```

---

### Task 5: Execute the exact validated privileged helper object

**Files:**
- Modify: `scripts/deploy_control.py`
- Modify: `tests/test_deploy_control.py`
- Modify: `tests/test_production_deploy_script.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `pytest.ini`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: `create_verified_helper(directory_fd, encoded_bytes, expected_sha256, os_module, stat_module, hashlib_module) -> tuple[int, str]` inside the embedded root wrapper.
- Produces: root-owned regular mode-0505 single-link helper in verified `/run/lock/axisai-production`; descriptor digest, path/descriptor inode, and owner/mode all validated.
- Consumes: `PRIVILEGE_DROP_SOURCE` executes only that unreplaceable path after dropping to the configured user.

- [ ] **Step 1: Add failing object-identity and race tests**

```python
def test_helper_path_replacement_after_validation_cannot_change_executed_bytes(helper_race_harness):
    result = helper_race_harness.run(validated=b"#!/bin/sh\necho validated\n", replacement=b"#!/bin/sh\necho attacker\n")
    assert result.replacement_succeeded is False
    assert result.stdout.strip() == "validated"
    assert result.executed_sha256 == hashlib.sha256(result.validated_bytes).hexdigest()


@pytest.mark.linux_helper_identity
def test_linux_root_controlled_helper_rejects_deploy_user_replacement(authoritative_linux_helper):
    result = authoritative_linux_helper.race_replace_after_validation()
    assert result.returncode == 0
    assert result.stdout.strip() == "validated"
```

Add injected-unit cases for symlink, multiple links, non-root owner, writable
mode, digest mismatch, path inode swap, and short/failed writes. Mark only real
Linux root/O_NOFOLLOW execution tests `linux_helper_identity`.

- [ ] **Step 2: Run portable tests and confirm RED**

```powershell
python -m pytest -q tests/test_deploy_control.py -k "helper_path_replacement or helper_object"
```

Expected: current code validates/chowns a deploy-user path and `execve` can see replacement bytes.

- [ ] **Step 3: Implement root-controlled helper materialization**

Within the verified root-owned runtime directory:

```python
fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o500, dir_fd=directory_fd)
write_all(fd, helper_bytes)
os.fsync(fd)
os.fchmod(fd, 0o505)
status = os.fstat(fd)
path_status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
if hashlib.sha256(read_all(fd)).hexdigest() != expected_sha256:
    raise OSError("helper digest mismatch")
if status.st_uid != 0 or not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
    raise OSError("unsafe helper object")
if stat.S_IMODE(status.st_mode) != 0o505 or (status.st_dev, status.st_ino) != (path_status.st_dev, path_status.st_ino):
    raise OSError("helper identity changed")
```

Open read/write initially so the same descriptor can be rewound and hashed;
after `fchmod(0505)` no deploy-user write is possible. Keep the parent directory
root-owned mode 0755 and pass the stable absolute path to the privilege-drop
child. Root removes the helper after child termination. Do not chown it to the
deploy user and do not execute `/proc/self/fd`.

- [ ] **Step 4: Run portable tests GREEN and verify CI selection**

```powershell
python -m pytest -q tests/test_deploy_control.py -k "helper or privilege_drop or root_lock"
python -m pytest --collect-only -q -m "linux_lock or linux_helper_identity" tests/test_production_deploy_script.py
```

Record real Linux execution as pending; collection must show both marker classes wired into CI.

- [ ] **Step 5: Commit**

```powershell
git add scripts/deploy_control.py tests/test_deploy_control.py tests/test_production_deploy_script.py .github/workflows/ci.yml pytest.ini docs/DEPLOYMENT.md
git diff --cached --check
git commit -m "fix(deploy): close privileged helper replacement race"
```

---

### Task 6: Complete CODEOWNERS deployment-authority coverage

**Files:**
- Modify: `.github/CODEOWNERS`
- Modify: `tests/test_deploy_workflow.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: explicit ownership rules for canonical authority paths.
- Produces: test helper `codeowner_for(path: PurePosixPath, rules: Sequence[Rule]) -> tuple[str, ...]` applying last-match-wins semantics.

- [ ] **Step 1: Add failing canonical coverage test**

```python
PRODUCTION_AUTHORITY_SURFACES = (
    ".github/CODEOWNERS", ".github/workflows/ci.yml", ".github/workflows/deploy.yml",
    "scripts/deploy_contract.py", "scripts/deploy_control.py", "scripts/production_deploy.sh",
    "scripts/check_cognito_pool.py", "scripts/check_email_lambda.py",
    "Dockerfile", "docker-compose.yml", ".dockerignore", "requirements.txt",
    "starter.py", "worker.py", "gunicorn.conf.py", "nginx.conf",
    "app/__init__.py", "app/config.py", "app/build_revision.py", "app/db_init.py",
    "migrations/env.py", "migrations/versions/f1a2b3c4d5e6_imm1_sync_drift_columns.py",
    "docs/DEPLOYMENT.md",
)

def test_every_production_authority_surface_has_an_owner():
    rules = parse_codeowners(CODEOWNERS.read_text(encoding="utf-8"))
    uncovered = [path for path in PRODUCTION_AUTHORITY_SURFACES if not codeowner_for(PurePosixPath(path), rules)]
    assert uncovered == []
```

Add assertions that the runbook states CODEOWNER enforcement is currently
absent and must be enabled externally, so file coverage cannot masquerade as a
merge gate.

- [ ] **Step 2: Run and confirm RED**

```powershell
python -m pytest -q tests/test_deploy_workflow.py -k "production_authority_surface or governance"
```

Expected: Docker, Compose, startup, migration, health/revision, and contract files are uncovered.

- [ ] **Step 3: Add minimal explicit ownership rules and governance text**

Add owner `@yusufbesirarslan` for the exact files above plus directory rules
`/migrations/** @yusufbesirarslan` and
`/migrations/versions/** @yusufbesirarslan`.
Do not add a repository-wide `*` rule. Document all five external gates from the
spec and explicitly say their reported current state is absent/not proven.

- [ ] **Step 4: Run ownership/workflow tests GREEN**

```powershell
python -m pytest -q tests/test_deploy_workflow.py
```

- [ ] **Step 5: Commit**

```powershell
git add .github/CODEOWNERS tests/test_deploy_workflow.py docs/DEPLOYMENT.md
git diff --cached --check
git commit -m "chore(deploy): cover production authority surfaces"
```

---

### Task 7: Pin Redis to an immutable compatible image

**Files:**
- Modify: `docker-compose.yml`
- Modify: `tests/test_compose_config.py`
- Modify: `tests/test_redis_compose_security.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: Redis image `redis:7.4.11-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`.
- Produces: production Compose parser guard requiring external `image:` values to match `name:explicit-version@sha256:<64-hex>`.

- [ ] **Step 1: Add the failing immutable-image guard**

```python
def test_every_external_production_image_is_versioned_and_digest_pinned(services):
    violations = {}
    for name, service in services.items():
        image = service.get("image")
        if image is not None and not re.fullmatch(r"[^:@\s]+:[^@\s]+@sha256:[0-9a-f]{64}", image):
            violations[name] = image
    assert violations == {}


def test_redis_stays_on_compatible_major_seven(services):
    assert services["redis"]["image"].startswith("redis:7.4.11-alpine@sha256:")
```

- [ ] **Step 2: Run and confirm RED**

```powershell
python -m pytest -q tests/test_compose_config.py tests/test_redis_compose_security.py -k "external_production_image or compatible_major"
```

Expected: `redis:alpine` is reported as mutable.

- [ ] **Step 3: Pin the authoritative Docker Hub index digest**

Set:

```yaml
image: redis:7.4.11-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf
```

This is the Docker Hub multi-platform index digest for Redis 7.4.11 Alpine,
preserving the existing non-root `redis` user, command, persistence, password,
healthcheck, and memory policy. Record that no other third-party production
Compose image exists; `web` and `worker` build the exact local context.

- [ ] **Step 4: Run Compose/security tests and configuration validation GREEN**

```powershell
python -m pytest -q tests/test_compose_config.py tests/test_redis_compose_security.py
docker compose config --quiet
```

- [ ] **Step 5: Commit**

```powershell
git add docker-compose.yml tests/test_compose_config.py tests/test_redis_compose_security.py docs/DEPLOYMENT.md
git diff --cached --check
git commit -m "chore(deploy): pin production Redis image"
```

---

### Task 8: Minor-finding audit and final runbook consistency

**Files:**
- Modify only if evidence exists: `docs/DEPLOYMENT.md`
- Inspect: Git history, reflogs, local desktop text files, repository review artifacts.

**Interfaces:**
- Produces: final report entries for each exact Minor finding, or an explicit evidence limitation if the source cannot be recovered.

- [ ] **Step 1: Search all reachable local review context without mutation**

```powershell
git log --all --decorate --oneline --grep="certification\|Minor\|hardening"
git reflog show --all
rg -n -i -C 4 "Minor: 2|Minor findings|production hardening|certification" C:\Users\yusuf\OneDrive\Masaüstü C:\Users\yusuf\fitness-coach -g "*.txt" -g "*.md"
```

- [ ] **Step 2: Classify recovered findings or record the evidence gap**

For each recovered item, write description, verified severity, `accept`/`fix`/`defer`, and rationale. If neither can be recovered, state exactly:

```text
The remediation input reports two Minor findings but omits their descriptions; no reachable local certification artifact contained them. They were not invented or silently remediated. Fresh independent certification must restate and reassess them.
```

Do not make a speculative code change. If a recovered item is Important, stop
this task, reproduce it with a failing test, implement it in a new commit, and
rerun the affected suite.

- [ ] **Step 3: Verify the runbook has no obsolete authority claims**

```powershell
rg -n "750|1,809|APP_REVISION.*server-owned|redis:alpine|CODEOWNER.*active" docs/DEPLOYMENT.md
```

Expected: no obsolete positive contract remains; historical explanation must be explicitly labeled as superseded if retained.

- [ ] **Step 4: Commit only if documentation changed**

```powershell
git add docs/DEPLOYMENT.md
git diff --cached --check
git diff --cached --quiet; if ($LASTEXITCODE -ne 0) { git commit -m "docs: record deployment certification gates" }
```

---

### Task 9: Scoped adversarial review and final validation

**Files:**
- Review: all files changed since `fa1a48d104409dfe33c6ba91d9a15e43ab0d3cab`
- Modify only through new test-first fix commits if review finds Critical/Important defects.

**Interfaces:**
- Produces: exact final HEAD, validation counts, Linux proof status, drift result, clean-tree status, and answers to all 29 required questions.

- [ ] **Step 1: Run focused security suites**

```powershell
python -m pytest -q tests/test_deploy_control.py tests/test_deploy_workflow.py tests/test_production_deploy_script.py tests/test_health.py tests/test_compose_config.py tests/test_redis_compose_security.py
```

- [ ] **Step 2: Run syntax and configuration validation**

```powershell
python -c "import pathlib,yaml; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github/workflows').glob('*.yml')]"
python -m compileall -q app scripts tests
docker compose config --quiet
bash -n scripts/production_deploy.sh
git diff --check origin/main...HEAD
```

If local Bash is unavailable, report that command as unavailable and rely only
on collected Ubuntu CI wiring; do not claim Linux syntax/execution success.

- [ ] **Step 3: Run the complete repository-standard non-load suite**

```powershell
python -m pytest -q
```

Capture the final summary line and exact passed/skipped/xfailed/failed counts.

- [ ] **Step 4: Perform scoped adversarial code review**

Review the exact diff for ways to bypass heartbeat freshness, timeout nesting,
Action pin parsing, baked revision authority, stale zero-mutation, helper object
identity, CODEOWNERS matching, and image immutability. Classify findings as
Critical/Important/Minor. Reproduce any Critical/Important defect with a
failing test before fixing it in a new commit.

- [ ] **Step 5: Fetch and integrate material main drift without rebase**

```powershell
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git merge-base HEAD origin/main
git diff --name-status 59cff9d54a059f9456f2b83a513784db21eef859..origin/main
```

If deployment-relevant main drift exists, run `git merge --no-ff origin/main`,
resolve substantively, commit the merge normally, and rerun Steps 1–3. Do not
merge if origin/main remains integrated.

- [ ] **Step 6: Verify clean state and prepare the bounded verdict**

```powershell
git status --short --untracked-files=all
git log --oneline fa1a48d104409dfe33c6ba91d9a15e43ab0d3cab..HEAD
```

Answer all 29 questions from the remediation request. Use
`IMPORTANT FINDINGS FIXED LOCALLY — FRESH INDEPENDENT CERTIFICATION REQUIRED`
only with zero scoped Critical/Important findings, green full local validation,
current main integrated, and a clean tree. Otherwise use `NOT READY`.
