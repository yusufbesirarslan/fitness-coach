"""Regression evidence for CI selection, execution and required-gate safety."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.pytest_sharding import load_artifacts, load_timings, partition, verify


def evidence():
    nodes = [f"test_{i}.py::test_x[{j}]" for i in range(8) for j in range(i + 1)]
    return [dict(schema=1, shard=i, count=4, revision="revision",
                 exitstatus=0, collection_only=False, timing_digest=None, full=nodes,
                 selected=selected, deselected=["test_load.py::test_load"],
                 collection_skips=["test_optional.py"],
                 reports={node: {phase: dict(outcome="passed", duration=0.1)
                                 for phase in ("setup", "call", "teardown")}
                          for node in selected})
            for i, selected in enumerate(partition(nodes))]


def test_file_partition_is_deterministic_complete_disjoint_and_balanced(tmp_path):
    docs = evidence()
    assert verify(docs, "revision") == 36
    assert partition(docs[0]["full"]) == partition(docs[0]["full"])
    owners = {}
    for doc in docs:
        for node in doc["selected"]:
            file = node.split("::")[0]
            assert owners.setdefault(file, doc["shard"]) == doc["shard"]
    assert max(len(d["selected"]) for d in docs) - min(len(d["selected"]) for d in docs) <= 8
    profile = {"test_0.py": {"items": 1, "seconds": 100},
               "test_1.py": {"items": 2, "seconds": 40}}
    weighted = partition(docs[0]["full"], profile)
    assert weighted != partition(docs[0]["full"])
    assert weighted == partition(docs[0]["full"], profile)
    assert sorted(n for shard in weighted for n in shard) == sorted(docs[0]["full"])
    assert len({i for i, shard in enumerate(weighted) if "test_0.py::test_x[0]" in shard}) == 1
    assert load_timings(tmp_path / "absent.json") == ({}, None)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"schema": 1, "files": profile}))
    loaded, digest = load_timings(path)
    assert loaded == profile and len(digest) == 64
    for bad in ({}, {"schema": 1, "files": {}},
                {"schema": 1, "files": {"file": {"items": 0, "seconds": 1}}},
                {"schema": 1, "files": {"file": {"items": 1, "seconds": -1}}}):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load_timings(path)
    with pytest.raises(ValueError, match="timing profile"):
        verify(docs, "revision", timing_digest="different")


@pytest.mark.parametrize("mutation", [
    "omitted", "duplicated", "new_unassigned", "missing_manifest", "malformed",
    "missing_execution", "incomplete_execution", "wrong_revision", "deselection",
    "failed_phase", "collection_only", "duplicate_shard", "collection_skip",
    "failed_exit", "empty", "wrong_count",
])
def test_verifier_fails_closed(mutation):
    docs = deepcopy(evidence())
    first = docs[0]
    node = first["selected"][0]
    if mutation == "omitted":
        first["selected"].remove(node)
    elif mutation == "duplicated":
        docs[1]["selected"].append(node)
    elif mutation == "new_unassigned":
        for doc in docs:
            doc["full"].append("outside_tests/test_new.py::test_new")
    elif mutation == "missing_manifest":
        docs.pop()
    elif mutation == "malformed":
        for field, bad in (("schema", True), ("exitstatus", False), ("count", "4")):
            malformed = deepcopy(docs)
            malformed[0][field] = bad
            with pytest.raises(ValueError):
                verify(malformed, "revision")
        malformed = deepcopy(docs)
        malformed[0]["reports"][node]["call"]["duration"] = True
        with pytest.raises(ValueError):
            verify(malformed, "revision")
        del first["full"]
    elif mutation == "missing_execution":
        del first["reports"][node]
    elif mutation == "incomplete_execution":
        del first["reports"][node]["call"]
    elif mutation == "wrong_revision":
        first["revision"] = "other"
    elif mutation == "deselection":
        first["deselected"].append(node)
    elif mutation == "failed_phase":
        first["reports"][node]["teardown"]["outcome"] = "failed"
    elif mutation == "collection_only":
        first["collection_only"] = True
    elif mutation == "duplicate_shard":
        docs[1]["shard"] = 0
    elif mutation == "collection_skip":
        first["collection_skips"] = []
    elif mutation == "failed_exit":
        first["exitstatus"] = 1
    elif mutation == "empty":
        first["full"] = []
    elif mutation == "wrong_count":
        first["count"] = 3
    with pytest.raises((ValueError, KeyError, TypeError)):
        verify(docs, "revision")


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "", None])
def test_unsuccessful_required_matrix_cannot_pass(result):
    with pytest.raises(ValueError, match="matrix result"):
        verify(evidence(), "revision", result=result)


def test_existing_setup_skip_counts_as_execution():
    docs = evidence()
    phases = next(iter(docs[0]["reports"].values()))
    phases.pop("call")
    phases["setup"]["outcome"] = "skipped"
    assert verify(docs, "revision") == 36


def test_real_pytest_discovery_markers_skips_and_execution(tmp_path):
    # Independent miniature repository: tests outside tests/, parametrization,
    # marker deselection, import skip and runtime skip; compare plain pytest.
    source = Path("scripts/pytest_sharding.py").read_text()
    (tmp_path / "sharding.py").write_text(source)
    (tmp_path / "pytest.ini").write_text('[pytest]\naddopts = -m "not load"\nmarkers =\n load: load\n')
    (tmp_path / "test_skip.py").write_text('import pytest\npytest.skip("optional", allow_module_level=True)\n')
    for i in range(8):
        directory = tmp_path / ("tests" if i < 4 else "outside")
        directory.mkdir(exist_ok=True)
        (directory / f"test_{i}.py").write_text(
            'import pytest\n@pytest.mark.parametrize("x", [0, 1])\n'
            'def test_x(x):\n assert x >= 0\n'
            '@pytest.mark.load\ndef test_load():\n assert False\n'
            '@pytest.mark.skip(reason="legitimate")\ndef test_skip():\n assert False\n')
    (tmp_path / "plain.py").write_text(
        'import json\nfrom pathlib import Path\n'
        'def pytest_collection_finish(session):\n'
        ' Path("plain.json").write_text(json.dumps([i.nodeid for i in session.items]))\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.invalid",
                    "commit", "--allow-empty", "-qm", "fixture"], cwd=tmp_path, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    def run(*args):
        return subprocess.run([sys.executable, "-m", "pytest", "-q", *args],
                              cwd=tmp_path, capture_output=True, text=True)
    plain = run("--collect-only", "-p", "plain")
    assert plain.returncode == 0, plain.stdout + plain.stderr
    manifests = []
    for index in range(4):
        process = run("-p", "sharding", f"--ci-shard={index}", f"--ci-manifest={index}.json")
        assert process.returncode == 0, process.stdout + process.stderr
        manifests.append(json.loads((tmp_path / f"{index}.json").read_text()))
    assert manifests[0]["full"] == json.loads((tmp_path / "plain.json").read_text())
    assert verify(manifests, revision) == 24
    assert len(manifests[0]["deselected"]) == 8
    assert manifests[0]["collection_skips"] == ["test_skip.py"]
    # Explicit paths would change authoritative discovery and must be rejected.
    assert run("-p", "sharding", "--ci-shard=0", "--ci-manifest=bad.json", "tests").returncode != 0


def test_workflow_retains_stable_fail_closed_gate():
    workflow = yaml.load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["name"] == "CI"
    assert set(workflow["on"]) == {"pull_request", "push"}
    assert workflow["on"]["push"]["branches"] == [
        "fix/mon-ops-r6-apple-secret-injection-02",
        "feat/mon-be-premium-allowance-enforcement-01",
        "feat/priv-d1-coach-native",
        "feat/priv-k6-b1-no-photo-completion", "feat/priv-k6-rollback-gate", "main", "privacy/pa03-be-core-01",
        "r6/03a-bluegreen-deploy-transaction", "feat/privacy-d1-backend-consent",
        "feat/mon-02-be-03b-01b-training-allowance",
        "fix/mon-02-be-03b-a2-a1-expiry-compensation", "fix/priv-k6-pg18-migration", "fix/mon-02-be-02-pg18-compatibility"]
    deploy = yaml.load(Path(".github/workflows/deploy.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert deploy["on"]["workflow_run"]["branches"] == ["main"]
    jobs = workflow["jobs"]
    shards = jobs["tests"]
    assert shards["strategy"] == {"fail-fast": "false", "max-parallel": "4",
                                  "matrix": {"shard": ["0", "1", "2", "3"]}}
    assert shards["runs-on"] == "ubuntu-latest"
    install = next(s for s in shards["steps"] if s.get("name") == "Install dependencies")
    assert install["timeout-minutes"] == "10"
    run = next(s["run"] for s in shards["steps"] if s.get("name") == "Run tests")
    assert "-p scripts.pytest_sharding" in run
    assert "--ci-shard=${{ matrix.shard }}" in run
    assert "--ci-manifest=shard-evidence/shard-${{ matrix.shard }}.json" in run
    upload = next(s for s in shards["steps"] if s.get("name") == "Upload execution and profiling evidence")
    assert upload["with"]["name"] == "pytest-shard-${{ github.run_attempt }}-${{ matrix.shard }}"
    assert upload["with"]["if-no-files-found"] == "error"
    gate = jobs["pytest-gate"]
    assert gate["name"] == "pytest"
    assert gate["needs"] == ["tests"]
    assert gate["if"] == "${{ always() }}"
    assert gate["steps"][0]["run"] == 'test "$SHARD_RESULT" = success'
    assert gate["steps"][0]["env"]["SHARD_RESULT"] == "${{ needs.tests.result }}"
    assert "--result=\"$SHARD_RESULT\"" in gate["steps"][-1]["run"]
    download = next(s for s in gate["steps"] if "actions/download-artifact@" in s.get("uses", ""))
    assert download["with"] == {"pattern": "pytest-shard-*-*", "path": "shard-evidence",
                                "merge-multiple": "false"}
    assert download["uses"] == "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    assert next(s for s in shards["steps"] if s.get("name") == "Run tests")["env"]["CI_HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert gate["steps"][-1]["env"]["CI_HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    for argument in ('--run-id="$GITHUB_RUN_ID"', '--run-attempt="$GITHUB_RUN_ATTEMPT"',
                     '--head-sha="$CI_HEAD_SHA"', '--revision="$GITHUB_SHA"'):
        assert argument in gate["steps"][-1]["run"]
    for job in (shards, gate):
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])


def write_artifact(directory, doc, attempt=1):
    doc = dict(doc, run_id="123", run_attempt=str(attempt), head_sha="head", tree="tree")
    artifact = directory / f"pytest-shard-{attempt}-{doc['shard']}"
    artifact.mkdir()
    path = artifact / f"shard-{doc['shard']}.json"
    path.write_text(json.dumps(doc))
    return path


def selected_artifacts(directory, attempt="2"):
    return load_artifacts(directory, run_id="123", run_attempt=attempt,
                          head_sha="head", revision="revision", tree="tree")


@pytest.mark.parametrize("attempts", [(1, 1, 1, 1), (1, 1, 2, 1), (2, 2, 2, 2),
                                     (9, 9, 10, 9)])
def test_artifact_selection_complete_and_mixed_attempts(tmp_path, attempts):
    docs = evidence()
    for doc, attempt in zip(docs, attempts):
        write_artifact(tmp_path, doc, attempt)
    if attempts[2] > 1:
        failed = dict(docs[2], exitstatus=1)
        write_artifact(tmp_path, failed, 1)
    selected = selected_artifacts(tmp_path, str(max(attempts)))
    assert [doc["run_attempt"] for doc in selected] == list(map(str, attempts))
    assert verify(selected, "revision") == 36


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "extra_file", "wrong_filename", "flat", "malformed_name",
    "leading_zero", "invalid_shard", "future_attempt", "symlink", "bad_json",
    "duplicate_json_key", "wrong_run", "wrong_attempt", "wrong_head", "wrong_tree",
    "wrong_revision", "wrong_shard", "newer_failed", "newer_invalid", "wrong_profile",
    "missing_execution",
])
def test_artifact_selection_rejects_invalid_evidence(tmp_path, mutation):
    docs = evidence()
    paths = [write_artifact(tmp_path, doc) for doc in docs]
    path = paths[2]
    doc = json.loads(path.read_text())
    if mutation == "missing":
        path.unlink()
        path.parent.rmdir()
    elif mutation in {"duplicate", "extra_file"}:
        (path.parent / "copy.json").write_text(path.read_text())
    elif mutation == "wrong_filename":
        path.rename(path.parent / "shard-0.json")
    elif mutation == "flat":
        path.rename(tmp_path / path.name)
    elif mutation in {"malformed_name", "leading_zero", "invalid_shard", "future_attempt"}:
        names = {"malformed_name": "pytest-shard-invalid-2", "leading_zero": "pytest-shard-1-02",
                 "invalid_shard": "pytest-shard-1-4", "future_attempt": "pytest-shard-3-2"}
        path.parent.rename(tmp_path / names[mutation])
    elif mutation == "symlink":
        original = path.read_text()
        path.unlink()
        target = tmp_path / "outside.json"
        target.write_text(original)
        path.symlink_to(target)
    elif mutation == "bad_json":
        path.write_text("{")
    elif mutation == "duplicate_json_key":
        path.write_text(path.read_text()[:-1] + ', "shard": 2}')
    elif mutation.startswith("wrong_") and mutation != "wrong_profile":
        field = {"wrong_run": "run_id", "wrong_attempt": "run_attempt", "wrong_head": "head_sha",
                 "wrong_tree": "tree", "wrong_revision": "revision", "wrong_shard": "shard"}[mutation]
        doc[field] = 0 if field == "shard" else "other"
        path.write_text(json.dumps(doc))
    elif mutation in {"newer_failed", "newer_invalid"}:
        newer = dict(docs[2], exitstatus=1) if mutation == "newer_failed" else dict(docs[2], schema=2)
        write_artifact(tmp_path, newer, 2)
    elif mutation == "wrong_profile":
        doc["timing_digest"] = "other"
        path.write_text(json.dumps(doc))
    elif mutation == "missing_execution":
        doc["reports"] = {}
        path.write_text(json.dumps(doc))
    with pytest.raises((ValueError, KeyError, TypeError)):
        verify(selected_artifacts(tmp_path), "revision")


def test_artifact_loader_cli_checks_provenance_and_parity(tmp_path):
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    timings, digest = load_timings()
    docs = evidence()
    parts = partition(docs[0]["full"], timings)
    for i, doc in enumerate(docs):
        doc.update(revision=revision, timing_digest=digest, selected=parts[i])
        doc["reports"] = {node: {phase: dict(outcome="passed", duration=0.1)
                                  for phase in ("setup", "call", "teardown")}
                          for node in parts[i]}
        path = write_artifact(tmp_path, doc, 2 if i == 2 else 1)
        saved = json.loads(path.read_text())
        saved["tree"] = tree
        path.write_text(json.dumps(saved))
    command = [sys.executable, "-m", "scripts.pytest_sharding", str(tmp_path),
               "--revision", revision, "--result", "success", "--run-id", "123",
               "--run-attempt", "2", "--head-sha", "head"]
    process = subprocess.run(command, capture_output=True, text=True)
    assert process.returncode == 0, process.stdout + process.stderr
    assert "Exact parity: 36 selected; 0 missing; 0 duplicates" in process.stdout
    command[-1] = "mismatched-head"
    assert subprocess.run(command, capture_output=True).returncode != 0
