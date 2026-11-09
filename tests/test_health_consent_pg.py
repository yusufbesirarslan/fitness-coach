"""Real disposable PostgreSQL: no test replacement of consent/locking."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from app.extensions import db
from app.models import User
from app.services import health_consent as consent, health_disclosure as disclosure
from test_health_consent import command

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires explicitly disposable PostgreSQL", allow_module_level=True)


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.fail("explicit disposable PostgreSQL required")
    app = Flask("health-consent-pg")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=url,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    monkeypatch.setattr(disclosure, "APPROVED_VERSION", disclosure.VERSION)
    with app.app_context():
        db.create_all()
        db.session.add(User(username="health-pg", email="synthetic@example.invalid"))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_real_default_deny_and_durable_grant(pg_app):
    with pg_app.app_context():
        with pytest.raises(consent.HealthConsentDenied):
            with consent.processing(1):
                pytest.fail("unconsented processing")
        consent.mutate(1, command())
        db.session.remove()
        assert consent.status(1)["state"] == "granted"
        with consent.processing(1) as generation:
            assert generation == 1


def test_withdrawal_serializes_with_processing_even_across_orm_commits(pg_app):
    entered, finish, withdrawing, acknowledged = (threading.Event() for _ in range(4))
    with pg_app.app_context():
        consent.mutate(1, command())

    def process():
        with pg_app.app_context(), consent.processing(1):
            user = db.session.get(User, 1)
            user.weight = 80
            db.session.commit()  # Must not release the independent consent lock.
            entered.set()
            assert finish.wait(5)
            user.weight = 81
            db.session.commit()

    def withdraw():
        assert entered.wait(5)
        with pg_app.app_context():
            withdrawing.set()
            result = consent.mutate(1, command(1, "withdraw", "pg-withdraw-0002"))
            acknowledged.set()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        work, revoke = pool.submit(process), pool.submit(withdraw)
        assert withdrawing.wait(5)
        assert not acknowledged.wait(0.15)
        finish.set()
        work.result(timeout=5)
        assert revoke.result(timeout=5)["state"] == "withdrawn"
    with pg_app.app_context():
        assert db.session.get(User, 1).weight == 81  # Retained; never auto-erased.
        with pytest.raises(consent.HealthConsentDenied):
            with consent.processing(1):
                pytest.fail("processing after acknowledged withdrawal")


def test_old_work_and_mutation_replay_cannot_revive_after_regrant(pg_app):
    with pg_app.app_context():
        grant = command()
        consent.mutate(1, grant)
        consent.mutate(1, command(1, "withdraw", "pg-stop-0002"))
        assert consent.mutate(1, grant)["state"] == "withdrawn"
        consent.mutate(1, command(2, request_id="pg-regrant-0003"))
        with pytest.raises(consent.HealthConsentDenied) as denied:
            with consent.processing(1, expected_generation=1):
                pytest.fail("stale queued work")
        assert denied.value.code == "HEALTH_CONSENT_CHANGED"
        with consent.processing(1, expected_generation=3):
            assert consent.current_generation() == 3


def test_conflicting_concurrent_decisions_have_one_winner(pg_app):
    barrier = threading.Barrier(2)
    def decide(index):
        with pg_app.app_context():
            barrier.wait(timeout=5)
            try:
                return consent.mutate(1, command(request_id=f"pg-race-{index:08}"))["generation"]
            except consent.HealthConsentDenied as error:
                return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, [1, 2]))
    assert sorted(results) == [1, 409]


def test_exception_releases_authority_for_withdrawal(pg_app):
    with pg_app.app_context():
        consent.mutate(1, command())
        with pytest.raises(RuntimeError):
            with consent.processing(1):
                raise RuntimeError("synthetic failure")
        assert consent.current_generation() is None
        assert consent.mutate(1, command(1, "withdraw", "pg-exit-0002"))["state"] == "withdrawn"


def test_new_consent_does_not_rewrite_old_health_values(pg_app):
    with pg_app.app_context():
        user = db.session.get(User, 1)
        user.weight = 87.25
        db.session.commit()
        assert consent.status(1)["generation"] == 0
        consent.mutate(1, command(action="decline"))
        assert db.session.get(User, 1).weight == 87.25
        consent.mutate(1, command(1, request_id="pg-prospective"))
        assert db.session.get(User, 1).weight == 87.25


def test_real_migration_accepts_complete_schema_and_refuses_evidence_loss(pg_app):
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from test_health_consent import _migration
    with pg_app.app_context():
        with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            _migration().upgrade()
        consent.mutate(1, command())
        with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="refuses to erase"):
                _migration().downgrade()


def test_wsgi_unclosed_response_cannot_lend_authority_to_another_request(pg_app):
    from app.health_boundary import HealthProcessingBoundary, MARKER
    with pg_app.app_context():
        consent.mutate(1, command())
    def app(environ, start_response):
        with pg_app.app_context():
            scope = consent.processing(1)
            scope.__enter__()
            environ[MARKER] = scope
            return iter([b"synthetic health"])
    response = HealthProcessingBoundary(app)({}, lambda *args: None)
    assert consent.current_generation() is None
    assert next(response) == b"synthetic health"
    assert consent.current_generation() is None
    started = threading.Event()
    def withdraw():
        with pg_app.app_context():
            started.set()
            return consent.mutate(1, command(1, "withdraw", "wsgi-stop-0002"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopped = pool.submit(withdraw)
        assert started.wait(5)
        assert not stopped.done()
        response.close()
        assert stopped.result(timeout=5)["state"] == "withdrawn"
    with pytest.raises(consent.HealthConsentDenied):
        HealthProcessingBoundary(app)({}, lambda *args: None)


def test_joined_worker_rechecks_original_health_generation_in_own_app_context(pg_app, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app.services import ai_consent
    from app.services.ai_allowances import execution
    # Isolate the D1 wrapper only; real health evidence and locks remain in use.
    monkeypatch.setattr(ai_consent, "current_operation", lambda: SimpleNamespace(owner=1))
    monkeypatch.setattr(ai_consent, "operation_scope", lambda _: nullcontext())
    with pg_app.app_context():
        consent.mutate(1, command())
        with consent.processing(1):
            worker = execution.bind_worker(lambda: consent.current_generation())
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(worker).result(timeout=5) == 1
        consent.mutate(1, command(1, "withdraw", "worker-stop-0002"))
        consent.mutate(1, command(2, request_id="worker-regrant-0003"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(consent.HealthConsentDenied):
                pool.submit(worker).result(timeout=5)
        # A D1 operation alone never supplies the missing first-party permission.
        unqualified = execution.bind_worker(lambda: pytest.fail("missing health snapshot"))
        with pytest.raises(consent.HealthConsentDenied):
            unqualified()
