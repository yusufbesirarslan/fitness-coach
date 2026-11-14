"""D1 consent-WRITE cohort gate (F-QR-W1 + screened-grant closure) on REAL PostgreSQL:
the real ``ai_consent._transaction`` (READ COMMITTED, owner row FOR UPDATE)
and the real launch disclosures. Shipping pins stay None except where a
scenario opens the TR minor notice pin (a qualified teen) or proves the
other pins never widen minor grants."""
import os

import pytest

import consent_cohort_grant_support as scenario
from app.extensions import db

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("explicit disposable PostgreSQL required", allow_module_level=True)


@pytest.fixture
def app(monkeypatch):
    from app import create_app
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    assert url.startswith(("postgresql://", "postgresql+psycopg2://"))
    monkeypatch.setenv("DATABASE_URL", url)
    application = create_app()
    application.config.update(TESTING=True)
    with application.app_context():
        db.create_all()
    try:
        with application.app_context():
            yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_adult_allowed_every_purpose_with_flag_off_and_on(app):
    scenario.adult_allowed_every_purpose()


def test_screened_adult_without_profile_age_allowed(app):
    scenario.screened_adult_without_profile_age_allowed()


def test_unscreened_refused_regardless_of_flag(app):
    scenario.unscreened_refused_regardless_of_flag()


def test_refusal_consumes_no_request_id(app):
    scenario.refusal_consumes_no_request_id()


def test_malformed_records_refused(app):
    scenario.malformed_records_refused()


def test_conflicting_profile_ages_refused(app, monkeypatch):
    scenario.conflicting_profile_ages_refused(monkeypatch)


def test_teen_training_allowed_and_withdrawable(app, monkeypatch):
    scenario.teen_training_allowed(monkeypatch)


def test_teen_forbidden_purposes_refused_before_persistence(app, monkeypatch):
    scenario.teen_forbidden_each_purpose(monkeypatch)


def test_unpinned_teen_record_refused_training_included(app):
    scenario.unpinned_teen_record_refused()


def test_teen_record_without_acknowledged_notice_refused(app, monkeypatch):
    scenario.teen_record_without_notice_refused(monkeypatch)


def test_legacy_grants_inspect_withdraw_replay_and_denied_regrant(app, monkeypatch):
    scenario.legacy_grants(monkeypatch)


def test_screened_adult_replay_and_withdrawal_after_record_loss(app):
    scenario.screened_adult_replay_after_record_loss()


def test_teen_pins_do_not_widen_grantable_purposes(app, monkeypatch):
    scenario.pins_do_not_widen_minor_grants(monkeypatch)
