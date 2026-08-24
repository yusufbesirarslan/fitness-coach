from pathlib import Path
import re

import pytest
import yaml


DOCKERIGNORE = Path(".dockerignore")


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
        "queue": "single",
    }


def test_deploy_checks_out_only_the_ci_approved_sha_with_full_history():
    job = _workflow_doc()["jobs"]["deploy"]
    checkout = next(
        step for step in job["steps"]
        if step.get("uses") == "actions/checkout@v7"
    )

    assert job["timeout-minutes"] == "40"
    assert checkout["with"] == {
        "ref": "${{ env.DEPLOY_SHA }}",
        "fetch-depth": "0",
    }


def test_deploy_lifecycle_has_one_controller_entrypoint_and_named_inputs():
    steps = _workflow_doc()["jobs"]["deploy"]["steps"]
    controller_steps = [
        step for step in steps
        if step.get("run") == "python scripts/deploy_control.py"
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
    assert "chmod 600" in controller
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
    assert "http://127.0.0.1:5000/health?deep=1" in body


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
    assert "chmod 600" in body
    assert ".env" in body


def test_deployment_runbook_defines_the_immutable_operational_contract():
    guide = _deployment_guide()

    required_contract_text = {
        "`A` — `.github/workflows/deploy.yml`",
        "`B` — `scripts/deploy_control.py`",
        "`C` — `scripts/production_deploy.sh`",
        "`DEPLOY_SHA` is the sole deployment authority",
        "`AWS-RunShellScript`",
        "`production-deploy`",
        "`cancel-in-progress: false`",
        "`queue: single`",
        "SSM may still complete C on the host",
        "`PingStatus` must be `Online`",
        "`LastPingDateTime` must be no more than five minutes old",
        "Workflow job timeout | 40 minutes",
        "Delivery timeout | 60 seconds",
        "Execution timeout | 1,800 seconds",
        "AWS expiry | 1,860 seconds",
        "Polling horizon | 2,100 seconds",
        "`/run/lock/axisai-production/production.lock`",
        "retry after lock contention",
        "`origin/main` differs from `DEPLOY_SHA`",
        "resets only to `DEPLOY_SHA`",
        "server-owned `revision` equals the expected SHA",
        "exact `PREV_COMMIT`",
        "Rollback resets exactly to `PREV_COMMIT`",
        "Code rollback does not roll back database migrations",
        "`Pending`, `Delayed`, and `In Progress` mean the command is waiting",
        "`Success` is the only successful terminal `StatusDetails` value",
        "`Failed`, `DeliveryTimedOut`, `ExecutionTimedOut`, `Undeliverable`, `Cancelled`, and `Terminated` are terminal failures",
        "optional `PUBLIC_HEALTH_URL` is HTTPS",
        "CloudWatch and S3 retention are deferred operations work",
        "SSM-agent upgrades are separate host hygiene work",
    }

    normalized_guide = " ".join(guide.split())
    assert all(text in normalized_guide for text in required_contract_text)


def _deploy_source_violations(source):
    violations = []

    assigned_names = re.findall(
        r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*(?:=|:)", source
    ) + re.findall(
        r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*\]\s*=", source
    )
    if any(
        "FEATURE" in name
        or name.endswith(("_ENABLED", "_DISABLED", "_FLAG"))
        for name in assigned_names
    ):
        violations.append("feature flag assignment")

    env_file_variables = set(re.findall(
        r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=.*\.env\b", source
    ))
    assignment_lines = source.splitlines()
    changed = True
    while changed:
        changed = False
        for line in assignment_lines:
            match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if not match:
                continue
            name, value = match.groups()
            if name in env_file_variables:
                continue
            if any(
                re.fullmatch(
                    rf"[\"']?\$(?:\{{{re.escape(variable)}\}}|{re.escape(variable)}\b)[\"']?",
                    value.strip(),
                )
                for variable in env_file_variables
            ):
                env_file_variables.add(name)
                changed = True
    env_file_variable_reference = re.compile("|".join(
            rf"\$(?:\{{{re.escape(name)}\}}|{re.escape(name)}\b)"
            for name in env_file_variables
        ) or r"(?!)")
    env_reference = re.compile(r"\.env\b")
    for line in source.splitlines():
        if not (env_reference.search(line) or env_file_variable_reference.search(line)):
            continue
        if re.match(
            r"\s*[A-Za-z_][A-Za-z0-9_]*=.*\.env\b", line
        ):
            continue
        alias_assignment = re.match(
            r"\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)", line
        )
        if (
            alias_assignment
            and alias_assignment.group(1) in env_file_variables
            and any(
                re.fullmatch(
                    rf"[\"']?\$(?:\{{{re.escape(variable)}\}}|{re.escape(variable)}\b)[\"']?",
                    alias_assignment.group(2).strip(),
                )
                for variable in env_file_variables
            )
        ):
            continue
        if re.fullmatch(
            r"\s*if\s+\[\s*!?\s*-f\s+[^;|`<()]+\]\s*;\s*then\s*",
            line,
        ):
            continue
        if re.fullmatch(
            r"\s*[A-Za-z_][A-Za-z0-9_]*=\"\$\((?:root_external\s+)?"
            r"stat\s+-c\s+%a\s+--\s+[^;|`<()]+\)\"\s*",
            line,
        ):
            continue
        if re.fullmatch(
            r"\s*(?:root_external\s+)?chmod\s+600\s+--\s+[^;|`<()]+\s*",
            line,
        ):
            continue
        if (
            re.match(r"\s*(?:echo|printf)\b", line)
            and not env_file_variable_reference.search(line)
            and "$(" not in line
            and "<" not in line
            and not re.search(r"[;|`]", line)
        ):
            continue
        violations.append(".env content output")

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
    mutable_main_variables = {
        name
        for name in re.findall(
            r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=", source
        )
        if "origin" in name.lower() and "main" in name.lower()
    }
    changed = True
    while changed:
        changed = False
        for line in assignment_lines:
            match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if not match:
                continue
            name, value = match.groups()
            if (
                "origin/main" in value
                or "refs/remotes/origin/main" in value
                or any(
                    re.search(rf"\$(?:\{{{re.escape(variable)}\}}|{re.escape(variable)}\b)", value)
                    for variable in mutable_main_variables
                )
            ) and name not in mutable_main_variables:
                mutable_main_variables.add(name)
                changed = True
    for target in re.findall(r"git\s+reset\s+--hard\s+([^\n]+)", source):
        if (
            "origin/main" in target
            or "refs/remotes/origin/main" in target
            or any(
                re.search(rf"\$(?:\{{{re.escape(variable)}\}}|{re.escape(variable)}\b)", target)
                for variable in mutable_main_variables
            )
        ):
            violations.append("mutable-main reset")
    return violations


def test_deploy_sources_cannot_expose_secrets_change_flags_or_reset_mutable_main():
    deploy_sources = "\n".join((_deploy_yaml(), _controller_source(), _host_script()))

    assert _deploy_source_violations(deploy_sources) == []


@pytest.mark.parametrize(
    ("unsafe_source", "expected_violation"),
    [
        ("AI_METRICS_ENABLED=1", "feature flag assignment"),
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
            'ORIGIN_MAIN="$(git rev-parse refs/remotes/origin/main)"\n'
            'candidate="$ORIGIN_MAIN"\n'
            'git reset --hard "$candidate"',
            "mutable-main reset",
        ),
    ],
)
def test_deploy_source_guard_rejects_structural_bypasses(
    unsafe_source, expected_violation
):
    assert expected_violation in _deploy_source_violations(unsafe_source)


def test_deploy_source_guard_allows_only_metadata_operations_on_env_file_aliases():
    safe_source = (
        'env_file="$deploy_dir/.env"\n'
        'env_copy="$env_file"\n'
        'chmod 600 -- "$env_copy"'
    )

    assert _deploy_source_violations(safe_source) == []
