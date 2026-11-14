"""Opt-in PostgreSQL races: legal hold versus deletion, and the record.

SQLite has no advisory locks and no row locks, so it cannot show that a hold
and a deletion of the same account are ordered. These run the real lifecycle,
the real `health_consent._authority` and the real `health-legal-hold-add` /
`cleanup-test-users` command bodies against a disposable PostgreSQL database;
only Cognito and the S3 client are faked.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \
        python -m pytest -m pg_concurrency tests/test_account_deletion_audit_pg.py
"""
import os
import threading
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )

REVIEW = "2099-01-01"


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    from flask import Flask
    from app.extensions import db
    from account_deletion_support import install_fakes

    app = Flask("account-deletion-audit-pg")
    app.config.update(
        TESTING=True, SECRET_KEY="disposable-pg-deletion-audit",
        SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    cognito, s3 = install_fakes(monkeypatch)
    with app.app_context():
        with db.engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS activity_clap")
        db.drop_all()
        db.create_all()
    try:
        yield app, cognito, s3
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _seed(app, cognito, s3, name):
    from app.extensions import db
    from app.models import User
    from app.services import session_store
    from account_deletion_support import object_key

    with app.app_context():
        user = User(username=name, email=f"{name}@example.invalid",
                    cognito_sub=f"sub-{name}")
        db.session.add(user)
        db.session.flush()
        key = object_key("avatars", user.id)
        user.profile_picture_key = key
        s3.objects.add(key)
        db.session.commit()
        cognito.live.add(user.cognito_sub)
        principal = (
            SimpleNamespace(id=user.id, cognito_sub=user.cognito_sub),
            SimpleNamespace(
                user_id=user.id, cognito_sub=user.cognito_sub,
                cognito_access_token=session_store.encrypt_token(
                    f"access|{user.cognito_sub}")),
            {"sub": user.cognito_sub})
        return user.id, key, principal


def _wait_for_lock_waiter(app, timeout=10):
    """True once another backend of this database is WAITING ON A LOCK (read
    from PostgreSQL, never inferred from a sleep)."""
    from app.extensions import db

    with app.app_context():
        engine = db.engine
    deadline = time.monotonic() + timeout
    with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            if connection.execute(sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND wait_event_type = 'Lock'")).scalar():
                return True
            time.sleep(0.01)
    return False


def _state(app, user_id):
    from app.extensions import db
    from app.health_consent_models import HealthLegalHold
    from app.models import AccountDeletionRecord, DeletedIdentityTombstone, User

    with app.app_context():
        try:
            return SimpleNamespace(
                user=db.session.get(User, user_id) is not None,
                holds=HealthLegalHold.query.filter_by(owner_id=user_id).count(),
                active_holds=HealthLegalHold.query.filter_by(
                    owner_id=user_id, released_at=None).count(),
                records=AccountDeletionRecord.query.count(),
                tombstones=DeletedIdentityTombstone.query.count())
        finally:
            db.session.remove()


def _add_hold(app, user_id):
    """The real controller command body."""
    from app.cli import health_legal_hold_add_cmd
    from app.extensions import db

    with app.app_context():
        try:
            health_legal_hold_add_cmd(user_id, "MATTER-PG", REVIEW, ("coach",))
        finally:
            db.session.remove()


def _run(target):
    box = {}

    def runner():
        try:
            box["value"] = target()
        except BaseException as error:           # SystemExit included
            box["error"] = error
    thread = threading.Thread(target=runner)
    thread.start()
    return thread, box


def _delete(app, principal):
    from app.extensions import db
    from app.services import account_deletion

    def call():
        with app.app_context():
            try:
                account_deletion.delete_account(*principal)
                return "ok"
            finally:
                db.session.remove()
    return call


def test_committed_hold_refuses_deletion_before_the_provider(pg_app):
    from app.services import account_deletion

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_held")
    _add_hold(app, a_id)

    with app.app_context():
        with pytest.raises(account_deletion.DeletionUnavailable) as refused:
            account_deletion.delete_account(*principal)
    assert refused.value.stage == "legal_hold"
    assert cognito.delete_calls == [] and s3.deleted == []
    state = _state(app, a_id)
    assert state.user and state.active_holds == 1
    assert state.records == 0 and state.tombstones == 0
    assert a_avatar in s3.objects


def test_hold_cannot_be_placed_while_a_deletion_is_in_flight(pg_app, monkeypatch):
    """The deletion holds the owner's exclusive authority from before the
    provider call to after the purge commit, so `health-legal-hold-add`
    cannot place a hold on records that are being destroyed. It fails; the
    deletion finishes; nothing held was lost and no hold row is orphaned."""
    from app.services import cognito_service, health_consent

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_inflight")
    at_provider, proceed = threading.Event(), threading.Event()
    real_delete = cognito.delete_user

    def slow_delete(token):
        at_provider.set()
        assert proceed.wait(timeout=20)
        return real_delete(token)
    monkeypatch.setattr(cognito_service, "delete_user", slow_delete)

    deleter, outcome = _run(_delete(app, principal))
    assert at_provider.wait(timeout=10)
    try:
        # Bounded by the authority's own lock_timeout (2 s): refused, no hold.
        with pytest.raises(health_consent.HealthConsentDenied):
            _add_hold(app, a_id)
        assert _state(app, a_id).holds == 0
    finally:
        proceed.set()
        deleter.join(timeout=20)

    assert outcome == {"value": "ok"}
    state = _state(app, a_id)
    assert not state.user and state.holds == 0
    assert state.records == 1 and state.tombstones == 1
    assert a_avatar in s3.deleted
    # A hold after the account is gone has nothing to attach to.
    with pytest.raises(Exception):
        _add_hold(app, a_id)
    assert _state(app, a_id).holds == 0


def test_deletion_waits_for_a_hold_being_placed_and_is_then_refused(pg_app):
    """The other order: the controller is inside `health-legal-hold-add`
    (exclusive authority held, hold not yet committed) when the deletion
    starts. The deletion WAITS for the authority instead of reading "no hold",
    then sees the committed hold and refuses before the provider."""
    from app.extensions import db
    from app.health_consent_models import HealthLegalHold
    from app.services import account_deletion, health_consent
    from datetime import datetime, timedelta

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_hold_first")
    inside, commit_now = threading.Event(), threading.Event()

    def controller():
        with app.app_context():
            try:
                with health_consent._authority(a_id, exclusive=True):
                    now = datetime.utcnow()
                    db.session.add(HealthLegalHold(
                        owner_id=a_id, categories=["coach"], matter_ref="M",
                        created_at=now, review_at=now + timedelta(days=30)))
                    db.session.flush()
                    inside.set()
                    assert commit_now.wait(timeout=20)
                    db.session.commit()
                return "held"
            finally:
                db.session.remove()

    holder, held = _run(controller)
    assert inside.wait(timeout=10)
    deleter, outcome = _run(_delete(app, principal))
    try:
        assert _wait_for_lock_waiter(app), "the deletion did not wait for the authority"
        assert cognito.delete_calls == []
    finally:
        commit_now.set()
        holder.join(timeout=20)
        deleter.join(timeout=20)

    assert held == {"value": "held"}
    error = outcome.get("error")
    assert isinstance(error, account_deletion.DeletionUnavailable)
    assert error.stage == "legal_hold"
    assert cognito.delete_calls == [] and s3.deleted == []
    state = _state(app, a_id)
    assert state.user and state.active_holds == 1 and state.records == 0


def test_busy_authority_is_unavailable_and_deletes_nothing(pg_app):
    from app.services import account_deletion, health_consent

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_busy")
    with app.app_context():
        with health_consent._authority(a_id, exclusive=True):
            outcome_thread, outcome = _run(_delete(app, principal))
            outcome_thread.join(timeout=20)
    error = outcome.get("error")
    assert isinstance(error, account_deletion.DeletionUnavailable)
    assert error.stage == "hold_authority"
    assert cognito.delete_calls == [] and s3.deleted == []
    assert _state(app, a_id).user

    # Free again: the ordinary deletion still works and leaves its record.
    with app.app_context():
        account_deletion.delete_account(*principal)
    state = _state(app, a_id)
    assert not state.user and state.records == 1 and state.tombstones == 1


def test_hold_written_without_the_authority_still_stops_the_purge(
        pg_app, monkeypatch):
    """Defence in depth: a writer that skipped the authority commits a hold
    after the first check. The recheck under the owner row lock stops the
    purge; the answer is INCOMPLETE and the held rows are all still there."""
    from app.extensions import db
    from app.health_consent_models import HealthLegalHold
    from app.services import account_deletion
    from datetime import datetime, timedelta

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_bypass")
    real = account_deletion._delete_identity

    def identity_then_rogue_hold(provider_access):
        step = real(provider_access)
        now = datetime.utcnow()
        db.session.add(HealthLegalHold(
            owner_id=a_id, categories=["coach"], matter_ref="M",
            created_at=now, review_at=now + timedelta(days=30)))
        db.session.commit()
        return step
    monkeypatch.setattr(account_deletion, "_delete_identity",
                        identity_then_rogue_hold)

    with app.app_context():
        with pytest.raises(account_deletion.DeletionIncomplete) as stopped:
            account_deletion.delete_account(*principal)
        db.session.remove()
    assert stopped.value.stage == "legal_hold"
    state = _state(app, a_id)
    assert state.user and state.active_holds == 1
    assert state.records == 0 and state.tombstones == 0


def test_operator_cleanup_in_flight_makes_a_late_hold_fail(pg_app, monkeypatch):
    """`cleanup-test-users` holds the owner row FOR UPDATE through its commit.
    A hold INSERT needs a key-share lock on that row: it waits, then fails its
    foreign key. The cleanup's "no hold" decision was therefore final."""
    import app.cli as cli
    from app.extensions import db

    app, cognito, s3 = pg_app
    a_id, _, _ = _seed(app, cognito, s3, "test")
    purging, proceed = threading.Event(), threading.Event()
    real_purge = cli._purge_user

    def slow_purge(user):
        purging.set()
        assert proceed.wait(timeout=20)
        return real_purge(user)
    monkeypatch.setattr(cli, "_purge_user", slow_purge)

    def cleanup():
        with app.app_context():
            try:
                cli.cleanup_test_users("test", None, True)
                return "cleaned"
            finally:
                db.session.remove()

    cleaner, cleaned = _run(cleanup)
    assert purging.wait(timeout=10)
    adder, added = _run(lambda: _add_hold(app, a_id))
    try:
        assert _wait_for_lock_waiter(app), "the hold INSERT was not blocked"
    finally:
        proceed.set()
        cleaner.join(timeout=20)
        adder.join(timeout=20)

    assert cleaned == {"value": "cleaned"}
    assert "error" in added                       # foreign key: the owner is gone
    state = _state(app, a_id)
    assert not state.user and state.holds == 0 and state.records == 1


def test_operator_cleanup_waits_for_a_hold_being_placed_and_refuses(pg_app):
    import app.cli as cli
    from app.extensions import db
    from app.health_consent_models import HealthLegalHold
    from datetime import datetime, timedelta

    app, cognito, s3 = pg_app
    a_id, _, _ = _seed(app, cognito, s3, "test")
    inside, commit_now = threading.Event(), threading.Event()

    def controller():
        with app.app_context():
            try:
                now = datetime.utcnow()
                db.session.add(HealthLegalHold(
                    owner_id=a_id, categories=["coach"], matter_ref="M",
                    created_at=now, review_at=now + timedelta(days=30)))
                db.session.flush()                # key-share lock on the owner row
                inside.set()
                assert commit_now.wait(timeout=20)
                db.session.commit()
                return "held"
            finally:
                db.session.remove()

    def cleanup():
        with app.app_context():
            try:
                cli.cleanup_test_users("test", None, True)
                return "cleaned"
            finally:
                db.session.remove()

    holder, held = _run(controller)
    assert inside.wait(timeout=10)
    cleaner, cleaned = _run(cleanup)
    try:
        assert _wait_for_lock_waiter(app), "the cleanup did not wait for the row"
    finally:
        commit_now.set()
        holder.join(timeout=20)
        cleaner.join(timeout=20)

    assert held == {"value": "held"}
    error = cleaned.get("error")
    assert isinstance(error, SystemExit) and error.code == 2
    state = _state(app, a_id)
    assert state.user and state.active_holds == 1 and state.records == 0


def test_concurrent_duplicate_deletions_leave_exactly_one_record(pg_app):
    app, cognito, s3 = pg_app
    a_id, _, principal = _seed(app, cognito, s3, "pg_twice")
    barrier = threading.Barrier(2)

    def deleter():
        barrier.wait(timeout=10)
        return _delete(app, principal)()

    runs = [_run(deleter) for _ in range(2)]
    for thread, _ in runs:
        thread.join(timeout=30)
    assert [box for _, box in runs] == [{"value": "ok"}, {"value": "ok"}]
    state = _state(app, a_id)
    assert not state.user and state.records == 1 and state.tombstones == 1


def test_record_outlives_the_account_and_the_database_enforces_its_shape(pg_app):
    from sqlalchemy.exc import IntegrityError
    from app.extensions import db
    from app.models import AccountDeletionRecord
    from app.services import account_deletion, deletion_audit

    app, cognito, s3 = pg_app
    a_id, _, principal = _seed(app, cognito, s3, "pg_record")
    b_id, _, other = _seed(app, cognito, s3, "pg_record_b")
    with app.app_context():
        account_deletion.delete_account(*principal)
        account_deletion.delete_account(*other)
        db.session.remove()
        rows = AccountDeletionRecord.query.order_by(AccountDeletionRecord.id).all()
        assert len(rows) == 2
        assert rows[0].counts["rows"]["user"] == 1
        assert rows[0].counts["objects_released"] == 1
        assert deletion_audit.verify() == {
            "records": 2, "integrity_failures": [],
            "tombstones_without_record": 0,
            "first_record_at": rows[0].completed_at.isoformat()}
        assert sa.inspect(db.engine).get_foreign_keys("account_deletion_record") == []
        for column, value in (("channel", "support"), ("outcome", "failed"),
                              ("identity_step", "skipped"), ("scope", "partial")):
            with pytest.raises(IntegrityError):
                db.session.execute(sa.text(
                    f"UPDATE account_deletion_record SET {column} = :v"), {"v": value})
            db.session.rollback()
        with pytest.raises(IntegrityError):
            db.session.execute(sa.text(
                "UPDATE account_deletion_record SET retain_until = completed_at"))
        db.session.rollback()
        db.session.remove()
