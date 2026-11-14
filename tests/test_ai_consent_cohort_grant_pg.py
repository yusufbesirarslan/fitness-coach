"""D1 consent-WRITE cohort gate (teen QR finding F-QR-W1) on REAL PostgreSQL:
the real ``ai_consent._transaction`` (READ COMMITTED, owner row FOR UPDATE)
and the real launch disclosures. Shipping pins stay None except where a
scenario monkeypatches them to prove they never widen minor grants."""
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


def test_adult_allowed_every_purpose(app):
    scenario.adult_allowed_every_purpose()


def test_unscreened_adult_unchanged(app):
    scenario.unscreened_adult_unchanged()


def test_teen_training_allowed_and_withdrawable(app):
    scenario.teen_training_allowed()


def test_teen_forbidden_purposes_refused_before_persistence(app):
    scenario.teen_forbidden_each_purpose()


def test_profile_age_minor_forbidden(app):
    scenario.profile_age_minor_forbidden()


def test_legacy_grant_inspect_withdraw_and_denied_regrant(app, monkeypatch):
    scenario.legacy_grant_inspect_withdraw_and_no_regrant(monkeypatch)


def test_teen_pins_do_not_widen_grantable_purposes(app, monkeypatch):
    scenario.pins_do_not_widen_minor_grants(monkeypatch)
