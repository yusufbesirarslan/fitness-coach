"""CI-only file sharding plugin and fail-closed evidence verifier.

Load explicitly with ``-p scripts.pytest_sharding``. Root discovery and pytest's
marker selection run unchanged before partitioning; no production imports.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import time

import pytest

SHARDS = 4
TIMINGS_PATH = Path(__file__).with_name("pytest_file_timings.json")


def load_timings(path=TIMINGS_PATH):
    """Read reviewed CI phase timings; malformed evidence fails closed."""
    if not path.exists():
        return {}, None
    raw = path.read_bytes()
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get("schema")) is not int
            or data["schema"] != 1 or not isinstance(data.get("files"), dict)
            or not data["files"]):
        raise ValueError("malformed file timing profile")
    for file, measurement in data["files"].items():
        if not isinstance(file, str) or not file or not isinstance(measurement, dict):
            raise ValueError("malformed file timing entry")
        count, seconds = measurement.get("items"), measurement.get("seconds")
        if type(count) is not int or count <= 0 or type(seconds) not in (int, float):
            raise ValueError("invalid timing count or duration")
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid timing duration")
    return data["files"], hashlib.sha256(raw).hexdigest()


def partition(nodeids, timings=None):
    """Largest file first, weighted by measured phase seconds; lexical ties.

    Scale measured cost by current selected count. New files use the profile's
    mean seconds per item; without a profile use selected item count throughout.
    Retain original item order inside each runner; never split a file.
    """
    files = Counter(node.split("::", 1)[0] for node in nodeids)
    timings = timings or {}
    average = (sum(t["seconds"] for t in timings.values()) /
               sum(t["items"] for t in timings.values())) if timings else 1
    weights = {file: count * (timings[file]["seconds"] / timings[file]["items"]
                             if file in timings else average)
               for file, count in files.items()}
    loads = [0] * SHARDS
    owners = {}
    for file in sorted(files, key=lambda file: (-weights[file], file)):
        shard = min(range(SHARDS), key=lambda i: (loads[i], i))
        owners[file] = shard
        loads[shard] += weights[file]
    return [[node for node in nodeids if owners[node.split("::", 1)[0]] == i]
            for i in range(SHARDS)]


def unique_nodes(value):
    if not isinstance(value, list) or any(not isinstance(n, str) or not n for n in value):
        raise ValueError("invalid node ID list")
    if len(value) != len(set(value)):
        raise ValueError("duplicate node IDs")
    return value


def verify(manifests, revision, *, collection_only=False, result="success",
           timings=None, timing_digest=None):
    if result != "success":
        raise ValueError(f"required matrix result is {result!r}, not success")
    if len(manifests) != SHARDS:
        raise ValueError("missing shard evidence")
    by_shard = {}
    for doc in manifests:
        if (not isinstance(doc, dict) or type(doc.get("schema")) is not int
                or doc["schema"] != 1):
            raise ValueError("malformed manifest")
        index = doc.get("shard")
        if type(index) is not int or index not in range(SHARDS) or index in by_shard:
            raise ValueError("invalid or duplicate shard")
        if (doc.get("revision") != revision or type(doc.get("count")) is not int
                or doc["count"] != SHARDS):
            raise ValueError("wrong revision or shard count")
        if doc["timing_digest"] != timing_digest:
            raise ValueError("wrong partition timing profile")
        if (type(doc.get("exitstatus")) is not int or doc["exitstatus"] != 0
                or doc.get("collection_only") is not collection_only):
            raise ValueError("unsuccessful or wrong-mode evidence")
        for field in ("full", "selected", "deselected", "collection_skips"):
            unique_nodes(doc[field])
        by_shard[index] = doc
    first = by_shard[0]
    if not first["full"]:
        raise ValueError("empty authoritative collection")
    expected = partition(first["full"], timings)
    union = []
    for index in range(SHARDS):
        doc = by_shard[index]
        for field in ("full", "deselected", "collection_skips"):
            if doc[field] != first[field]:
                raise ValueError(f"inconsistent authoritative {field}")
        if doc["selected"] != expected[index]:
            raise ValueError("omitted, duplicated, unassigned, or reordered tests")
        union.extend(doc["selected"])
        if not collection_only:
            reports = doc["reports"]
            if not isinstance(reports, dict) or set(reports) != set(doc["selected"]):
                raise ValueError("execution coverage cannot be proven")
            for phases in reports.values():
                if not isinstance(phases, dict) or set(phases) not in (
                    {"setup", "teardown"}, {"setup", "call", "teardown"}
                ):
                    raise ValueError("incomplete test execution")
                for report in phases.values():
                    if report["outcome"] not in {"passed", "skipped"}:
                        raise ValueError("test phase failed")
                    duration = report["duration"]
                    if type(duration) not in (int, float) or not 0 <= duration < float("inf"):
                        raise ValueError("invalid phase duration")
                if phases["setup"]["outcome"] == "passed" and "call" not in phases:
                    raise ValueError("test call omitted")
    if Counter(union) != Counter(first["full"]):
        raise ValueError("full-suite coverage mismatch")
    return len(union)


def pytest_addoption(parser):
    group = parser.getgroup("ci-sharding")
    group.addoption("--ci-shard", type=int, choices=range(SHARDS))
    group.addoption("--ci-manifest")


def pytest_configure(config):
    index = config.getoption("ci_shard")
    if index is None:
        return
    if not config.getoption("ci_manifest"):
        raise pytest.UsageError("--ci-manifest is required")
    if config.args != [str(config.rootpath)] and config.args != ["."]:
        raise pytest.UsageError("shards require default repository-root discovery")
    config.pluginmanager.register(Evidence(config, index), "ci-shard-evidence")


class Evidence:
    def __init__(self, config, index):
        self.config = config
        self.started = time.monotonic()
        self.timings, timing_digest = load_timings()
        self.doc = dict(schema=1, shard=index, count=SHARDS,
                        run_id=os.environ.get("GITHUB_RUN_ID"),
                        run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
                        head_sha=os.environ.get("CI_HEAD_SHA"),
                        tree=subprocess.check_output(
                            ["git", "rev-parse", "HEAD^{tree}"], text=True).strip(),
                        revision=subprocess.check_output(
                            ["git", "rev-parse", "HEAD"], text=True).strip(),
                        collection_only=config.option.collectonly,
                        timing_digest=timing_digest, full=[], selected=[], deselected=[],
                        collection_skips=[], reports={})

    def pytest_deselected(self, items):
        self.doc["deselected"].extend(item.nodeid for item in items)

    def pytest_collectreport(self, report):
        if report.skipped:
            self.doc["collection_skips"].append(report.nodeid)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, session, config, items):
        yield
        self.doc["full"] = [item.nodeid for item in items]
        selected = partition(self.doc["full"], self.timings)[self.doc["shard"]]
        self.doc["selected"] = selected
        selected_set = set(selected)
        items[:] = [item for item in items if item.nodeid in selected_set]

    def pytest_runtest_logreport(self, report):
        phases = self.doc["reports"].setdefault(report.nodeid, {})
        if report.when in phases:
            raise pytest.UsageError("duplicate test phase execution")
        phases[report.when] = dict(outcome=report.outcome, duration=report.duration)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        yield
        self.doc["exitstatus"] = int(session.exitstatus)
        self.doc["elapsed_seconds"] = time.monotonic() - self.started
        path = Path(self.config.getoption("ci_manifest"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.doc, indent=2) + "\n")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


# Fields every successful shard of one run must agree on, superseded or not.
COLLECTION_FIELDS = ("schema", "count", "collection_only", "timing_digest",
                     "full", "deselected", "collection_skips")


def load_artifacts(directory, *, run_id, run_attempt, head_sha, revision, tree):
    """Select the newest evidence per shard from this run, never flatten or fall back.

    The download action is scoped to the current run (no token/run-id override).
    Canonical artifact directories preserve attempt identity; manifests bind it
    to the executed checkout, which must BE the PR HEAD (never a synthetic merge).
    A shard retained from an earlier attempt (selective rerun) is accepted only
    because every artifact of every attempt proves the same run and source, and
    every successful one the same test collection; a conflict refuses instead of
    picking one.
    verify() still proves parity and success of the selected set.
    """
    if (not re.fullmatch(r"[1-9][0-9]*", run_id)
            or not re.fullmatch(r"[1-9][0-9]*", run_attempt)):
        raise ValueError("invalid run identity")
    if revision != head_sha:
        raise ValueError("executed revision is not the PR HEAD")
    latest = {}
    identities = set()
    collection = None
    for artifact in sorted(directory.iterdir()):
        match = re.fullmatch(r"pytest-shard-([1-9][0-9]*)-([0-9]+)", artifact.name)
        if not match or artifact.is_symlink() or not artifact.is_dir():
            raise ValueError(f"invalid artifact directory: {artifact.name}")
        attempt, shard = map(int, match.groups())
        identity = (attempt, shard)
        if (identity in identities or str(shard) != match[2]
                or shard not in range(SHARDS) or attempt > int(run_attempt)):
            raise ValueError("invalid or duplicate artifact identity")
        identities.add(identity)
        path = artifact / f"shard-{shard}.json"
        if list(artifact.iterdir()) != [path] or path.is_symlink() or not path.is_file():
            raise ValueError("missing, duplicate, or unexpected manifest")
        doc = json.loads(path.read_text(), object_pairs_hook=_unique_object)
        if (not isinstance(doc, dict) or type(doc.get("shard")) is not int
                or doc["shard"] != shard or doc.get("run_id") != run_id
                or doc.get("run_attempt") != str(attempt)
                or doc.get("head_sha") != head_sha or doc.get("revision") != revision
                or doc.get("tree") != tree):
            raise ValueError("mismatched artifact provenance")
        if doc.get("exitstatus") == 0:  # a crashed attempt may have collected partially
            shape = {field: doc.get(field) for field in COLLECTION_FIELDS}
            if collection is None:
                collection = shape
            elif shape != collection:
                raise ValueError("conflicting collection evidence across attempts")
        if shard not in latest or attempt > latest[shard][0]:
            latest[shard] = (attempt, doc, artifact.name)
    if set(latest) != set(range(SHARDS)):
        raise ValueError("missing shard evidence")
    for shard, (attempt, _, name) in sorted(latest.items()):
        superseded = sorted(a for a, s in identities if s == shard and a != attempt)
        print(f"Selected {name}: run={run_id} attempt={attempt} shard={shard} "
              f"superseded={superseded or 'none'} "
              f"head={head_sha} revision={revision} tree={tree}")
    return [latest[shard][1] for shard in range(SHARDS)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--collection-only", action="store_true")
    args = parser.parse_args()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    manifests = load_artifacts(args.directory, run_id=args.run_id,
                              run_attempt=args.run_attempt, head_sha=args.head_sha,
                              revision=args.revision, tree=tree)
    timings, timing_digest = load_timings()
    count = verify(manifests, args.revision, collection_only=args.collection_only,
                   result=args.result, timings=timings, timing_digest=timing_digest)
    totals = defaultdict(float)
    for doc in manifests:
        for node, phases in doc["reports"].items():
            totals[node.split("::", 1)[0]] += sum(p["duration"] for p in phases.values())
    print(f"Exact parity: {count} selected; 0 missing; 0 duplicates; 0 unexpected deselections")
    print("File phase-seconds (setup + call + teardown):")
    for file, seconds in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))[:30]:
        print(f"{seconds:.3f} {file}")


if __name__ == "__main__":
    main()
