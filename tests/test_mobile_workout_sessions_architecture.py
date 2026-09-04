"""Architecture guards for the native workout-session write contracts (PR5).

Each guard names one invariant from the PR5 brief's section 56 list. They are
structural on purpose: a behaviour test proves the code does the right thing
today, a guard proves the NEXT change cannot quietly move an authority.

Sprint 14 PR2 moved several of those authorities on purpose -- the request
contract, the typed failure vocabulary and the checkpoint orchestration are now
``app.services.workout_session``'s, shared with the browser transport instead of
being reimplemented for it. The guards below follow them: each asserts its
invariant at the canonical module AND asserts that the native adapter delegates
rather than keeping a second copy. Pointing them back at the adapter would let
the two transports silently diverge again, which is the exact defect PR2 exists
to close.
"""
import ast
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.extensions import db
from app.models import PumpCheck, TrainingPlan, User, WorkoutLog, WorkoutSession
from app.services import mobile_auth
from app.timeutil import APP_TZ, audit_clock


ROUTE_PATH = Path("app/blueprints/mobile_workout_sessions.py")
SERVICE_PATH = Path("app/services/mobile_workout_sessions/service.py")
PROJECTION_PATH = Path("app/services/mobile_workout_sessions/projection.py")
# Canonical and transport-neutral since Sprint 14 PR2 -- shared with the browser.
CHECKPOINT_PATH = Path("app/services/workout_session/checkpoint.py")
ERRORS_PATH = Path("app/services/workout_session/errors.py")
EXECUTION_PATH = Path("app/services/workout_session/execution.py")
SESSION_QUERIES_PATH = Path("app/services/workout_session/queries.py")
COMPLETION_SERVICE_PATH = Path("app/services/workout_completion/service.py")
MODEL_PATH = Path("app/models.py")
MIGRATION_PATH = Path(
    "migrations/versions/f5a6b7c8d9e0_add_workout_session_native_execution.py")
CI_PATH = Path(".github/workflows/ci.yml")
# The native adapter package: everything that is still transport-specific.
PACKAGE_PATHS = (
    SERVICE_PATH, PROJECTION_PATH,
    Path("app/services/mobile_workout_sessions/__init__.py"),
)
# The canonical execution modules the adapter now delegates to. They carry the
# same "no browser controller, no provider" obligations, because the native
# surface reaches production through them.
CANONICAL_PATHS = (CHECKPOINT_PATH, ERRORS_PATH, EXECUTION_PATH)
ENDPOINTS = (
    "mobile_api.start_workout_session",
    "mobile_api.current_workout_session",
    "mobile_api.resume_workout_session",
    "mobile_api.checkpoint_workout_session",
    "mobile_api.abandon_workout_session",
    "mobile_api.complete_workout_session",
)
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


def _source(path):
    return path.read_text(encoding="utf-8")


def _imports(path):
    modules = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# 1. Native workout writes require bearer auth.
def test_every_native_session_write_uses_the_shared_bearer_decorator(app):
    for endpoint in ENDPOINTS:
        view = app.view_functions[endpoint]
        assert getattr(view, "_require_mobile_auth", False) is True, endpoint


def test_no_session_route_reads_an_owner_from_the_request(app):
    source = _source(ROUTE_PATH)

    # Ownership comes from the authenticated principal and nowhere else.
    assert "g.mobile_user.id" in source
    for forbidden in (
        "request.args", "current_user", "session[", "request.cookies",
        "user_id=request", "login_required",
    ):
        assert forbidden not in source


# 2. No browser controller dependency.
def test_the_native_surface_never_depends_on_a_browser_controller():
    for path in (ROUTE_PATH,) + PACKAGE_PATHS + CANONICAL_PATHS:
        modules = _imports(path)
        for module in modules:
            assert not module.startswith("app.blueprints.training")
            assert not module.startswith("app.blueprints.tracking")
            assert not module.startswith("app.blueprints.social")
        assert "flask_login" not in modules


# 3. No provider calls.
def test_the_session_service_package_can_reach_no_provider():
    for path in PACKAGE_PATHS + CANONICAL_PATHS:
        for module in _imports(path):
            assert not module.startswith("app.services.ai")
            assert not module.startswith("app.prompts")
            assert not module.startswith("app.services.training_generation")
            assert module not in {"openai", "anthropic", "boto3", "groq"}
    joined = "".join(_source(path) for path in PACKAGE_PATHS + CANONICAL_PATHS)
    for forbidden in (
        "invoke_model(", "openai_client", "bedrock_client", "_heavy_complete",
        "generate_training_plan_candidate",
    ):
        assert forbidden not in joined


def test_only_completion_pays_for_a_provider_and_the_cheap_writes_do_not():
    source = _source(ROUTE_PATH)
    complete_at = source.index("def complete_workout_session(")
    checkpoint_at = source.index("def checkpoint_workout_session(")

    # The vision gate and its concurrency slot are attached to completion only.
    assert source.index("mobile_ai_concurrency_gate(") < complete_at
    assert source.index("BEDROCK_RATELIMIT") < complete_at
    assert "validate_pump_check(" in source
    # Progress persistence gets its own cheap ceiling, never a generation limit.
    assert "WORKOUT_CHECKPOINT_RATELIMIT" in source
    assert source.index("WORKOUT_CHECKPOINT_RATELIMIT,") < checkpoint_at
    assert "AI_RATELIMIT" not in source


# 4. Opaque workout/session refs only. 5. Raw DB IDs never exposed.
def test_the_public_projection_carries_no_internal_identifier():
    projection = _source(PROJECTION_PATH)
    tree = ast.parse(projection)
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys.update(
                key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )

    assert "session_ref" in keys
    for forbidden in ("id", "session_id", "user_id", "plan_id", "pump_check_id"):
        assert forbidden not in keys
    assert "row.id" not in projection
    assert "row.user_id" not in projection
    assert "row.plan_fingerprint" not in projection


def test_the_session_reference_is_the_opaque_public_id_and_is_length_bounded():
    execution = _source(EXECUTION_PATH)
    service = _source(SERVICE_PATH)

    # The bound and the ownership-scoped lookup are ONE definition, in the
    # canonical domain, so both server transports resolve a reference the same
    # way and a hostile path segment is bounded before it reaches a query.
    assert "SESSION_REF_MAX" in execution
    assert "get_owned_session(user_id, session_ref)" in execution
    # The adapter delegates to it and keeps no lookup of its own.
    assert "row.public_id" in service
    assert "_owned_session = owned_session" in service
    assert "def _owned_session(" not in service
    for source in (execution, service):
        # No lookup by primary key from a client-supplied value.
        assert "WorkoutSession.query.get(" not in source
        assert "filter_by(id=" not in source


# 6. Start cannot create duplicate active sessions.
def test_the_one_active_session_claim_is_a_database_index_not_a_code_check():
    model = _source(MODEL_PATH)

    assert "uq_workout_session_active_owner" in model
    service = _source(SERVICE_PATH)
    # The adapter DELEGATES the claim: it holds no counting, no pre-check and no
    # "is there already one?" query of its own.
    assert "start_session(user_id, today=today, native=identity)" in service
    for forbidden in (
        "filter_by(status=", ".count()", "WorkoutSession(", "if active is None",
    ):
        assert forbidden not in service


# 7. Checkpoint is durable. 8. It requires an optimistic revision.
def test_checkpoint_persistence_is_one_conditional_update_on_the_base_revision():
    queries = _source(SESSION_QUERIES_PATH)
    advance = queries[queries.index("def advance_checkpoint("):]
    advance = advance[:advance.index("\ndef ", 1)] if "\ndef " in advance[1:] else advance

    assert "checkpoint_revision" in advance
    assert "base_revision" in advance
    assert "checkpoint_data" in advance
    assert "WORKOUT_SESSION_ACTIVE" in advance
    # Exactly one write statement: no read-modify-write, no second UPDATE.
    assert advance.count("update(") == 1


def test_the_route_refuses_a_checkpoint_without_if_match_or_a_key():
    source = _source(ROUTE_PATH)
    checkpoint = source[source.index("def checkpoint_workout_session("):]
    checkpoint = checkpoint[:checkpoint.index("@bp.")]

    assert "parse_revision(request.headers.get(\"If-Match\"))" in checkpoint
    assert "parse_idempotency_key(request.headers.get(\"Idempotency-Key\"))" in (
        checkpoint)
    assert "parse_optional_revision" not in checkpoint


# 9. Same checkpoint retry cannot double-advance the revision.
def test_replay_is_decided_before_any_mutation_and_again_after_a_lost_race():
    execution = _source(EXECUTION_PATH)
    body = execution[execution.index("def record_checkpoint("):
                     execution.index("def _replay_or_conflict(")]

    assert body.index("_replay_or_conflict(") < body.index("advance_checkpoint(")
    assert body.count("_replay_or_conflict(") == 2
    assert "parsed.fingerprint" in execution


def test_the_native_adapter_keeps_no_second_checkpoint_orchestration():
    """The point of Sprint 14 PR2: ONE execution authority, two transports.

    A transport that grows its own replay check, its own revision comparison or
    its own call to the durable write has re-created the divergence PR2 removed
    -- the browser and the phone would once again be able to disagree about who
    wins. The adapter may name its session's workout; it may not decide who wins.
    """
    service = _source(SERVICE_PATH)

    assert "record_checkpoint(" in service
    for forbidden in (
        # The durable write, the replay decision, and every column that decides
        # who wins. Declaring a precondition it was handed
        # (``expected_checkpoint_revision=``) is fine; READING the row's own
        # progress columns to decide anything is not.
        "advance_checkpoint(", "_replay_or_conflict(", "row.checkpoint_revision",
        ".checkpoint_fingerprint", ".checkpoint_idempotency_key",
        ".checkpoint_data",
    ):
        assert forbidden not in service, forbidden


# 10. Terminal sessions reject later checkpoint mutation.
def test_every_mutating_command_rejects_a_terminal_session():
    execution = _source(EXECUTION_PATH)
    service = _source(SERVICE_PATH)

    assert "def reject_terminal(row)" in execution
    checkpoint = execution[execution.index("def record_checkpoint("):
                           execution.index("def _replay_or_conflict(")]
    # Refused up front, and re-checked after a lost race before any conflict is
    # reported -- a terminal session must never look merely "stale".
    assert checkpoint.count("reject_terminal(row)") == 2
    assert "WORKOUT_SESSION_ACTIVE" in execution
    # The adapter's own terminal commands still branch on the canonical states.
    assert "_reject_terminal = reject_terminal" in service
    assert "WORKOUT_SESSION_ABANDONED" in service
    assert "WORKOUT_SESSION_COMPLETED" in service


# 11. Completion uses the canonical completion orchestration.
def test_the_native_completion_writes_no_artifact_of_its_own():
    joined = "".join(_source(path) for path in (ROUTE_PATH,) + PACKAGE_PATHS)

    for forbidden in (
        "PumpCheck(", "WorkoutLog(", "award_xp(", "_claim_quest(",
        "log_activity(", "record_event(", "session.add(", "session.commit(",
        "MobileWorkoutLog",
    ):
        assert forbidden not in joined, forbidden
    assert "complete_session(" in _source(SERVICE_PATH)


def test_no_second_completion_table_exists():
    model = _source(MODEL_PATH)

    assert "MobileWorkoutLog" not in model
    assert "class MobileWorkoutSession" not in model
    # The one canonical completion claim is unchanged.
    assert "uq_pump_check_day" in model


# 12. Completion replay cannot duplicate side effects.
def test_the_revision_precondition_is_evaluated_under_the_session_row_lock():
    completion = _source(COMPLETION_SERVICE_PATH)
    lock_at = completion.index("lock_session_for_completion(")
    precondition_at = completion.index("expected_checkpoint_revision")
    preflight_at = completion.index("already_completed_today(")

    assert lock_at < precondition_at < preflight_at
    assert 'reason="revision"' in completion


def test_the_completion_route_skips_the_proof_when_the_day_is_already_done():
    source = _source(ROUTE_PATH)
    complete = source[source.index("def complete_workout_session("):]

    assert "already_completed_today(" in complete
    assert complete.index("already_completed_today(") < complete.index(
        "_completion_proof()")
    assert "replaying" in complete


# 15. Rollout flags remain unchanged.
def test_pr5_introduces_no_new_flag_and_changes_no_rollout_default():
    from app.feature_flags import ROLLOUT_FLAGS

    registry = {flag.key: flag for flag in ROLLOUT_FLAGS}
    sessions = registry["FITX_WORKOUT_SESSIONS_ENABLED"]

    # PR5 reuses the EXISTING session flag; it mints no second switch and moves
    # no default. A native surface behind a new, separately-defaulted flag would
    # make "are sessions on?" two different questions.
    assert sessions.default is False
    assert registry["MOBILE_AUTH_ENABLED"].default is False
    assert "/api/v1/training/workout-sessions" in sessions.capability
    # Both surfaces read the SAME config key at request time.
    assert "_sessions_enabled" in _source(ROUTE_PATH)
    assert "FITX_WORKOUT_SESSIONS_ENABLED" in _source(
        Path("app/blueprints/mobile_training.py"))


def test_the_flag_gate_is_applied_outside_every_throttle_and_the_ai_gate():
    """Decorator ORDER, not just presence.

    A dark surface that answers 429 or 503 is not absent — it has told the caller
    it exists. ``_flag_gated`` must therefore sit outside the limiter and the
    concurrency gate on every route that has them.
    """
    source = _source(ROUTE_PATH)
    tree = ast.parse(source)
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            names.append(
                target.attr if isinstance(target, ast.Attribute) else
                getattr(target, "id", ""))
        if "_flag_gated" not in names:
            continue
        seen += 1
        gate_at = names.index("_flag_gated")
        for costly in ("limit", "mobile_ai_concurrency_gate"):
            if costly in names:
                assert gate_at < names.index(costly), (node.name, names)
    assert seen == len(ENDPOINTS), seen


def test_every_session_route_is_absent_while_the_flag_is_off(app, monkeypatch):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    user = User(username="flag-off", email="flag-off@example.com",
                cognito_sub="sub-flag-off")
    db.session.add(user)
    db.session.commit()
    user_id = user.id
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            db.session.get(User, user_id), SimpleNamespace(id=1), {"sub": "s"}))
    headers = {"Authorization": "Bearer opaque"}
    client = app.test_client()
    base = "/api/v1/training/workout-sessions"

    responses = [
        client.post(base, headers=headers, json={"workout_ref": "x" * 24}),
        client.get(base + "/current", headers=headers),
        client.post(f"{base}/abc/resume", headers=headers),
        client.put(f"{base}/abc/checkpoint",
                   headers={**headers, "If-Match": "0",
                            "Idempotency-Key": "flag-off-key-01"},
                   json={"checkpoint": {}}),
        client.post(f"{base}/abc/abandon", headers=headers, json={}),
        client.post(f"{base}/abc/complete",
                    headers={**headers, "If-Match": "0",
                             "Idempotency-Key": "flag-off-key-02"}),
    ]

    for response in responses:
        assert response.status_code == 404
    assert WorkoutSession.query.count() == 0
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0


# Migration + schema shape.
def test_the_migration_only_adds_columns_and_keeps_one_alembic_head():
    migration = _source(MIGRATION_PATH)

    assert 'down_revision = "e4f5a6b7c8d9"' in migration
    assert "op.add_column" in migration
    for forbidden in ("op.drop_table", "op.rename_table", "op.alter_column"):
        assert forbidden not in migration
    revisions, parents = set(), set()
    for path in Path("migrations/versions").glob("*.py"):
        for node in ast.walk(ast.parse(_source(path))):
            if not isinstance(node, ast.Assign):
                continue
            names = {getattr(target, "id", "") for target in node.targets}
            value = node.value
            if "down_revision" in names:
                # A merge revision names several parents as a tuple.
                for item in (value.elts if isinstance(value, ast.Tuple)
                             else [value]):
                    if isinstance(item, ast.Constant) and item.value:
                        parents.add(item.value)
                continue
            if not isinstance(value, ast.Constant) or not value.value:
                continue
            if "revision" in names:
                revisions.add(value.value)
    assert "f5a6b7c8d9e0" in revisions
    assert revisions - parents == {"f5a6b7c8d9e0"}, sorted(revisions - parents)


# 13/14. Untouched neighbours.
def test_ci_runs_the_native_session_races_in_the_postgresql_job():
    ci = _source(CI_PATH)
    job = ci[ci.index("mobile-pg-concurrency:"):]

    assert "tests/test_mobile_workout_sessions_pg.py" in job


def test_the_native_surface_logs_no_payload_token_or_identifier():
    joined = "".join(_source(path) for path in (ROUTE_PATH,) + PACKAGE_PATHS)

    for forbidden in (
        "checkpoint_data", "Authorization", "access_credential",
        "parsed.snapshot", "request.get_json()",
    ):
        assert forbidden not in joined or forbidden == "checkpoint_data"
    route = _source(ROUTE_PATH)
    for call in ("logger.error(", "logger.info("):
        for index in _call_offsets(route, call):
            statement = route[index:route.index(")", index)]
            assert "session_ref" not in statement
            assert "checkpoint" not in statement
            assert "g.mobile_user" not in statement


def _call_offsets(source, needle):
    offset = source.find(needle)
    while offset != -1:
        yield offset
        offset = source.find(needle, offset + 1)


def test_the_byte_backstop_admits_the_largest_snapshot_the_bounds_allow():
    """The per-dimension bounds and the byte cap must not contradict each other.

    If the byte cap were the tighter limit, a client running a legitimate
    maximal workout would be told its progress is malformed and would have no
    way to comply -- the request IS within every documented bound. The cap is a
    backstop against a pathological encoding, never a second contract.
    """
    # Import the MODULE by path: the package re-exports a `checkpoint` command
    # of the same name, which would shadow it.
    import importlib

    contract = importlib.import_module(
        "app.services.workout_session.checkpoint")

    worst_case = {
        "current_exercise_index": 0,
        "elapsed_seconds": contract.MAX_ELAPSED_SECONDS,
        "exercises": [
            {
                # Longest exercise identity the persisted column can hold.
                "exercise_id": "e" * 64,
                "sets": [
                    {"index": index, "completed": True,
                     "reps": contract.MAX_REPS,
                     "weight_kg": contract.MAX_WEIGHT_KG}
                    for index in range(contract.MAX_SETS_PER_EXERCISE)
                ],
            }
            for _ in range(contract.MAX_EXERCISES)
        ],
    }
    encoded = contract._canonical_json(worst_case).encode("utf-8")

    assert len(encoded) <= contract.MAX_SNAPSHOT_BYTES, (
        len(encoded), contract.MAX_SNAPSHOT_BYTES)


# Query bounds (section 64).
def test_a_checkpoint_costs_a_bounded_number_of_statements(
    app, monkeypatch, make_user
):
    """One checkpoint must not scale with sets, exercises or history."""
    from sqlalchemy import event
    from app.services import mobile_training

    user = make_user("bounded-checkpoint")
    user_id = user.id
    document = {"program": [
        {"gun": name, "tip": "dinlenme", "odak": "Recovery", "sure_dk": 0,
         "tahmini_kalori": 0, "egzersizler": []}
        for name in WEEKDAYS
    ]}
    document["program"][3] = {
        "gun": "Perşembe", "tip": "antrenman", "odak": "Full body",
        "sure_dk": 45, "tahmini_kalori": 320,
        "egzersizler": [{
            "exercise_id": "ex_barbell_back_squat", "isim": "Squat",
            "set": 3, "tekrar": "8-10", "dinlenme": "90 sn", "not": "",
        }],
    }
    db.session.add(TrainingPlan(
        user_id=user_id, plan_data=json.dumps(document, ensure_ascii=False),
        score=8.0, created_at=datetime(2026, 7, 1, 8, 30),
        lineage_id="bounded-lineage", mutation_version=1))
    db.session.commit()
    reference = mobile_training.workout_ref(
        app.config["SECRET_KEY"], user_id, "bounded-lineage", 1, 3)
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            db.session.get(User, user_id), SimpleNamespace(id=1), {"sub": "s"}))
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    client = app.test_client()
    headers = {"Authorization": "Bearer opaque"}
    base = "/api/v1/training/workout-sessions"
    with audit_clock(FIXED_NOW):
        started = client.post(base, headers=headers, json={"workout_ref": reference})
    session_ref = started.json["session"]["session_ref"]

    def _snapshot(set_count):
        return {
            "current_exercise_index": 0, "elapsed_seconds": 60,
            "exercises": [{"exercise_id": "ex_barbell_back_squat", "sets": [
                {"index": i, "completed": True, "reps": 8, "weight_kg": 60.0}
                for i in range(set_count)
            ]}],
        }

    counts = []
    for revision, set_count in enumerate((1, 20)):
        statements = []
        engine = db.engine

        def _record(conn, cursor, statement, params, context, many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            with audit_clock(FIXED_NOW):
                response = client.put(
                    f"{base}/{session_ref}/checkpoint",
                    headers={**headers, "If-Match": str(revision),
                             "Idempotency-Key": f"bounded-key-0000{revision}"},
                    json={"checkpoint": _snapshot(set_count)})
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        assert response.status_code == 200
        counts.append(len(statements))

    # 1 set and 20 sets cost the SAME number of statements: no per-set query.
    assert counts[0] == counts[1]
    assert counts[0] <= 12, counts
