"""D1 consent-WRITE cohort gate (F-QR-W1 + screened-grant closure) on a SQLite stand-in.

``ai_consent._transaction`` refuses non-PostgreSQL, so this module swaps in a
SQLite connection with the same owner-row check. It is NOT distributed
authority qualification: the same scenarios run on real PostgreSQL in
tests/test_ai_consent_cohort_grant_pg.py (CI PG list).
"""
from contextlib import contextmanager
from datetime import datetime, timezone
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


def test_adult_allowed_every_purpose_with_flag_off_and_on(sqlite_authority):
    scenario.adult_allowed_every_purpose()


def test_screened_adult_without_profile_age_allowed(sqlite_authority):
    scenario.screened_adult_without_profile_age_allowed()


def test_unscreened_refused_regardless_of_flag(sqlite_authority):
    scenario.unscreened_refused_regardless_of_flag()


def test_refusal_consumes_no_request_id(sqlite_authority):
    scenario.refusal_consumes_no_request_id()


def test_malformed_records_refused(sqlite_authority):
    scenario.malformed_records_refused()


def test_conflicting_profile_ages_refused(sqlite_authority, monkeypatch):
    scenario.conflicting_profile_ages_refused(monkeypatch)


def test_teen_training_allowed_and_withdrawable(sqlite_authority, monkeypatch):
    scenario.teen_training_allowed(monkeypatch)


def test_teen_forbidden_purposes_refused_before_persistence(sqlite_authority, monkeypatch):
    scenario.teen_forbidden_each_purpose(monkeypatch)


def test_unpinned_teen_record_refused_training_included(sqlite_authority):
    scenario.unpinned_teen_record_refused()


def test_teen_record_without_acknowledged_notice_refused(sqlite_authority, monkeypatch):
    scenario.teen_record_without_notice_refused(monkeypatch)


def test_legacy_grants_inspect_withdraw_replay_and_denied_regrant(sqlite_authority, monkeypatch):
    scenario.legacy_grants(monkeypatch)


def test_screened_adult_replay_and_withdrawal_after_record_loss(sqlite_authority):
    scenario.screened_adult_replay_after_record_loss()


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
    assert {"d1_grant_cohort", "minority_signal", "_minor_grant_purposes"} <= names
    assert not names & {"ai_service_eligible", "require_cohort", "ai_launch"}
    assert "_require_grant_cohort" not in inspect.getsource(ai_launch)
    assert "d1_grant_cohort" not in inspect.getsource(ai_launch)


def test_grant_cohort_never_reads_the_rollout_flag():
    # Independent of ACCOUNT_ELIGIBILITY_ENABLED (inspected, not assumed).
    import inspect
    for function in (consent._require_grant_cohort, eligibility.d1_grant_cohort,
                     eligibility._record, eligibility._adult_tr):
        source = inspect.getsource(function)
        assert "enabled(" not in source and "_enforced(" not in source
        assert "ACCOUNT_ELIGIBILITY_ENABLED" not in source.split('"""')[-1]


NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def _account(age, band=None, country="TR", subject="sub-unit", **changes):
    user = SimpleNamespace(cognito_sub=subject, age=age, user_metadata={})
    if band:
        scenario.signed_record(user, band, country, **changes)
    return user


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("age,band,country,expected", [
    (30, "18_plus", "TR", "adult_tr"),
    (None, "18_plus", "TR", "adult_tr"),
    (30, "18_plus", "US", None),
    (16, "18_plus", "TR", None),
    (15, "13_17", "TR", "tr_minor"),
    (20, "13_17", "TR", None),
    (None, "13_17", "TR", None),
    (30, None, "TR", None),
    (16, None, "TR", None),
    (None, None, "TR", None),
])
def test_d1_grant_cohort(app, monkeypatch, flag, pinned, age, band, country, expected):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = flag
    if pinned:
        scenario.open_minor_notice(monkeypatch)
    elif expected == "tr_minor":
        expected = None  # no canonical TR minor record while the notice pin is closed
    assert eligibility.d1_grant_cohort(_account(age, band, country), NOW) == expected


def test_d1_grant_cohort_requires_a_subject_and_a_past_declaration(app):
    adult = _account(30, "18_plus")
    assert eligibility.d1_grant_cohort(adult, NOW) == "adult_tr"
    assert eligibility.d1_grant_cohort(adult, datetime(2026, 10, 31, tzinfo=timezone.utc)) is None
    adult.cognito_sub = None
    assert eligibility.d1_grant_cohort(adult, NOW) is None
    moved = _account(30, "18_plus")
    moved.cognito_sub = "sub-another-account"
    assert eligibility.d1_grant_cohort(moved, NOW) is None
