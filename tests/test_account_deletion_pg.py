"""Opt-in PostgreSQL races for LP-11 account deletion.

SQLite cannot prove any of this: it has no row locks, and releasing an
outermost SAVEPOINT commits there. These run the real service against a
disposable PostgreSQL database with only Cognito and the S3 client faked.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \
        python -m pytest -m pg_concurrency tests/test_account_deletion_pg.py
"""
import os
import threading
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


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

    app = Flask("account-deletion-pg-race")
    app.config.update(
        TESTING=True, SECRET_KEY="disposable-pg-account-deletion",
        SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    cognito, s3 = install_fakes(monkeypatch)
    with app.app_context():
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


def _meal(user_id, photo_key=None):
    from app.models import MealLog
    return MealLog(user_id=user_id, ogun="Öğle", yemekler="race meal",
                   tarih="2026-09-30", photo_key=photo_key)


def test_child_committed_during_deletion_is_purged_and_its_media_released(
        pg_app):
    """A racing writer holds a key-share lock on the owner row with a new meal
    photo; the purge waits for it, sees the photo under its lock, releases it,
    and only then purges. Nothing is orphaned in the DB or the bucket."""
    from app.extensions import db
    from app.models import MealLog, User
    from app.services import account_deletion
    from account_deletion_support import object_key

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_alice")
    b_id, b_avatar, _ = _seed(app, cognito, s3, "pg_bob")
    racing_key = object_key("meals", a_id)
    s3.objects.add(racing_key)

    inserted, release_writer = threading.Event(), threading.Event()
    outcome = {}

    def writer():
        with app.app_context():
            db.session.add(_meal(a_id, racing_key))
            db.session.flush()          # holds FOR KEY SHARE on user a
            inserted.set()
            release_writer.wait(timeout=10)
            db.session.commit()
            outcome["writer_committed_at"] = time.monotonic()
            db.session.remove()

    def deleter():
        with app.app_context():
            inserted.wait(timeout=10)
            try:
                account_deletion.delete_account(*principal)
                outcome["deleter"] = "ok"
            except Exception as error:  # pragma: no cover - surfaced below
                outcome["deleter"] = type(error).__name__
            outcome["deleter_done_at"] = time.monotonic()
            db.session.remove()

    threads = [threading.Thread(target=writer), threading.Thread(target=deleter)]
    for thread in threads:
        thread.start()
    time.sleep(0.8)                      # deleter is now blocked on the lock
    assert "deleter_done_at" not in outcome
    release_writer.set()
    for thread in threads:
        thread.join(timeout=20)

    assert outcome["deleter"] == "ok"
    assert outcome["deleter_done_at"] > outcome["writer_committed_at"]
    with app.app_context():
        assert db.session.get(User, a_id) is None
        assert MealLog.query.filter_by(user_id=a_id).count() == 0
        assert db.session.get(User, b_id) is not None
    assert racing_key in s3.deleted and a_avatar in s3.deleted
    assert b_avatar in s3.objects and b_avatar not in s3.deleted


def test_writer_blocked_by_purge_lock_fails_its_foreign_key(
        pg_app, monkeypatch):
    """A write that arrives while the purge holds the owner lock waits, then
    fails: it can neither survive the purge nor recreate the owner."""
    import app.cli as cli
    from app.extensions import db
    from app.models import MealLog, User
    from app.services import account_deletion

    app, cognito, s3 = pg_app
    a_id, _key, principal = _seed(app, cognito, s3, "pg_carol")
    locked = threading.Event()
    real_purge = cli._purge_user

    def slow_purge(user):
        locked.set()
        time.sleep(0.8)                  # writer arrives and blocks meanwhile
        real_purge(user)
    monkeypatch.setattr(cli, "_purge_user", slow_purge)
    outcome = {}

    def deleter():
        with app.app_context():
            account_deletion.delete_account(*principal)
            outcome["deleter"] = "ok"
            db.session.remove()

    def writer():
        with app.app_context():
            locked.wait(timeout=10)
            try:
                db.session.add(_meal(a_id))
                db.session.commit()
                outcome["writer"] = "committed"
            except IntegrityError:
                db.session.rollback()
                outcome["writer"] = "fk_violation"
            db.session.remove()

    threads = [threading.Thread(target=deleter), threading.Thread(target=writer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert outcome == {"deleter": "ok", "writer": "fk_violation"}
    with app.app_context():
        assert db.session.get(User, a_id) is None
        assert MealLog.query.filter_by(user_id=a_id).count() == 0


def test_concurrent_duplicate_deletions_converge(pg_app):
    from app.extensions import db
    from app.models import User
    from app.services import account_deletion

    app, cognito, s3 = pg_app
    a_id, a_avatar, principal = _seed(app, cognito, s3, "pg_dave")
    b_id, b_avatar, _ = _seed(app, cognito, s3, "pg_erin")
    barrier = threading.Barrier(2)
    outcomes = []

    def deleter():
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                account_deletion.delete_account(*principal)
                outcomes.append("ok")
            except Exception as error:  # pragma: no cover - surfaced below
                outcomes.append(type(error).__name__)
            db.session.remove()

    threads = [threading.Thread(target=deleter) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert outcomes == ["ok", "ok"]
    assert cognito.deleted_subs == ["sub-pg_dave", "sub-pg_dave"]
    assert "sub-pg_erin" in cognito.live
    with app.app_context():
        assert db.session.get(User, a_id) is None
        assert db.session.get(User, b_id) is not None
    assert a_avatar in s3.deleted
    assert b_avatar in s3.objects and b_avatar not in s3.deleted


@pytest.mark.parametrize("transport", ["mobile", "web"])
def test_login_create_racing_the_purge_waits_for_it_then_sees_the_tombstone(
        pg_app, monkeypatch, transport):
    """The one interleaving in which "check for a tombstone, then create"
    would resurrect a deleted identity, run for real.

    The login looked its subject up BEFORE the account existed (a miss), and
    reaches creation while the deletion's purge is written but UNCOMMITTED:
    at that instant there is no tombstone to see. The guard therefore writes
    first — the INSERT carrying the subject — and checks after. The INSERT
    waits on `uq_user_cognito_sub` until the purge commits, and the check that
    follows sees the tombstone committed with it. Checking before writing
    would pass here and then create the row. The renamed-legacy shape (local
    username/e-mail differ from the provider's) is what makes reconciliation
    fall through to creation.
    """
    import contextlib

    import app.cli as cli
    from app.blueprints import auth as auth_bp
    from app.extensions import db
    from app.models import DeletedIdentityTombstone, User
    from app.services import account_deletion, deleted_identity, mobile_auth

    app, cognito, s3 = pg_app
    sub = "sub-pg_legacy"
    claims = {"sub": sub, "email": "pg_frank@example.invalid",
              "email_verified": True, "cognito:username": "pg_frank"}
    looked_up, go_login = threading.Event(), threading.Event()
    purged, go_commit = threading.Event(), threading.Event()
    outcome = {}

    module = mobile_auth if transport == "mobile" else auth_bp
    real_reconcilable = module.reconcilable_local_user

    def paused_reconcilable(*args):
        looked_up.set()
        go_login.wait(timeout=10)
        return real_reconcilable(*args)
    monkeypatch.setattr(module, "reconcilable_local_user", paused_reconcilable)

    real_purge = cli._purge_user

    def purge_and_hold(user):
        real_purge(user)
        db.session.flush()               # tombstone + purge written, not committed
        purged.set()
        go_commit.wait(timeout=10)
    monkeypatch.setattr(cli, "_purge_user", purge_and_hold)

    def login():
        context = (app.test_request_context() if transport == "web"
                   else contextlib.nullcontext())
        with app.app_context(), context:
            try:
                if transport == "mobile":
                    user = mobile_auth._resolve_user(claims)
                else:
                    assert User.query.filter_by(cognito_sub=sub).first() is None
                    user = auth_bp._reconcile_local_user(claims, "pg_frank")
                if user is None:
                    outcome["login"] = "refused"
                else:
                    db.session.commit()
                    outcome["login"] = "created"
            except mobile_auth.MobileAuthFailure as failure:
                outcome["login"] = failure.reason
            db.session.rollback()
            outcome["login_done_at"] = time.monotonic()
            db.session.remove()

    def deleter(principal):
        with app.app_context():
            account_deletion.delete_account(*principal)
            outcome["deleter"] = "ok"
            db.session.remove()

    login_thread = threading.Thread(target=login)
    login_thread.start()
    assert looked_up.wait(timeout=10)            # subject lookup missed
    a_id, _key, principal = _seed(app, cognito, s3, "pg_legacy")
    deleter_thread = threading.Thread(target=deleter, args=(principal,))
    deleter_thread.start()
    assert purged.wait(timeout=10)
    go_login.set()
    time.sleep(0.8)
    assert "login_done_at" not in outcome        # INSERT waits on the purge
    go_commit.set()
    for thread in (login_thread, deleter_thread):
        thread.join(timeout=20)

    assert outcome["deleter"] == "ok"
    assert outcome["login"] == (
        "identity_deleted" if transport == "mobile" else "refused")
    with app.app_context():
        assert db.session.get(User, a_id) is None
        assert User.query.filter_by(cognito_sub=sub).count() == 0
        assert User.query.filter_by(username="pg_frank").count() == 0
        assert [t.fingerprint for t in DeletedIdentityTombstone.query] == [
            deleted_identity.fingerprint(sub)]
