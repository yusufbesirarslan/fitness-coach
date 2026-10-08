"""Regression evidence for CI selection, execution and required-gate safety."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.pytest_sharding import load_timings, partition, verify


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
    workflow = yaml.load(Path(".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["name"] == "CI"
    assert set(workflow["on"]) == {"pull_request", "push"}
    assert workflow["on"]["push"]["branches"] == ["main"]
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
    assert download["with"]["pattern"] == "pytest-shard-${{ github.run_attempt }}-*"
    for job in (shards, gate):
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])
