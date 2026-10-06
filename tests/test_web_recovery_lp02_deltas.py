"""The deliberate browser-visible differences LP-02 introduces.

Everything else about `/forgot-password` and `/reset-password` is pinned,
unchanged, by `tests/test_web_recovery_characterization.py`. Each test here
FAILS against the pre-extraction code (verified) and documents why the new
answer is correct:

1. The provider calls now run inside the shared `blocking_concurrency_slot`
   (house rule for every blocking Cognito call; the web recovery calls were
   the last ungated ones). A saturated slot answers the LP-01 web capacity
   answer — `503 auth.service_busy` + `Retry-After: 15` — instead of parking a
   thread on the provider.
2. Non-string JSON field values are treated as missing (the route's existing
   400) instead of raising `AttributeError` → 500.
3. A provider password-history rejection is a password-policy answer, not
   "code invalid or expired" (which sent the user to request a new code that
   would fail the same way).

    python -m pytest tests/test_web_recovery_lp02_deltas.py -v
"""
import threading
import time

import pytest

from app.i18n import t
from app.services import ai_gate, cognito_service
from app.services.cognito_service import CognitoServiceError


def _tr(key):
    return t(key, locale="tr")


@pytest.fixture
def provider(monkeypatch):
    calls = {"forgot": [], "confirm": []}
    failures = {}

    def forgot_password(username, language=None):
        calls["forgot"].append(username)

    def confirm_forgot_password(username, code, new_password):
        calls["confirm"].append(username)
        if "confirm" in failures:
            raise failures["confirm"]

    monkeypatch.setattr(cognito_service, "forgot_password", forgot_password)
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", confirm_forgot_password)
    return {"calls": calls, "failures": failures}


def _context(client, username="alice"):
    with client.session_transaction() as state:
        state["password_reset_username"] = username
        state["password_reset_started_at"] = time.time()


def _reset(client, **overrides):
    body = {"code": "123456", "password": "Newpass123",
            "confirm_password": "Newpass123"}
    body.update(overrides)
    return client.post("/reset-password", json=body)


@pytest.fixture
def saturated(monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    assert semaphore.acquire(blocking=False)
    yield
    semaphore.release()


def _assert_service_busy(response):
    assert response.status_code == 503
    assert response.get_json() == {"error": _tr("auth.service_busy")}
    assert response.headers["Retry-After"] == "15"


def test_forgot_saturated_capacity_is_a_retryable_503(client, provider,
                                                      saturated):
    response = client.post("/forgot-password", json={"identifier": "alice"})
    _assert_service_busy(response)
    assert provider["calls"]["forgot"] == []
    # No reset context is armed for a request the provider never saw.
    with client.session_transaction() as state:
        assert "password_reset_username" not in state


def test_reset_saturated_capacity_is_a_retryable_503(client, provider,
                                                     saturated):
    _context(client)
    response = _reset(client)
    _assert_service_busy(response)
    assert provider["calls"]["confirm"] == []
    with client.session_transaction() as state:
        assert state["password_reset_username"] == "alice"


@pytest.mark.parametrize("identifier", [42, True, ["alice"], {"u": "a"}])
def test_forgot_non_string_identifier_is_the_missing_identifier_400(
        client, provider, identifier):
    response = client.post("/forgot-password", json={"identifier": identifier})
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.identifier_required")}
    assert provider["calls"]["forgot"] == []


@pytest.mark.parametrize("overrides", [
    {"code": 123456},
    {"code": ["123456"]},
    {"password": ["Newpass123"], "confirm_password": ["Newpass123"]},
    {"password": 12345678, "confirm_password": 12345678},
])
def test_reset_non_string_fields_are_the_missing_fields_400(
        client, provider, overrides):
    _context(client)
    response = _reset(client, **overrides)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.reset_fields_required")}
    assert provider["calls"]["confirm"] == []


def test_reset_password_history_rejection_is_a_policy_answer(client,
                                                             provider):
    _context(client)
    provider["failures"]["confirm"] = CognitoServiceError(
        "x", "PasswordHistoryPolicyViolationException")
    response = _reset(client)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.reset_password_rejected")}
