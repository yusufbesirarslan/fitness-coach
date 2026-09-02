"""Unprivileged pre-approval gate: skip superseded SHAs, release waiting runs."""

from pathlib import Path

from scripts.deploy_control import CandidateSuperseded, ConfigError
from scripts.deploy_gate import (
    deployable_runs_to_release,
    main,
    write_gate_output,
)


CURRENT = "a" * 40
SUPERSEDED = "b" * 40
NEWER = "c" * 40
DIVERGENT = "d" * 40


def fake_git_with_history(origin_main, merge_bases):
    calls = []

    def run_git(args):
        calls.append(args)
        if args[-2:] == ["rev-parse", "refs/remotes/origin/main"]:
            return origin_main
        if len(args) > 3 and args[-3] == "merge-base":
            left = args[-2]
            return merge_bases.get(left, "0" * 40)
        return ""

    run_git.calls = calls
    return run_git


def test_a_current_candidate_is_deployable(tmp_path):
    output = tmp_path / "github_output"
    exit_code = main(
        environ={"DEPLOY_SHA": CURRENT, "GITHUB_OUTPUT": str(output),
                 "GITHUB_RUN_ID": "9"},
        repo_path=Path("/candidate"),
        run_git=fake_git_with_history(CURRENT, {CURRENT: CURRENT}),
        list_runs=lambda: [],
        list_jobs=lambda run_id: [],
        cancel_run=lambda run_id: None,
    )

    assert exit_code == 0
    assert "deploy=true" in output.read_text(encoding="utf-8")


def test_a_superseded_candidate_skips_without_failing(tmp_path):
    output = tmp_path / "github_output"
    cancelled = []
    exit_code = main(
        environ={"DEPLOY_SHA": SUPERSEDED, "GITHUB_OUTPUT": str(output),
                 "GITHUB_RUN_ID": "9"},
        repo_path=Path("/candidate"),
        run_git=fake_git_with_history(NEWER, {SUPERSEDED: SUPERSEDED}),
        list_runs=lambda: [],
        list_jobs=lambda run_id: [],
        cancel_run=cancelled.append,
    )

    assert exit_code == 0
    assert cancelled == []
    assert "deploy=false" in output.read_text(encoding="utf-8")


def test_a_divergent_candidate_still_fails_closed(tmp_path):
    output = tmp_path / "github_output"
    exit_code = main(
        environ={"DEPLOY_SHA": DIVERGENT, "GITHUB_OUTPUT": str(output),
                 "GITHUB_RUN_ID": "9"},
        repo_path=Path("/candidate"),
        run_git=fake_git_with_history(NEWER, {DIVERGENT: "e" * 40}),
        list_runs=lambda: [],
        list_jobs=lambda run_id: [],
        cancel_run=lambda run_id: None,
    )

    assert exit_code == 1
    assert not output.exists() or "deploy=true" not in output.read_text(
        encoding="utf-8")


def test_waiting_superseded_runs_are_released_and_in_flight_deploys_are_not():
    runs = [
        {"id": 100, "head_sha": SUPERSEDED, "status": "waiting"},
        {"id": 101, "head_sha": SUPERSEDED, "status": "in_progress"},
        {"id": 102, "head_sha": CURRENT, "status": "waiting"},
        {"id": 103, "head_sha": DIVERGENT, "status": "waiting"},
        {"id": 9, "head_sha": CURRENT, "status": "in_progress"},
    ]
    jobs = {
        100: [{"name": "deploy", "status": "waiting", "runner_id": 0}],
        101: [{"name": "deploy", "status": "in_progress", "runner_id": 7}],
        102: [{"name": "deploy", "status": "waiting", "runner_id": 0}],
        103: [{"name": "deploy", "status": "waiting", "runner_id": 0}],
        9: [{"name": "candidate", "status": "in_progress", "runner_id": 3}],
    }

    released = deployable_runs_to_release(
        runs,
        jobs_by_id=jobs,
        current_run_id=9,
        origin_main=CURRENT,
        merge_base=lambda sha, main: sha if sha == SUPERSEDED else (
            main if sha == CURRENT else "f" * 40),
    )

    assert released == [100]


def test_main_cancels_only_the_waiting_superseded_run(tmp_path):
    output = tmp_path / "github_output"
    cancelled = []
    jobs = {
        100: [{"name": "deploy", "status": "waiting", "runner_id": 0}],
        101: [{"name": "deploy", "status": "in_progress", "runner_id": 7}],
    }

    exit_code = main(
        environ={"DEPLOY_SHA": CURRENT, "GITHUB_OUTPUT": str(output),
                 "GITHUB_RUN_ID": "9"},
        repo_path=Path("/candidate"),
        run_git=fake_git_with_history(CURRENT, {
            CURRENT: CURRENT, SUPERSEDED: SUPERSEDED,
        }),
        list_runs=lambda: [
            {"id": 100, "head_sha": SUPERSEDED, "status": "waiting"},
            {"id": 101, "head_sha": SUPERSEDED, "status": "in_progress"},
        ],
        list_jobs=lambda run_id: jobs[run_id],
        cancel_run=cancelled.append,
    )

    assert exit_code == 0
    assert cancelled == [100]
    assert "deploy=true" in output.read_text(encoding="utf-8")


def test_write_gate_output_appends_the_github_output_file(tmp_path):
    path = tmp_path / "out"
    write_gate_output(path, "false")
    write_gate_output(path, "true")
    assert path.read_text(encoding="utf-8") == "deploy=false\ndeploy=true\n"


def test_candidate_superseded_is_the_skip_signal():
    assert not issubclass(CandidateSuperseded, ConfigError)
