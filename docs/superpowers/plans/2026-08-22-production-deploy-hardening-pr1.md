# Production Deployment Hardening PR1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production deployment use exactly the CI-approved SHA with stale rejection, GitHub and host serialization, bounded SSM delivery/execution, and authoritative running-revision verification.

**Architecture:** A Python runner controller validates the GitHub candidate and AWS target, sends one safely encoded host script through SSM, and polls the detailed invocation lifecycle. A locked Bash host transaction fetches and checks out the exact SHA, builds and starts Compose, verifies the server-owned revision plus health, and rolls back to the exact previous SHA on failure.

**Tech Stack:** GitHub Actions YAML, Python 3.11 standard library, AWS CLI v2, Bash, `flock`, Git, Docker Compose, Flask, pytest, PyYAML.

**Spec:** `docs/superpowers/specs/2026-08-22-production-deploy-hardening-pr1-design.md`

## Global Constraints

- Start from `7a5b2a7cd4dacd782f7932760e151037ee1b4662` on `ops/deploy-hardening-pr1` in the isolated worktree.
- Do not push, open a PR, merge, deploy, invoke `workflow_dispatch`, call real `ssm:SendCommand`, mutate AWS, update the SSM agent, or change production flags.
- `DEPLOY_SHA` comes only from `github.event.workflow_run.head_sha` and must be a lowercase 40-hex commit.
- Production region is `eu-central-1`; exactly one structurally valid EC2 instance is allowed.
- Delivery/start timeout is 60 seconds, execution timeout is 1800 seconds, AWS total expiry bound is 1860 seconds, and polling horizon is 2100 seconds.
- GitHub concurrency uses `production-deploy`, `cancel-in-progress: false`, and `queue: single`.
- Host mutation lock acquisition is bounded to 60 seconds.
- Internal success requires container revision, deep-health revision, and deep-health HTTP 200 to agree.
- Rollback is code/container rollback only; it does not reverse database migrations.
- No test may access real AWS or trigger the production workflow.

---

### Task 1: Pin trigger, SHA authority, and GitHub serialization

**Files:**
- Modify: `tests/test_deploy_workflow.py`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: GitHub `workflow_run` payload fields.
- Produces: workflow-level `DEPLOY_SHA` environment and one serialized production control path.

- [ ] **Step 1: Add semantic workflow tests that fail on the current YAML**

Add a PyYAML BaseLoader helper and focused assertions:

```python
import yaml


def _workflow_doc():
    return yaml.load(_deploy_yaml(), Loader=yaml.BaseLoader)


def test_deploy_has_only_ci_workflow_run_authority():
    trigger = _workflow_doc()["on"]
    assert set(trigger) == {"workflow_run"}
    assert trigger["workflow_run"]["workflows"] == ["CI"]
    assert trigger["workflow_run"]["branches"] == ["main"]
    assert trigger["workflow_run"]["types"] == ["completed"]
    body = _deploy_yaml()
    assert "github.event.workflow_run.head_sha" in body
    assert "github.event.workflow_run.head_branch == 'main'" in body
    assert "github.event.workflow_run.event == 'push'" in body
    assert "workflow_dispatch" not in body


def test_production_deploys_are_coalesced_without_cancelling_running_work():
    concurrency = _workflow_doc()["concurrency"]
    assert concurrency == {
        "group": "production-deploy",
        "cancel-in-progress": "false",
        "queue": "single",
    }
```

Retain the existing CI-success assertions and strengthen them so a raw `push`
or manual dispatch cannot bypass the workflow-run contract.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_deploy_workflow.py
```

Expected: failures identify the existing `workflow_dispatch`, missing
`head_sha` authority, and missing concurrency contract.

- [ ] **Step 3: Add the minimal workflow trigger and concurrency contract**

At workflow scope, remove `workflow_dispatch`, add:

```yaml
concurrency:
  group: production-deploy
  cancel-in-progress: false
  queue: single
```

Set `DEPLOY_SHA` from `github.event.workflow_run.head_sha` and require the job
condition to include success, main branch, and source event `push`. Do not yet
replace the SSM body; later tasks wire the controller.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_deploy_workflow.py
```

Expected: all workflow tests pass.

- [ ] **Step 5: Commit the trigger boundary**

```powershell
git add .github/workflows/deploy.yml tests/test_deploy_workflow.py
git commit -m "fix(deploy): pin production trigger authority"
```

### Task 2: Implement fail-closed local and AWS preflight

**Files:**
- Create: `scripts/deploy_control.py`
- Create: `tests/test_deploy_control.py`

**Interfaces:**
- Consumes: `DEPLOY_SHA`, `AWS_REGION`, `EC2_INSTANCE_ID`, `DEPLOY_USER`, `DEPLOY_DIR`, optional `PUBLIC_HEALTH_URL`, repository path, AWS CLI JSON.
- Produces: `DeployConfig`, validated current candidate, and a verified single Online/fresh managed instance.

- [ ] **Step 1: Write failing configuration and candidate tests**

Define tests around this wished-for API:

```python
from datetime import datetime, timezone
from scripts.deploy_control import ConfigError, DeployConfig, validate_candidate


VALID_ENV = {
    "DEPLOY_SHA": "a" * 40,
    "AWS_REGION": "eu-central-1",
    "EC2_INSTANCE_ID": "i-0c6f5352fc214e68d",
    "DEPLOY_USER": "deploy",
    "DEPLOY_DIR": "/srv/axisai",
    "PUBLIC_HEALTH_URL": "https://fitness.example/health",
}


def test_config_accepts_one_explicit_production_target():
    config = DeployConfig.from_environ(VALID_ENV)
    assert config.deploy_sha == "a" * 40
    assert config.instance_id == "i-0c6f5352fc214e68d"


@pytest.mark.parametrize("sha", ["", "main", "A" * 40, "a" * 39, "a" * 41, "a" * 39 + ";"])
def test_config_rejects_noncanonical_sha(sha):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "DEPLOY_SHA": sha})


def test_validate_candidate_requires_origin_main_to_equal_deploy_sha(tmp_path):
    with pytest.raises(ConfigError, match="stale"):
        validate_candidate(tmp_path, "a" * 40, run_git=fake_git_returning("b" * 40))
```

Also cover empty/multiple/malformed instance IDs, region mismatch, unsafe deploy
user, non-absolute deploy directory, newline input, and non-HTTPS non-empty
public health URL.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_deploy_control.py
```

Expected: import failure because `scripts.deploy_control` does not exist.

- [ ] **Step 3: Implement immutable configuration and repository validation**

Create:

```python
SHA_RE = re.compile(r"[0-9a-f]{40}")
INSTANCE_RE = re.compile(r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class DeployConfig:
    deploy_sha: str
    region: str
    instance_id: str
    deploy_user: str
    deploy_dir: str
    public_health_url: str | None

    @classmethod
    def from_environ(cls, env: Mapping[str, str]) -> "DeployConfig":
        deploy_sha = env.get("DEPLOY_SHA", "")
        region = env.get("AWS_REGION", "")
        instance_id = env.get("EC2_INSTANCE_ID", "")
        deploy_user = env.get("DEPLOY_USER", "")
        deploy_dir = env.get("DEPLOY_DIR", "")
        public_url = env.get("PUBLIC_HEALTH_URL", "").strip() or None
        if not SHA_RE.fullmatch(deploy_sha):
            raise ConfigError("DEPLOY_SHA must be lowercase 40-hex")
        if region != "eu-central-1":
            raise ConfigError("AWS_REGION must be eu-central-1")
        if not INSTANCE_RE.fullmatch(instance_id):
            raise ConfigError("EC2_INSTANCE_ID must name one instance")
        if not USER_RE.fullmatch(deploy_user):
            raise ConfigError("DEPLOY_USER is unsafe")
        if not PurePosixPath(deploy_dir).is_absolute() or "\n" in deploy_dir:
            raise ConfigError("DEPLOY_DIR must be one absolute path")
        validate_public_health_url(public_url)
        return cls(deploy_sha, region, instance_id, deploy_user, deploy_dir, public_url)
```

Use full-match regexes for SHA, EC2 ID, and deploy user. Require exactly
`eu-central-1`, reject delimiters/whitespace/newlines, and validate the optional
URL with `urllib.parse.urlsplit` (`https`, hostname present, no username or
password). Implement `validate_candidate()` with subprocess argument arrays:
fetch `origin main --prune`, `cat-file -e <sha>^{commit}`, and compare
`rev-parse refs/remotes/origin/main` exactly to `DEPLOY_SHA`.

Define `ConfigError(RuntimeError)` and `PreflightError(RuntimeError)`, plus:

```python
AwsJsonRunner = Callable[[list[str]], dict[str, Any]]


@dataclass(frozen=True)
class ManagedInstance:
    instance_id: str
    last_ping: datetime
```

`validate_public_health_url(None)` returns normally; any non-empty value must
be HTTPS with a hostname and without embedded credentials. The test helper
`fake_git_returning(sha)` returns a callable that records argument arrays and
returns the requested SHA only for the final `rev-parse` call.

- [ ] **Step 4: Write failing EC2/SSM preflight tests**

Use an injected callable `aws(args: list[str]) -> dict` and a fixed UTC clock.
Cover:

```python
def test_preflight_accepts_running_online_fresh_single_target():
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    calls = []
    responses = iter([
        {"Reservations": [{"Instances": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "State": {"Name": "running"},
        }]}]},
        {"InstanceInformationList": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "PingStatus": "Online",
            "LastPingDateTime": "2026-08-22T17:58:00+00:00",
        }]},
    ])

    def aws(args):
        calls.append(args)
        return next(responses)

    managed = preflight(DeployConfig.from_environ(VALID_ENV), aws, now)
    assert managed.instance_id == VALID_ENV["EC2_INSTANCE_ID"]
    assert calls[0][:2] == ["ec2", "describe-instances"]
    assert calls[1][:2] == ["ssm", "describe-instance-information"]


@pytest.mark.parametrize("state,ping,heartbeat", [
    ("stopped", "Online", "2026-08-22T17:58:00+00:00"),
    ("running", "Offline", "2026-08-22T17:58:00+00:00"),
    ("running", "Online", "2026-08-22T17:50:00+00:00"),
])
def test_preflight_rejects_unusable_target_before_send(
        state, ping, heartbeat, aws_fixture):
    aws, calls = aws_fixture(state=state, ping=ping, heartbeat=heartbeat)
    with pytest.raises(PreflightError):
        preflight(
            DeployConfig.from_environ(VALID_ENV),
            aws,
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )
    assert not any(call[:2] == ["ssm", "send-command"] for call in calls)
```

Each rejecting test records calls and asserts no `ssm send-command` call was
made.

`aws_fixture` returns `(aws, calls)`. Its `aws` callable returns one EC2 response
using the requested `state`, followed by one SSM response using `ping` and
`heartbeat`; any third call fails the test. Separate tests return zero and two
SSM entries and raise `AwsCliError` directly to pin ambiguity and API failure.

- [ ] **Step 5: Implement and pass AWS preflight**

Implement:

```python
def preflight(
    config: DeployConfig,
    aws: AwsJsonRunner,
    now: datetime,
) -> ManagedInstance:
    ec2 = aws(["ec2", "describe-instances", "--instance-ids", config.instance_id,
               "--region", config.region])
    instances = [item for reservation in ec2.get("Reservations", [])
                 for item in reservation.get("Instances", [])]
    if len(instances) != 1 or instances[0].get("InstanceId") != config.instance_id:
        raise PreflightError("expected exactly one EC2 target")
    if instances[0].get("State", {}).get("Name") != "running":
        raise PreflightError("EC2 target is not running")
    ssm = aws(["ssm", "describe-instance-information", "--filters",
               f"Key=InstanceIds,Values={config.instance_id}", "--region", config.region])
    return validate_managed_instance(ssm, config.instance_id, now)
```

Require exactly one EC2 reservation/instance with matching ID and `running`;
then exactly one SSM `InstanceInformationList` entry with matching ID,
`PingStatus == "Online"`, and `now - LastPingDateTime <= timedelta(minutes=5)`.
Parse timestamps as aware datetimes and reject future-skew beyond one minute.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest -q tests/test_deploy_control.py
git add scripts/deploy_control.py tests/test_deploy_control.py
git commit -m "fix(deploy): fail closed before SSM delivery"
```

### Task 3: Model SendCommand delivery and polling lifecycle

**Files:**
- Modify: `scripts/deploy_control.py`
- Modify: `tests/test_deploy_control.py`

**Interfaces:**
- Consumes: validated config, immutable host-script bytes, AWS invocation JSON, monotonic clock.
- Produces: one SSM command ID and a terminal `InvocationResult` without swallowed CLI failures.

- [ ] **Step 1: Write failing payload and lifecycle tests**

Pin constants and state semantics:

```python
DELIVERY_TIMEOUT_SECONDS = 60
EXECUTION_TIMEOUT_SECONDS = 1800
AWS_EXPIRY_SECONDS = 1860
POLL_HORIZON_SECONDS = 2100


def test_send_payload_separates_delivery_and_execution_timeout():
    calls = []

    def aws(args):
        calls.append(args)
        return {"Command": {"CommandId": "11111111-1111-1111-1111-111111111111"}}

    command_id = send_command(DeployConfig.from_environ(VALID_ENV), b"echo safe", aws)
    assert command_id == "11111111-1111-1111-1111-111111111111"
    args = calls[0]
    assert args[args.index("--timeout-seconds") + 1] == "60"
    parameters = json.loads(args[args.index("--parameters") + 1])
    assert parameters["executionTimeout"] == ["1800"]


def test_in_progress_is_first_execution_proof(fake_clock):
    aws = invocation_sequence("Pending", "Delayed", "InProgress", "Success")
    messages = []
    result = wait_for_invocation(
        DeployConfig.from_environ(VALID_ENV), "command-id", aws,
        fake_clock.monotonic, fake_clock.sleep, messages.append,
    )
    assert result.status_details == "Success"
    assert sum("host execution started" in message for message in messages) == 1
    assert messages.index("host execution started") > messages.index("SSM status: Delayed")
```

Parameterize terminal closed failures over `Failed`, `DeliveryTimedOut`,
`ExecutionTimedOut`, `Undeliverable`, `Cancelled`, and `Terminated`. Add tests
for unknown status, malformed JSON, AWS runner exception, and Pending through
the 2100-second horizon. Assert API failure is never converted to `Pending`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest -q tests/test_deploy_control.py
```

Expected: lifecycle functions/constants are missing.

- [ ] **Step 3: Implement safe payload construction and detailed polling**

Add:

```python
@dataclass(frozen=True)
class InvocationResult:
    status_details: str
    response_code: int
    stdout: str
    stderr: str


def build_remote_command(config: DeployConfig, script: bytes) -> str:
    encoded_script = base64.b64encode(script).decode("ascii")
    encoded_dir = base64.b64encode(config.deploy_dir.encode()).decode("ascii")
    encoded_url = base64.b64encode((config.public_health_url or "").encode()).decode("ascii")
    return render_bootstrap(
        encoded_script, config.deploy_sha, encoded_dir,
        config.deploy_user, encoded_url,
    )


def send_command(config: DeployConfig, script: bytes, aws: AwsJsonRunner) -> str:
    parameters = {
        "commands": [build_remote_command(config, script)],
        "executionTimeout": [str(EXECUTION_TIMEOUT_SECONDS)],
    }
    response = aws([
        "ssm", "send-command", "--region", config.region,
        "--instance-ids", config.instance_id,
        "--document-name", "AWS-RunShellScript",
        "--timeout-seconds", str(DELIVERY_TIMEOUT_SECONDS),
        "--parameters", json.dumps(parameters, separators=(",", ":")),
    ])
    return require_command_id(response)


def wait_for_invocation(config, command_id, aws, monotonic, sleep, log):
    deadline = monotonic() + POLL_HORIZON_SECONDS
    execution_reported = False
    while monotonic() < deadline:
        result = read_invocation(config, command_id, aws)
        log(f"SSM status: {result.status_details}")
        if result.status_details == "InProgress" and not execution_reported:
            log("host execution started")
            execution_reported = True
        if result.status_details == "Success":
            return result
        if result.status_details in FAILURE_STATES:
            raise InvocationFailed(result)
        if result.status_details not in WAITING_STATES:
            raise InvocationProtocolError(result.status_details)
        sleep(POLL_INTERVAL_SECONDS)
    raise InvocationPollingTimeout(command_id)
```

Base64-encode the script and positional values. The remote bootstrap decodes to
a secure temporary file, validates/looks up the deploy user, changes ownership,
and invokes the script as that user. Pass `--timeout-seconds 60`; pass
`executionTimeout=["1800"]` in the document parameters. Construct all local AWS
CLI invocations as argument lists, not shell strings.

Normalize AWS `StatusDetails` spelling only at the comparison boundary while
preserving the original value in logs. Emit "host execution started" only for
`InProgress`. Return only `Success`; raise a typed failure for every other
terminal state or timeout.

Define the complete state and exception vocabulary in the controller:

```python
WAITING_STATES = frozenset({"Pending", "Delayed", "InProgress"})
FAILURE_STATES = frozenset({
    "Failed", "DeliveryTimedOut", "ExecutionTimedOut",
    "Undeliverable", "Cancelled", "Terminated",
})
POLL_INTERVAL_SECONDS = 10


class AwsCliError(RuntimeError):
    pass


class InvocationFailed(RuntimeError):
    def __init__(self, result: InvocationResult):
        super().__init__(f"SSM invocation failed: {result.status_details}")
        self.result = result


class InvocationProtocolError(RuntimeError):
    pass


class InvocationPollingTimeout(RuntimeError):
    pass
```

`render_bootstrap()` accepts only already validated/base64 values and produces
the fixed decode/chown/sudo script. `require_command_id()` requires one UUID.
`read_invocation()` calls `get-command-invocation`, reads `StatusDetails`,
`ResponseCode`, `StandardOutputContent`, and `StandardErrorContent`, and raises
`InvocationProtocolError` if any required field has the wrong type. The
`invocation_sequence()` and `fake_clock` test helpers return deterministic
responses/times and never sleep in real time.

- [ ] **Step 4: Run all controller tests and commit**

```powershell
python -m pytest -q tests/test_deploy_control.py
git add scripts/deploy_control.py tests/test_deploy_control.py
git commit -m "fix(deploy): bound and report SSM lifecycle"
```

### Task 4: Implement the locked exact-SHA host transaction

**Files:**
- Create: `scripts/production_deploy.sh`
- Create: `tests/test_production_deploy_script.py`

**Interfaces:**
- Consumes: positional `DEPLOY_SHA`, `DEPLOY_DIR`, optional `PUBLIC_HEALTH_URL`; standard host tools through `PATH`.
- Produces: exact checked-out Compose release, verified revision/health, or a non-zero closed failure/verified rollback.

- [ ] **Step 1: Write failing static and fake-command tests**

Tests invoke `C:\Program Files\Git\bin\bash.exe` when present and otherwise
`bash`. Use a temporary `fake-bin` directory containing executable stubs for
`flock`, `docker`, and `curl`; each appends safe arguments to a trace file.

Cover:

```python
def test_host_script_is_valid_bash(bash_executable):
    result = subprocess.run(
        [bash_executable, "-n", str(HOST_SCRIPT)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_lock_contention_fails_before_git_or_docker(
        bash_executable, host_fixture):
    fixture = host_fixture(flock_exit=1)
    result = fixture.run(bash_executable, deploy_sha="a" * 40)
    assert result.returncode == 73
    assert "deployment lock unavailable" in result.stderr
    assert fixture.trace.read_text(encoding="utf-8") == "flock -w 60 9\n"


def test_wrong_running_revision_rolls_back_despite_health_200(
        bash_executable, host_fixture):
    fixture = host_fixture(container_revision="b" * 40, health_code=200)
    result = fixture.run(bash_executable, deploy_sha="a" * 40)
    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert f"git reset --hard {fixture.prev_commit}" in trace
    assert f"APP_REVISION={fixture.prev_commit}" in trace
```

The lock-contention stub exits non-zero and the assertion verifies the trace has
no checkout/build/up entries. Git behavior uses temporary local bare/working
repositories; no network remote is used.

`host_fixture` creates a bare `origin`, a production checkout with distinct
`PREV_COMMIT` and candidate commits, executable fake commands, and a trace file.
It exposes `prev_commit`, `trace`, and `run(bash_executable, deploy_sha)`. The
fake `flock` either records `flock -w 60 9` and succeeds or returns the requested
failure. Fake Docker records every Compose argument and returns the configured
container revision; fake curl writes deterministic JSON/status outputs. The
rapid-candidate test holds the first fake lock with an event file and proves the
second invocation exits 73 without a Git/Docker mutation.

- [ ] **Step 2: Run host tests and verify RED**

```powershell
python -m pytest -q tests/test_production_deploy_script.py
```

Expected: missing host script.

- [ ] **Step 3: Implement validation, lock, and exact checkout**

The script starts with:

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly DEPLOY_SHA="${1:-}"
readonly DEPLOY_DIR="${2:-}"
readonly PUBLIC_HEALTH_URL="${3:-}"
readonly LOCK_PATH="$DEPLOY_DIR/.axisai-production-deploy.lock"

exec 9>"$LOCK_PATH"
if ! flock -w 60 9; then
  echo "deployment lock unavailable after 60 seconds" >&2
  exit 73
fi
```

Validate inputs before mutation. Record `PREV_COMMIT`; fetch only `origin main
--prune`; prove both commits exist; require `origin/main == DEPLOY_SHA` and
`git merge-base --is-ancestor "$PREV_COMMIT" "$DEPLOY_SHA"`; then
`git reset --hard "$DEPLOY_SHA"` and re-read HEAD before Docker runs.

- [ ] **Step 4: Implement revision-aware start and rollback**

Generate a temporary Compose override with `APP_REVISION` for both `web` and
`worker`, with cleanup through `trap`. Use the same explicit `-f` arguments for
build, up, ps, logs, and rollback. Verify container environment with
`docker compose exec -T web printenv APP_REVISION`, then fetch deep health to a
temporary file and parse `status` and `revision` with Python's JSON module.

On any failure after checkout starts, reset to exact `PREV_COMMIT`, rebuild/up
with `APP_REVISION=PREV_COMMIT`, verify container revision, and verify health.
For an old rollback revision that lacks deep-health `revision`, permit only the
documented one-time compatibility proof: exact checkout + container env + HTTP
200. Never use `origin/main` as rollback authority.

- [ ] **Step 5: Run host tests and commit**

```powershell
python -m pytest -q tests/test_production_deploy_script.py
git add scripts/production_deploy.sh tests/test_production_deploy_script.py
git commit -m "fix(deploy): lock exact SHA host mutations"
```

### Task 5: Expose the server-owned revision through deep health

**Files:**
- Modify: `app/config.py`
- Modify: `app/__init__.py`
- Modify: `tests/test_health.py`

**Interfaces:**
- Consumes: trusted container environment `APP_REVISION`.
- Produces: internal deep-health JSON field `revision`; shallow health remains unchanged.

- [ ] **Step 1: Write failing health revision tests**

Add `revision` to `_DEEP_KEYS` and tests:

```python
def test_deep_health_reports_server_owned_revision(client, monkeypatch):
    monkeypatch.setenv("APP_REVISION", "a" * 40)
    body = client.get(
        "/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"}
    ).get_json()
    assert body["revision"] == "a" * 40


def test_shallow_health_hides_revision(client, monkeypatch):
    monkeypatch.setenv("APP_REVISION", "a" * 40)
    assert "revision" not in client.get("/health").get_json()
```

Also test the safe local fallback `unknown` and prove request headers/query
parameters cannot override it.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest -q tests/test_health.py
```

Expected: deep health lacks `revision`.

- [ ] **Step 3: Add the minimal application revision signal**

Load `APP_REVISION` through application configuration with default `unknown`.
Inside the already-authorized deep-health branch only, add:

```python
body["revision"] = app.config["APP_REVISION"]
```

Do not change the status calculation, feature flags, or public shallow body.

- [ ] **Step 4: Run health and adjacent contract tests, then commit**

```powershell
python -m pytest -q tests/test_health.py tests/test_auth_contract.py
git add app/config.py app/__init__.py tests/test_health.py
git commit -m "feat(health): expose internal running revision"
```

### Task 6: Wire the controller into the production workflow

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_deploy_workflow.py`
- Modify: `tests/test_deploy_control.py`

**Interfaces:**
- Consumes: exact checked-out controller/host helper and GitHub environment values.
- Produces: one preflight-gated, lifecycle-bounded production SSM path.

- [ ] **Step 1: Add failing end-to-end workflow architecture guards**

Assert the workflow:

- checks out `${{ env.DEPLOY_SHA }}` with non-shallow history
- calls only `python scripts/deploy_control.py`
- provides the expected environment variables
- no longer contains inline `aws ssm send-command`, `git reset --hard
  origin/main`, `2>/dev/null || true`, or the premature Turkish "started on
  server" claim
- retains existing non-deployment nginx/Cognito/Lambda/RDS safeguards without
  printing secrets or changing flags

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest -q tests/test_deploy_workflow.py tests/test_deploy_control.py
```

- [ ] **Step 3: Replace the inline SSM block with the controller**

Use exact checkout:

```yaml
- uses: actions/checkout@v7
  with:
    ref: ${{ env.DEPLOY_SHA }}
    fetch-depth: 0
```

Give the controller only named environment values. Keep secrets in environment
references, never echo them. Set job `timeout-minutes: 40`. The controller's
`main()` executes candidate validation, preflight, host-script load,
`send_command`, and detailed polling in that order.

- [ ] **Step 4: Run workflow/controller tests and commit**

```powershell
python -m pytest -q tests/test_deploy_workflow.py tests/test_deploy_control.py
git add .github/workflows/deploy.yml scripts/deploy_control.py tests/test_deploy_workflow.py tests/test_deploy_control.py
git commit -m "fix(deploy): integrate safe SSM controller"
```

### Task 7: Document the deploy contract and add final static guards

**Files:**
- Modify: `docs/DEPLOYMENT.md`
- Modify: `tests/test_deploy_workflow.py`

**Interfaces:**
- Consumes: implemented workflow/controller/host contracts.
- Produces: operational runbook and non-vacuous regression guard coverage.

- [ ] **Step 1: Add failing documentation/safety tests**

Assert the canonical document names exact SHA authority, concurrency/coalescing,
SSM Online plus five-minute freshness, the four lifecycle numbers, host lock,
revision equality, exact rollback SHA, code-only migration rollback,
`StatusDetails` meanings, optional public HTTPS coverage, deferred CloudWatch/S3
retention, and separate SSM-agent hygiene.

Add source guards proving no deploy file contains feature-flag assignment,
`.env` content output, `set -x`, AWS credential output, or mutable-main reset.

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest -q tests/test_deploy_workflow.py
```

- [ ] **Step 3: Rewrite `docs/DEPLOYMENT.md` as the operational contract**

Replace the mutable-main pipeline diagram. Document A/B/C behavior, runner loss
after SendCommand, lock-timeout retry behavior, every terminal AWS status,
revision mismatch handling, rollback verification, and the migration limitation.
State that CloudWatch/S3 output and SSM-agent upgrade require separate ops work.

- [ ] **Step 4: Run static/focused tests and commit**

```powershell
python -m pytest -q tests/test_deploy_workflow.py tests/test_deploy_control.py tests/test_production_deploy_script.py tests/test_health.py
git add docs/DEPLOYMENT.md tests/test_deploy_workflow.py
git commit -m "docs: define immutable deploy operations"
```

### Task 8: Local verification, implementation commit review, and hard gate

**Files:**
- Inspect: every file changed since base SHA
- Modify only if a failing regression test first demonstrates a review defect

**Interfaces:**
- Consumes: complete local implementation history.
- Produces: verified clean worktree ready for independent adversarial review.

- [ ] **Step 1: Run syntax and focused verification**

```powershell
python -m compileall -q scripts app
python -m pytest -q tests/test_deploy_workflow.py tests/test_deploy_control.py tests/test_production_deploy_script.py tests/test_health.py tests/test_auth_contract.py tests/test_compose_config.py
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/production_deploy.sh
```

- [ ] **Step 2: Run the repository-standard adjacent suite**

```powershell
python -m pytest -q
```

Expected: all default, non-load tests pass. No PostgreSQL opt-in or real AWS
test is run.

- [ ] **Step 3: Inspect the complete diff and safety boundary**

```powershell
git diff --check 7a5b2a7cd4dacd782f7932760e151037ee1b4662..HEAD
git diff --stat 7a5b2a7cd4dacd782f7932760e151037ee1b4662..HEAD
git diff 7a5b2a7cd4dacd782f7932760e151037ee1b4662..HEAD
git status --short --untracked-files=all
```

Explicitly verify no secrets, target replacement, feature change, flag change,
AWS-resource mutation, staging work, or product behavior change beyond internal
revision exposure.

- [ ] **Step 4: Request independent adversarial review**

The reviewer must attempt to break immutable SHA authority, stale rejection,
GitHub concurrency, host lock, SSM delivery bound, Pending lifecycle, runner
loss, AWS polling failure, revision verification, rollback, workflow-run
semantics, shell quoting, and secret hygiene. Findings are classified Critical,
Important, or Minor.

- [ ] **Step 5: Apply the hard review gate**

If any Critical or Important finding exists, write a failing regression test,
make the minimal fix, run focused plus adjacent verification, and create a new
commit without amending. Stop with `LOCALLY FIXED — INDEPENDENT RE-REVIEW
REQUIRED`.

If Critical and Important are both zero, rerun Step 1 through Step 3, confirm
the worktree is clean, and prepare the required local review report. Do not push,
open a PR, merge, deploy, call SendCommand, mutate AWS, or change flags.
