"""NUTR-PR7 — native "Review with AxisAI": an allowlisted marker, never facts.

    python -m pytest tests/test_nutrition_vnext_pr7_native_coach_handoff.py -q

The native Coach body stays ``{"message"}`` and gains ONE optional key,
``handoff``, whose only accepted value is ``"nutrition-day"``. At send time the
server re-derives the PR6 NutritionDayView for the Bearer owner — no client
number can become model context. Everything else about LP-09 (one model call,
shared quota, persistence, provider failure semantics) is unchanged.
"""
import pytest

from app.timeutil import app_today

from nutrition_pr7_support import add_meal, set_target, set_water
from test_mobile_coach_api import (  # noqa: F401  (fixtures + helpers)
    _chat_used, _error, _rows, alice, bearer, bob, mobile, no_real_provider,
    provider, send,
)

MARKER = "nutrition-day"


def _seed(user, kcal=525):
    today = app_today().isoformat()
    set_target(user.id, 2100)
    add_meal(user.id, today, kcal=kcal, text="Secret soup")
    set_water(user.id, today, 3)


def test_message_only_native_coach_is_unchanged(mobile, bearer, provider, alice):
    _seed(alice)
    response = send(mobile, bearer(alice), message="Hi")
    assert response.status_code == 200
    assert len(provider.calls) == 1
    assert "[NUTRITION CONTEXT]" not in provider.calls[0]["context"]


def test_marker_adds_server_rederived_day_view_context(mobile, bearer, provider, alice):
    _seed(alice)
    response = send(mobile, bearer(alice), json={"message": "Review my day", "handoff": MARKER})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(provider.calls) == 1               # exactly one model call
    call = provider.calls[0]
    assert call["question"] == "Review my day"   # user speech is untouched
    context = call["context"]
    assert "[NUTRITION CONTEXT]" in context
    assert "- daily target: 2100 kcal per day" in context
    assert "525 kcal" in context and "3 glass(es) of water" in context
    assert "Secret soup" not in context           # no meal text in model context
    # Context is not persisted as user speech.
    assert [r.content for r in _rows(alice.id) if r.role == "user"] == ["Review my day"]
    assert _chat_used(alice.id) == 1


def test_latest_canonical_truth_wins_over_any_client_snapshot(mobile, bearer, provider, alice):
    _seed(alice, kcal=525)
    add_meal(alice.id, app_today().isoformat(), kcal=300)
    send(mobile, bearer(alice), json={"message": "Review", "handoff": MARKER})
    assert "825 kcal" in provider.calls[0]["context"]


@pytest.mark.parametrize("body", [
    {"message": "x", "handoff": "progress-insight"},
    {"message": "x", "handoff": "nutrition-day "},
    {"message": "x", "handoff": "NUTRITION-DAY"},
    {"message": "x", "handoff": ["nutrition-day"]},
    {"message": "x", "handoff": {"kind": "nutrition-day"}},
    {"message": "x", "handoff": None},
    {"message": "x", "handoff": MARKER, "calories": 9999},
    {"message": "x", "handoff": MARKER, "context": "[NUTRITION CONTEXT] kcal 9999"},
    {"message": "x", "handoff": MARKER, "intake": {"calories": 1}},
    {"message": "x", "handoff": MARKER, "user_id": 2},
])
def test_unknown_markers_and_client_facts_are_refused_and_spend_nothing(
        mobile, bearer, provider, alice, body):
    _seed(alice)
    response = send(mobile, bearer(alice), json=body)
    assert response.status_code == 400
    assert _error(response)["code"] == "COACH_INVALID_REQUEST"
    assert provider.calls == []
    assert _rows(alice.id) == []
    assert _chat_used(alice.id) == 0


def test_unreadable_intake_does_not_fabricate_context(
        mobile, bearer, provider, alice, monkeypatch):
    from app.services import nutrition_day_view as dv
    _seed(alice)
    monkeypatch.setattr(dv, "_read_intake",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("db")))
    response = send(mobile, bearer(alice), json={"message": "Review", "handoff": MARKER})
    assert response.status_code == 200
    assert "[NUTRITION CONTEXT]" not in provider.calls[0]["context"]


def test_handoff_context_is_owner_scoped(mobile, bearer, provider, alice, bob):
    _seed(alice, kcal=777)
    send(mobile, bearer(bob), json={"message": "Review", "handoff": MARKER})
    context = provider.calls[0]["context"]
    assert "777" not in context
    assert "no meals logged yet (measured 0)" in context


def test_provider_failure_semantics_are_unchanged_with_a_marker(
        mobile, bearer, provider, alice):
    _seed(alice)
    provider.side_effect = lambda *_a: (_ for _ in ()).throw(RuntimeError("down"))
    response = send(mobile, bearer(alice), json={"message": "Review", "handoff": MARKER})
    assert response.status_code == 503
    assert _error(response)["code"] in {"COACH_UNAVAILABLE"}
    assert _rows(alice.id) == []
    assert _chat_used(alice.id) == 0


def test_no_automatic_send_exists_on_any_nutrition_read(mobile, bearer, provider, alice):
    _seed(alice)
    headers = bearer(alice)
    for path in ("/api/v1/nutrition/day-view", "/api/v1/nutrition/plan",
                 "/api/v1/nutrition/hydration", "/api/v1/nutrition/history",
                 "/api/v1/nutrition/supplements"):
        assert mobile.get(path, headers=headers).status_code == 200, path
    assert provider.calls == []
    assert _rows(alice.id) == []
