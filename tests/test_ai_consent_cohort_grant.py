"""D1 consent-WRITE cohort gate (teen QR finding F-QR-W1) on a SQLite stand-in.

``ai_consent._transaction`` refuses non-PostgreSQL, so this module swaps in a
SQLite connection with the same owner-row check. It is NOT distributed
authority qualification: the same scenarios run on real PostgreSQL in
tests/test_ai_consent_cohort_grant_pg.py (CI PG list).
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import consent_cohort_grant_support as scenario
from app.extensions import db
from app.services import account_eligibility as eligibility, ai_consent as consent


@pytest.fixture
def sqlite_authority(app, monkeypatch):
    @contextmanager
    def transaction(owner):
        if type(owner) is not int or owner <= 0:
            raise consent.ConsentDenied()
        with db.engine.begin() as conn:
            _, _, users = consent._tables()
            if conn.execute(select(users.c.id).where(users.c.id == owner)).scalar_one_or_none() is None:
                raise consent.ConsentDenied()
            yield conn
    monkeypatch.setattr(consent, "_transaction", transaction)
    return app


def test_adult_allowed_every_purpose(sqlite_authority):
    scenario.adult_allowed_every_purpose()


def test_unscreened_adult_unchanged(sqlite_authority):
    scenario.unscreened_adult_unchanged()


def test_teen_training_allowed_and_withdrawable(sqlite_authority):
    scenario.teen_training_allowed()


def test_teen_forbidden_purposes_refused_before_persistence(sqlite_authority):
    scenario.teen_forbidden_each_purpose()


def test_profile_age_minor_forbidden(sqlite_authority):
    scenario.profile_age_minor_forbidden()


def test_legacy_grant_inspect_withdraw_and_denied_regrant(sqlite_authority, monkeypatch):
    scenario.legacy_grant_inspect_withdraw_and_no_regrant(monkeypatch)


def test_teen_pins_do_not_widen_grantable_purposes(sqlite_authority, monkeypatch):
    scenario.pins_do_not_widen_minor_grants(monkeypatch)


def test_minor_grant_purposes_are_exactly_training():
    assert consent._minor_grant_purposes() == frozenset({"training.plan"})


@pytest.mark.parametrize("age,band,expected", [
    (15, "13_17", True), (None, "13_17", True), (16, None, True), (12, None, True),
    (16, "18_plus", True), (30, "18_plus", False), (18, None, False), (None, None, False),
])
def test_minority_signal(app, age, band, expected):
    user = SimpleNamespace(cognito_sub="sub-signal", age=age, user_metadata={})
    if band:
        scenario.signed_record(user, band)
    assert eligibility.minority_signal(user) is expected


def test_forged_minor_record_falls_back_to_profile_age(app):
    user = SimpleNamespace(cognito_sub="sub-forged", age=None,
                           user_metadata={eligibility.METADATA_KEY: "forged.value"})
    assert eligibility.minority_signal(user) is False
    user.age = 17
    assert eligibility.minority_signal(user) is True


def test_refusal_envelope_is_stable_and_not_retryable(app):
    from app.blueprints.ai_consent import consent_error_response
    with app.test_request_context():
        response = consent_error_response(consent.ConsentDenied(scenario.REFUSAL, 403))
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == scenario.REFUSAL
    assert response.get_json()["error"]["retryable"] is False
    assert response.headers["Cache-Control"] == "no-store"


def test_grant_gate_is_not_provider_admission():
    # Independent gates: the write gate never calls admission, and admission
    # never consults the write gate (inspected, not assumed).
    import inspect
    from app.services import ai_launch
    names = set(consent._require_grant_cohort.__code__.co_names)
    assert {"minority_signal", "_minor_grant_purposes"} <= names
    assert not names & {"ai_service_eligible", "require_cohort", "ai_launch"}
    assert "_require_grant_cohort" not in inspect.getsource(ai_launch)
