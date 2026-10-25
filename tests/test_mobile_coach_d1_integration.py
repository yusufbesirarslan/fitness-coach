"""Native admission/refusal composition; synthetic provider, no activation.

Real PostgreSQL ordering stays with the D1 owner's dedicated suite. These
tests exercise the authenticated route, quota and shared pipeline together.
"""
import pytest

from app.services import ai, ai_consent as consent, ai_provider_call, ai_recovery
from app.blueprints import mobile_coach as route
from test_mobile_coach_api import (  # noqa: F401 -- shared authenticated fixtures
    alice, bearer, mobile, no_real_provider, provider, send, _chat_used, _rows,
)


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("code,status", [
    ("AI_CONSENT_REQUIRED", 403),
    ("AI_CONSENT_CHANGED", 409),
    ("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503),
])
def test_admission_denial_precedes_allowance_and_context(
        app, monkeypatch, mobile, alice, bearer, provider, code, status):
    observed = []

    def deny(owner, feature, recipient, model):
        observed.append((owner, feature, recipient, model))
        raise consent.ConsentDenied(code, status)

    def forbidden(*args, **kwargs):
        pytest.fail("denied admission reached quota or context")

    monkeypatch.setattr(consent, "begin_operation", deny)
    monkeypatch.setattr(route, "reserve_ai_quota", forbidden)
    from app.services import ai_pipeline
    monkeypatch.setattr(ai_pipeline, "_context_stage", forbidden)
    response = send(mobile, bearer(alice))
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert response.json["error"]["retryable"] is (status == 503)
    assert observed == [(alice.id, "coach", *ai.heavy_primary_route())]
    assert provider.calls == [] and _chat_used(alice.id) == 0
    assert _rows(alice.id) == []


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_one_original_authority_spans_quota_context_provider_and_projection(
        app, monkeypatch, mobile, alice, bearer, provider):
    authority = consent.OperationAuthority(alice.id, "coach", 7, "synthetic-v")
    captures, boundaries = [], []

    def capture(*args):
        captures.append(args)
        return authority

    def observe(label):
        assert consent.current_operation() is authority
        boundaries.append(label)

    original_reserve = route.reserve_ai_quota
    original_project = route.mobile_coach.turn_payload
    from app.services import ai_pipeline
    original_context = ai_pipeline._context_stage

    def reserve(*args):
        observe("quota")
        return original_reserve(*args)

    def context(*args, **kwargs):
        observe("context")
        return original_context(*args, **kwargs)

    def project(*args):
        observe("projection")
        return original_project(*args)

    monkeypatch.setattr(consent, "begin_operation", capture)
    monkeypatch.setattr(route, "reserve_ai_quota", reserve)
    monkeypatch.setattr(ai_pipeline, "_context_stage", context)
    monkeypatch.setattr(route.mobile_coach, "turn_payload", project)
    provider.side_effect = lambda *args: observe("provider")
    response = send(mobile, bearer(alice))
    assert response.status_code == 200
    assert captures == [(alice.id, "coach", *ai.heavy_primary_route())]
    assert boundaries == ["quota", "context", "provider", "projection"]
    assert consent.current_operation() is None


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("possible_attempt", [False, True])
@pytest.mark.parametrize("code,status", [
    ("AI_CONSENT_CHANGED", 409),
    ("AI_CONSENT_REQUIRED", 403),
    ("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503),
])
def test_terminal_denial_preserves_type_and_truthful_allowance(
        app, monkeypatch, mobile, alice, bearer, provider,
        possible_attempt, code, status):
    failures = []
    monkeypatch.setattr(ai_recovery, "record_ai_failure", failures.append)

    def refuse(*args):
        if possible_attempt:
            # The established physical-door signal, not a fake usage result.
            ai_provider_call._before_physical_attempt()
        raise consent.ConsentDenied(code, status)

    provider.side_effect = refuse
    response = send(mobile, bearer(alice))
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert "messages" not in response.json
    assert failures == [] and _rows(alice.id) == []
    assert _chat_used(alice.id) == int(possible_attempt)
    assert consent.current_operation() is None


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_generic_failure_after_possible_attempt_does_not_refund(
        app, mobile, alice, bearer, provider):
    def fail(*args):
        ai_provider_call._before_physical_attempt()
        raise RuntimeError("synthetic provider timeout")

    provider.side_effect = fail
    response = send(mobile, bearer(alice))
    assert response.status_code == 503
    assert response.json["error"]["code"] == "COACH_UNAVAILABLE"
    assert _chat_used(alice.id) == 1 and _rows(alice.id) == []


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_soft_fallback_after_possible_attempt_does_not_refund(
        app, mobile, alice, bearer, provider):
    from app.services.response_formatter import error_fallback

    def fallback(*args):
        ai_provider_call._before_physical_attempt()
        return error_fallback(alice.language)

    provider.side_effect = fallback
    response = send(mobile, bearer(alice))
    assert response.status_code == 503
    assert response.json["error"]["code"] == "COACH_UNAVAILABLE"
    assert _chat_used(alice.id) == 1 and _rows(alice.id) == []
