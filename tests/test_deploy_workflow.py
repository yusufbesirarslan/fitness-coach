import ast
from functools import lru_cache
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from scripts.deploy_contract import (
    SSM_HEARTBEAT_FUTURE_SKEW_SECONDS,
    SSM_HEARTBEAT_MAX_AGE_SECONDS,
)


DOCKERIGNORE = Path(".dockerignore")
CODEOWNERS = Path(".github/CODEOWNERS")


WORKFLOW = Path(".github/workflows/deploy.yml")
CONTROLLER = Path("scripts/deploy_control.py")
HOST_SCRIPT = Path("scripts/production_deploy.sh")
DEPLOYMENT_GUIDE = Path("docs/DEPLOYMENT.md")
DOCKERFILE = Path("Dockerfile")


def _deploy_yaml():
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow_doc():
    return yaml.load(_deploy_yaml(), Loader=yaml.BaseLoader)


def _controller_source():
    return CONTROLLER.read_text(encoding="utf-8")


def _host_script():
    return HOST_SCRIPT.read_text(encoding="utf-8")


def _dockerfile_source():
    return DOCKERFILE.read_text(encoding="utf-8")


def _dockerfile_instructions(text):
    """Dockerfile metnini sıralı ``(TALIMAT, argüman)`` çiftlerine ayrıştırır.

    Yorum satırları (ilk boşluksuz karakteri '#') ve boş satırlar atlanır;
    ters bölü (\\) ile süren fiziksel satırlar TEK mantıksal talimatta
    birleştirilir. Talimat anahtar kelimesi büyük harfe çevrilir, argüman
    metni olduğu gibi tutulur (yalnızca iç boşluklar teke indirilir).
    """
    kept_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    logical_lines = []
    buffer = ""
    for line in kept_lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
        else:
            buffer += stripped
            logical_lines.append(buffer)
            buffer = ""
    if buffer:
        logical_lines.append(buffer)

    instructions = []
    for logical in logical_lines:
        parts = logical.strip().split(None, 1)
        if not parts:
            continue
        keyword = parts[0].upper()
        argument = " ".join(parts[1].split()) if len(parts) > 1 else ""
        instructions.append((keyword, argument))
    return instructions


def _deployment_guide():
    return DEPLOYMENT_GUIDE.read_text(encoding="utf-8")


def trusted_action_references(paths):
    """Yield mutable third-party action references as ``(path, reference)``."""
    for workflow_path in paths:
        for line in workflow_path.read_text(encoding="utf-8").splitlines():
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if not match:
                continue
            reference = match.group(1)
            if reference.startswith("./"):
                continue
            if not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference):
                yield workflow_path, reference


@pytest.mark.parametrize(
    "workflow_path",
    [Path(".github/workflows/ci.yml"), Path(".github/workflows/deploy.yml")],
)
def test_every_trusted_action_reference_is_immutable(workflow_path):
    assert list(trusted_action_references([workflow_path])) == []


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
    assert '-m "linux_lock or linux_helper_identity"' in run_steps
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


# The SSM command lifecycle belongs to the controller alone. Any other shipped
# file that submits or reads a command is a second lifecycle that never passes
# through the send-boundary freshness proof.
#
# Matched on the operation names themselves rather than on `aws ... ssm ...`:
# the CLI is one of several ways to reach these APIs, and requiring a
# neighbouring `ssm` token let `op=send-command; aws ssm $op` and
# `subprocess.run(["aws", "ssm", "send-command"])` through. In a file that has
# no business running an SSM command, naming the operation at all is the
# finding.
SSM_LIFECYCLE_OPERATIONS = (
    r"\bsend-command\b",
    r"\bsend_command\b",
    r"\bget-command-invocation\b",
    r"\bget_command_invocation\b",
    r"\blist-command-invocations\b",
    r"\blist_command_invocations\b",
    # Reads too: `list-commands` returns Status and StatusDetails, which is a
    # complete fail-open invocation-polling loop on its own.
    r"\blist-commands\b",
    r"\blist_commands\b",
    # Indirect submission paths that never say "send-command".
    r"\bstart-automation-execution\b",
    r"\bcreate-association\b",
)

# Anything CI or the host can execute. `.py` is here because the deploy job
# runs `python3 scripts/*.py` after assuming the production role; `.yaml`
# because a SAM/CloudFormation template is deployable infrastructure that can
# declare SSM automation of its own.
# Suffixes that cannot execute. Everything else shipped in this tree is
# treated as executable -- the inversion is the point. Enumerating what runs
# was defeated twice: once by an extensionless file with no shebang, once by
# `.js`, and this tree also ships 320 `.json`, `nginx.conf`, `.ini` and
# `.toml`, none of which any list named. A new suffix now joins the scanned
# set by default instead of silently escaping it.
PROSE_SUFFIXES = (".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc")

BINARY_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pdf", ".zip", ".gz", ".tar", ".db", ".sqlite", ".pyc", ".mo",
)

# Kinds that must be present, so the inversion cannot quietly stop admitting a
# whole class of file. `.js` and `.json` are here because they were the hole.
REQUIRED_EXECUTABLE_KINDS = (".py", ".yml", ".sh", ".js", ".json")

SSM_MENTION_ALLOWED = frozenset({
    Path("scripts/deploy_control.py"),
    Path("tests/test_deploy_control.py"),
    Path("tests/test_deploy_workflow.py"),
    Path(".github/workflows/deploy.yml"),
    # A config template whose comment describes moving secrets to SSM
    # Parameter Store. Prose in a file that is not prose.
    Path(".env.example"),
    # Operator-facing prose that discusses the one lifecycle. A new markdown
    # file naming `ssm` is a deliberate act, same as a new executable: it is
    # not auto-allowed, which is how a copy-paste runbook is refused.
    Path("docs/DEPLOYMENT.md"),
    Path("docs/STATUS.md"),
    Path("CLAUDE.md"),
    Path("docs/triage-2026-07-02.md"),
    Path("docs/superpowers/plans/2026-08-22-production-deploy-hardening-pr1.md"),
    Path("docs/superpowers/plans/2026-08-25-production-deploy-hardening-pr1-remediation.md"),
    Path("docs/superpowers/specs/2026-08-22-production-deploy-hardening-pr1-design.md"),
    Path("docs/superpowers/specs/2026-08-24-production-deploy-hardening-pr1-remediation-design.md"),
})

SSM_LIFECYCLE_OWNERS = frozenset({
    Path("scripts/deploy_control.py"),      # the one authorised lifecycle
    Path("tests/test_deploy_control.py"),   # its behavioural tests
    Path("tests/test_deploy_workflow.py"),  # this file's own patterns
})

# Historical specs/plans that name the controller's operations as documentation
# of that one lifecycle. They are not owners: a new markdown file with a fenced
# `aws ssm send-command` is a second lifecycle even if it lives under docs/.
SSM_LIFECYCLE_ALLOWED = SSM_LIFECYCLE_OWNERS | frozenset({
    Path("docs/superpowers/plans/2026-08-22-production-deploy-hardening-pr1.md"),
    Path("docs/superpowers/plans/2026-08-25-production-deploy-hardening-pr1-remediation.md"),
    Path("docs/superpowers/specs/2026-08-22-production-deploy-hardening-pr1-design.md"),
    Path("docs/superpowers/specs/2026-08-24-production-deploy-hardening-pr1-remediation-design.md"),
})


SEPARATORS_TO_SPACE = str.maketrans(
    {character: " " for character in ",[]()"} | {c: None for c in "\\\"'"}
)

SSM_LIFECYCLE_RE = re.compile("|".join(SSM_LIFECYCLE_OPERATIONS))


def _normalised_source(text):
    """A view that quoting, escaping and argv-splitting cannot hide a word in.

    Shell and Python both offer many spellings of one token: `"send-command"`,
    `send\\-command`, `'send'-'command'`, and `["aws", "ssm", "send-command"]`
    are the same call. Join line continuations, then drop the characters that
    only ever quote or separate.

    `str.translate` rather than a regex sweep, and no whitespace collapse: the
    patterns are whole words, so runs of spaces are already irrelevant, and
    this runs over every shipped executable in the tree.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", text) if "\\" in text else text
    return joined.translate(SEPARATORS_TO_SPACE).lower()


@lru_cache(maxsize=1)
def _shipped_executable_sources():
    """Every shipped file that CI or the host can execute."""
    listing = subprocess.run(
        [
            "git", "-c", "safe.directory=*", "ls-files", "-z",
            "--cached", "--others", "--exclude-standard",
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    def executable(name):
        suffix = Path(name).suffix.lower()
        return suffix not in BINARY_SUFFIXES and suffix not in PROSE_SUFFIXES

    return tuple(sorted(
        Path(name) for name in listing.split("\0") if name and executable(name)
    ))


@lru_cache(maxsize=1)
def _shipped_operator_sources():
    """Everything an operator can execute, including copy-pasteable prose.

    The executable corpus inverts "what CI can run" and therefore drops
    markdown. A fenced ``aws ssm send-command`` in a runbook is the document
    an operator actually executes, so the SSM mention and lifecycle scans
    read the union.
    """
    return tuple(sorted(
        set(_shipped_executable_sources()) | set(_shipped_markdown())
    ))


# Files that must be scanned. Named individually because a count alone is
# satisfied by issue templates, which can never contain an SSM call.
REQUIRED_EXECUTABLE_SOURCES = (
    Path(".github/workflows/deploy.yml"),
    Path(".github/workflows/ci.yml"),
    Path("scripts/production_deploy.sh"),
    Path("scripts/deploy_control.py"),
    Path("scripts/check_cognito_pool.py"),
    Path("docker-compose.yml"),
    Path("infra/cognito-email-sender/template.yaml"),
)


def test_the_executable_corpus_covers_every_kind_of_thing_ci_can_run():
    corpus = _shipped_executable_sources()

    for required in REQUIRED_EXECUTABLE_SOURCES:
        assert required in corpus, required

    # Every kind that has to be there is there. `.js` and `.json` are named
    # because they were the hole: the corpus used to enumerate what runs, and
    # a `.js` file spelling `ssm` three times and naming two lifecycle
    # operations shipped green.
    for kind in REQUIRED_EXECUTABLE_KINDS:
        assert any(
            path.name.lower().endswith(kind) for path in corpus
        ), kind

    # Nothing prose or binary leaked in through the inversion.
    for path in corpus:
        suffix = path.suffix.lower()
        assert suffix not in PROSE_SUFFIXES, path
        assert suffix not in BINARY_SUFFIXES, path

    # An extensionless file is executable until proven otherwise, and is also
    # read as prose -- belt and braces, because it can be either.
    assert Path(".github/CODEOWNERS") in corpus
    assert Path(".github/CODEOWNERS") in _shipped_markdown()

    assert len(corpus) > 100

    for owner in SSM_LIFECYCLE_OWNERS:
        assert owner in corpus, owner


def test_the_operator_corpus_includes_copy_pasteable_prose():
    """The executable inversion dropped markdown; the SSM scans must not.

    A fenced `aws ssm send-command` in a runbook is what an operator
    executes. `docs/DEPLOYMENT.md` is prose, so it is absent from the
    executable corpus and present in the operator union.
    """
    operator = set(_shipped_operator_sources())
    assert set(_shipped_executable_sources()) <= operator
    assert set(_shipped_markdown()) <= operator
    assert Path("docs/DEPLOYMENT.md") in operator
    assert Path("docs/DEPLOYMENT.md") not in _shipped_executable_sources()
    assert Path("docs/DEPLOYMENT.md") in _shipped_markdown()
    # An allow-list entry for a path that is not in the corpus is dead.
    for path in SSM_MENTION_ALLOWED | SSM_LIFECYCLE_ALLOWED:
        assert path in operator, path


def test_nothing_outside_the_controller_mentions_ssm_at_all():
    """The completeness half of the lifecycle guard.

    The operation scan below is anchored on the operation name, and one string
    interpolation defeats it: `verb=send; noun=command; aws ssm "${verb}-${noun}"`
    contains no operation name to find. That class of guard cannot be completed
    by adding spellings, so this one is anchored on the service instead. Four
    of five hundred and fourteen shipped executables mention `ssm`; a fifth is
    a deliberate act and should read as one.

    This is still a text scan and it is still not a security boundary -- the
    real single point of control is the deploy role's `ssm:SendCommand` grant.
    It is a drift guard, and it is honest about which. Operator markdown is
    in the corpus: a fenced send-command is the document an operator runs.
    """
    offenders = []
    for path in _shipped_operator_sources():
        if path in SSM_MENTION_ALLOWED:
            continue
        text = _normalised_source(path.read_text(encoding="utf-8", errors="replace"))
        if re.search(r"\bssm\b", text):
            offenders.append(path.as_posix())

    assert offenders == []


def test_no_second_ssm_lifecycle_exists_outside_the_controller():
    offenders = []
    for path in _shipped_operator_sources():
        if path in SSM_LIFECYCLE_ALLOWED:
            continue
        normalised = _normalised_source(path.read_text(encoding="utf-8"))
        if SSM_LIFECYCLE_RE.search(normalised):
            offenders += [
                f"{path.as_posix()}: {pattern}"
                for pattern in SSM_LIFECYCLE_OPERATIONS
                if re.search(pattern, normalised)
            ]

    assert offenders == []


def test_workflow_has_no_fail_open_or_mutable_lifecycle_shortcuts():
    body = _deploy_yaml()

    # A local composite action's own `action.yml` is in the corpus above, but
    # say so explicitly: quoting the path is not an exemption.
    assert re.search(r"""uses:\s*['"]?\s*\.""", body) is None

    assert "git reset --hard origin/main" not in body
    assert "2>/dev/null || true" not in body
    assert "Sunucuda komutlar" not in body


def test_the_lifecycle_guard_recognises_the_invocations_it_claims_to():
    # A negative assertion over a corpus that happens to be clean proves nothing
    # about the patterns. Every spelling below walked past an earlier round.
    spellings = (
        "aws ssm send-command --instance-ids i-1",
        "aws --region eu-central-1 ssm send-command --instance-ids i-1",
        "aws --profile deploy --output json --no-cli-pager ssm send-command",
        "/usr/local/bin/aws ssm send-command --instance-ids i-1",
        "aws  ssm   send-command --instance-ids i-1",
        "aws2 ssm send-command --instance-ids i-1",
        "aws \\\n  ssm send-command --instance-ids i-1",
        'aws ssm "send-command" --instance-ids i-1',
        "aws ssm 'send-command' --instance-ids i-1",
        "aws ssm send\\-command --instance-ids i-1",
        "op=send-command; aws ssm $op --instance-ids i-1",
        'subprocess.run(["aws", "ssm", "send-command", "--instance-ids", x])',
        "ssm-cli send-command --instance-ids i-1",
        "aws ssm list-commands --command-id $CID",
        "aws ssm get-command-invocation --command-id x",
        "aws ssm list-command-invocations --command-id x",
        "aws ssm start-automation-execution --document-name d",
        "aws ssm create-association --name n",
        "ssm_client.send_command(InstanceIds=[instance])",
        "client . get_command_invocation( CommandId=cid )",
        'getattr(client, "send_command")(InstanceIds=[instance])',
        "client.list_command_invocations(CommandId=cid)",
        "client.list_commands(CommandId=cid)",
    )
    covered = set()
    for spelling in spellings:
        normalised = _normalised_source(spelling)
        matched = [
            pattern
            for pattern in SSM_LIFECYCLE_OPERATIONS
            if re.search(pattern, normalised)
        ]
        assert matched, spelling
        covered.update(matched)

    # And no pattern is carried along untested.
    assert covered == set(SSM_LIFECYCLE_OPERATIONS)


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


# Bu test METİN İÇİNDE geçme (substring) değil, Dockerfile TALİMAT SIRASINI
# doğrular: garanti sıraya bağlıdır ("appuser'ın /app'e recursive chown'u
# BUILD_REVISION'ı bake etmeden ÖNCE mi oluyor, /app root'a geri mi alınıyor
# appuser'a geçmeden ÖNCE mi") — metnin dosyada bir yerde var olması bunu
# KANITLAMAZ. Ham substring testleri, useradd+chown -R'ın bake adımından
# SONRAYA taşınmasını ya da uzunluk kontrolünün silinmesini yakalayamaz.
def test_dockerfile_bakes_immutable_build_revision():
    dockerfile = _dockerfile_source()
    instructions = _dockerfile_instructions(dockerfile)
    host = _host_script()

    arg_indices = [
        i for i, (kw, arg) in enumerate(instructions)
        if kw == "ARG" and arg == "BUILD_REVISION"
    ]
    recursive_chown_indices = [
        i for i, (kw, arg) in enumerate(instructions)
        if kw == "RUN" and "chown -R appuser:appuser /app" in arg
    ]
    bake_indices = [
        i for i, (kw, arg) in enumerate(instructions)
        if kw == "RUN"
        and 'printf \'%s\\n\' "$BUILD_REVISION" > /app/BUILD_REVISION' in arg
    ]
    root_reclaim_indices = [
        i for i, (kw, arg) in enumerate(instructions)
        if kw == "RUN" and re.search(r"chown root:root /app(?!\S)", arg)
    ]
    user_indices = [i for i, (kw, arg) in enumerate(instructions) if kw == "USER"]

    assert len(arg_indices) == 1
    assert len(recursive_chown_indices) == 1
    assert len(bake_indices) == 1
    assert len(root_reclaim_indices) == 1
    assert len(user_indices) == 1

    recursive_chown_idx = recursive_chown_indices[0]
    arg_idx = arg_indices[0]
    bake_idx = bake_indices[0]
    root_reclaim_idx = root_reclaim_indices[0]
    user_idx = user_indices[0]

    assert (
        recursive_chown_idx
        < arg_idx
        < bake_idx
        < root_reclaim_idx
        < user_idx
    )

    bake_argument = instructions[bake_idx][1]
    assert 'case "$BUILD_REVISION" in' in bake_argument
    assert "*[!0-9a-f]*" in bake_argument
    assert 'test "${#BUILD_REVISION}" -eq 40' in bake_argument
    assert "chown root:root /app/BUILD_REVISION" in bake_argument
    assert "chmod 0444 /app/BUILD_REVISION" in bake_argument

    assert instructions[user_idx][1] == "appuser"

    assert 'git archive --format=tar "$revision"' in host
    assert "BUILD_REVISION: '$revision'" in host
    assert "cat /app/BUILD_REVISION" in host


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
        "`LastPingDateTime` must be no more than 360 seconds old",
        "performs one more SSM managed-instance describe as its last AWS "
        "operation before SendCommand",
        "samples its injected UTC clock immediately after that response",
        "Both boundaries reject a bare timestamp as a typed configuration "
        "error before any AWS call",
        "each boundary re-reads the clock after its own describe response",
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
        "`BUILD_REVISION` set to the exact candidate SHA",
        "the running `web` container's `/app/BUILD_REVISION` equals the expected SHA",
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
    missing = sorted(
        text for text in required_contract_text if text not in normalized_guide
    )
    assert missing == []


# The retired contract. "five minutes old" and the early-clock sentence are
# distinctive enough to match anywhere; a bare "300 seconds" is only wrong when
# it describes the heartbeat, so that one is scoped to heartbeat sentences.
# Note: "the heartbeat decision is fresh at the send boundary" is deliberately
# NOT listed -- it is a true description of the current contract.
RETIRED_HEARTBEAT_CLAIMS = {
    r"(?:five|5)[\s-]+minutes?\s+old":
        "The heartbeat may be five minutes old.",
    # This was a 63-character verbatim sentence, so only a byte-for-byte revert
    # could trip it. The retired contract's *shape* is binding the clock sample
    # to a describe rather than to the send boundary's own response; the
    # sentence the runbook ships says "after that response" and is deliberately
    # not matched, which the control below asserts.
    r"samples?\b[^.]{0,40}?\bclock\b[^.]{0,40}?"
    r"\bafter the (?:SSM |managed-instance )?describe\b":
        "B samples its UTC clock immediately after the SSM describe response.",
}
RETIRED_HEARTBEAT_PATTERNS = tuple(RETIRED_HEARTBEAT_CLAIMS)
FOREIGN_HEARTBEAT_CEILING = r"(?<![\d,])300\s+seconds"


# The freshness contract is pinned whole, not by required substrings. An
# allow-list cannot notice a *false* sentence being added next to the true ones,
# nor a true clause being deleted. Rewording this paragraph is a deliberate
# contract change and must be made here in the same commit.
FRESHNESS_CONTRACT_PARAGRAPH = (
    "B accepts only the configured running EC2 instance. Git candidate "
    "commands are individually bounded to 60 seconds. Its SSM "
    "managed-instance record must be unique and match the configured ID: "
    "`PingStatus` must be `Online` and `LastPingDateTime` must be no more "
    "than 360 seconds old (nor more than one minute in the future). After "
    "the EC2/SSM preflight, B performs one more SSM managed-instance "
    "describe as its last AWS operation before SendCommand and samples "
    "its injected UTC clock immediately after that response. Both "
    "boundaries reject a bare timestamp as a typed configuration error "
    "before any AWS call, and each boundary re-reads the clock after its "
    "own describe response, so controller time spent between preflight "
    "and send counts against heartbeat age. Failure is fail-closed; "
    "correct the instance or SSM registration before retrying."
)

# The gate's own nouns. A contradiction pattern that contains one of these
# cannot be read as a true sentence about anything else in the tree, which is
# what licenses scanning the whole corpus for it.
# The prefilter for both contradiction scans. Wider than the nouns the
# corpus-wide patterns are built from, which is the safe direction: a prefilter
# may admit a sentence no pattern wants, never the reverse.
GATE_NOUNS = (
    "freshness", "heartbeat", "describe", "proof", "recheck", "gate", "check",
    "preflight", "clock", "sample", "decision", "send",
)

# The nouns a pattern must name to earn the corpus-wide tier. `gate` and `check`
# are deliberately NOT here: they are ordinary English, and corpus-wide they
# condemned "The Redis availability check is fail-open" and "the lease handoff
# and its ownership check are atomic" -- true sentences this repository
# documents about other subsystems. Those claims are still caught inside the
# runbook by the loose tier-two patterns, where the subject scope makes them
# safe.
CORPUS_SAFE_NOUNS = (
    "freshness", "heartbeat", "describe", "proof", "recheck", "preflight",
    "clock", "sample", "decision", "send",
)

# Doubles as the corpus-wide scan's prefilter. Substring rather than whole-word,
# to match exactly the claim the tier-one rule below asserts about every
# pattern's source; `test_each_contradicted_claim_pattern_matches_the_claim_it_names`
# proves the superset behaviourally, not by reading the patterns.
GATE_NOUN_RE = re.compile("|".join(GATE_NOUNS), re.IGNORECASE)

# Every claim shape that contradicts the contract, paired with the sentence it
# was written for. The pattern tuples are DERIVED from these mappings, so a
# pattern cannot be added without a positive control -- the assertion that used
# to catch that omission is now impossible to violate.
#
# Tier one: the pattern names the gate itself. Scanned in every shipped
# document, because a README section stating the retired contract as current is
# exactly as wrong as the runbook doing it.
GATE_SPECIFIC_CLAIMS = {
    r"no time (?:can|will|may|could) (?:elapse|pass)\b[^.]{0,50}?"
    r"\b(?:freshness|heartbeat|describe|proof|recheck|gate|check|send)":
        "No time can elapse between the freshness proof and the send.",
    r"no recheck":
        "There is no recheck of the SSM target before sending.",
    r"(?:freshness|heartbeat|describe|proof|recheck|gate|check|preflight|clock"
    r"|sample)\b[^.]{0,40}?\bremains? authoritative":
        "The preflight clock remains authoritative at send time.",
    # Both voices of the same false claim. Active: "SSM re-provisions the
    # heartbeat every N seconds"; passive: "the heartbeat is re-provisioned
    # every N seconds". Only the first was matched.
    r"re-?prov\w+ the heartbeat every":
        "SSM re-provisions the heartbeat every 30 seconds.",
    r"heartbeat is (?:re-?\w+|refreshed|renewed|updated) every":
        "The heartbeat is refreshed every 30 seconds.",
    # Scoped to the gate's own nouns, in both voices: "bounded best-effort
    # cleanup" of the authority token is a true sentence about something else.
    r"best[- ]effort\s+(?:freshness|heartbeat|describe|proof|recheck)":
        "The controller makes a best-effort freshness attempt.",
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,40}?"
    r"\bis best[- ]effort":
        "The final SSM describe before SendCommand is best-effort.",
    # Framing that demotes a fail-closed gate to a hint, in its gate-named
    # voice. `is` and `as` both, because "treat it as informational" is the
    # same instruction as "it is informational".
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,40}?"
    r"\b(?:is|as)\s+(?:only )?(?:an? )?(?:hint|advisory|informational|warning)\b":
        "Operators may treat a failed final describe as informational.",
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,40}?"
    r"\b(?:are|is) atomic\b":
        "The heartbeat proof and SendCommand are atomic.",
    # The single most likely false claim about a fail-closed gate, in its
    # commonest voices -- each tied to the gate, since "the rate limiter stays
    # fail-open" is a true sentence about a different subsystem.
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,40}?"
    r"\bis fail[- ]open":
        "The final SSM describe is fail-open.",
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,40}?"
    r"\b(?:does not block|is non-?blocking)":
        "A failed freshness describe does not block submission.",
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,60}?"
    r"\b(?:logs?|logged|logging)\b[^.]{0,60}?\b(?:continues?|proceeds?|submits?)":
        "If the final SSM describe fails, B logs the condition and "
        "continues to submission.",
    # ... and without the word "log", which the pattern above requires.
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,50}?"
    r"\bcarr(?:y|ies|ied) on\b":
        "If the final SSM describe errors, B carries on.",
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,60}?"
    r"\bsubmits?\b[^.]{0,30}\banyway\b":
        "If the final SSM describe errors, B submits the command anyway.",
    r"(?:freshness|heartbeat|describe|proof|recheck)\b[^.]{0,40}?"
    r"\bis (?:only )?(?:a )?courtesy\b":
        "The final describe before SendCommand is a courtesy.",
    r"\bneed not\b[^.]{0,40}\b(?:stop|block|treat|reject)\b[^.]{0,40}?"
    r"\b(?:freshness|heartbeat|describe|proof|recheck)":
        "Operators need not treat a failed final describe as a stop.",
    # Reusing an earlier sample IS the retired early-clock contract, whatever
    # words surround it.
    r"re-?uses?\b[^.]{0,40}\b(?:preflight|earlier|first|that|the)\s+"
    r"(?:sample|decision|proof|clock)":
        "B samples its UTC clock during preflight and reuses that "
        "decision at SendCommand.",
    r"re-?uses?\b[^.]{0,20}\bpreflight\b":
        "B reuses the preflight UTC sample at SendCommand.",
}

# Tier two: ordinary English, whose safety comes entirely from the runbook's
# tight subject scope. Corpus-wide these condemned an atomically-moved work
# lease, a redis `--no-auth-warning ... ping` healthcheck, a schema described as
# "a hint", and a login limiter that is deliberately fail-open -- all true
# sentences about other things. Each has a tier-one counterpart above in its
# gate-named voice, so nothing loses cover by being demoted here.
RUNBOOK_ONLY_CLAIMS = {
    r"atomic": "The heartbeat proof and SendCommand are atomic.",
    r"advisory": "The heartbeat ceiling is advisory.",
    r"(?:only )?(?:a |as )?(?:hint|warning|informational)\b":
        "Operators may treat a failed final describe as informational.",
    r"fail[- ]open": "The final SSM describe is fail-open.",
    r"does not block":
        "A failed freshness describe does not block submission.",
    r"non-?blocking": "The SendCommand freshness proof is non-blocking.",
    r"(?:logs?|logged|logging)\b.{0,60}?\b(?:continues?|proceeds?|submits?)":
        "If the final SSM describe fails, B logs the condition and "
        "continues to submission.",
    r"carr(?:y|ies|ied) on\b":
        "If the final SSM describe errors, B carries on.",
    r"\bcourtesy\b":
        "The final describe before SendCommand is a courtesy.",
}

GATE_SPECIFIC_CONTRADICTIONS = tuple(GATE_SPECIFIC_CLAIMS)
RUNBOOK_ONLY_CONTRADICTIONS = tuple(RUNBOOK_ONLY_CLAIMS)

# Claim shapes that contradict the contract wherever in the runbook they appear.
CONTRADICTED_OPERATIONAL_CLAIMS = (
    GATE_SPECIFIC_CONTRADICTIONS + RUNBOOK_ONLY_CONTRADICTIONS
)


SUPERSEDED_MARKER = "> Superseded:"

# Every spelling the retired ceiling has actually been written in. The digit and
# comma guards keep "65 minutes" and "1,300 seconds" out.
# Wider than HEARTBEAT_SUBJECTS, and used only at sentence scope: these words
# name the freshness gate without naming its mechanism.
RETIRED_CEILING_SUBJECTS = (
    "freshness", "ceiling", "stale", "max age", "maximum age",
    "SendCommand", "send-command",
)


RETIRED_CEILING_SUBJECT_RE = None  # bound below


def _names_a_retired_ceiling_subject(text):
    return RETIRED_CEILING_SUBJECT_RE.search(text) is not None


RETIRED_CEILING_SPELLINGS = (
    # `[\s-]` because "five-minute ceiling" and "300-second window" state the
    # contract exactly as plainly as the spaced forms.
    r"(?<![\d,])(?:five|5)[\s-]+minutes?",
    r"timedelta\(\s*minutes\s*=\s*5\s*\)",
    r"(?<![\d,])300[\s-]+seconds?",
    r"(?<![\d,])300\s*s\b",
    # Abbreviations state it just as plainly. Bounded to the two numbers that
    # are the retired contract, so "45 min" elsewhere is untouched.
    r"(?<![\d,])5\s*mins?\b",
    r"(?<![\d,])300\s*secs?\b",
    r"(?<![\d,])5\s*m\b",
)

# A number is only the retired contract when it is asserted AS a bound on
# heartbeat age. These words say it is; the cadence words say it is about how
# often the agent pings, which is a different true fact.
CEILING_WORDS = (
    r"\bceiling\b", r"\bthreshold\b", r"\bwindow\b", r"\bmax(?:imum)?\b",
    r"\blimit\b", r"\bno more than\b", r"\bup to\b", r"\bolder than\b",
    r"\bwithin\b", r"\bold\b", r"\bstale\b",
)
CADENCE_WORDS = (
    r"\bcadence\b", r"\binterval\b", r"\bfrequency\b", r"\bevery\b",
    r"\bHealthFrequency\w*\b",
)
CEILING_WORD_RE = re.compile("|".join(CEILING_WORDS), re.IGNORECASE)
CADENCE_WORD_RE = re.compile("|".join(CADENCE_WORDS), re.IGNORECASE)

# How much either side of a match counts as its own claim. Deliberately not the
# containing section: the design spec states the agent's five-minute *cadence*
# two sentences above the 360-second *ceiling* it justifies, and a section-scope
# test can only see that both words are somewhere in the same section.
CLAIM_WINDOW = 60

MARKDOWN_EMPHASIS = str.maketrans({character: None for character in "*_`~"})
ZERO_WIDTH = str.maketrans({
    "\u200b": None, "\u200c": None, "\u200d": None, "\ufeff": None,
})


def _plain(text):
    """Markdown emphasis removed before any pattern reads the text.

    `**300** seconds` and `*five* minutes` state the retired ceiling exactly as
    plainly as the unadorned forms, and every spelling missed them. Zero-width
    characters are the same act: `f\\u200bive minutes` is still "five minutes".
    """
    return text.translate(MARKDOWN_EMPHASIS).translate(ZERO_WIDTH)

# A cheap literal superset of every spelling above, used only to skip documents
# wholesale. It must never be the patterns themselves: those carry lookbehinds
# that can fail on a joined document while matching inside one of its sections.
# Still a strict superset: every spelling above contains "min" or "300" as a
# literal, which `test_the_retired_ceiling_prefilter_cannot_narrow_the_scan`
# asserts structurally. "5 m" is the exception and carries its own token.
RETIRED_CEILING_TOKENS = ("min", "300", "5 m", "5m")

# Documents stating the ceiling as current, in the spellings that have actually
# been tried against this scan. Emphasis included: it is how the guard was
# defeated, not a hypothetical.
RETIRED_CEILING_EXAMPLES = (
    "The heartbeat may be up to five minutes old.",
    "The heartbeat may be up to f\u200bive minutes old.",
    "The heartbeat ceiling is 5 minutes.",
    "The heartbeat ceiling is *five* minutes.",
    "A five-minute heartbeat ceiling applies.",
    "The boundary compares timedelta(minutes=5).",
    "The freshness ceiling is 300 seconds.",
    "The freshness ceiling is **300** seconds.",
    "A 300-second freshness window applies.",
    "The freshness ceiling is 300s.",
    "The heartbeat ceiling is 5 min.",
    "A stale LastPingDateTime is one older than 300 sec.",
    "The heartbeat ceiling is 5m.",
)


def _ceiling_claims(text):
    """Retired-ceiling spellings in `text` that are asserted as a ceiling.

    A ceiling word in the match's neighbourhood wins outright, so "the ceiling
    is five minutes (one agent cadence)" is still caught; a cadence word with no
    ceiling word means the sentence is about how often, not how old; and a bare
    number with neither -- `timedelta(minutes=5)` -- is the retired contract
    written as code and stays caught.
    """
    claimed = []
    for pattern in RETIRED_CEILING_SPELLINGS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            window = text[
                max(0, match.start() - CLAIM_WINDOW):match.end() + CLAIM_WINDOW
            ]
            if CEILING_WORD_RE.search(window) or not CADENCE_WORD_RE.search(window):
                claimed.append(pattern)
                break
    return claimed


# True statements about the agent's ping cadence, which is not the controller's
# ceiling. The first is the sentence that actually broke this scan.
CADENCE_STATEMENTS = (
    "AWS documents a five-minute managed-node health signal cadence.",
    "The SSM Agent's default HealthFrequencyMinutes is five minutes.",
    "One normal five-minute interval plus 60 seconds of jitter.",
    "The health-signal frequency is 300 seconds.",
    "The agent re-registers every 5 minutes.",
)


def test_a_cadence_is_not_a_ceiling_but_a_ceiling_still_is():
    # Both halves, because either one alone is satisfied by a helper that
    # always answers the same way.
    for example in RETIRED_CEILING_EXAMPLES:
        assert _ceiling_claims(_plain(example)), example

    for example in CADENCE_STATEMENTS:
        assert _ceiling_claims(_plain(example)) == [], example

    # Naming the cadence in the same breath does not launder a ceiling claim.
    assert _ceiling_claims(
        "The heartbeat ceiling is five minutes, matching the agent cadence."
    )


def test_the_retired_ceiling_prefilter_cannot_narrow_the_scan():
    # Structural, not merely example-based: each pattern's own source must
    # contain one of the prefilter tokens as a literal. That is not a proof --
    # a token could appear only inside a lookbehind -- so the examples below
    # remain, and any new spelling has to satisfy both.
    for pattern in RETIRED_CEILING_SPELLINGS:
        assert any(
            token.replace(" ", "") in pattern.lower().replace("\\s*", "")
            for token in RETIRED_CEILING_TOKENS
        ), pattern

    for example in RETIRED_CEILING_EXAMPLES:
        plain = _plain(example)
        matched = [
            pattern for pattern in RETIRED_CEILING_SPELLINGS
            if re.search(pattern, plain, flags=re.IGNORECASE)
        ]
        assert matched, example
        assert any(
            token in plain.lower() for token in RETIRED_CEILING_TOKENS
        ), example

    # And every spelling is exercised by at least one example.
    covered = {
        pattern
        for pattern in RETIRED_CEILING_SPELLINGS
        for example in RETIRED_CEILING_EXAMPLES
        if re.search(pattern, _plain(example), flags=re.IGNORECASE)
    }
    assert covered == set(RETIRED_CEILING_SPELLINGS)
HEARTBEAT_SUBJECTS = (
    "LastPingDateTime", "PingStatus", "heartbeat", "ping", "SSM target",
)


def _word_alternation(subjects):
    """One compiled whole-word alternation, case-insensitive.

    Case-insensitively because "Heartbeat" starts sentences, and the retired
    ceilings were already matched that way -- capitalising one letter used to
    defeat every document guard at once. By whole words because a bare "ping"
    substring also fires on "mapping" and "shipping".
    """
    return re.compile(
        r"\b(?:%s)\b" % "|".join(re.escape(subject) for subject in subjects),
        re.IGNORECASE,
    )


HEARTBEAT_SUBJECT_RE = None  # bound below, once HEARTBEAT_SUBJECTS exists


def _names_a_heartbeat(text):
    return HEARTBEAT_SUBJECT_RE.search(text) is not None


def test_the_heartbeat_subject_matcher_reads_words_not_substrings():
    # Both halves matter: the guard has to fire on the subject however it is
    # capitalised, and stay silent on words that merely contain it.
    for naming in (
        "Heartbeat age is bounded.",
        "the LASTPINGDATETIME field",
        "PingStatus must be Online",
        "a stale ping is rejected",
        "the sole SSM target",
    ):
        assert _names_a_heartbeat(naming), naming

    for silent in (
        "the mapping between roles",
        "shipping the artefact",
        "unpinged is not a word but pinged is not a subject either",
        "no subject at all",
    ):
        assert not _names_a_heartbeat(silent), silent


# A banner only excuses a document if it is a banner about *this* contract:
# either it names what the retired wording was about, or it names the ceiling
# that replaced it.
SUPERSEDED_SUBJECTS = HEARTBEAT_SUBJECTS + (
    "ceiling", "freshness", str(SSM_HEARTBEAT_MAX_AGE_SECONDS),
)
SUPERSEDED_SUBJECT_RE = _word_alternation(SUPERSEDED_SUBJECTS)


def _superseded(text):
    """True when the document opens with a banner retiring *this* contract.

    An opening banner about something else used to exempt the whole file, which
    short-circuited the per-paragraph scan entirely.
    """
    blocks = text.split("\n\n")
    if len(blocks) < 2:
        return False
    banner = " ".join(blocks[1].split())
    if SUPERSEDED_MARKER not in banner:
        return False
    # Whole words. As a substring test this fired on "mapping", "shipping" and
    # "stopping" via `ping`, so a banner about "the mapping of legacy deploy
    # routes" bought an entire file a pass from every ceiling scan. Every other
    # subject test in this module already went through `_word_alternation`;
    # this was the one that did not.
    return SUPERSEDED_SUBJECT_RE.search(banner) is not None


# Prose is not only markdown. A retired ceiling stated in reStructuredText is
# read by exactly the same operator.
MARKDOWN_SUFFIXES = PROSE_SUFFIXES

# Documents that must be in the scanned corpus. A negative assertion over an
# empty corpus passes, so any drift in the listing has to fail loudly instead.
REQUIRED_SCANNED_DOCUMENTS = (
    Path("docs/DEPLOYMENT.md"),
    Path("README.md"),
)


@lru_cache(maxsize=1)
def _shipped_markdown():
    """Every markdown file that actually ships, not one directory of them.

    The scan used to walk `docs/` alone, so a runbook at the repo root could
    state the retired ceiling as current and pass. Operator-facing prose already
    lives in README.md, SECURITY.md, deploy/ and infra/ as well.

    Tracked files plus untracked ones that are not ignored: the first is what
    ships, the second is what an author is about to add, so a new offender is
    caught before it is even staged. Filtering happens here rather than in a
    pathspec because git's pathspec is case-sensitive and `RUNBOOK.MD` is a
    document like any other.
    """
    listing = subprocess.run(
        [
            "git", "-c", "safe.directory=*", "ls-files", "-z",
            "--cached", "--others", "--exclude-standard",
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    def prose(name):
        # Extensionless files too: `docs/DEPLOY_RUNBOOK` stating the retired
        # contract as current was read by exactly the same operator as the
        # runbook, and was in neither corpus.
        return (
            name.lower().endswith(MARKDOWN_SUFFIXES)
            or not Path(name).suffix
        )

    return tuple(sorted(
        Path(name) for name in listing.split("\0") if name and prose(name)
    ))


def _sections(text):
    """Markdown sections: a heading and the paragraphs beneath it.

    Paragraph scope alone was evaded by naming the subject in a heading or an
    introductory sentence and stating the retired ceiling in the next paragraph.
    """
    sections = []
    current = []
    for line in text.split("\n"):
        if line.startswith("#") and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    sections.append("\n".join(current))
    return sections


def test_the_scanned_document_corpus_is_not_empty():
    corpus = _shipped_markdown()

    assert len(corpus) > 20
    for required in REQUIRED_SCANNED_DOCUMENTS:
        assert required in corpus


def test_no_document_states_a_retired_heartbeat_ceiling_as_current():
    # docs/DEPLOYMENT.md is the runbook, but it is not the only place a reader
    # greps -- nor is docs/ the only place prose ships. Historical records may
    # keep their original wording behind an opening superseded banner about this
    # contract; without one the retired ceiling reads as the contract in force.
    #
    # Scoped per paragraph, not per file: a banner buried at the bottom of a
    # document must not license the retired ceiling at the top, and an unrelated
    # "5 minutes" elsewhere in the runbook must not be flagged.
    offenders = []
    for path in _shipped_markdown():
        raw = path.read_text(encoding="utf-8")
        if _superseded(raw):
            continue
        text = _plain(raw)
        # Almost no document mentions a retired ceiling at all, and asking the
        # subject question of every section and sentence in the tree costs
        # eighteen seconds to learn the same thing.
        lowered = text.lower()
        if not any(token in lowered for token in RETIRED_CEILING_TOKENS):
            continue

        for section in _sections(text):
            # Wide scope, narrow subject: "## SSM target heartbeat" followed two
            # paragraphs later by "It is 300 seconds" is one claim, however it
            # is laid out. Only the unmistakable subjects, because a whole
            # section is a lot of unrelated prose to hold responsible.
            body = " ".join(section.split())
            if _ceiling_claims(body) and _names_a_heartbeat(section):
                offenders.append(f"{path.as_posix()}: {body[:60]}")

        # Narrow scope, wide subject: "The freshness ceiling is 300 seconds"
        # names the gate without using any of the words above. Sentence scope
        # is what makes the wider subject list safe -- an unrelated true
        # sentence about a 300-second controller budget stays untouched.
        for sentence in _sentences(text):
            if (
                _ceiling_claims(sentence)
                and _names_a_retired_ceiling_subject(sentence)
            ):
                offenders.append(f"{path.as_posix()}: {sentence[:60]}")

    assert offenders == []


def test_the_runbook_freshness_contract_paragraph_is_pinned_verbatim():
    paragraphs = [
        " ".join(block.split())
        for block in _deployment_guide().split("\n\n")
    ]
    contract = [block for block in paragraphs if "LastPingDateTime" in block]

    assert contract == [FRESHNESS_CONTRACT_PARAGRAPH]


def test_the_runbook_speaks_about_heartbeat_freshness_in_exactly_one_place():
    # Pinning one paragraph verbatim cannot notice a *second* paragraph being
    # added elsewhere that states the freshness contract differently. Pinning
    # the whole section verbatim would freeze the timeout table and the
    # invocation-polling prose, which this task does not own. So: freshness is
    # allowed to be discussed once, in the pinned paragraph, and nowhere else.
    speaking = [
        " ".join(block.split())
        for block in _deployment_guide().split("\n\n")
        if _names_a_heartbeat(block)
    ]

    assert speaking == [FRESHNESS_CONTRACT_PARAGRAPH]


def test_the_pinned_paragraph_states_the_canonical_threshold_itself():
    # And the pin has to track the constant: bumping the canonical ceiling
    # without rewriting the runbook fails here rather than shipping a document
    # that quietly disagrees with the boundary.
    assert (
        f"{SSM_HEARTBEAT_MAX_AGE_SECONDS} seconds old"
        in FRESHNESS_CONTRACT_PARAGRAPH
    )
    # Derived, not hardcoded, so the prose and the constant cannot drift apart
    # in the direction the ceiling above is already protected against.
    assert SSM_HEARTBEAT_FUTURE_SKEW_SECONDS % 60 == 0
    minutes = SSM_HEARTBEAT_FUTURE_SKEW_SECONDS // 60
    spelled = "one minute" if minutes == 1 else f"{minutes} minutes"
    assert f"{spelled} in the future" in FRESHNESS_CONTRACT_PARAGRAPH


FRESHNESS_CLAIM_SUBJECTS = HEARTBEAT_SUBJECTS + (
    "freshness", "preflight", "clock", "SendCommand", "send-command",
    # "a failed final describe is informational" names the gate perfectly well
    # without using any of the words above it.
    "describe", "SSM", "authority", "boundary", "fail-closed",
)


FRESHNESS_CLAIM_SUBJECT_RE = None  # bound below


def _names_the_freshness_gate(text):
    return FRESHNESS_CLAIM_SUBJECT_RE.search(text) is not None


HEARTBEAT_SUBJECT_RE = _word_alternation(HEARTBEAT_SUBJECTS)
RETIRED_CEILING_SUBJECT_RE = _word_alternation(
    HEARTBEAT_SUBJECTS + RETIRED_CEILING_SUBJECTS
)
FRESHNESS_CLAIM_SUBJECT_RE = _word_alternation(FRESHNESS_CLAIM_SUBJECTS)


def _sentences(text):
    return re.split(r"(?<=[.:])\s+", " ".join(text.split()))


def test_the_runbook_states_no_claim_the_controller_contradicts():
    # Sentence-scoped, and only sentences about the freshness gate: "the
    # directory swap is atomic" and "these notes are advisory" are true
    # statements about other things, and a guard that rejects them teaches
    # authors to route around it.
    offenders = []
    for sentence in _sentences(_plain(_deployment_guide())):
        # `GATE_NOUN_RE`, not `_names_the_freshness_gate`. The subject list is
        # narrower than the nouns these patterns are built from -- it has no
        # `recheck`, `proof`, `gate` or `check` -- so "There is no recheck
        # before submission. The proof is best-effort. The gate does not block."
        # was discarded before a single pattern ran, in the one document these
        # guards exist for. `_plain` for the same reason it is everywhere else:
        # `fail-*open*` is the claim, emphasis and all.
        if not GATE_NOUN_RE.search(sentence):
            continue
        offenders += [
            f"{pattern}: {sentence[:60]}"
            for pattern in CONTRADICTED_OPERATIONAL_CLAIMS
            if re.search(pattern, sentence, flags=re.IGNORECASE)
        ]

    assert offenders == []


def test_no_shipped_document_contradicts_the_controller():
    # The runbook was the only document scanned for these claims, so a README
    # section could state the entire retired contract as current and pass.
    # A superseded banner does not exempt a document here: it licenses the old
    # *ceiling* being written down as history, not a claim that the gate
    # fails open.
    offenders = []
    for path in _shipped_markdown():
        if path == DEPLOYMENT_GUIDE:
            continue  # covered above, sentence for sentence
        for sentence in _sentences(_plain(path.read_text(encoding="utf-8"))):
            # Not the runbook scan's subject list, which is narrower than the
            # nouns these patterns use -- "the recheck is advisory" would have
            # been filtered out before it was read. The gate's own nouns
            # instead, which every tier-one pattern is proven to require.
            if not GATE_NOUN_RE.search(sentence):
                continue
            offenders += [
                f"{path.as_posix()}: {pattern}: {sentence[:60]}"
                for pattern in GATE_SPECIFIC_CONTRADICTIONS
                if re.search(pattern, sentence, flags=re.IGNORECASE)
            ]

    assert offenders == []


def test_no_shipped_document_states_the_retired_heartbeat_contract():
    # docs/DEPLOYMENT.md was the only document these ran over, so a README
    # could state the retired contract as current and pass. A superseded banner
    # about this contract still licenses the historical wording.
    offenders = []
    for path in _shipped_markdown():
        raw = path.read_text(encoding="utf-8")
        if _superseded(raw):
            continue
        for sentence in _sentences(_plain(raw)):
            offenders += [
                f"{path.as_posix()}: {pattern}: {sentence[:60]}"
                for pattern in RETIRED_HEARTBEAT_PATTERNS
                if re.search(pattern, sentence, flags=re.IGNORECASE)
            ]

    assert offenders == []


def test_each_retired_heartbeat_pattern_matches_the_claim_it_names():
    for pattern, sentence in RETIRED_HEARTBEAT_CLAIMS.items():
        assert re.search(pattern, sentence, flags=re.IGNORECASE), pattern

    # ... and the sentence the runbook actually ships is not one of them. A
    # guard that condemns the true contract is a guard authors delete.
    for current in (
        "B samples its injected UTC clock immediately after that response.",
        "`LastPingDateTime` must be no more than 360 seconds old.",
    ):
        assert [
            pattern for pattern in RETIRED_HEARTBEAT_PATTERNS
            if re.search(pattern, current, flags=re.IGNORECASE)
        ] == [], current


def test_each_contradicted_claim_pattern_matches_the_claim_it_names():
    # A negative assertion proves nothing about its patterns. Every one of them
    # has to fire on the sentence it was written for, in context.
    for pattern, sentence in {
        **GATE_SPECIFIC_CLAIMS, **RUNBOOK_ONLY_CLAIMS
    }.items():
        assert _names_the_freshness_gate(sentence), sentence
        assert re.search(pattern, sentence, flags=re.IGNORECASE), pattern

    # The tiers are disjoint, so a pattern cannot be listed in both and then
    # quietly deleted from the corpus-wide one.
    assert not set(GATE_SPECIFIC_CLAIMS) & set(RUNBOOK_ONLY_CLAIMS)
    assert set(CONTRADICTED_OPERATIONAL_CLAIMS) == (
        set(GATE_SPECIFIC_CLAIMS) | set(RUNBOOK_ONLY_CLAIMS)
    )

    # And tier one earns its reach structurally rather than by having been
    # spot-checked once: each of its patterns names one of the gate's nouns,
    # and none of tier two's does. This is the whole reason `atomic` and
    # `advisory` are scanned in the runbook and nowhere else.
    for pattern in GATE_SPECIFIC_CONTRADICTIONS:
        assert any(noun in pattern.lower() for noun in CORPUS_SAFE_NOUNS), pattern
    for pattern in RUNBOOK_ONLY_CONTRADICTIONS:
        assert not any(noun in pattern.lower() for noun in GATE_NOUNS), pattern

    # Reading the source only shows a noun is present, not that matching needs
    # it -- a noun sitting inside an optional group would satisfy the loop
    # above and still let a sentence through the prefilter unread. So strip the
    # nouns out of each control sentence and require the pattern to fall
    # silent. That is the property the corpus-wide prefilter depends on.
    for pattern, sentence in GATE_SPECIFIC_CLAIMS.items():
        stripped = GATE_NOUN_RE.sub(" ", sentence)
        assert not re.search(pattern, stripped, flags=re.IGNORECASE), pattern

    # The prefilter guards the runbook scan too, and that scan runs BOTH tiers.
    # So the superset claim has to hold for every pattern, not only tier one:
    # whenever a pattern matches a control sentence, the prefilter must admit
    # that sentence. This is the assertion whose absence let four true claims
    # about the gate sit in the runbook unread.
    for pattern, sentence in {
        **GATE_SPECIFIC_CLAIMS, **RUNBOOK_ONLY_CLAIMS
    }.items():
        if re.search(pattern, sentence, flags=re.IGNORECASE):
            assert GATE_NOUN_RE.search(sentence), pattern


def _heartbeat_sentences(guide):
    return [
        sentence
        for sentence in re.split(r"(?<=\.)\s+", guide)
        if "LastPingDateTime" in sentence or "heartbeat" in sentence.lower()
    ]


def test_deployment_runbook_heartbeat_contract_matches_the_send_boundary():
    # Task 1: the runbook is not free prose. Its stated heartbeat ceiling is the
    # canonical constant the controller enforces, and the final freshness proof
    # is the SendCommand authority boundary -- not an earlier preflight clock.
    guide = " ".join(_deployment_guide().split())

    stated_ages = [
        int(value)
        for value in re.findall(
            r"`LastPingDateTime` must be no more than (\d+) seconds old", guide
        )
    ]
    assert stated_ages == [SSM_HEARTBEAT_MAX_AGE_SECONDS]

    describe_index = guide.index(
        "performs one more SSM managed-instance describe as its last AWS "
        "operation before SendCommand"
    )
    clock_index = guide.index(
        "samples its injected UTC clock immediately after that response"
    )
    assert describe_index < clock_index

    assert [
        pattern
        for pattern in RETIRED_HEARTBEAT_PATTERNS
        if re.search(pattern, guide, flags=re.IGNORECASE)
    ] == []

    heartbeat_sentences = _heartbeat_sentences(guide)
    assert heartbeat_sentences
    assert [
        sentence
        for sentence in heartbeat_sentences
        if re.search(FOREIGN_HEARTBEAT_CEILING, sentence)
    ] == []


def _controller_module():
    return ast.parse(_controller_source())


def _function_def(module, name):
    # Both flavours: making a boundary `async` is a refactor, not an escape
    # from every structural guard that looks it up.
    for node in ast.walk(module):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{name} is not defined in the controller")


def _rejection_tests(function, message_fragment):
    """The `if` conditions that reject with a given message.

    Reading the comparison by its consequence, rather than by the operators or
    the constructors it happens to spell, keeps the guard aimed at the contract
    while leaving the arithmetic free to be written any equivalent way.
    """
    return [
        node.test
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(raised, ast.Raise)
            and message_fragment in ast.unparse(raised).lower()
            for raised in ast.walk(ast.Module(body=node.body, type_ignores=[]))
        )
    ]


def _resolves_to(module, node, constant):
    """True if `constant` is compared, directly or through a module binding.

    `age > CEILING`, where `CEILING = timedelta(seconds=<constant>)` sits at
    module level, is the same contract as comparing the constant inline. A
    guard that cannot see that punishes a refactor it has no opinion about.
    """
    names = {
        found.id for found in ast.walk(node) if isinstance(found, ast.Name)
    }
    if constant in names:
        return True
    for statement in module.body:
        targets = (
            [statement.target] if isinstance(statement, ast.AnnAssign)
            else getattr(statement, "targets", [])
        )
        bound = {
            target.id for target in targets if isinstance(target, ast.Name)
        }
        if bound & names and statement.value is not None:
            if constant in ast.unparse(statement.value):
                return True
    return False


def _statement_expression(statement):
    """The single expression a statement evaluates, or None if it does more.

    A bare call, a call whose value is bound to a name, or a returned call --
    all three are the same amount of work. Binding a return value is a refactor
    and the guard has no opinion about it.
    """
    if isinstance(statement, ast.Expr):
        return statement.value
    if isinstance(statement, ast.Return):
        return statement.value
    if isinstance(statement, ast.AnnAssign):
        return statement.value if isinstance(statement.target, ast.Name) else None
    if isinstance(statement, ast.Assign) and all(
        isinstance(target, ast.Name) for target in statement.targets
    ):
        return statement.value
    return None


def _innermost_call(expression):
    """The deepest call in a chain of single-call wrappers, or None."""
    if not isinstance(expression, ast.Call):
        return None
    while True:
        nested = [
            argument for argument in
            list(expression.args) + [k.value for k in expression.keywords]
            if isinstance(argument, ast.Call)
        ]
        if len(nested) != 1:
            return expression
        expression = nested[0]


def _evaluates_only(statement, call):
    """True when `statement` evaluates `call` over already-computed arguments.

    The argument check is the load-bearing half: an expression nested in the
    argument list runs between the freshness proof and the wire just as surely
    as a statement between them would.
    """
    expression = _statement_expression(statement)
    if expression is None:
        return False
    # Every call in the statement must be on the single wrapper chain that ends
    # at `call`; anything else is a second expression being evaluated here.
    chain, cursor = [], expression
    while isinstance(cursor, ast.Call):
        chain.append(cursor)
        if cursor is call:
            break
        nested = [
            argument for argument in
            list(cursor.args) + [k.value for k in cursor.keywords]
            if isinstance(argument, ast.Call)
        ]
        if len(nested) != 1:
            return False
        cursor = nested[0]
    if not chain or chain[-1] is not call:
        return False
    if [node for node in ast.walk(statement)
            if isinstance(node, ast.Call)] != chain:
        return False
    # The innermost call's own arguments must already be computed. This is the
    # load-bearing half: an expression here runs before the wire.
    arguments = list(call.args) + [keyword.value for keyword in call.keywords]
    return all(
        isinstance(argument, (ast.Name, ast.Constant, ast.Attribute))
        for argument in arguments
    )


def test_the_statement_shape_guard_reads_arguments_as_well_as_statements():
    # Positive control for both halves, since the assertions above are
    # negative and would hold vacuously against a helper that never says no.
    module = ast.parse(
        "def f():\n"
        "    proven = prove()\n"
        "    x = send(args)\n"
        "    y = send(settle(args))\n"
        "    settle(prove())\n"
        "    z = [send(args)]\n"
    )
    body = module.body[0].body
    bind_proof, plain_send, nested_send, wrapped_proof, listed_send = body

    assert _evaluates_only(bind_proof, bind_proof.value)
    assert _evaluates_only(plain_send, plain_send.value)
    assert not _evaluates_only(nested_send, nested_send.value)
    assert _statement_expression(listed_send) is not None
    assert not _evaluates_only(listed_send, listed_send.value)

    # A wrapper around the runner is allowed -- it runs after the wire -- and
    # the innermost call is found through it. A call in the innermost call's
    # OWN argument list is not, wherever the chain starts.
    wrapped = ast.parse("x = wrap(send(args))\n").body[0]
    assert _innermost_call(wrapped.value) is wrapped.value.args[0]
    assert _evaluates_only(wrapped, wrapped.value.args[0])

    # Laundering is caught by WHICH call ends up innermost, not by the chain
    # test: `settle` runs before the wire, so it -- not the runner -- is the
    # deepest call, and the guard's runner-identity assertion rejects it.
    laundered = ast.parse("x = wrap(send(settle(args)))\n").body[0]
    assert _innermost_call(laundered.value).func.id == "settle"
    direct = ast.parse("x = send(settle(args))\n").body[0]
    assert _innermost_call(direct.value).func.id == "settle"

    # Two calls side by side are two expressions, not a wrapper chain.
    forked = ast.parse("x = wrap(send(args), other(args))\n").body[0]
    assert _innermost_call(forked.value) is forked.value
    assert not _evaluates_only(forked, forked.value)


def _calls_to(node, name):
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == name
    ]


def test_controller_proves_heartbeat_freshness_at_the_send_authority_boundary():
    # Structural, not textual: whatever statement form submits the command, the
    # statement immediately before it must be the freshness proof, so no work --
    # bounded or not -- can be introduced between the proof and the authority it
    # protects. Statement *form* is deliberately not constrained; only order is.
    send_command = _function_def(_controller_module(), "send_command")

    # Keyed by annotation, like the clock below: renaming the injected runner is
    # a refactor, and the guard has no opinion about what it is called.
    runners = [
        argument.arg
        for argument in send_command.args.args + send_command.args.kwonlyargs
        if argument.annotation is not None
        and ast.unparse(argument.annotation) == "AwsJsonRunner"
    ]
    assert len(runners) == 1

    submit_indexes = [
        index
        for index, statement in enumerate(send_command.body)
        if _calls_to(statement, runners[0])
    ]
    assert len(submit_indexes) == 1
    assert submit_indexes[0] > 0

    proof = send_command.body[submit_indexes[0] - 1]
    proof_calls = _calls_to(proof, "require_fresh_ssm_target")
    assert len(proof_calls) == 1

    # Ordering alone is not enough, and neither is pinning one of the two
    # statements. `_settle(require_fresh_ssm_target(...))` and
    # `aws(_settle(send_args))` are each "the right statement in the right
    # order" with an arbitrary wait inside them -- 45 seconds turns a heartbeat
    # proven at 359s into a send at 404s. Both statements must evaluate exactly
    # one call, over arguments that were computed earlier.
    assert _evaluates_only(proof, proof_calls[0])

    # The runner call is the INNERMOST call of the send statement. Requiring it
    # to be the outermost rejected `return require_command_id(aws(send_args))`,
    # which is strictly less work than the two statements it replaces: a
    # wrapper's arguments are evaluated first, so it runs after the wire and
    # cannot spend heartbeat age. What must not appear is a call *inside* the
    # runner's argument list, which is where `aws(_settle(send_args))` puts it.
    send = send_command.body[submit_indexes[0]]
    submission = _innermost_call(_statement_expression(send))
    assert isinstance(submission, ast.Call)
    assert isinstance(submission.func, ast.Name)
    assert submission.func.id == runners[0]
    assert _evaluates_only(send, submission)

    # The proof must be handed the boundary's own injected clock, not some other
    # value -- and the clock is identified by its annotation, so renaming the
    # parameter stays a refactor rather than a contract change.
    clocks = [
        argument.arg
        for argument in send_command.args.args + send_command.args.kwonlyargs
        if argument.annotation is not None
        and ast.unparse(argument.annotation) == "UtcClock"
    ]
    assert len(clocks) == 1

    argument_names = {
        argument.id
        for argument in ast.walk(proof_calls[0])
        if isinstance(argument, ast.Name)
    }
    assert clocks[0] in argument_names


def test_the_freshness_boundary_does_nothing_after_it_proves_freshness():
    # The ordering guard above reads `send_command.body`, so the tail of the
    # boundary function was invisible even though it sits strictly between the
    # proof and the authority it protects: splitting the return into
    # `instance = validate(...)` / `time.sleep(0)` / `return instance` survived
    # the whole suite. The proof must therefore be the boundary's last act.
    boundary = _function_def(_controller_module(), "require_fresh_ssm_target")

    proving = [
        index
        for index, statement in enumerate(boundary.body)
        if _calls_to(statement, "validate_managed_instance")
    ]
    assert len(proving) == 1
    assert proving[0] == len(boundary.body) - 1

    # "Last statement" is not "last act": `return validate(...) if _settle()
    # else None` satisfies the index check while `_settle()` busy-waits for as
    # long as it likes. The proof must be the returned expression itself.
    final = boundary.body[-1]
    assert isinstance(final, ast.Return)
    assert isinstance(final.value, ast.Call)
    assert isinstance(final.value.func, ast.Name)
    assert final.value.func.id == "validate_managed_instance"


# Everything the send path is allowed to call, by resolved callee name. This
# is a WHITELIST because the blocklist it replaced could not work: it matched
# the spelling of the callee, and the spelling is the attacker's to choose.
# `_settle = time.sleep`, called from inside the freshness proof, slept 45
# seconds between the heartbeat verdict and SendCommand with every test green.
#
# The cost is that adding a call here is a deliberate edit. That is the point,
# and it is also the fix for the asymmetry the blocklist had: it rejected a
# pure helper named `_select_call_timeout` for its name while accepting a real
# sleep named `_settle`. Now both require a line, and only one of them is a lie.
APPROVED_SEND_PATH_CALLS = frozenset({
    # (calling function, resolved callee). Qualified by the caller because an
    # unqualified list admits a generic approved name ANYWHERE on the path:
    # `run` is legitimate in `run_aws_json` and is the subprocess launch, and
    # `run = getattr(time, "sleep")` called from the freshness proof was
    # therefore whitelisted. The same was true of `str`, `get`, `len`,
    # `search`, `encode`, `decode`, `loads`, `dumps`, `quote` and `group`.
    ("_authority_parameter", "ConfigError"),
    ("_authority_parameter", "UUID"),
    ("_authority_parameter", "str"),
    ("_parse_heartbeat", "PreflightError"),
    ("_parse_heartbeat", "fromisoformat"),
    ("_parse_heartbeat", "isinstance"),
    ("_parse_heartbeat", "utcoffset"),
    ("_require_live_clock", "ConfigError"),
    ("_require_live_clock", "callable"),
    ("_sample_utc", "PreflightError"),
    ("_sample_utc", "_require_live_clock"),
    ("_sample_utc", "_require_live_clock()"),
    ("_sample_utc", "isinstance"),
    ("build_remote_command", "_authority_parameter"),
    ("build_remote_command", "b64encode"),
    ("build_remote_command", "decode"),
    ("build_remote_command", "encode"),
    ("build_remote_command", "render_bootstrap"),
    ("build_remote_command", "str"),
    ("build_remote_command", "uuid4"),
    ("render_bootstrap", "b64encode"),
    ("render_bootstrap", "decode"),
    ("render_bootstrap", "encode"),
    ("render_bootstrap", "quote"),
    ("require_command_id", "InvocationProtocolError"),
    ("require_command_id", "UUID"),
    ("require_command_id", "get"),
    ("require_command_id", "isinstance"),
    ("require_command_id", "str"),
    ("require_fresh_ssm_target", "_require_live_clock"),
    ("require_fresh_ssm_target", "_sample_utc"),
    ("require_fresh_ssm_target", "aws"),
    ("require_fresh_ssm_target", "validate_managed_instance"),
    ("run_aws_json", "AwsCliError"),
    ("run_aws_json", "group"),
    ("run_aws_json", "isinstance"),
    ("run_aws_json", "len"),
    ("run_aws_json", "loads"),
    ("run_aws_json", "run"),
    ("run_aws_json", "search"),
    ("send_command", "ConfigError"),
    ("send_command", "_require_live_clock"),
    ("send_command", "aws"),
    ("send_command", "build_remote_command"),
    ("send_command", "dumps"),
    ("send_command", "len"),
    ("send_command", "require_command_id"),
    ("send_command", "require_fresh_ssm_target"),
    ("send_command", "str"),
    ("send_command", "uuid4"),
    ("validate_managed_instance", "ManagedInstance"),
    ("validate_managed_instance", "PreflightError"),
    ("validate_managed_instance", "_parse_heartbeat"),
    ("validate_managed_instance", "get"),
    ("validate_managed_instance", "isinstance"),
    ("validate_managed_instance", "len"),
    ("validate_managed_instance", "timedelta"),
    ("validate_managed_instance", "utcoffset"),
})

# Modules whose attributes block. Binding one to a name is how the send-path
# whitelist would be defeated without adding an obviously-blocking callee.
BLOCKING_MODULES = frozenset({
    "time", "select", "socket", "signal", "subprocess", "asyncio", "selectors",
    "threading", "queue", "concurrent",
})


def _callee_name(func):
    """What a call resolves to, independent of how it was reached.

    `time.sleep(...)`, `_settle(...)` and `base64.b64encode(...).decode(...)`
    are named `sleep`, `_settle` and `decode`. Attribute chains collapse to the
    final attribute, and calling a call is marked with a trailing `()`.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _callee_name(func.func) + "()"
    return ast.unparse(func)

# Everything that can run between the freshness proof and SendCommand reaching
# the wire. `run_aws_json` is a root rather than an edge because the runner is
# injected: no static edge connects it to `send_command`.
BOUNDED_SEND_PATH_ROOTS = (
    "send_command", "require_fresh_ssm_target", "run_aws_json",
)


def _send_path_tree(definition):
    """Statements that run when this definition is entered at send time.

    A class decorator runs at import, not between the freshness verdict and
    the wire. Methods and field initialisers run at construction, which is
    after the staleness raise and before SendCommand.
    """
    if isinstance(definition, ast.ClassDef):
        return ast.Module(body=list(definition.body), type_ignores=[])
    return definition


def _reachable_controller_functions(module, roots):
    """Every controller function or class reachable from `roots` by a call."""
    definitions = {}
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            definitions[node.name] = node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not isinstance(definitions.get(node.name), ast.ClassDef):
                definitions[node.name] = node
    seen, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in seen or name not in definitions:
            continue
        seen.add(name)
        definition = definitions[name]
        # Dataclass construction calls inherited `__post_init__` / `__new__`
        # even when those methods are not on this ClassDef. Bases are not a
        # Call, so they have to be followed by name.
        if isinstance(definition, ast.ClassDef):
            for base in definition.bases:
                if isinstance(base, ast.Name):
                    pending.append(base.id)
                elif isinstance(base, ast.Attribute):
                    pending.append(base.attr)
        tree = _send_path_tree(definition)
        # A `raise AwsCliError(...)` constructs a class, but that is the
        # fail-closed path. Following it put exception `__init__` on the send
        # path. `return ManagedInstance(...)` is the fresh path and is followed.
        under_raise = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                for child in ast.walk(node.exc):
                    under_raise.add(id(child))
        for call in ast.walk(tree):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and id(call) not in under_raise
            ):
                pending.append(call.func.id)
    return {name: definitions[name] for name in seen}


def test_nothing_on_the_send_path_can_loop_or_wait():
    # Reading one function was not enough: `_delay(0.001)`, a local helper whose
    # own name contains no "sleep", spent heartbeat age that the proof had
    # already accounted for while `run_aws_json` itself stayed clean. Follow the
    # calls. (`select.select([], [], [], 45)` is the same attack without a
    # helper, which is why the pattern is not just "sleep".)
    module = _controller_module()
    reachable = _reachable_controller_functions(module, BOUNDED_SEND_PATH_ROOTS)

    assert set(BOUNDED_SEND_PATH_ROOTS) <= set(reachable)
    # The walk really followed edges rather than returning its own roots.
    assert len(reachable) > len(BOUNDED_SEND_PATH_ROOTS)
    # Construction after the verdict is on the path. A method on this class
    # is work the heartbeat proof has already spent.
    assert "ManagedInstance" in reachable

    for name, function in sorted(reachable.items()):
        tree = _send_path_tree(function)
        assert [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        ] == [], name

    # A comprehension is neither a `For` nor a `While`, and it can spin as long
    # as it likes: `[c for c in remote_command if c not in remote_command]`
    # over the real 65 536-character command is ~4e9 comparisons between the
    # heartbeat verdict and the wire.
    for name, function in sorted(reachable.items()):
        tree = _send_path_tree(function)
        assert [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp))
        ] == [], name

    # The whitelist, not a blocklist, and qualified by the caller. Exact
    # equality rather than a subset, so a name that stops being called has to
    # leave the list too -- an entry nobody can account for is how a list like
    # this rots into permission to do anything.
    called = {
        (name, _callee_name(node.func))
        for name, function in reachable.items()
        for node in ast.walk(_send_path_tree(function))
        if isinstance(node, ast.Call)
    }
    assert called == APPROVED_SEND_PATH_CALLS

    # Exactly one launch in the runner, and it is the subprocess call itself.
    runner = reachable["run_aws_json"]
    launches = [
        node for node in ast.walk(runner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run"
    ]
    assert len(launches) == 1


def _blocking_module_names(module):
    """Every local name that refers to a blocking module.

    `import time`, `import time as _t` and `import time, select` all bind a
    name to something whose attributes block, and the guard used to know only
    the first spelling.
    """
    names = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKING_MODULES:
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _expression_reaches_blocking(value, blocking):
    """True when `value` can resolve to a blocking callable.

    Attribute-of-Name (`time.sleep`) and Call-walking-Names (`getattr(time,
    "sleep")`) were the two shapes round 11 knew. A Subscript
    (`time.__dict__["sleep"]`) is neither, and `__import__("time").sleep`
    never binds a blocking Name at all -- the module is a string.
    """
    for child in ast.walk(value):
        if isinstance(child, ast.Name) and (
            child.id in blocking or child.id in {"__import__", "import_module"}
        ):
            return True
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and child.value.split(".")[0] in BLOCKING_MODULES
        ):
            return True
    return False


def _blocking_bindings(module):
    """Assignments that bind a name to something that can block."""
    blocking = _blocking_module_names(module)
    offenders = []
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom) and node.module in BLOCKING_MODULES:
            offenders.append(ast.unparse(node))
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        if _expression_reaches_blocking(value, blocking):
            offenders.append(ast.unparse(node))
    return offenders


def test_the_controller_binds_no_alias_to_a_blocking_call():
    # The whitelist next door is defeated in one line if a blocking function
    # can be rebound to an approved-looking name. `_settle = time.sleep` was
    # the first spelling; `run = getattr(time, "sleep")` was the second, and it
    # borrowed an approved name as well.
    assert _blocking_bindings(_controller_module()) == []


def test_the_alias_guard_resolves_names_rather_than_matching_one_shape():
    # Positive control. The assertion above is negative and would hold
    # vacuously against a module with no assignments at all.
    module = ast.parse(
        "import time\n"
        "import time as _t\n"
        "import json\n"
        "_settle = time.sleep\n"
        "_also = _t.sleep\n"
        "run = getattr(time, \"sleep\")\n"
        "_via_dict = time.__dict__[\"sleep\"]\n"
        "_via_import = __import__(\"time\").sleep\n"
        "_via_module = importlib.import_module(\"time\").sleep\n"
        "_via_attr = operator.attrgetter(\"sleep\")(__import__(\"time\"))\n"
        "_via_vars = vars()[\"time\"].sleep\n"
        "fine = json.dumps\n"
    )
    assert _blocking_module_names(module) == {"time", "_t"}
    assert sorted(_blocking_bindings(module)) == [
        "_also = _t.sleep",
        "_settle = time.sleep",
        "_via_attr = operator.attrgetter('sleep')(__import__('time'))",
        "_via_dict = time.__dict__['sleep']",
        "_via_import = __import__('time').sleep",
        "_via_module = importlib.import_module('time').sleep",
        "_via_vars = vars()['time'].sleep",
        "run = getattr(time, 'sleep')",
    ]

    # `from time import sleep as _settle` is the same act by another route.
    assert _blocking_bindings(
        ast.parse("from time import sleep as _settle\n")
    ) == ["from time import sleep as _settle"]

    # And the callee resolver reads through every shape the send path uses.
    calls = ast.parse("time.sleep(1)\nbase64.b64encode(x).decode()\nf(a)()\n")
    assert sorted(
        _callee_name(node.func) for node in ast.walk(calls)
        if isinstance(node, ast.Call)
    ) == ["b64encode", "decode", "f", "f()", "sleep"]


def test_nothing_follows_the_staleness_verdict_but_the_return():
    """The adjacency guard, applied one level down.

    `send_command`'s two statements are pinned, but the function that actually
    renders the verdict is `validate_managed_instance`, and its tail was
    unpinned -- so a sleep or an O(n^2) comprehension placed after the
    staleness `raise` sat strictly between the heartbeat decision and the wire
    with nothing to say about it.
    """
    validate = _function_def(_controller_module(), "validate_managed_instance")

    ceilings = [
        index for index, statement in enumerate(validate.body)
        if isinstance(statement, ast.If)
        and "SSM_HEARTBEAT_MAX_AGE_SECONDS" in ast.unparse(statement.test)
    ]
    assert len(ceilings) == 1

    tail = _fresh_path_after_ceiling(validate)
    assert len(tail) == 1
    assert isinstance(tail[0], ast.Return)
    result = tail[0].value
    assert isinstance(result, ast.Call)
    assert isinstance(result.func, ast.Name)
    assert result.func.id == "ManagedInstance"
    assert _evaluates_only(tail[0], result)


def _fresh_path_after_ceiling(function):
    """Statements that run after the staleness `If` decides the target is fresh.

    Round 11 pinned `validate.body[ceiling + 1:]` and missed `If.orelse` -- the
    fresh path, and the only path that proceeds to SendCommand. A callee-free
    `_ = instance_id` in that `else` sat between the verdict and the wire.
    """
    ceilings = [
        index for index, statement in enumerate(function.body)
        if isinstance(statement, ast.If)
        and "SSM_HEARTBEAT_MAX_AGE_SECONDS" in ast.unparse(statement.test)
    ]
    if len(ceilings) != 1:
        return []
    ceiling = function.body[ceilings[0]]
    return list(ceiling.orelse) + list(function.body[ceilings[0] + 1:])


def test_the_verdict_tail_pin_reads_the_fresh_else_branch():
    sneaky = ast.parse(
        "def validate_managed_instance():\n"
        "    if age > timedelta(seconds=SSM_HEARTBEAT_MAX_AGE_SECONDS):\n"
        "        raise PreflightError('stale')\n"
        "    else:\n"
        "        _ = instance_id\n"
        "    return ManagedInstance(instance_id=instance_id, last_ping=last_ping)\n"
    ).body[0]
    tail = _fresh_path_after_ceiling(sneaky)
    assert any(isinstance(statement, ast.Assign) for statement in tail)
    assert len(tail) == 2


def test_the_send_path_guard_follows_indirection_and_names_waiting():
    module = ast.parse(
        "import select\n"
        "import time\n"
        "def run_aws_json():\n"
        "    _delay(0.001)\n"
        "def _delay(seconds):\n"
        "    time.sleep(seconds)\n"
        "def _block():\n"
        "    select.select([], [], [], 45)\n"
        "def unrelated():\n"
        "    while True:\n"
        "        pass\n"
    )
    reachable = _reachable_controller_functions(module, ("run_aws_json",))

    # The helper is reached through the call, and what it calls is visible.
    assert set(reachable) == {"run_aws_json", "_delay"}
    called = {
        (name, _callee_name(node.func))
        for name, function in reachable.items()
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }
    # Neither the helper nor the thing it waits on is approved, so the
    # whitelist rejects this closure -- whatever the helper had been named.
    assert called == {("run_aws_json", "_delay"), ("_delay", "sleep")}
    assert not called <= APPROVED_SEND_PATH_CALLS

    # Unreached functions are not the send path's business.
    assert "unrelated" not in reachable


def test_the_send_path_guard_enters_a_class_constructed_after_the_verdict():
    # `return ManagedInstance(...)` is an approved send-path Call. The class
    # body is a ClassDef, so a walk that only indexes FunctionDef never enters
    # `__post_init__`, and a busy-wait there runs after the staleness verdict.
    module = ast.parse(
        "def validate_managed_instance():\n"
        "    raise PreflightError('stale')\n"
        "    return ManagedInstance()\n"
        "class ManagedInstance:\n"
        "    def __post_init__(self):\n"
        "        remaining = 8\n"
        "        while remaining:\n"
        "            remaining -= 1\n"
        "class PreflightError:\n"
        "    def __init__(self, message):\n"
        "        while True:\n"
        "            pass\n"
        "def unrelated():\n"
        "    while True:\n"
        "        pass\n"
    )
    reachable = _reachable_controller_functions(
        module, ("validate_managed_instance",)
    )
    assert "ManagedInstance" in reachable
    # Fail-closed construction is not the send path.
    assert "PreflightError" not in reachable
    assert "unrelated" not in reachable
    tree = _send_path_tree(reachable["ManagedInstance"])
    assert [
        node for node in ast.walk(tree) if isinstance(node, ast.While)
    ], "constructor body must be on the send path"


def test_the_send_path_guard_enters_inherited_constructor_bodies():
    # Dataclass construction calls an inherited `__post_init__` even when
    # that method is not in `ManagedInstance.__dict__`. Bases are not a
    # Call, so a walk that only follows `ManagedInstance(...)` never
    # entered `_Record` and a busy-wait there ran after the verdict.
    module = ast.parse(
        "class _Record:\n"
        "    def __post_init__(self):\n"
        "        remaining = 8\n"
        "        while remaining:\n"
        "            remaining -= 1\n"
        "class ManagedInstance(_Record):\n"
        "    pass\n"
        "def validate_managed_instance():\n"
        "    return ManagedInstance()\n"
    )
    reachable = _reachable_controller_functions(
        module, ("validate_managed_instance",)
    )
    assert "ManagedInstance" in reachable
    assert "_Record" in reachable
    tree = _send_path_tree(reachable["_Record"])
    assert [
        node for node in ast.walk(tree) if isinstance(node, ast.While)
    ], "inherited constructor body must be on the send path"


def test_managed_instance_is_a_frozen_record_with_no_behaviour():
    """Construction after the verdict is not a place to hide work.

    Production `ManagedInstance` stays a two-field frozen dataclass with
    no bases. A method, a `__post_init__`, a base whose `__post_init__`
    dataclass construction would call, or any statement that is not a
    field annotation would run after the staleness raise. The send-path
    walk would then see it; this pin refuses the method or base existing
    at all.
    """
    definition = next(
        node for node in _controller_module().body
        if isinstance(node, ast.ClassDef) and node.name == "ManagedInstance"
    )
    assert any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "dataclass"
        and any(
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
        for decorator in definition.decorator_list
    )
    assert definition.bases == []
    assert [
        node for node in definition.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ] == []
    assert all(isinstance(node, ast.AnnAssign) for node in definition.body)
    assert [
        node.target.id for node in definition.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ] == ["instance_id", "last_ping"]


def test_controller_imports_the_canonical_threshold_instead_of_redeclaring_it():
    # A redeclared ceiling -- at any scope, under any spelling or annotation --
    # would let the controller drift away from the canonical contract module.
    module = _controller_module()

    bindings = set()
    for node in ast.walk(module):
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else
            node.targets if isinstance(node, ast.Assign) else []
        )
        bindings.update(
            target.id for target in targets if isinstance(target, ast.Name)
        )
    assert "SSM_HEARTBEAT_MAX_AGE_SECONDS" not in bindings

    imported = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "SSM_HEARTBEAT_MAX_AGE_SECONDS" in imported


def test_the_canonical_threshold_is_the_value_the_boundary_actually_compares():
    # Forbidding one identifier is not enough: a second ceiling declared under a
    # different name, with the canonical import left present but unused, would
    # satisfy that guard while the boundary compares against the impostor. Tie
    # the guard to the comparison site instead of to a spelling.
    module = _controller_module()
    boundary = _function_def(module, "validate_managed_instance")

    # A dead `_ = SSM_HEARTBEAT_MAX_AGE_SECONDS` satisfies a whole-body search
    # while the comparison runs against a shadow local, so read the comparison
    # that actually rejects. Anchored on the rejection rather than on the word
    # "timedelta", so hoisting the ceiling to a module constant or comparing
    # `age.total_seconds()` stays green -- both are the same contract.
    stale_tests = _rejection_tests(boundary, "stale")
    assert len(stale_tests) == 1
    assert any(isinstance(op, (ast.Gt, ast.GtE)) for op in stale_tests[0].ops)
    assert _resolves_to(module, stale_tests[0], "SSM_HEARTBEAT_MAX_AGE_SECONDS")

    # Its sibling tolerance is canonical too, and it is the opposite direction.
    skew_tests = _rejection_tests(boundary, "future")
    assert len(skew_tests) == 1
    assert any(isinstance(op, (ast.Lt, ast.LtE)) for op in skew_tests[0].ops)
    assert _resolves_to(module, skew_tests[0], "SSM_HEARTBEAT_FUTURE_SKEW_SECONDS")

    # ... and the name must still mean the canonical constant, unaliased.
    for name in (
        "SSM_HEARTBEAT_MAX_AGE_SECONDS", "SSM_HEARTBEAT_FUTURE_SKEW_SECONDS",
    ):
        sources = [
            (node.module, alias.asname)
            for node in ast.walk(module)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name == name
        ]
        assert sources, name
        assert set(sources) == {("deploy_contract", None)}, name


def test_send_boundary_clock_is_typed_as_a_live_clock_not_a_frozen_sample():
    # A `datetime` in this annotation is a clock sampled at some earlier point
    # in the lifecycle -- exactly the retired early-clock contract.
    module = _controller_module()

    for name in ("preflight", "require_fresh_ssm_target", "send_command", "run_deploy"):
        function = _function_def(module, name)
        arguments = function.args.args + function.args.kwonlyargs
        # Keyed by the annotation, not the spelling: renaming the parameter is a
        # refactor, and exactly one clock per boundary is the real invariant.
        # The trade is that the alias `UtcClock` is itself contract surface now
        # -- renaming the alias is a deliberate change, made here too.
        clocks = [
            argument for argument in arguments
            if argument.annotation is not None
            and "Clock" in ast.unparse(argument.annotation)
        ]
        assert [ast.unparse(clock.annotation) for clock in clocks] == ["UtcClock"], name
        assert not [
            argument for argument in arguments
            if argument.annotation is not None
            and "datetime" in ast.unparse(argument.annotation)
        ], name

    # Keyed by the argument itself: a default here would be a clock baked in at
    # import time, and positional indexing would silently follow the wrong one.
    send_command = _function_def(module, "send_command")
    keyword_defaults = dict(
        zip(send_command.args.kwonlyargs, send_command.args.kw_defaults)
    )
    clock = next(
        argument for argument in send_command.args.kwonlyargs
        if argument.annotation is not None
        and ast.unparse(argument.annotation) == "UtcClock"
    )
    assert keyword_defaults[clock] is None


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
