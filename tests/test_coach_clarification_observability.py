import logging
import time

import pytest
from flask import session as flask_session

from app.observability import assign_request_id, current_request_id
from app.services.coach_plan_tools import clarifications
from app.services.coach_plan_tools import grounding
from app.services.coach_plan_tools.schemas import ADD_EXERCISE_TOOL


def _record(**overrides):
    record = {
        "operation": "add_exercise",
        "request_id": "clarification-lineage-123",
        "day": "Cuma",
        "exercise": "Sensitive Exercise Name",
        "sets": None,
        "reps": "15",
        "reason": "missing_sets",
    }
    record.update(overrides)
    return record


def _events(caplog):
    return [
        item.getMessage() for item in caplog.records
        if "component=coach_clarification" in item.getMessage()
    ]


def test_initial_memory_write_emits_created_lifecycle_event(
        app, monkeypatch, caplog):
    """Removing initial-write emission must make this test fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        correlation_id = current_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())

    assert _events(caplog) == [
        "component=coach_clarification event=created reason=initial_write "
        "operation=add request_id=clarification-lineage-123 backend=memory "
        "missing_fields=sets record_present_before=false "
        "record_present_after=true ttl_before=null ttl_after=1800 "
        f"correlation_id={correlation_id}"
    ]


def test_same_lineage_memory_write_emits_monotonic_update(
        app, monkeypatch, caplog):
    """Treating an overwrite as another creation must fail this test."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        correlation_id = current_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record(sets=4, reps=None,
                                            reason="missing_reps"))
        clarifications.remember(41, _record(sets=4, reps="15",
                                            reason="ambiguous_workout"))

    assert _events(caplog)[-1] == (
        "component=coach_clarification event=updated "
        "reason=monotonic_merge operation=add "
        "request_id=clarification-lineage-123 backend=memory "
        "missing_fields=day record_present_before=true "
        "record_present_after=true ttl_before=1800 ttl_after=1800 "
        f"correlation_id={correlation_id}"
    )


def test_incompatible_request_emits_superseded_event(
        app, monkeypatch, caplog):
    """A silent request-boundary supersession must fail this test."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        grounding.supersede_stale_clarification(41, ADD_EXERCISE_TOOL, {
            "exercise": "Different Exercise",
        })

    assert "event=superseded reason=incompatible_request" in _events(caplog)[-1]
    assert "request_id=clarification-lineage-123" in _events(caplog)[-1]


@pytest.mark.parametrize(("message", "reason"), [
    ("Start a different request", "request_boundary_new_exercise"),
    (".", "request_boundary_noncontinuation"),
])
def test_request_start_retirement_names_the_exact_refresh_branch(
        app, monkeypatch, caplog, message, reason):
    """Collapsing distinct refresh branches into one reason must fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        grounding.refresh_clarification_for_turn(message, user_id=41)

    assert f"event=superseded reason={reason}" in _events(caplog)[-1]


def test_missing_day_weekday_survives_request_boundary(
        app, monkeypatch, caplog):
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record(
            day="", sets=4, reps="15", reason="ambiguous_workout",
            candidate_days=("Pazartesi", "Cuma")))
        grounding.refresh_clarification_for_turn("Monday", user_id=41)
        stored = clarifications.load(41)

    assert stored is not None
    assert stored["day"] == ""
    assert "event=superseded" not in "\n".join(_events(caplog))


@pytest.mark.parametrize("message", ("4", "6"))
def test_record_valid_bare_set_value_precedes_numeric_exercise_false_positive(
        app, monkeypatch, caplog, message):
    original_exercise_from_text = grounding._exercise_from_text

    def numeric_false_positive(text):
        if isinstance(text, str) and text.isascii() and text.isdecimal():
            return text
        return original_exercise_from_text(text)

    monkeypatch.setattr(grounding, "_exercise_from_text", numeric_false_positive)
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        grounding.refresh_clarification_for_turn(message, user_id=41)
        stored = clarifications.load(41)

    assert stored is not None
    assert stored["reps"] == "15"
    assert "event=superseded" not in "\n".join(_events(caplog))


def test_real_new_exercise_request_still_supersedes_active_clarification(
        app, monkeypatch, caplog):
    """Disabling request-boundary supersession must make this test fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        grounding.refresh_clarification_for_turn(
            "Add Hammer Curl 3x10 to my chest workout", user_id=41)
        stored = clarifications.load(41)

    assert stored is None
    assert (
        "event=superseded reason=request_boundary_new_exercise"
        in "\n".join(_events(caplog)))


def test_consume_once_emits_consumed_event(app, monkeypatch, caplog):
    """Removing consume-once emission must make this test fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        clarifications.consume(41)

    assert "event=consumed reason=continuation_consume" in _events(caplog)[-1]
    assert "record_present_before=true record_present_after=false" in _events(caplog)[-1]


def test_explicit_clear_emits_deleted_event(app, monkeypatch, caplog):
    """A silent explicit retirement must make this test fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        clarifications.clear(41)

    assert "event=deleted reason=explicit_retirement" in _events(caplog)[-1]


@pytest.mark.parametrize(("stored", "reason"), [
    (None, "record_missing"),
    (_record(created_at=1.0), "ttl_expired"),
])
def test_missing_or_expired_lookup_emits_bounded_event(
        app, monkeypatch, caplog, stored, reason):
    """A missing or stale authority lookup without a reason must fail."""
    memory = {} if stored is None else {41: dict(stored, user_id=41)}
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", memory)
    if stored is not None:
        monkeypatch.setattr(clarifications.time, "time", lambda: 999999.0)
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        assert clarifications.load(41) is None

    assert f"event=expired_or_missing reason={reason}" in _events(caplog)[-1]
    assert "backend=memory" in _events(caplog)[-1]


def test_missing_authority_uses_session_mirror_only_for_bounded_lineage(
        app, monkeypatch, caplog):
    """Losing lineage on an authority miss must make this test fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        flask_session[clarifications._KEY] = dict(
            _record(), user_id=41, created_at=time.time())
        caplog.set_level(logging.INFO, logger="app")
        assert clarifications.load(41) is None

    event = _events(caplog)[-1]
    assert "event=expired_or_missing reason=record_missing" in event
    assert "request_id=clarification-lineage-123" in event


class _FailingRedis:
    def get(self, _key):
        raise RuntimeError("secret redis failure")


class _Redis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl
        return True

    def ttl(self, key):
        return self.ttls.get(key, -2)

    def delete(self, key):
        existed = key in self.store
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)

    def getdel(self, key):
        value = self.store.pop(key, None)
        self.ttls.pop(key, None)
        return value


def test_redis_unavailable_lookup_emits_rejected_then_fails_closed(
        app, monkeypatch, caplog):
    """A Redis fail-closed lookup without a bounded event must fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: _FailingRedis())
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        with pytest.raises(clarifications.ClarificationAuthorityUnavailable):
            clarifications.load(41)

    assert "event=rejected reason=redis_unavailable" in _events(caplog)[-1]
    assert "backend=redis" in _events(caplog)[-1]
    assert "secret redis failure" not in _events(caplog)[-1]


def test_redis_setex_and_getdel_emit_authoritative_transitions(
        app, monkeypatch, caplog):
    """Dropping Redis write or GETDEL emission must make this test fail."""
    redis = _Redis()
    monkeypatch.setattr(clarifications, "_redis", lambda: redis)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        assert clarifications.consume(41)["request_id"] == (
            "clarification-lineage-123")

    created, consumed = _events(caplog)
    assert "event=created reason=initial_write" in created
    assert "backend=redis" in created
    assert "ttl_after=1800" in created
    assert "event=consumed reason=continuation_consume" in consumed
    assert "backend=redis" in consumed
    assert "record_present_before=true record_present_after=false" in consumed


def test_lifecycle_events_never_leak_sensitive_payload(
        app, monkeypatch, caplog):
    """Adding any raw clarification payload to the event must fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record(sets=4, reps="15"))
        clarifications.consume(41)

    emitted = "\n".join(_events(caplog))
    assert "Sensitive Exercise Name" not in emitted
    assert "Cuma" not in emitted
    assert "reps=15" not in emitted
    assert "sets=4" not in emitted
    assert "user_id=" not in emitted
    assert "fitx:coach:plan_clarification:" not in emitted


def test_consumed_identity_mismatch_emits_rejected_event(
        app, monkeypatch, caplog):
    """Silencing the post-consume identity rejection must fail this test."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.reject(
            dict(_record(), user_id=41), "authoritative_record_mismatch")

    assert "event=rejected reason=authoritative_record_mismatch" in _events(caplog)[-1]
    assert "request_id=clarification-lineage-123" in _events(caplog)[-1]


def test_lifecycle_reason_is_closed_even_if_a_caller_passes_raw_text(
        app, monkeypatch, caplog):
    """Trusting a caller-supplied reason string must make this test fail."""
    monkeypatch.setattr(clarifications, "_redis", lambda: None)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        caplog.set_level(logging.INFO, logger="app")
        clarifications.remember(41, _record())
        clarifications.clear(41, reason="raw user text must never appear")

    event = _events(caplog)[-1]
    assert "reason=invalid_lifecycle_reason" in event
    assert "raw user text must never appear" not in event
