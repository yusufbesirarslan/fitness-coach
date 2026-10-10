"""R6-03A critical mutation qualification; worktree sources are never edited."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TX = "scripts/r6_deploy_transaction.py"
MUTANTS = [
    ("migration-overlap-gate", TX, "        if problems:\n", "        if False:\n",
     "test_contract_migration_is_refused_before_build_and_database"),
    ("release-prepare-before-start", TX, "            self._release_prepare()\n            self._start_candidate()\n",
     "            self._start_candidate()\n            self._release_prepare()\n",
     "test_first_cutover_legacy_to_blue_reaches_the_exact_steady_state"),
    ("candidate-pre-switch-reverify", TX, "            self._pre_switch()\n", "",
     "test_pre_switch_reverify_failure_rolls_back_without_switching"),
    ("route-rollback", TX, 'route_ok = self._restore_route(self.budget.deadline(steps["route_restore"]))',
     "route_ok = True", "test_post_switch_proof_failure_restores_previous_route_then_removes_candidate"),
    ("worker-rollback", TX, 'worker_ok = self._restore_worker(self.budget.deadline(steps["worker_restore"]))',
     "worker_ok = True", "test_worker_failure_restores_route_web_and_exact_previous_worker_in_order"),
    ("capacity-gate", "scripts/web_slot_runtime.py", "        if not decision.allowed:\n",
     "        if False:\n", "test_insufficient_memory_refuses_before_anything_starts"),
    ("exact-revision-proof-image", TX, "        if baked != self.deploy_sha:\n",
     "        if False:\n", "test_refusals_before_any_mutation_leave_production_exactly_as_it_was"),
    ("exact-revision-proof-worker", TX, "        if baked != revision or worker.app_revision != revision:\n",
     "        if False:\n", "test_candidate_worker_with_a_wrong_revision_is_rolled_back"),
    ("exact-revision-proof-serving", TX, "        if revision != self.previous_commit:\n",
     "        if False:\n", "test_serving_revision_must_equal_the_checkout_head_even_when_consistent"),
    ("redis-isolation", TX, 'self._compose(timeout, compose, override, "up", "-d", "--no-deps",',
     'self._compose(timeout, compose, override, "up", "-d", "--remove-orphans",',
     "test_worker_update_touches_only_the_worker_with_the_exact_image"),
    ("redis-identity", TX, 'if (len(redis) != 1 or redis[0].id != self.redis_baseline.id',
     'if (False and (len(redis) != 1 or redis[0].id != self.redis_baseline.id',
     "test_redis_restart_during_worker_update_rolls_back"),
    ("drain-bound", TX, "self.drain_max_seconds = drain_max_seconds",
     "self.drain_max_seconds = 3600", "test_drain_deadline_forces_bounded_retirement_without_rollback"),
    ("pre-switch-route-drift", TX, "        if route.state != self.previous_route:\n            raise StepFailed(f\"route drifted to {route.state} before the switch\")",
     "        if False:\n            raise StepFailed(f\"route drifted to {route.state} before the switch\")", "test_pre_switch_route_drift_is_never_repaired_or_switched"),
    ("public-health-gate", TX, "        return status == 200\n",
     "        return True\n", "test_post_switch_proof_failure_restores_previous_route_then_removes_candidate"),
    ("mobile-envelope-gate", TX, 'return (error.get("code") == MOBILE_INGRESS_CODE\n                and error.get("retryable") is False)',
     "return True", "test_post_switch_proof_failure_restores_previous_route_then_removes_candidate"),
    ("retirement-after-commit", TX, '        self._call(deadline, "worker update", self.ops.worker_up, "candidate",',
     '        self._retire()\n        self._call(deadline, "worker update", self.ops.worker_up, "candidate",',
     "test_old_backend_is_never_retired_before_the_commit_point"),
    ("worker-immutable-rollback", TX, "            if pinned != self.previous_worker.image_id:\n",
     "            if False:\n", "test_worker_rollback_tag_drift_keeps_candidate_and_reports_incomplete"),
    ("exact-candidate-image-id", TX, "or current.image_id != self.candidate_image_id",
     "or False", "test_existing_same_revision_candidate_must_use_the_built_image"),
    ("retirement-route-reproof", TX,
     '        route = self._call(deadline, "pre-retirement route", self.ops.route_status)\n'
     '        if (route.state != self.target_slot\n'
     '                or route.backend != ROUTE_BACKENDS[self.target_slot]):\n'
     '            raise StepFailed("route drifted before previous backend retirement")\n',
     "", "test_route_drift_during_drain_never_retires_the_now_serving_previous_backend"),
    ("candidate-removal-route-reproof", TX,
     '            route = self.ops.route_status(self._time_left(deadline))\n'
     '            if (route.state != self.previous_route\n'
     '                    or route.backend != ROUTE_BACKENDS[self.previous_route]):\n'
     '                raise StepFailed("route drifted before candidate removal")\n',
     "", "test_rollback_route_drift_during_worker_restore_keeps_the_active_candidate"),
    ("migrator-cleanup-proof", TX,
     'manual = (["release-prepare container cleanup unproven"]\n'
     '                  if self.release_prepare_cleanup_unproven else [])',
     "manual = []", "test_failed_release_prepare_cleanup_cannot_report_verified_rollback"),
    ("compound-operation-cutoff", TX,
     "timeout = min(timeout, self._command_deadline - self.clock())",
     "timeout = timeout", "test_compound_host_operation_decreases_each_command_timeout"),
    ("docker-absence-proof", TX,
     'if re.fullmatch(r"(?:Error: |Error response from daemon: )?No such (?:container|object): "\n'
     '                            + re.escape(container_id), err.strip(), re.IGNORECASE):',
     'if "no such" in err.lower():',
     "test_missing_docker_socket_cannot_prove_migrator_absence"),
]
# The Redis identity guard spans a parenthesized expression; keep its mutant syntactically valid.
REDIS_CLOSE = 'or redis[0].status != "running"):\n'
REDIS_MUTANT_CLOSE = 'or redis[0].status != "running")):\n'

def run_test(sandbox, node):
    module = "tests/test_r6_slot_runtime.py" if "capacity" in node or "insufficient_memory" in node else "tests/test_r6_deploy_transaction.py"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "--noconftest", "-o", "addopts=",
         "-p", "no:cacheprovider", "-q", f"{module}::{node}"],
        cwd=sandbox, capture_output=True, text=True, timeout=90)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    watched = {rel: (ROOT / rel).read_bytes() for rel in {m[1] for m in MUTANTS}}
    records = []
    with tempfile.TemporaryDirectory(prefix="r6-03a-mutants-") as temp:
        sandbox = Path(temp)
        shutil.copytree(ROOT / "scripts", sandbox / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (sandbox / "tests").mkdir()
        for name in ("test_r6_deploy_transaction.py", "test_r6_slot_runtime.py"):
            shutil.copy(ROOT / "tests" / name, sandbox / "tests" / name)
        shutil.copytree(ROOT / "deploy", sandbox / "deploy")
        for name in ("docker-compose.yml", "docker-compose.web-slot.yml"):
            shutil.copy(ROOT / name, sandbox / name)
        baseline_nodes = sorted({m[4] for m in MUTANTS})
        for node in baseline_nodes:
            baseline = run_test(sandbox, node)
            if baseline.returncode:
                raise RuntimeError(f"baseline {node} failed:\n{baseline.stdout}\n{baseline.stderr}")
        for name, rel, old, new, node in MUTANTS:
            source = watched[rel].decode("utf-8").replace("\r\n", "\n")
            if source.count(old) != 1:
                raise RuntimeError(f"{name}: anchor must match exactly once")
            mutant = source.replace(old, new, 1)
            if name == "redis-identity":
                assert mutant.count(REDIS_CLOSE) == 1
                mutant = mutant.replace(REDIS_CLOSE, REDIS_MUTANT_CLOSE, 1)
            compile(mutant, name, "exec")
            (sandbox / rel).write_text(mutant, encoding="utf-8", newline="\n")
            result = run_test(sandbox, node)
            # A collection/import/syntax failure is never a killed safety mutant.
            killed = result.returncode == 1 and "FAILED " in result.stdout and "ERROR " not in result.stdout
            records.append({"name": name, "test": node, "killed": killed,
                            "returncode": result.returncode, "output": result.stdout + result.stderr})
            print(f"{name}: {'KILLED' if killed else 'SURVIVED/INVALID'}", flush=True)
            (sandbox / rel).write_bytes(watched[rel])
    unchanged = all((ROOT / rel).read_bytes() == raw for rel, raw in watched.items())
    report = {"killed": sum(r["killed"] for r in records), "total": len(records),
              "worktree_source_byte_exact": unchanged,
              "source_sha256": {rel: hashlib.sha256(raw).hexdigest() for rel, raw in watched.items()},
              "mutants": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f'{report["killed"]}/{report["total"]} killed; worktree unchanged={unchanged}')
    return 0 if unchanged and report["killed"] == report["total"] else 1

if __name__ == "__main__":
    sys.exit(main())
