"""PRIV-K6 rollback gate: application contract of the completion-authority fence.

SQLite and static checks only. The fence itself is PostgreSQL triggers and is
qualified on real PostgreSQL in ``tests/test_completion_fence_pg.py``; here we
pin what the application and the release tooling must agree on:

* one capability token, one fence revision, one marker literal, one lock key
  across the migration, the app package, the release engine and the manifest;
* the closed SQLSTATE vocabulary (every code the SQL raises is classified, and
  nothing else is) and its retry decisions;
* a fence refusal is a typed, fully rolled-back failure on every entry path,
  retryable ONLY when a retry can succeed;
* the manifest declares the capability only while the tree implements it.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, TrainingPlan, WorkoutCompletionDay
from app.services import completion_fence
from app.services.workout_completion import (
    CompleteWorkoutCommand, CompletionFenceRefused, complete_workout,
)
from app.services.workout_completion import service as completion_service
from tests.test_mobile_workout_sessions_api import (  # noqa: F401 - fixtures
    as_mobile, completion_proof, lifecycle_events, owner, plan, sessions_enabled,
    stranger, workout_ref,
)
from tests.test_priv_k6_no_photo_completion import NOTHING, _complete_raw, _rows, _started

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations/versions/d7e3a9c1f5b8_completion_authority_fence.py"
MANIFEST = ROOT / "release/completion-capabilities"


def _migration():
    spec = importlib.util.spec_from_file_location("fence_migration_under_test", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _r6():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import r6_deploy_transaction
    finally:
        sys.path.pop(0)
    return r6_deploy_transaction


class _Orig(Exception):
    def __init__(self, pgcode):
        super().__init__(f"fence {pgcode}")
        self.pgcode = pgcode


def _fence_error(code):
    return sa.exc.DBAPIError("COMMIT", None, _Orig(code))


# ── one contract across migration, app, release engine, manifest ───────────

def test_capability_constants_agree_everywhere():
    migration, r6 = _migration(), _r6()
    assert migration.revision == completion_fence.FENCE_REVISION == "d7e3a9c1f5b8"
    assert migration.CAPABILITY == completion_fence.CAPABILITY == r6.CAPABILITY
    assert r6.CAPABILITY_IMPLEMENTATION == {r6.CAPABILITY: migration.revision}
    assert MANIFEST.read_text(encoding="utf-8") == f"{r6.CAPABILITY}\n"
    assert r6.parse_capability_manifest(MANIFEST.read_text(encoding="utf-8")) == {r6.CAPABILITY}


def test_floor_lock_key_is_the_derived_domain_key_everywhere():
    digest = hashlib.sha256(b"fitx:completion-capability-floor:v1\0").digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    migration = _migration()
    assert key == migration.FLOOR_LOCK_KEY == completion_fence.FLOOR_LOCK_KEY
    assert (migration.FLOOR_LOCK_CLASSID, migration.FLOOR_LOCK_OBJID) == (
        (key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF)
    bodies = "".join(migration.FUNCTIONS.values())
    assert bodies.count(f"pg_advisory_xact_lock_shared({key})") == 3
    assert f"l.classid = {migration.FLOOR_LOCK_CLASSID}::oid" in bodies
    assert f"l.objid = {migration.FLOOR_LOCK_OBJID}::oid" in bodies


def test_marker_literal_in_the_fence_is_the_application_marker():
    migration = _migration()
    assert migration.MARKER == WORKOUT_COMPLETION_MARKER
    marker = next(stmt for name, _, stmt in migration.TRIGGERS if name == "completion_fence_marker")
    assert f"NEW.exercise_name = '{WORKOUT_COMPLETION_MARKER}'" in marker
    assert f"'{WORKOUT_COMPLETION_MARKER}'::text" in migration.TRIGGER_DEFS["completion_fence_marker"]


def test_every_sqlstate_the_fence_raises_is_classified_and_nothing_else():
    migration = _migration()
    raised = set(re.findall(r"ERRCODE = '([A-Z0-9]{5})'", "".join(migration.FUNCTIONS.values())))
    assert raised == set(completion_fence.REFUSALS)
    assert {code: r.retryable for code, r in completion_fence.REFUSALS.items()} == {
        "AXF01": True, "AXF02": False, "AXF03": False,
        "AXF04": True, "AXF05": False, "AXF06": False}
    assert completion_fence.refusal_of(_fence_error("23505")) is None
    assert completion_fence.refusal_of(RuntimeError("x")) is None
    assert completion_fence.refusal_of(_fence_error("AXF02")).reason == "writer_below_floor"


def test_every_fence_function_pins_search_path_and_qualifies_relations():
    migration = _migration()
    relation = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_.\"]+)", re.IGNORECASE)
    for name, body in migration.FUNCTIONS.items():
        for target in relation.findall(body):
            assert target.startswith(("public.", "pg_catalog.")), (name, target)
    assert migration.FUNCTION_CONFIG == ["search_path=pg_catalog, pg_temp"]
    assert all(stmt.split(" ON ")[1].startswith("public.") or " ON public." in stmt
               for _, _, stmt in migration.TRIGGERS)


def test_manifest_declares_the_capability_only_with_its_implementation():
    """The token is a reviewed declaration; it must never outlive the code that
    makes it true (fence migration, claim writer, union reader, floor tooling)."""
    service = (ROOT / "app/services/workout_completion/service.py").read_text(encoding="utf-8")
    queries = (ROOT / "app/services/workout_completion/queries.py").read_text(encoding="utf-8")
    tree = ast.parse(service)
    constructs_claim = any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) == "WorkoutCompletionDay"
        for node in ast.walk(tree))
    assert MANIFEST.exists()
    assert MIGRATION_PATH.exists()
    assert constructs_claim
    assert "union(claimed, historical)" in queries
    assert (ROOT / "app/services/completion_fence/__init__.py").exists()


def test_reviewed_ddl_pin_matches_the_fence_migration():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import migration_expand_contract as mec
    finally:
        sys.path.pop(0)
    assert mec.REVIEWED_DDL == {
        "d7e3a9c1f5b8": mec.normalized_digest(MIGRATION_PATH.read_bytes())}
    result = mec.scan()
    assert result["classes"]["d7e3a9c1f5b8"] == mec.EXPAND
    assert "d7e3a9c1f5b8_completion_authority_fence.py" not in result["violations"]


def test_any_edit_to_the_reviewed_fence_fails_the_gate(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import migration_expand_contract as mec
    finally:
        sys.path.pop(0)
    versions = tmp_path / "versions"
    versions.mkdir()
    for path in (ROOT / "migrations/versions").glob("*.py"):
        versions.joinpath(path.name).write_bytes(path.read_bytes())
    target = versions / MIGRATION_PATH.name
    target.write_text(target.read_text(encoding="utf-8").replace(
        "USING ERRCODE = 'AXF02'", "USING ERRCODE = 'AXF02' -- edited"), encoding="utf-8")
    assert MIGRATION_PATH.name in mec.scan(versions_dir=versions)["violations"]


# ── typed refusal: service and entry paths ──────────────────────────────────

@pytest.mark.parametrize("code,reason,retryable", [
    ("AXF01", "activation_pending", True), ("AXF04", "historical_conflict", True),
    ("AXF02", "writer_below_floor", False), ("AXF06", "isolation_unsupported", False),
])
def test_service_turns_a_fence_refusal_into_a_typed_rolled_back_failure(
        app, make_user, monkeypatch, code, reason, retryable):
    from app.timeutil import app_today
    user = make_user(f"fence_{code.lower()}")

    def refuse(*args, **kwargs):
        raise _fence_error(code)

    monkeypatch.setattr(completion_service, "log_activity", refuse)
    with pytest.raises(CompletionFenceRefused) as caught:
        complete_workout(CompleteWorkoutCommand(
            user_id=user.id, today=app_today(), image_key=None, location_type="gym",
            description="fence", valid=True, fallback=False, base_xp=10,
            photo_bonus=25, entry_path="route"))
    assert (caught.value.reason, caught.value.retryable) == (reason, retryable)
    assert WorkoutCompletionDay.query.filter_by(user_id=user.id).count() == 0


@pytest.mark.parametrize("code,public,retryable,resolution", [
    ("AXF01", "TRAINING_SESSION_UNAVAILABLE", True, "retry"),
    ("AXF04", "TRAINING_SESSION_UNAVAILABLE", True, "retry"),
    ("AXF02", "TRAINING_COMPLETION_UNAVAILABLE", False, "terminal"),
    ("AXF05", "TRAINING_COMPLETION_UNAVAILABLE", False, "terminal"),
])
def test_native_completion_maps_a_fence_refusal_without_a_retry_loop(
        client, owner, as_mobile, workout_ref, monkeypatch, code, public, retryable,
        resolution):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    def refuse(*args, **kwargs):
        raise _fence_error(code)

    monkeypatch.setattr(completion_service, "log_activity", refuse)
    response = _complete_raw(client, headers, ref)

    assert response.status_code == 503
    assert response.json["error"]["code"] == public
    assert response.json["error"]["retryable"] is retryable
    assert response.headers["Session-Resolution"] == resolution
    assert _rows(owner.id) == NOTHING


@pytest.mark.parametrize("code,retryable", [("AXF01", True), ("AXF02", False)])
def test_browser_completion_maps_a_fence_refusal(app, client, auth_user, monkeypatch,
                                                 code, retryable):
    from app.blueprints import training as training_bp
    db.session.add(TrainingPlan(user_id=auth_user.id, plan_data='{"v": 1}', score=7.0))
    db.session.commit()
    monkeypatch.setattr(training_bp, "validate_pump_check_image",
                        lambda *a, **k: (b"jpeg", "image/jpeg", None))
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a, **k: {"valid": True, "fallback": False})
    monkeypatch.setattr("app.blueprints.ai_consent.require_legacy_ai", lambda: None)

    def refuse(*args, **kwargs):
        raise _fence_error(code)

    monkeypatch.setattr(completion_service, "log_activity", refuse)
    response = client.post("/workout/complete", json={"image": "x", "location_type": "salon"})

    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "completion_unavailable" and body["retryable"] is retryable
    assert ("Retry-After" in response.headers) is retryable
    assert WorkoutCompletionDay.query.filter_by(user_id=auth_user.id).count() == 0


@pytest.mark.parametrize("code,retryable", [("AXF01", True), ("AXF02", False)])
def test_ai_coach_tool_reports_a_fence_refusal_without_awarding(app, make_user, monkeypatch,
                                                                code, retryable):
    import s3_helper
    from app.services import ai_coach
    user = make_user(f"fence_tool_{code.lower()}")
    key = f"pump-checks/{user.id}/2026/06/x.jpg"
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(s3_helper, "get_object_bytes", lambda k, expected_user_id=None: b"jpeg")
    monkeypatch.setattr(ai_coach, "_bedrock_validate_image",
                        lambda *a, **k: '{"is_gym": true, "form_rating": 7, "reason": "salon"}')

    def refuse(*args, **kwargs):
        raise _fence_error(code)

    monkeypatch.setattr(completion_service, "log_activity", refuse)
    answer = json.loads(ai_coach._tool_analyze_gym_photo(user.id, key))
    assert answer["status"] == "error" and answer["awarded"] is False
    assert answer["retryable"] is retryable
