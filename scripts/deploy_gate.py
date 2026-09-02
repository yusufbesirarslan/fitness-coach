"""Unprivileged pre-approval gate for production deploys.

Runs before the production environment is requested. A superseded candidate
(main moved forward, this SHA is still an ancestor) skips with exit 0 and
never asks for approval. A newer run also releases sibling Deploy-to-EC2
runs that are only waiting for that approval gate on a superseded SHA, so
they cannot block the concurrency slot or fail red when rejected.

A deploy job that already has a runner is left alone: that is the in-flight
host transaction the production lock must not cancel.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

if __package__:
    from .deploy_control import (
        GIT_CALL_TIMEOUT_SECONDS,
        CandidateSuperseded,
        ConfigError,
        GitCliError,
        SHA_RE,
        _run_git,
        validate_candidate,
    )
else:
    from deploy_control import (
        GIT_CALL_TIMEOUT_SECONDS,
        CandidateSuperseded,
        ConfigError,
        GitCliError,
        SHA_RE,
        _run_git,
        validate_candidate,
    )


ACTIVE_RUN_STATUSES = frozenset({
    "in_progress", "waiting", "queued", "pending", "requested",
})
GitRunner = Callable[[list[str]], str]
RunLister = Callable[[], list[dict[str, Any]]]
JobLister = Callable[[int], list[dict[str, Any]]]
RunCanceller = Callable[[int], None]


def write_gate_output(path: Path, deploy: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"deploy={deploy}\n")


def deployable_runs_to_release(
    runs: list[dict[str, Any]],
    *,
    jobs_by_id: Mapping[int, list[dict[str, Any]]],
    current_run_id: int,
    origin_main: str,
    merge_base: Callable[[str, str], str],
) -> list[int]:
    """Return superseded runs that are not executing the privileged deploy job."""
    released: list[int] = []
    current_run_id = int(current_run_id)
    for run in runs:
        run_id = int(run["id"])
        if run_id == current_run_id:
            continue
        sha = str(run["head_sha"])
        if not SHA_RE.fullmatch(sha) or sha == origin_main:
            continue
        try:
            base = merge_base(sha, origin_main)
        except (ConfigError, GitCliError, OSError, subprocess.SubprocessError):
            continue
        if base != sha:
            continue
        jobs = jobs_by_id.get(run_id, [])
        if any(
            job.get("name") == "deploy"
            and job.get("status") == "in_progress"
            and int(job.get("runner_id") or 0) != 0
            for job in jobs
        ):
            continue
        released.append(run_id)
    return released


def _run_gh_json(args: list[str]) -> Any:
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=GIT_CALL_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise ConfigError("unable to query GitHub Actions runs")
    try:
        return json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ConfigError("unable to query GitHub Actions runs") from error


def _list_runs() -> list[dict[str, Any]]:
    rows = _run_gh_json([
        "gh", "run", "list",
        "--workflow", "deploy.yml",
        "--json", "databaseId,headSha,status",
        "--limit", "20",
    ])
    if not isinstance(rows, list):
        raise ConfigError("unable to query GitHub Actions runs")
    active = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        if status not in ACTIVE_RUN_STATUSES:
            continue
        active.append({
            "id": row["databaseId"],
            "head_sha": row["headSha"],
            "status": status,
        })
    return active


def _list_jobs(run_id: int) -> list[dict[str, Any]]:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repository:
        raise ConfigError("GITHUB_REPOSITORY is required to list workflow jobs")
    payload = _run_gh_json([
        "gh", "api", f"repos/{repository}/actions/runs/{run_id}/jobs",
    ])
    if not isinstance(payload, dict):
        raise ConfigError("unable to query GitHub Actions jobs")
    jobs = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        jobs.append({
            "name": job.get("name"),
            "status": job.get("status"),
            "runner_id": job.get("runner_id") or 0,
        })
    return jobs


def _cancel_run(run_id: int) -> None:
    completed = subprocess.run(
        ["gh", "run", "cancel", str(run_id)],
        capture_output=True,
        text=True,
        timeout=GIT_CALL_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise ConfigError(f"unable to cancel superseded deploy run {run_id}")


def _merge_base_using(
    run_git: GitRunner, repo_path: Path,
) -> Callable[[str, str], str]:
    git_prefix = ["git", "-C", str(repo_path)]

    def merge_base(sha: str, origin_main: str) -> str:
        run_git([*git_prefix, "cat-file", "-e", f"{sha}^{{commit}}"])
        return run_git([*git_prefix, "merge-base", sha, origin_main])

    return merge_base


def main(
    *,
    environ: Mapping[str, str] | None = None,
    repo_path: Path | None = None,
    run_git: GitRunner | None = None,
    list_runs: RunLister | None = None,
    list_jobs: JobLister | None = None,
    cancel_run: RunCanceller | None = None,
    log: Callable[[str], None] = print,
) -> int:
    env = os.environ if environ is None else environ
    deploy_sha = env.get("DEPLOY_SHA", "")
    output_path = Path(env.get("GITHUB_OUTPUT", ""))
    try:
        current_run_id = int(env.get("GITHUB_RUN_ID", ""))
    except (TypeError, ValueError):
        log("deployment failed: GITHUB_RUN_ID must be an integer")
        return 1
    if not SHA_RE.fullmatch(deploy_sha):
        log("deployment failed: DEPLOY_SHA must be lowercase 40-hex")
        return 1
    if env.get("GITHUB_OUTPUT", "") == "":
        log("deployment failed: GITHUB_OUTPUT is required")
        return 1

    git = _run_git if run_git is None else run_git
    path = Path.cwd() if repo_path is None else repo_path
    list_runs_fn = _list_runs if list_runs is None else list_runs
    list_jobs_fn = _list_jobs if list_jobs is None else list_jobs
    cancel_fn = _cancel_run if cancel_run is None else cancel_run

    try:
        validate_candidate(path, deploy_sha, run_git=git)
    except CandidateSuperseded as skipped:
        log(f"deployment skipped: {skipped}")
        write_gate_output(output_path, "false")
        return 0
    except (ConfigError, GitCliError) as error:
        log(f"deployment failed: {error}")
        return 1

    try:
        runs = list_runs_fn()
        merge_base = _merge_base_using(git, path)
        jobs_by_id = {}
        for run in runs:
            run_id = int(run["id"])
            if run_id == current_run_id:
                continue
            jobs_by_id[run_id] = list_jobs_fn(run_id)
        released = deployable_runs_to_release(
            runs,
            jobs_by_id=jobs_by_id,
            current_run_id=current_run_id,
            origin_main=deploy_sha,
            merge_base=merge_base,
        )
        for run_id in released:
            log(f"releasing waiting superseded deploy run {run_id}")
            cancel_fn(run_id)
    except (ConfigError, GitCliError) as error:
        log(f"deployment failed: {error}")
        return 1

    write_gate_output(output_path, "true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
