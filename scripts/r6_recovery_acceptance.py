"""Pure post/watch checks for an R6 same-head recovery attempt.

Call with evidence from the pinned GitHub workflow and the existing release
host probe. The checker supplies the GitHub read callback. This profile adds
evidence checks without executing a host probe or authorizing deployment.
"""

import re
from dataclasses import dataclass
from datetime import datetime


SHA = re.compile(r"[0-9a-f]{40}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER = re.compile(r"[0-9a-f]{64}")
STARTED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z")
EVENT = re.compile(r"^\S+Z r6-deploy: ([a-z-]+)(?: (.*))?$", re.M)
GITHUB_TIME = "%Y-%m-%dT%H:%M:%SZ"
DEPLOY_PATH = ".github/workflows/deploy.yml"
CI_PATH = ".github/workflows/ci.yml"
POST_PHASES = frozenset({"post", "post2", "watch1", "watch2"})
FAILURE_EVENTS = frozenset({
    "release-failed", "rollback-incomplete", "rollback-verified", "commit-receipt-unproven",
    "rollback-refused-below-floor", "adult-failure-disposition", "retirement-incomplete",
    "drain-deadline", "capability-activation-incomplete", "recovery-schema-evidence",
    "previous-eligibility-not-required", "previous-eligibility", "serving-revision", "worker-baseline",
})


@dataclass(frozen=True)
class RuntimeProof:
    revision: str
    image: str
    web: str
    web_started: str
    worker: str
    worker_started: str
    snapshot: str


def verify_identity(*, phase, candidate, tree, run_id, attempt, ci_id, main, actual_tree, run, job, ci):
    """Validate attempt-scoped GitHub objects before reading the deploy log."""
    if phase not in POST_PHASES:
        raise ValueError("R6_RECOVERY_PHASE_INVALID")
    if not (isinstance(candidate, str) and SHA.fullmatch(candidate) and main == candidate
            and isinstance(tree, str) and SHA.fullmatch(tree) and actual_tree == tree):
        raise ValueError("R6_RECOVERY_SOURCE_IDENTITY_MISMATCH")
    if any(type(value) is not int or value < 1 for value in (run_id, attempt, ci_id)):
        raise ValueError("R6_RECOVERY_ATTEMPT_NOT_BOUND")
    expected_run = dict(id=run_id, run_attempt=attempt, head_sha=candidate, head_branch="main",
                        path=DEPLOY_PATH, event="workflow_run", status="completed", conclusion="success")
    expected_job = dict(run_id=run_id, run_attempt=attempt, head_sha=candidate,
                        name="deploy", status="completed", conclusion="success")
    expected_ci = dict(id=ci_id, head_sha=candidate, head_branch="main", path=CI_PATH,
                       event="push", status="completed", conclusion="success")
    for observed, expected in ((run, expected_run), (job, expected_job), (ci, expected_ci)):
        if not isinstance(observed, dict) or any(observed.get(key) != value for key, value in expected.items()):
            raise ValueError("R6_RECOVERY_GITHUB_IDENTITY_MISMATCH")
    try:
        start = datetime.strptime(job.get("started_at"), GITHUB_TIME)
        end = datetime.strptime(job.get("completed_at"), GITHUB_TIME)
    except (TypeError, ValueError):
        raise ValueError("R6_RECOVERY_JOB_TIME_UNPROVEN") from None
    if not (type(job.get("id")) is int and job["id"] > 0 and start <= end):
        raise ValueError("R6_RECOVERY_JOB_TIME_UNPROVEN")


def github_recovery(phase, binding, gh, repo, diff_hash):
    """Obtain the exact job log through the checker's read-only GitHub callback."""
    if phase not in POST_PHASES:
        raise ValueError("R6_RECOVERY_PHASE_INVALID")
    candidate, previous, tree = (binding.get(key) for key in ("candidate", "serving", "candidate_tree"))
    if not all(isinstance(value, str) and SHA.fullmatch(value) for value in (candidate, previous, tree)):
        raise ValueError("R6_RECOVERY_SOURCE_IDENTITY_MISMATCH")
    run_id, ci_id = (binding.get(key) for key in ("run", "ci_run"))
    attempt = binding.get("recovery_attempt")
    if not (all(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) for value in (run_id, ci_id))
            and type(attempt) is int and attempt > 0):
        raise ValueError("R6_RECOVERY_ATTEMPT_NOT_BOUND")

    root = f"repos/{repo}"
    deploy = f"{root}/actions/runs/{run_id}"
    latest = gh(deploy)
    if latest.get("run_attempt") != attempt:
        raise ValueError("R6_RECOVERY_ATTEMPT_SUPERSEDED")
    scope = f"{deploy}/attempts/{attempt}"
    run = gh(scope)
    result = gh(f"{scope}/jobs?per_page=100")
    jobs = result.get("jobs")
    selected = [job for job in jobs if job.get("name") == "deploy"] if isinstance(jobs, list) else []
    if len(selected) != 1 or result.get("total_count") != len(jobs):
        raise ValueError("R6_RECOVERY_DEPLOY_JOB_UNPROVEN")
    job = selected[0]
    main = gh(f"{root}/commits/main").get("sha")
    actual_tree = (gh(f"{root}/git/commits/{candidate}").get("tree") or {}).get("sha")
    ci = gh(f"{root}/actions/runs/{ci_id}")
    verify_identity(phase=phase, candidate=candidate, tree=tree, run_id=int(run_id), attempt=attempt,
                    ci_id=int(ci_id), main=main, actual_tree=actual_tree, run=run, job=job, ci=ci)
    comparison = gh(f"{root}/compare/{previous}...{candidate}")
    files = comparison.get("files")
    if (comparison.get("status") != "ahead" or comparison.get("behind_by") != 0
            or not isinstance(files, list) or not files
            or diff_hash(file["filename"] for file in files) != binding.get("reviewed_diff")):
        raise ValueError("R6_RECOVERY_REVIEWED_DIFF_UNPROVEN")
    log = gh(f"{root}/actions/jobs/{job['id']}/logs", raw=True)
    proof = parse_recovery_log(log, previous=previous, candidate=candidate,
                               old_slot=binding.get("serving_slot"), db_heads=binding.get("db_heads"))
    return job, proof


def parse_recovery_log(log, *, previous, candidate, old_slot, db_heads):
    """Accept only the successful same-head sequence from the selected attempt."""
    if not (all(isinstance(sha, str) and SHA.fullmatch(sha) for sha in (previous, candidate))
            and previous != candidate
            and old_slot in ("blue", "green") and db_heads
            and all(isinstance(head, str) and re.fullmatch(r"[a-z0-9_]+", head) for head in db_heads)):
        raise ValueError("R6_RECOVERY_LOG_BINDING_INVALID")
    target = "blue" if old_slot == "green" else "green"
    events = list(EVENT.finditer(log))
    names = [match[1] for match in events]
    detail = {match[1]: match[2] or "" for match in events}
    if FAILURE_EVENTS.intersection(names):
        raise ValueError("R6_RECOVERY_FAILURE_OR_RESIDUE")

    preparation = [item for item in ("release-prepared", "release-prepare-retained") if item in names]
    routing = [item for item in ("route-switched", "route-already-candidate") if item in names]
    if len(preparation) != 1 or len(routing) != 1:
        raise ValueError("R6_RECOVERY_PREPARATION_OR_ROUTE_UNPROVEN")
    preparation, routing = preparation[0], routing[0]
    required = (
        "route-baseline", "recovery-baseline", "migration-delta", "image-proven", "same-head-schema-evidence",
        "old-registration-closed", preparation, "candidate-verified", routing, "post-switch-proven",
        "worker-updated", "adult-reopen-ready", "adult-registration-open", "release-committed",
        "same-head-runtime-proven", "release-complete",
    )
    if any(names.count(name) != 1 for name in required):
        raise ValueError("R6_RECOVERY_EVENTS_MISSING_OR_DUPLICATE")
    positions = [events[names.index(name)].start() for name in required]
    if positions != sorted(positions):
        raise ValueError("R6_RECOVERY_EVENTS_OUT_OF_ORDER")

    image = detail["image-proven"].removeprefix("image=")
    switched = routing == "route-already-candidate"
    expected = {
        "route-baseline": f"previous={old_slot} candidate={target}",
        "migration-delta": "",
        "image-proven": f"image={image}",
        "same-head-schema-evidence": f"current={','.join(sorted(db_heads))}",
        "old-registration-closed": f"old={previous} inflight=terminated-with-old-processes old_port=never-reused",
        preparation: f"image={image}" if preparation == "release-prepare-retained" else "",
        "candidate-verified": f"slot={target} eligibility=enforced",
        routing: f"route={target}" if switched else f"to={target}",
        "post-switch-proven": "",
        "worker-updated": f"revision={candidate}",
        "adult-reopen-ready": "generation=retired acl=accepted web_worker_pools=UTC admission=still-closed",
        "adult-registration-open": f"revision={candidate}",
        "release-committed": f"revision={candidate} route={target}",
        "release-complete": f"route={target} revision={candidate}",
    }
    baseline = f"route={target if switched else old_slot} candidate={target} switched={'yes' if switched else 'no'}"
    if (not IMAGE.fullmatch(image) or any(detail[key] != value for key, value in expected.items())
            or not re.fullmatch(baseline + r" previous_web=(?:proven|unproven) previous_worker=(?:proven|unproven)",
                               detail["recovery-baseline"])
            or (switched and preparation != "release-prepare-retained")):
        raise ValueError("R6_RECOVERY_EVENT_DETAIL_MISMATCH")

    runtime = re.fullmatch(rf"revision={candidate} image={image} web=({CONTAINER.pattern}) "
                           rf"web_started=({STARTED.pattern}) worker=({CONTAINER.pattern}) "
                           rf"worker_started=({STARTED.pattern})", detail["same-head-runtime-proven"])
    if not runtime or runtime[1] == runtime[3]:
        raise ValueError("R6_RECOVERY_RUNTIME_LOG_UNPROVEN")
    authorized = list(re.finditer(r"^\S+Z forward recovery authorized for this exact run attempt;[^\n]*$", log, re.M))
    snapshots = list(re.finditer(rf"^\S+Z Recovery snapshot Available for candidate {candidate}: "
                                 rf"(predeploy-{candidate}-[0-9a-f]{{32}})[ \t]*$", log, re.M))
    verified = list(re.finditer(rf"^\S+Z deployment verified at {candidate}[ \t]*$", log, re.M))
    if (len(authorized) != 1 or len(snapshots) != 1 or len(verified) != 1
            or not authorized[0].start() < snapshots[0].start() < positions[0]
            or verified[0].start() <= positions[-1]):
        raise ValueError("R6_RECOVERY_CONTROLLER_OR_SNAPSHOT_UNPROVEN")
    return RuntimeProof(candidate, image, *runtime.groups(), snapshots[0][1])


def verify_runtime(proof, web, worker):
    """Match the exact containers and their boot identities at post/watch."""
    for service, identity, started in ((web, proof.web, proof.web_started),
                                       (worker, proof.worker, proof.worker_started)):
        expected = dict(container_id=identity, started_at=started, image_id=proof.image,
                        app_revision=proof.revision, build_revision=proof.revision,
                        state="running", health="healthy", restart_count=0)
        if (not isinstance(service, dict) or type(service.get("restart_count")) is not int
                or any(service.get(key) != value for key, value in expected.items())):
            raise ValueError("R6_RECOVERY_LIVE_PAIR_CHANGED")
