from pathlib import Path
import re

import pytest
import yaml


DOCKERIGNORE = Path(".dockerignore")
CODEOWNERS = Path(".github/CODEOWNERS")


WORKFLOW = Path(".github/workflows/deploy.yml")
CONTROLLER = Path("scripts/deploy_control.py")
HOST_SCRIPT = Path("scripts/production_deploy.sh")
DEPLOYMENT_GUIDE = Path("docs/DEPLOYMENT.md")


def _deploy_yaml():
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow_doc():
    return yaml.load(_deploy_yaml(), Loader=yaml.BaseLoader)


def _controller_source():
    return CONTROLLER.read_text(encoding="utf-8")


def _host_script():
    return HOST_SCRIPT.read_text(encoding="utf-8")


def _deployment_guide():
    return DEPLOYMENT_GUIDE.read_text(encoding="utf-8")


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
    }


def test_deploy_checks_out_only_the_ci_approved_sha_with_full_history():
    job = _workflow_doc()["jobs"]["deploy"]
    checkout = next(
        step for step in job["steps"]
        if step.get("uses") ==
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )

    assert job["timeout-minutes"] == "65"
    assert checkout["with"] == {
        "ref": "${{ env.DEPLOY_SHA }}",
        "fetch-depth": "0",
        "persist-credentials": "false",
    }


def test_privileged_deploy_job_requires_default_branch_execution_sha_to_equal_candidate():
    job = _workflow_doc()["jobs"]["deploy"]

    assert job["if"] == (
        "${{ github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.head_branch == 'main' && "
        "github.event.workflow_run.event == 'push' && "
        "github.sha == github.event.workflow_run.head_sha }}"
    )


def test_workflow_identity_is_verified_before_checkout_or_oidc():
    steps = _workflow_doc()["jobs"]["deploy"]["steps"]
    identity_gate = steps[0]

    assert identity_gate["env"] == {
        "CANDIDATE_SHA": "${{ github.event.workflow_run.head_sha }}",
        "WORKFLOW_SHA": "${{ github.workflow_sha }}",
    }
    assert identity_gate["run"] == (
        'test -n "$WORKFLOW_SHA" && '
        'test "$WORKFLOW_SHA" = "$CANDIDATE_SHA"'
    )
    assert steps[1]["uses"] == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert steps[2]["uses"] == (
        "aws-actions/configure-aws-credentials@"
        "e6de054238d6b7531b4efff3b6587d9aade6a06c"
    )


def test_privileged_workflow_has_no_mutable_executable_dependencies():
    workflow = _workflow_doc()
    steps = workflow["jobs"]["deploy"]["steps"]

    action_refs = [step["uses"] for step in steps if "uses" in step]
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref) for ref in action_refs)
    assert "pip install" not in _deploy_yaml()
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] == "false"


def test_repository_variables_enter_privileged_shell_only_through_validated_env():
    snapshot = next(
        step for step in _workflow_doc()["jobs"]["deploy"]["steps"]
        if step.get("name") == "Pre-deploy RDS snapshot (non-blocking)"
    )

    assert snapshot["env"] == {"RDS_INSTANCE_ID": "${{ vars.RDS_INSTANCE_ID }}"}
    assert "${{ vars.RDS_INSTANCE_ID }}" not in snapshot["run"]
    assert 'RDS_ID="$RDS_INSTANCE_ID"' in snapshot["run"]
    assert "^[a-z][a-z0-9-]{0,62}$" in snapshot["run"]


def test_governance_contract_has_codeowners_for_every_privileged_surface():
    owners = CODEOWNERS.read_text(encoding="utf-8")

    for protected_path in (
        "/.github/CODEOWNERS",
        "/.github/workflows/deploy.yml",
        "/.github/workflows/ci.yml",
        "/scripts/deploy_control.py",
        "/scripts/production_deploy.sh",
        "/docs/DEPLOYMENT.md",
    ):
        assert re.search(rf"(?m)^{re.escape(protected_path)}\s+@yusufbesirarslan$", owners)


def test_ci_has_mandatory_root_linux_lock_job():
    ci = yaml.load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job = ci["jobs"]["linux-production-locks"]

    assert job["runs-on"] == "ubuntu-latest"
    run_steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert "sudo" in run_steps
    assert "-m linux_lock" in run_steps
    assert "--run-authoritative-linux-lock-tests" in run_steps


def test_deploy_lifecycle_has_one_controller_entrypoint_and_named_inputs():
    steps = _workflow_doc()["jobs"]["deploy"]["steps"]
    controller_steps = [
        step for step in steps
        if step.get("run") == "python3 scripts/deploy_control.py"
    ]

    assert len(controller_steps) == 1
    assert controller_steps[0]["env"] == {
        "AWS_REGION": "${{ vars.AWS_REGION || 'eu-central-1' }}",
        "EC2_INSTANCE_ID": "${{ secrets.EC2_INSTANCE_ID }}",
        "DEPLOY_USER": "${{ secrets.DEPLOY_USER }}",
        "DEPLOY_DIR": "${{ secrets.DEPLOY_DIR }}",
        "PUBLIC_HEALTH_URL": "${{ vars.PUBLIC_HEALTH_URL }}",
    }
    assert _workflow_doc()["env"] == {
        "DEPLOY_SHA": "${{ github.event.workflow_run.head_sha }}",
    }


def test_workflow_has_no_second_or_fail_open_ssm_lifecycle():
    body = _deploy_yaml()

    assert "aws ssm send-command" not in body
    assert "aws ssm get-command-invocation" not in body
    assert "git reset --hard origin/main" not in body
    assert "2>/dev/null || true" not in body
    assert "Sunucuda komutlar" not in body


def test_existing_non_deploy_safeguards_remain_without_secret_output():
    workflow = _deploy_yaml()
    controller = _controller_source()

    assert "scripts/check_cognito_pool.py" in workflow
    assert "scripts/check_email_lambda.py" in workflow
    assert "aws rds create-db-snapshot" in workflow
    assert workflow.count("continue-on-error: true") == 3
    assert "add_header Content-Security-Policy" in controller
    assert "nginx -t" in controller
    assert "127.0.0.1:3000" in controller
    assert "os.fchmod(env_fd, 0o600)" in controller
    assert all(
        "${{ secrets." not in line
        for line in workflow.splitlines()
        if "echo " in line
    )


def test_production_build_context_excludes_development_and_backups():
    ignored = {line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")}
    assert {"*.bak", "tests/", ".github/", "*.md",
            "requirements-dev.txt", "requirements-mcp.txt"} <= ignored
    host = _host_script()
    assert 'git archive --format=tar "$revision"' in host
    assert 'tar -xf "$BUILD_ARCHIVE" -C "$BUILD_CONTEXT_DIR"' in host
    assert "build:" in host and "context:" in host


def test_deploy_fails_on_live_nginx_csp_header_instead_of_sed_mutation():
    body = _controller_source()

    assert "add_header Content-Security-Policy" in body
    assert "sed -i" not in body
    assert "exit 1" in body


def test_deploy_health_gate_and_rollback_are_required():
    body = _host_script()

    assert "http://127.0.0.1:5000/health" in body
    assert "%{http_code}" in body
    assert "PREV_COMMIT" in body
    assert "ROLLBACK" in body


def test_deploy_warns_when_fatsecret_proxy_not_listening():
    # I4: FatSecret loopback proxy'sini (127.0.0.1:3000) kimse süpervize etmiyor;
    # deploy en azından dinleyici yokluğunu görünür uyarıyla raporlamalı
    # (başarısızlık DEĞİL — proxy düşükken app deploy'u bloklanmasın).
    body = _controller_source()
    assert ":3000" in body
    assert "fatsecret" in body.lower()


def test_deploy_gate_uses_deep_health():
    # I2: birincil gate derin sağlığa bakmalı — Redis-down'da login fail-closed
    # iken deploy "yeşil" geçmesin. (Rollback probe'u sığ kalır: kod geri
    # dönüşünü ölçer, Redis'i değil.)
    body = _host_script()
    assert "docker compose" in body
    assert "exec -T web python3" in body
    assert "urllib.request.urlopen" in body
    assert "http://127.0.0.1:5000/health?deep=1" in body
    assert "run_external curl" not in body.split("verify_public_health_once", 1)[0]


def test_deploy_is_gated_on_ci_success():
    """H3: deploy CI'a KAPILI olmalı.

    Eskiden `push: main` ile tetikleniyor ve ci.yml ile PARALEL koşuyordu — ne
    `needs:` ne `workflow_run:` vardı. main branch koruması da yok (API: 404
    "Branch not protected"), yani testleri kıran bir commit prod'a gidebiliyordu.
    """
    body = _deploy_yaml()
    assert "workflow_run:" in body
    assert 'workflows: ["CI"]' in body
    assert "github.event.workflow_run.conclusion == 'success'" in body
    assert "github.event.workflow_run.head_branch == 'main'" in body
    assert "github.event.workflow_run.event == 'push'" in body
    # Ham `push:` tetikleyicisi GİTMİŞ olmalı — yoksa gate baypas edilir.
    assert "\n  push:\n" not in body


def test_deploy_checks_cognito_pool_config_drift():
    # H4: havuz IaC'de değil; uygulamanın kimlik sağlayıcısı hakkındaki
    # varsayımları (PreventUserExistenceErrors, MFA, auth flows) her deploy'da
    # doğrulanmalı. Şimdilik bloklamaz (deploy rolünde izin yok).
    body = _deploy_yaml()
    assert "scripts/check_cognito_pool.py" in body


def test_deploy_takes_pre_deploy_rds_snapshot():
    # M5: rollback migration'ları geri almaz (A2). Snapshot, kurtarılamaz olayı
    # kurtarılabilir yapar. RDS_INSTANCE_ID yoksa uyarır, deploy'u bloklamaz.
    body = _deploy_yaml()
    assert "aws rds create-db-snapshot" in body
    assert "RDS_INSTANCE_ID" in body


def test_deploy_enforces_env_file_permissions():
    # M4: .env düz metin SECRET_KEY, RDS kimlik bilgileri, COGNITO_TOKEN_ENC_KEY,
    # RESEND_API_KEY ve OPENAI_API_KEY taşır — grup/dünya okunabilir OLMAMALI.
    body = _controller_source()
    assert "os.O_NOFOLLOW" in body
    assert "os.fchmod(env_fd, 0o600)" in body
    assert "env_status.st_nlink != 1" in body
    assert "follow_symlinks=False" in body
    assert "root_external chmod 600" not in body


def test_deployment_runbook_defines_the_immutable_operational_contract():
    guide = _deployment_guide()

    required_contract_text = {
        "`A` — `.github/workflows/deploy.yml`",
        "`B` — `scripts/deploy_control.py`",
        "`C` — `scripts/production_deploy.sh`",
        "`DEPLOY_SHA` is the sole deployment authority",
        "default-branch execution SHA (`github.sha`) to equal the CI candidate SHA",
        "workflow-file identity (`github.workflow_sha`) to equal that candidate",
        "main branch protection or a repository ruleset",
        "`AWS-RunShellScript`",
        "`production-deploy`",
        "`cancel-in-progress: false`",
        "at most one pending job",
        "SSM may still complete C on the host",
        "`PingStatus` must be `Online`",
        "`LastPingDateTime` must be no more than five minutes old",
        "samples its UTC clock immediately after the SSM describe response",
        "Workflow job timeout | 65 minutes",
        "Controller step timeout | 46 minutes",
        "Delivery timeout | 60 seconds",
        "Execution timeout | 1,800 seconds",
        "AWS expiry | 1,860 seconds",
        "Polling horizon | 2,100 seconds",
        "`/run/lock/axisai-production/production.lock`",
        "inherited descriptor 7",
        "retry after lock contention",
        "`origin/main` differs from `DEPLOY_SHA`",
        "resets only to `DEPLOY_SHA`",
        "server-owned `revision` equals the expected SHA",
        "exact `PREV_COMMIT`",
        "Rollback resets exactly to `PREV_COMMIT`",
        "Code rollback does not roll back database migrations",
        "immediately logs the non-secret command ID",
        "ambiguous SendCommand response cannot authorize an unknown command",
        "`InvocationDoesNotExist`",
        "Only that structured error code is retried",
        "`Pending`, `Delayed`, and `In Progress` are non-terminal SSM lifecycle states",
        "not proof that the host helper process has started",
        "`Success` is the only successful terminal `StatusDetails` value",
        "`Failed`, `DeliveryTimedOut`, `ExecutionTimedOut`, `Undeliverable`, `Cancelled`, and `Terminated` are terminal failures",
        "optional `PUBLIC_HEALTH_URL` is HTTPS",
        "materializes each build context from `git archive`",
        "probed inside the running `web` container",
        "protect the `production` environment with required reviewers",
        "CloudWatch and S3 retention are deferred operations work",
        "SSM-agent upgrades are separate host hygiene work",
    }

    normalized_guide = " ".join(guide.split())
    assert all(text in normalized_guide for text in required_contract_text)


def _deploy_source_violations(source):
    violations = []

    declaration = r"(?:readonly|declare(?:\s+-[A-Za-z]+)?|typeset(?:\s+-[A-Za-z]+)?|local|export)"
    assigned_names = re.findall(
        rf"(?m)^\s*(?:{declaration}\s+)*([A-Z][A-Z0-9_]*)\s*(?:=|:)",
        source,
    ) + re.findall(
        r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*\]\s*=", source
    )
    if any(
        "FEATURE" in name or name.endswith(("_ENABLED", "_DISABLED", "_FLAG"))
        for name in assigned_names
    ):
        violations.append("feature flag assignment")

    canonical_env_lines = {
        'env_file="$deploy_dir/.env"',
        'if [ ! -f "$env_file" ]; then',
        'env_permissions="$(root_external stat -c %a -- "$env_file")"',
        'root_external chmod 600 -- "$env_file"',
        "echo 'deployment .env file is missing' >&2",
        'echo "WARNING: correcting .env permissions from $env_permissions to 600"',
        "echo 'deployment .env permissions remain unsafe' >&2",
        "echo '.env permissions: 600'",
        '".env",',
        '".env", dir_fd=deploy_dir_fd, follow_symlinks=False',
        'raise OSError("unsafe deployment .env file")',
        'raise OSError("deployment .env permissions remain unsafe")',
        "print('.env permissions: 600')",
    }
    for line in source.splitlines():
        if re.search(r"\.env\b", line) or "$env_file" in line or "${env_file}" in line:
            if line.strip() not in canonical_env_lines:
                violations.append(".env content output")

    allowed_reset_lines = {
        'run_external git reset --hard "$DEPLOY_SHA"',
        '"${ROLLBACK_RESET_TIMEOUT_SECONDS}s" git reset --hard "$PREV_COMMIT"; then',
    }
    for line in source.splitlines():
        stripped = line.strip()
        if not re.search(r"(?<![A-Za-z0-9_])reset(?![A-Za-z0-9_])", stripped):
            continue
        if stripped in allowed_reset_lines:
            continue
        if stripped.startswith(("#", "echo ")):
            continue
        if "reset" in stripped:
            violations.append("mutable-main reset")

    if re.search(r"(?m)^\s*set\s+-[^\n]*x", source):
        violations.append("shell tracing")
    if re.search(
        r"(?im)^\s*(?:echo|printf)\b[^\n]*(?:AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)",
        source,
    ) or not all(
        credential not in source
        for credential in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        )
    ):
        violations.append("AWS credential output")
    return violations


def test_deploy_sources_cannot_expose_secrets_change_flags_or_reset_mutable_main():
    deploy_sources = "\n".join((_deploy_yaml(), _controller_source(), _host_script()))

    assert _deploy_source_violations(deploy_sources) == []


@pytest.mark.parametrize(
    ("unsafe_source", "expected_violation"),
    [
        ("AI_METRICS_ENABLED=1", "feature flag assignment"),
        ("readonly AI_METRICS_ENABLED=1", "feature flag assignment"),
        (
            'env_file="$deploy_dir/.env"\n'
            'env_alias="$env_file"\n'
            'emit-file -- "$env_alias"',
            ".env content output",
        ),
        (
            'env_file="$deploy_dir/.env"\n'
            'stat -c %a -- "$env_file"; emit-file -- "$env_file"',
            ".env content output",
        ),
        (
            'env_file="$deploy_dir/.env"\n'
            'permissions="$(stat -c %a -- "$env_file" "$(emit-file -- "$env_file")")"',
            ".env content output",
        ),
        ("printf '%s' < .env", ".env content output"),
        (
            'env_name=.env\n'
            'env_file="$deploy_dir/$env_name"\n'
            'base64 "$env_file"',
            ".env content output",
        ),
        (
            'ORIGIN_MAIN="$(git rev-parse refs/remotes/origin/main)"\n'
            'candidate="$ORIGIN_MAIN"\n'
            'git reset --hard "$candidate"',
            "mutable-main reset",
        ),
        (
            'remote=origin\n'
            'branch=main\n'
            'ref="$remote/$branch"\n'
            'git reset --hard "$ref"',
            "mutable-main reset",
        ),
        ('git -C "$DEPLOY_DIR" reset --hard "$ref"', "mutable-main reset"),
        ('alias reset="git reset --hard \'$DEPLOY_SHA\'"', "mutable-main reset"),
    ],
)
def test_deploy_source_guard_rejects_structural_bypasses(
    unsafe_source, expected_violation
):
    assert expected_violation in _deploy_source_violations(unsafe_source)


def test_deploy_source_guard_allows_the_canonical_env_file_metadata_forms():
    safe_source = (
        'env_file="$deploy_dir/.env"\n'
        'if [ ! -f "$env_file" ]; then\n'
        'env_permissions="$(root_external stat -c %a -- "$env_file")"\n'
        'root_external chmod 600 -- "$env_file"'
    )

    assert _deploy_source_violations(safe_source) == []
