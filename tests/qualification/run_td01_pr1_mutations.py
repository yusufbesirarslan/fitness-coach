"""Run isolated TD-01 PR1 semantic mutations locally; restore sources after each run.

Usage: python tests/qualification/run_td01_pr1_mutations.py
Only run in the dedicated PR1 worktree, without concurrent tests. The PostgreSQL
mutation (``drop_collate_c``) runs only when FITX_PG_CONCURRENCY_TEST=1 and
PG_TEST_DATABASE_URL point at a database whose default collation is locale-aware
(CI postgres:16 = en_US.utf8).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
UNIT = "tests/test_exercise_performance_history.py"
PG = "tests/test_exercise_performance_history_pg.py"
PKG = "app/services/exercise_performance_history"
MUTATIONS = (
    ("no_history_despite_gap", f"{PKG}/selection.py",
     "    return STATE_NO_HISTORY if coverage.complete else STATE_UNDETERMINED",
     "    return STATE_NO_HISTORY",
     UNIT, "test_h_c8_only_corrupt_candidate_is_undetermined_never_no_history"),
    ("scan_limit_is_not_a_gap", f"{PKG}/models.py",
     "return not self.unavailable and not self.scan_limit_reached",
     "return not self.unavailable",
     UNIT, "test_h_c12_scan_limit_without_a_match_is_undetermined"),
    ("scan_limit_strict", f"{PKG}/queries.py",
     "len(rows) >= MAX_SCAN_ROWS", "len(rows) > MAX_SCAN_ROWS",
     UNIT, "test_h_c12_scan_limit_without_a_match_is_undetermined"),
    ("empty_string_is_missing", f"{PKG}/selection.py",
     "if row.checkpoint_data is None and row.checkpoint_revision == 0:",
     "if not row.checkpoint_data and row.checkpoint_revision == 0:",
     UNIT, "test_h_c6_c7_c15_missing_is_exactly_null_at_revision_zero"),
    ("silently_skip_corrupt", f"{PKG}/selection.py",
     "    except InvalidSessionRequest:\n        return None",
     "    except InvalidSessionRequest:\n        return []",
     UNIT, "test_h_c8_only_corrupt_candidate_is_undetermined_never_no_history"),
    ("broad_catch", f"{PKG}/selection.py",
     "    except InvalidSessionRequest:\n        return None",
     "    except Exception:\n        return None",
     UNIT, "test_h_c19b_unexpected_parser_exceptions_propagate_unchanged"),
    ("uncompleted_set_counts", f"{PKG}/selection.py",
     'if item["completed"] is True)', 'if item["reps"] is not None)',
     UNIT, "test_h_s4_uncompleted_prefilled_sets_are_not_facts"),
    ("keep_cross_date", f"{PKG}/selection.py",
     "    if app_date_of(row.completed_at) != day:", "    if False:",
     UNIT, "test_h_s15_cross_date_is_excluded_never_re_dated"),
    ("no_missing_completed_at_rule", f"{PKG}/selection.py",
     "    if row.completed_at is None:", "    if False:",
     UNIT, "test_h_s14_null_completed_at_is_excluded_not_a_gap"),
    ("null_weight_to_zero", f"{PKG}/selection.py",
     'weight_kg=item["weight_kg"])', 'weight_kg=item["weight_kg"] or 0.0)',
     UNIT, "test_h_s6_s7_s8_null_is_unknown_and_zero_is_zero"),
    ("drop_owner_scope", f"{PKG}/queries.py",
     "            WorkoutSession.user_id == user_id,\n", "",
     UNIT, "test_h_o1_o2_owner_isolation_in_both_directions"),
    ("window_off_by_one", f"{PKG}/queries.py",
     "range(HISTORY_DAYS, -1, -1)", "range(HISTORY_DAYS - 1, -1, -1)",
     UNIT, "test_h_s16_window_edges_and_future_rows"),
    ("reject_inactive", "app/services/exercise_catalog.py",
     '        raise ExerciseUnknown("unknown exercise ID")\n    return exercise',
     '        raise ExerciseUnknown("unknown exercise ID")\n    if not exercise.active:\n'
     '        raise ExerciseInactive("inactive")\n    return exercise',
     UNIT, "test_h_i2_retired_exercise_is_served_identically"),
    ("unknown_as_malformed", "app/services/exercise_catalog.py",
     'raise ExerciseUnknown("unknown exercise ID")',
     'raise ExerciseIdentityInvalid("unknown exercise ID")',
     UNIT, "test_h_i3_unknown_exercise_has_the_exact_type_and_reads_nothing"),
    ("drop_collate_c", f"{PKG}/queries.py",
     'return WorkoutSession.public_id.collate("C")', "return WorkoutSession.public_id",
     PG, "test_h_p4_ties_break_by_public_id_byte_order_and_the_collation_matters"),
)


def main():
    pg_ready = os.environ.get("FITX_PG_CONCURRENCY_TEST") == "1" and os.environ.get("PG_TEST_DATABASE_URL")
    results = []
    for name, relative, before, after, module, test in MUTATIONS:
        if module == PG and not pg_ready:
            results.append({"mutation": name, "test": test, "killed": None, "note": "PG not configured"})
            continue
        path = ROOT / relative
        original = path.read_text()
        if original.count(before) != 1:
            raise RuntimeError(f"mutation must apply exactly once: {name}")
        try:
            path.write_text(original.replace(before, after))
            command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       f"{module}::{test}"]
            if module == PG:
                command[3:3] = ["-m", "pg_concurrency"]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            killed = run.returncode == 1 and " failed" in run.stdout
            results.append({"mutation": name, "test": test, "killed": killed})
            if not killed:
                print(run.stdout[-3000:] + run.stderr[-3000:])
        finally:
            path.write_text(original)
    print(json.dumps(results, indent=2))
    return 0 if all(item["killed"] is not False for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
