"""Run isolated PRIV-K6-B1 semantic mutations locally; restore sources after each run.

Usage: python tests/qualification/run_priv_k6_b1_mutations.py
Only run in the dedicated K6 worktree, without concurrent tests. The PostgreSQL
mutations run only when FITX_PG_CONCURRENCY_TEST=1 and PG_TEST_DATABASE_URL name
a DISPOSABLE postgres:16 database.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
UNIT = "tests/test_priv_k6_no_photo_completion.py"
PG = "tests/test_priv_k6_no_photo_completion_pg.py"
ROUTE = "app/blueprints/mobile_workout_sessions.py"
SERVICE = "app/services/workout_completion/service.py"
QUERIES = "app/services/workout_completion/queries.py"
MODELS = "app/services/workout_completion/models.py"
MIGRATION = "migrations/versions/f9c6d0a1b2e3_workout_completion_day_claim.py"
MUTATIONS = (
    # Route: a submitted photo must never become a photo-free success.
    ("always_photo_free", ROUTE,
     "        if replaying or not _photo_evidence_submitted():",
     "        if True:",
     UNIT, "test_k6c_submitted_photo_meets_the_real_d1_gate_and_is_never_discarded"),
    ("original_multipart_downgrade", ROUTE,
     '    if request.mimetype in {"multipart/form-data", "application/x-www-form-urlencoded"}:',
     '    if request.files or request.form:',
     UNIT, "test_r1_multipart_transport_never_downgrades"),
    ("original_ai_capacity_dependency", ROUTE,
     'def complete_workout_session(session_reference):',
     '@mobile_ai_concurrency_gate("TRAINING_SESSION_COMPLETION_BUSY", "Workout completion is busy.")\ndef complete_workout_session(session_reference):',
     UNIT, "test_r1_no_photo_and_replay_ignore_real_ai_saturation"),
    ("raw_body_discarded", ROUTE,
     "        return any(name in body for name in _PHOTO_FIELDS)\n    return True",
     "        return any(name in body for name in _PHOTO_FIELDS)\n    return False",
     UNIT, "test_k6c_submitted_photo_meets_the_real_d1_gate_and_is_never_discarded"),
    ("photo_path_skips_consent", ROUTE,
     "    require_legacy_ai()\n    image_bytes", "    image_bytes",
     UNIT, "test_k6c_submitted_photo_meets_the_real_d1_gate_and_is_never_discarded"),
    ("photo_free_requires_consent", ROUTE,
     "            proof = _no_photo_completion()",
     "            from app.blueprints.ai_consent import require_legacy_ai\n"
     "            require_legacy_ai()\n            proof = _no_photo_completion()",
     UNIT, "test_k6a_ai_declined_user_completes_without_a_photo"),
    ("replay_processes_photo", ROUTE,
     "        if replaying or not _photo_evidence_submitted():",
     "        if not replaying and not _photo_evidence_submitted():",
     UNIT, "test_k6f_photo_retry_after_photo_free_completion_processes_no_photo"),
    ("photo_free_claims_photo_text", ROUTE,
     '        "activity_text": _NATIVE_NO_PHOTO_ACTIVITY_TEXT,',
     '        "activity_text": _NATIVE_ACTIVITY_TEXT,',
     UNIT, "test_k6a_ai_declined_user_completes_without_a_photo"),
    # Service: no fabricated PumpCheck, no photo-only side effects, one claim.
    ("fabricated_pump_check", SERVICE,
     "    ) if is_photo else None\n", "    )\n",
     UNIT, "test_k6a_ai_declined_user_completes_without_a_photo"),
    ("photo_challenge_event", SERVICE,
     "        if pump_check is not None:\n            # Best-effort",
     "        if True:\n            # Best-effort",
     UNIT, "test_k6b_no_image_means_no_challenge_photo_event_and_no_gallery_item"),
    ("no_claim_written", SERVICE,
     "        db.session.add(claim)\n", "",
     UNIT, "test_k6f_retries_replay_one_completion_one_reward_one_terminalization"),
    ("missing_session_waved_through", SERVICE,
     "        if session is None and not is_photo:",
     "        if False:",
     UNIT, "test_k6k_photo_free_command_without_a_lockable_owned_session_fails_closed"),
    ("no_locked_recheck", SERVICE,
     "        if already_completed_today(command.user_id, command.today):\n"
     "            if mark_session_completed(session, now):\n"
     "                db.session.commit()\n            else:\n"
     "                db.session.rollback()\n"
     '            _log(command.entry_path, "already_completed_locked")',
     "        if False:\n"
     "            if mark_session_completed(session, now):\n"
     "                db.session.commit()\n            else:\n"
     "                db.session.rollback()\n"
     '            _log(command.entry_path, "already_completed_locked")',
     PG, "test_k6h_locked_recheck_refuses_a_day_claimed_while_waiting_for_the_lock"),
    ("forced_failure_commits_claim", SERVICE,
     "        db.session.flush()\n\n        if pump_check is not None:",
     "        db.session.flush()\n        db.session.commit()\n\n        if pump_check is not None:",
     PG, "test_k6j_forced_failure_rolls_back_the_whole_photo_free_completion"),
    # Command contract.
    ("photo_bonus_allowed", MODELS,
     "or self.visibility != \"private\" or self.photo_bonus\n",
     "or self.visibility != \"private\"\n",
     UNIT, "test_a_photo_free_command_cannot_carry_photo_evidence"),
    # Readers + classification.
    ("reader_ignores_claims", QUERIES,
     "    rows = db.session.execute(union(claimed, historical)).all()",
     "    rows = db.session.execute(historical).all()",
     UNIT, "test_completed_days_reads_both_claims_in_one_statement"),
    ("reader_ignores_history", QUERIES,
     "    rows = db.session.execute(union(claimed, historical)).all()",
     "    rows = db.session.execute(claimed).all()",
     UNIT, "test_k6i_historical_daily_pump_check_still_counts_as_completed"),
    ("check_violation_is_replay", QUERIES,
     '        or "UNIQUE constraint failed: workout_completion_day.user_id, "\n'
     '           "workout_completion_day.date_key" in message',
     '        or "workout_completion_day" in message',
     UNIT, "test_a_real_sqlite_check_violation_is_not_swallowed_as_replay"),
    ("any_pg_constraint_is_replay", QUERIES,
     "        return constraint_name == COMPLETION_DAY_CONSTRAINT",
     "        return True",
     PG, "test_c7_only_the_day_claim_unique_violation_classifies_as_replay"),
    # Migration.
    ("verifier_accepts_missing_unique", MIGRATION,
     '        _fail("day uniqueness")', "        pass",
     UNIT, "test_sqlite_migration_accepts_create_all_shape_and_refuses_drift"),
    ("verifier_accepts_not_valid_check", MIGRATION,
     "        if validated.get(PROOF_CHECK) is not True or not all(validated.values()):",
     "        if False:",
     PG, "test_migration_accepts_only_its_own_existing_schema"),
    ("downgrade_drops_populated_claims", MIGRATION,
     "    if retained:", "    if False:",
     UNIT, "test_sqlite_downgrade_refuses_populated_claims"),
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
            evidence_dir = os.environ.get("PRIV_K6_MUTATION_EVIDENCE_DIR")
            if evidence_dir:
                target = Path(evidence_dir)
                target.mkdir(parents=True, exist_ok=True)
                (target / f"{name}.log").write_text(run.stdout + run.stderr)
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
