"""Synthetic approved facts through real D1/domain PostgreSQL boundaries."""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from test_coach_publication_transport_pg import pg_generation_app, server_rig  # noqa: F401
from app.extensions import db
from app.services import ai_consent as consent, ai_coach, ai_spend_guard, memory_manager
from app.models import PendingAction, MealLog, WorkoutLog, CoachConversation, CoachMessage

pytestmark = pytest.mark.pg_concurrency


def authority(r):
    return consent.begin_operation(r.owners[0], "coach", *r.policy.recipients[0])


def test_status_exact_text_and_direct_grant_require_both_published_locales(server_rig):
    from app.services import ai_consent_disclosures as disclosures
    r = server_rig
    with r.app.app_context():
        projected, = consent.status(r.owners[0])["available_disclosures"]
        assert set(projected["localized_texts"]) == {"en", "tr"}
        r.mutate()
        r.patch.setattr(disclosures, "PUBLISHED_TEXTS", {
            (r.policy.version, "en"): projected["localized_texts"]["en"]})
        assert consent.status(r.owners[0])["available_disclosures"] == []
        with pytest.raises(consent.ConsentDenied) as denied:
            r.mutate("affirmative_grant")
        assert denied.value.code == "AI_CONSENT_DISCLOSURE_UNAVAILABLE"
        assert consent.generation(r.owners[0]) == 2


def stage(r, kind):
    if kind == "meal":
        r.patch.setattr(ai_coach, "_coach_search_food", lambda _: [
            {"name": "Synthetic food", "per_100g": {"calories": 100}}])
        return ai_coach._tool_fetch_nutrition_and_stage_log(r.owners[0], "100g synthetic food")
    return ai_coach._tool_stage_workout_log(r.owners[0], "Synthetic squat", 3, 10, 20)


@pytest.mark.parametrize("kind", ["meal", "workout"])
def test_original_pending_operation_stays_revoked_after_regrant(server_rig, kind):
    r = server_rig
    with r.app.app_context(), ai_spend_guard.subject_scope(r.owners[0]):
        original = authority(r)
        with consent.operation_scope(original):
            result = json.loads(stage(r, kind))
            assert result["status"] == "staged"
            assert consent.PAYLOAD_AUTHORITY_KEY not in json.dumps(result)
        pending = PendingAction.query.one()
        assert pending.payload[consent.PAYLOAD_AUTHORITY_KEY] == original.to_context()
        db.session.rollback()
        r.mutate()
        r.mutate("affirmative_grant")
        from flask import g
        g._coach_staged_ids = set()  # confirming user turn
        with consent.operation_scope(authority(r)):
            tool = (ai_coach._tool_confirm_and_commit_meal_log if kind == "meal"
                    else ai_coach._tool_confirm_and_commit_workout_log)
            with pytest.raises(consent.ConsentDenied):
                tool(r.owners[0])
            db.session.rollback()
        assert PendingAction.query.count() == 1
        assert MealLog.query.count() == WorkoutLog.query.count() == 0


@pytest.mark.parametrize("kind", ["meal", "workout"])
def test_allowed_pending_confirmation_commits_once(server_rig, kind):
    r = server_rig
    with r.app.app_context(), ai_spend_guard.subject_scope(r.owners[0]):
        with consent.operation_scope(authority(r)):
            assert json.loads(stage(r, kind))["status"] == "staged"
        # A new Flask app context represents the confirming user turn.
    with r.app.app_context(), ai_spend_guard.subject_scope(r.owners[0]):
        with consent.operation_scope(authority(r)):
            tool = (ai_coach._tool_confirm_and_commit_meal_log if kind == "meal"
                    else ai_coach._tool_confirm_and_commit_workout_log)
            result = json.loads(tool(r.owners[0]))
            assert result["status"] == "committed"
            assert consent.PAYLOAD_AUTHORITY_KEY not in json.dumps(result)
            assert json.loads(tool(r.owners[0]))["status"] == "no_pending"
        assert PendingAction.query.count() == 0
        assert (MealLog.query.count(), WorkoutLog.query.count()) == ((1, 0) if kind == "meal" else (0, 1))


def test_revoked_operation_cannot_create_conversation_or_turn(server_rig):
    r = server_rig
    with r.app.app_context(), ai_spend_guard.subject_scope(r.owners[0]):
        original = authority(r)
        r.mutate()
        with consent.operation_scope(original):
            with pytest.raises(consent.ConsentDenied):
                memory_manager.get_or_create_active_conversation(r.owners[0])
            db.session.rollback()
        assert CoachConversation.query.count() == CoachMessage.query.count() == 0


def test_account_switch_denies_tool_and_conversation_writes(server_rig):
    r = server_rig
    with r.app.app_context():
        original = authority(r)
        with consent.operation_scope(original), ai_spend_guard.subject_scope(r.owners[1]):
            with pytest.raises(consent.ConsentDenied):
                stage(r, "workout")
            db.session.rollback()
            with pytest.raises(consent.ConsentDenied):
                memory_manager.get_or_create_active_conversation(r.owners[0])
            db.session.rollback()
        assert PendingAction.query.count() == CoachConversation.query.count() == 0


@pytest.mark.parametrize("provider", ["bedrock", "openai"])
@pytest.mark.parametrize("round_number", [1, 2])
def test_provider_loop_never_softens_typed_denial(server_rig, provider, round_number):
    r = server_rig
    attempts = []
    denied = consent.ConsentDenied("AI_CONSENT_CHANGED", 409)

    @contextmanager
    def admit(**kwargs):
        attempts.append(kwargs["tool_round"])
        if len(attempts) == round_number:
            raise denied
        if provider == "bedrock":
            response = SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(
                type="tool_use", name="query_fitx_metrics", input={}, id="synthetic-tool")])
        else:
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="", tool_calls=[SimpleNamespace(id="synthetic-tool", function=SimpleNamespace(
                    name="query_fitx_metrics", arguments="{}"))]))])
        yield SimpleNamespace(create=lambda *a, **kw: response)

    r.patch.setattr(ai_coach.ai_provider_call, "admit", admit)
    r.patch.setattr(ai_coach, "_dispatch_coach_tool", lambda *a: '{"status":"ok"}')
    r.patch.setattr(ai_coach.coach_confirmation, "reply_after_tools", lambda *a: None)
    function = getattr(ai_coach, "_run_coach_conversation_" + provider)
    with r.app.app_context():
        with pytest.raises(consent.ConsentDenied) as actual:
            function(r.owners[0], "synthetic question", {}, [], language="en")
        assert actual.value is denied
        assert attempts == list(range(1, round_number + 1))
