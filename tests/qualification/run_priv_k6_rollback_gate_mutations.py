"""PRIV-K6 rollback gate mutation qualification; worktree sources are never edited.

Each mutant removes ONE load-bearing rule (Layer A database fence, Layer B
release admission or the application/tooling contract) in a sandbox copy of the repository and names the test that
must fail for the intended reason. A collection/import/syntax failure never
counts as a kill. Mutants whose test is PostgreSQL need a disposable PG 16 database
(``PG_TEST_DATABASE_URL`` + ``FITX_PG_CONCURRENCY_TEST=1``); without one they are
reported as ``skipped``, never as killed.

    python -B tests/qualification/run_priv_k6_rollback_gate_mutations.py \\
        --output qualification/priv-k6-rollback-gate-mutations.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TX = "scripts/r6_deploy_transaction.py"
FENCE = "migrations/versions/d7e3a9c1f5b8_completion_authority_fence.py"
APP = "app/services/completion_fence/__init__.py"
CLI = "app/cli.py"
B = "tests/test_r6_completion_capability.py"
A = "tests/test_completion_fence_pg.py"
C = "tests/test_completion_fence.py"

# (name, layer, file, old, new, test node)
MUTANTS = [
    # ── Layer B: release admission ──
    ("B1-candidate-admission", "B", TX,
     "        if self._floor_requires_capability(floor) and CAPABILITY not in capabilities:\n",
     "        if False:\n",
     f"{B}::test_incapable_candidate_is_refused_before_any_mutation"),
    ("B2-automatic-rollback-guard", "B", TX,
     "        if (self.route_switch_attempted or self.worker_attempted) and \\\n",
     "        if False and \\\n",
     f"{B}::test_post_activation_rollback_never_restores_an_incapable_previous"),
    ("B3-forged-declaration", "B", TX,
     "        if baked != source:\n",
     "        if False:\n",
     f"{B}::test_declaration_not_matching_the_reviewed_source_is_refused"),
    ("B4-unknown-floor-is-absent", "B", TX,
     "        return (floor is None or floor[\"floor\"] != \"absent\"\n",
     "        return (floor is not None and floor[\"floor\"] != \"absent\"\n",
     f"{B}::test_incapable_candidate_is_refused_before_any_mutation"),
    ("B5-activation-before-retirement", "B", TX,
     "        if status == EXIT_OK and CAPABILITY in self.candidate_capabilities:\n",
     "        if CAPABILITY in self.candidate_capabilities:\n",
     f"{B}::test_activation_never_runs_while_the_old_backend_is_retained"),
    ("B6-implementation-evidence", "B", TX,
     "        if missing:\n            return frozenset(), f\"declared without its implementation: {missing}\"\n",
     "",
     f"{B}::test_declaration_without_its_implementing_migration_is_refused"),
    ("B7-partial-web-worker", "B", TX,
     "        self.previous_capabilities = web & worker\n",
     "        self.previous_capabilities = web\n",
     f"{B}::test_partial_capability_web_capable_worker_not_refuses_both"),
    ("B8-pre-switch-floor-reread", "B", TX,
     "            self._admit_before_switch()\n",
     "",
     f"{B}::test_floor_raised_between_admission_and_switch_refuses_an_incapable_candidate"),
    ("B9-post-prepare-fence-verification", "B", TX,
     "        if CAPABILITY in self.candidate_capabilities and (\n"
     "                floor is None or floor[\"fence\"] != \"verified\"):\n",
     "        if False:\n",
     f"{B}::test_capable_candidate_whose_fence_does_not_verify_never_takes_traffic"),
    # ── Layer A: database fence (PostgreSQL) ──
    ("A1-evidence-check", "A", FENCE,
     "        RAISE EXCEPTION 'completion evidence without a claim in this transaction (writer below capability floor)'\n"
     "            USING ERRCODE = 'AXF02';\n    END IF;\n    RETURN NULL;\nEND\n\"\"\",\n    \"completion_fence_marker\"",
     "        NULL;\n    END IF;\n    RETURN NULL;\nEND\n\"\"\",\n    \"completion_fence_marker\"",
     f"{A}::test_a3_legacy_writer_refused_after_photo_claim_and_gallery_delete"),
    ("A2-transaction-identity", "A", FENCE,
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint\n"
     "              AND c.user_id = NEW.user_id AND c.date_key = NEW.date_key) THEN",
     "            WHERE c.user_id = NEW.user_id AND c.date_key = NEW.date_key) THEN",
     f"{A}::test_a5_an_earlier_claim_never_authorizes_new_evidence"),
    ("A3-floor-lock", "A", FENCE,
     "    PERFORM pg_catalog.pg_advisory_xact_lock_shared(-3391128469959823719);",
     "    PERFORM 1;",
     f"{A}::test_c2_raise_waits_for_an_in_flight_historical_commit"),
    ("A4-floor-monotonicity", "A", FENCE,
     "    RAISE EXCEPTION 'the completion capability floor is monotonic'\n        USING ERRCODE = 'AXF03';\n",
     "    RETURN NULL;\n",
     f"{A}::test_d1_floor_cannot_be_lowered_deleted_or_truncated"),
    ("A5-historical-conflict-I4", "A", FENCE,
     "    IF NEW.proof = 'none' AND EXISTS (",
     "    IF false AND EXISTS (",
     f"{A}::test_c4_photo_free_claim_racing_an_unlocked_historical_writer"),
    ("A6-claim-before-floor", "A", FENCE,
     "        RAISE EXCEPTION 'completion claims are not authorized before the capability floor is raised'\n"
     "            USING ERRCODE = 'AXF01';\n",
     "        NULL;\n",
     f"{A}::test_a0_dormant_fence_refuses_claims_and_keeps_historical_writers"),
    ("A7-unlocked-floor-insert", "A", FENCE,
     "        RAISE EXCEPTION 'the capability floor is raised only under the exclusive floor lock'\n"
     "            USING ERRCODE = 'AXF03';\n",
     "        NULL;\n",
     f"{A}::test_d2_floor_insert_without_the_exclusive_lock_is_refused"),
    ("A8-binding-forgery", "A", FENCE,
     "    IF TG_OP = 'UPDATE' OR pg_catalog.pg_trigger_depth() < 2 THEN",
     "    IF TG_OP = 'UPDATE' THEN",
     f"{A}::test_d4_bindings_cannot_be_forged_or_rewritten"),
    ("A9a-claims-present-keeps-pump-check-fence", "A", FENCE,
     "       AND NOT EXISTS (SELECT 1 FROM public.workout_completion_day) THEN\n"
     "        RETURN NULL;\n    END IF;\n    IF NOT EXISTS (\n"
     "            SELECT 1 FROM public.completion_claim_transaction b\n"
     "            JOIN public.workout_completion_day c ON c.id = b.claim_id\n"
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint\n"
     "              AND c.user_id = NEW.user_id AND c.date_key = NEW.date_key) THEN",
     "       AND true THEN\n"
     "        RETURN NULL;\n    END IF;\n    IF NOT EXISTS (\n"
     "            SELECT 1 FROM public.completion_claim_transaction b\n"
     "            JOIN public.workout_completion_day c ON c.id = b.claim_id\n"
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint\n"
     "              AND c.user_id = NEW.user_id AND c.date_key = NEW.date_key) THEN",
     f"{A}::test_d3_floor_table_recreated_by_ddl_with_claims_present_stays_enforced"),
    ("A9b-claims-present-keeps-marker-fence", "A", FENCE,
     "       AND NOT EXISTS (SELECT 1 FROM public.workout_completion_day) THEN\n"
     "        RETURN NULL;\n    END IF;\n    IF NOT EXISTS (\n"
     "            SELECT 1 FROM public.completion_claim_transaction b\n"
     "            JOIN public.workout_completion_day c ON c.id = b.claim_id\n"
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint\n"
     "              AND c.user_id = NEW.user_id) THEN",
     "       AND true THEN\n"
     "        RETURN NULL;\n    END IF;\n    IF NOT EXISTS (\n"
     "            SELECT 1 FROM public.completion_claim_transaction b\n"
     "            JOIN public.workout_completion_day c ON c.id = b.claim_id\n"
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint\n"
     "              AND c.user_id = NEW.user_id) THEN",
     f"{A}::test_d3_floor_table_recreated_by_ddl_with_claims_present_stays_enforced"),
    ("A10-read-committed-only", "A", FENCE,
     "    IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN",
     "    IF false THEN",
     f"{A}::test_a16_repeatable_read_and_serializable_writers_are_refused"),
    ("A11-marker-owner-binding", "A", FENCE,
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint\n"
     "              AND c.user_id = NEW.user_id) THEN",
     "            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint) THEN",
     f"{A}::test_a6b_another_owners_claim_never_authorizes_a_marker_alone"),
    ("A12-claim-retention", "A", FENCE,
     "    IF EXISTS (SELECT 1 FROM public.\"user\" u WHERE u.id = OLD.user_id) THEN",
     "    IF false THEN",
     f"{A}::test_a17_account_deletion_cascades_claims_and_nothing_else_deletes_them"),
    # ── application contract ──
    ("C1-permanent-refusal-advertised-retryable", "C", APP,
     '    "AXF02": FenceRefusal("AXF02", "writer_below_floor", False),\n',
     '    "AXF02": FenceRefusal("AXF02", "writer_below_floor", True),\n',
     f"{C}::test_every_sqlstate_the_fence_raises_is_classified_and_nothing_else"),
    ("C2-cli-without-app-context", "C", CLI,
     '@click.group("completion-floor", cls=AppGroup)\n',
     '@click.group("completion-floor")\n',
     f"{A}::test_f1_cli_status_and_raise_lines_are_what_the_release_engine_parses"),
]


def _needs_pg(node):
    return node.startswith(A)


def _sandbox(temp):
    sandbox = Path(temp) / "repo"
    shutil.copytree(ROOT, sandbox, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".venv", "node_modules", "instance", "*.pyc"))
    return sandbox


def run_test(sandbox, node):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q",
         "-m", "pg_concurrency or not pg_concurrency", node],
        cwd=sandbox, capture_output=True, text=True, timeout=600, env=env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layer", choices=("A", "B", "C", "all"), default="all")
    args = parser.parse_args()
    pg = (os.environ.get("FITX_PG_CONCURRENCY_TEST") == "1"
          and os.environ.get("PG_TEST_DATABASE_URL", "").startswith("postgresql"))
    selected = [m for m in MUTANTS if args.layer in ("all", m[1])]
    watched = {rel: (ROOT / rel).read_bytes() for rel in {m[2] for m in selected}}
    records = []
    with tempfile.TemporaryDirectory(prefix="priv-k6-gate-mutants-") as temp:
        sandbox = _sandbox(temp)
        for node in sorted({m[5] for m in selected if not _needs_pg(m[5]) or pg}):
            baseline = run_test(sandbox, node)
            if baseline.returncode:
                raise RuntimeError(f"baseline {node} failed:\n{baseline.stdout}\n{baseline.stderr}")
        for name, layer, rel, old, new, node in selected:
            if _needs_pg(node) and not pg:
                records.append({"name": name, "layer": layer, "test": node,
                                "killed": False, "skipped": True})
                print(f"{name}: SKIPPED (no disposable PostgreSQL)", flush=True)
                continue
            source = watched[rel].decode("utf-8").replace("\r\n", "\n")
            if source.count(old) != 1:
                raise RuntimeError(f"{name}: anchor must match exactly once")
            mutant = source.replace(old, new, 1)
            compile(mutant, name, "exec")
            (sandbox / rel).write_text(mutant, encoding="utf-8", newline="\n")
            try:
                result = run_test(sandbox, node)
            finally:
                (sandbox / rel).write_bytes(watched[rel])
            killed = (result.returncode == 1 and "failed" in result.stdout
                      and " error" not in result.stdout.lower().split("short test summary")[-1])
            records.append({"name": name, "layer": layer, "test": node, "killed": killed,
                            "skipped": False, "returncode": result.returncode,
                            "output": (result.stdout + result.stderr)[-4000:]})
            print(f"{name}: {'KILLED' if killed else 'SURVIVED/INVALID'}", flush=True)
    unchanged = all((ROOT / rel).read_bytes() == raw for rel, raw in watched.items())
    ran = [r for r in records if not r["skipped"]]
    report = {"killed": sum(r["killed"] for r in ran), "ran": len(ran),
              "skipped": len(records) - len(ran), "total": len(records),
              "worktree_source_byte_exact": unchanged,
              "source_sha256": {rel: hashlib.sha256(raw).hexdigest()
                                for rel, raw in watched.items()},
              "mutants": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f'{report["killed"]}/{report["ran"]} killed, {report["skipped"]} skipped; '
          f"worktree unchanged={unchanged}")
    return 0 if unchanged and ran and report["killed"] == len(ran) else 1


if __name__ == "__main__":
    sys.exit(main())
