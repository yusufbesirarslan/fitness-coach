"""MON-02-BE-03B-A1: the pre-attempt evidence door and the settlement mapping.

SQLite unit level. The real D1 fence, crash boundaries and races run on
PostgreSQL in tests/test_training_allowance_zero_attempt_pg.py.
"""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from app.services import ai_allowances, ai_consent, ai_provider_call, ai_spend_guard
from app.services.mobile_training_generation import allowance
from app.services.training_generation.preference_contract import (
    CODE_GENERATION_PARSE_FAILED, CODE_GENERATION_SCHEMA_INVALID, CODE_GENERATION_UNAVAILABLE,
)

PAYLOAD = {"model": "synthetic", "max_tokens": 10,
           "messages": [{"role": "user", "content": "SYNTHETIC"}]}


@pytest.fixture
def door(monkeypatch):
    """A recording stand-in for the D1 fence; everything else is the real door."""
    events = []

    @contextmanager
    def dispatch(owner, feature, recipient, payload, generation):
        events.append("consent")
        if events.count("consent") in door.deny_on:
            raise ai_consent.ConsentDenied("AI_CONSENT_REQUIRED")
        yield
        events.append("fence_released")

    door = SimpleNamespace(events=events, deny_on=set())
    monkeypatch.setattr(ai_consent, "generation", lambda owner: 0)
    monkeypatch.setattr(ai_consent, "dispatch", dispatch)
    monkeypatch.setattr(ai_provider_call.time, "sleep", lambda _: None)
    return door


def _call(sdk, observer=None):
    scope = ai_provider_call.attempt_observer(observer) if observer else nullcontext()
    with ai_spend_guard.subject_scope(7), scope:
        with ai_provider_call.admit(feature="training_plan", provider="openai",
                                    payload=PAYLOAD, gate=False, charge=False) as call:
            return call.create(sdk)


def test_observer_runs_inside_the_fence_after_consent_and_before_every_sdk_call(door):
    class ServerError(Exception):
        status_code = 500
    outcomes = iter([ServerError(), "ok"])

    def sdk(**kwargs):
        door.events.append("sdk")
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(usage=None)
    _call(sdk, observer=lambda: door.events.append("evidence"))
    assert door.events == ["consent", "evidence", "sdk", "consent", "evidence", "sdk", "fence_released"]


def test_consent_refusal_runs_no_observer_and_no_sdk(door):
    door.deny_on = {1}
    sdk = []
    with pytest.raises(ai_consent.ConsentDenied):
        _call(lambda **kw: sdk.append(1), observer=lambda: door.events.append("evidence"))
    assert door.events == ["consent"] and sdk == []


@pytest.mark.parametrize("failure", [RuntimeError("db down"), ai_provider_call.AttemptEvidenceUnavailable()])
def test_unrecorded_evidence_never_sends_and_is_a_non_retryable_refusal(door, failure):
    sdk = []

    def observer():
        raise failure
    with pytest.raises(ai_provider_call.AttemptEvidenceUnavailable) as refused:
        _call(lambda **kw: sdk.append(1), observer=observer)
    assert isinstance(refused.value, ai_spend_guard.AISpendLimitExceeded)  # router never falls back
    assert sdk == [] and door.events == ["consent"]


def test_without_an_observer_the_door_is_unchanged(door):
    sdk = []
    _call(lambda **kw: sdk.append(1) or SimpleNamespace(usage=None))
    assert sdk == [1] and door.events == ["consent", "fence_released"]


def test_stream_records_evidence_before_opening_the_stream(monkeypatch):
    events = []
    monkeypatch.setattr(ai_consent, "generation", lambda owner: 0)
    monkeypatch.setattr(ai_consent, "stream_dispatch", lambda *a, **k: nullcontext())

    @contextmanager
    def stream(**kwargs):
        events.append("stream")
        yield SimpleNamespace(get_final_message=lambda: SimpleNamespace(usage=None))
    with ai_spend_guard.subject_scope(7), ai_provider_call.attempt_observer(lambda: events.append("evidence")):
        with ai_provider_call.admit(feature="training_plan", provider="openai",
                                    payload=PAYLOAD, gate=False, charge=False) as call:
            with call.stream(stream) as live:
                live.get_final_message()
    assert events == ["evidence", "stream"]


def _operation(status, marker, code=None):
    return SimpleNamespace(status=status, provider_attempt_possible=marker, error_code=code)


@pytest.mark.parametrize("status,marker,code,expected", [
    ("SUCCEEDED", True, None, ("success", "UNKNOWN")),
    ("SUCCEEDED", False, None, ("success", "UNKNOWN")),  # unchanged: success consumes
    ("FAILED", False, CODE_GENERATION_UNAVAILABLE, ("refused_before_provider", "CONFIRMED_NOT_BILLED")),
    ("FAILED", False, "TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN",
     ("refused_before_provider", "CONFIRMED_NOT_BILLED")),
    ("FAILED", False, CODE_GENERATION_SCHEMA_INVALID, ("invalid_output", "CONFIRMED_NOT_BILLED")),
    ("FAILED", True, CODE_GENERATION_UNAVAILABLE, ("provider_error", "UNKNOWN")),
    ("FAILED", True, CODE_GENERATION_PARSE_FAILED, ("invalid_output", "UNKNOWN")),
    ("FAILED", True, "TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN", ("provider_timeout", "UNKNOWN")),
    # NULL = dispatched by code without A1 evidence: never treated as zero attempts.
    ("FAILED", None, CODE_GENERATION_UNAVAILABLE, ("provider_error", "UNKNOWN")),
    ("FAILED", None, "TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN", ("provider_timeout", "UNKNOWN")),
])
def test_settlement_mapping_uses_only_durable_evidence(status, marker, code, expected):
    outcome, evidence = allowance._settlement(_operation(status, marker, code))
    assert (outcome, evidence) == expected
    assert outcome == "success" or outcome in ai_allowances.FAILURE_OUTCOMES
    if evidence == ai_allowances.BILLING_CONFIRMED_NOT_BILLED:
        assert marker is False
