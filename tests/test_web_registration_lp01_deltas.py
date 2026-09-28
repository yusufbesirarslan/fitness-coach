"""The two deliberate browser-visible deltas LP-01 introduces.

Everything else about the web registration routes is pinned, unchanged, by
tests/test_web_registration_characterization.py. These are the only
differences, both on paths that previously had no defined answer:

1. Saturated blocking capacity. The provider call now runs inside the shared
   `blocking_concurrency_slot` (house rule for every blocking provider call);
   a full slot answers 503 + Retry-After instead of parking a request thread.
2. Non-string JSON field values. They used to raise inside the route
   (`AttributeError`/`TypeError` → unhandled 500), or — a list password — be
   length-checked as if it were text; the canonical normalizer treats every
   non-string as absent, so the route answers its existing "fields required"
   400.
"""
import threading

import pytest

from app.blueprints import auth as auth_bp
from app.i18n import t
from app.services import ai_gate, cognito_service


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    calls = []
    for name in ("sign_up", "confirm_sign_up", "resend_code"):
        monkeypatch.setattr(
            cognito_service, name,
            lambda _name=name, **kwargs: calls.append(_name) or "sub-x")
    return calls


@pytest.mark.parametrize(("path", "body"), [
    ("/register", {"username": "busyuser", "email": "busy@example.com",
                   "password": "Sifre123"}),
    ("/verify", {"username": "busyuser", "code": "123456"}),
    ("/verify/resend", {"username": "busyuser"}),
])
def test_saturated_capacity_is_a_retryable_503_without_provider_call(
        client, provider, monkeypatch, path, body):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    assert semaphore.acquire(blocking=False)
    try:
        response = client.post(path, json=body)
    finally:
        semaphore.release()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "15"
    assert response.get_json() == {"error": t("auth.service_busy", locale="tr")}
    assert provider == []


@pytest.mark.parametrize(("path", "body", "key"), [
    ("/register", {"username": 5, "email": "x@example.com",
                   "password": "Sifre123"}, "auth.all_fields_required"),
    ("/register", {"username": "someone", "email": 12,
                   "password": "Sifre123"}, "auth.all_fields_required"),
    ("/register", {"username": "someone", "email": "x@example.com",
                   "password": ["Sifre123"]}, "auth.all_fields_required"),
    ("/verify", {"username": ["x"], "code": "123456"},
     "auth.verify_fields_required"),
    ("/verify", {"username": "someone", "code": 123456},
     "auth.verify_fields_required"),
    ("/verify/resend", {"username": {"a": 1}}, "auth.username_required"),
])
def test_non_string_fields_are_missing_fields_not_a_crash(
        client, provider, path, body, key):
    response = client.post(path, json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": t(key, locale="tr")}
    assert provider == []
