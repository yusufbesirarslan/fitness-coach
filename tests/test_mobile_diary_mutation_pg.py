"""Opt-in PostgreSQL races for stale-safe mobile diary mutations."""
import os
import threading
from datetime import date, datetime

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


DAY = date(2026, 8, 11)
DAY_KEY = DAY.isoformat()


@pytest.fixture
def pg_mutation_app():
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
    from app.models import MealLog, User

    app = Flask("mobile-diary-mutation-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-diary-mutation-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(
            username="pg-diary-mutation",
            email="pg-diary-mutation@example.invalid",
            cognito_sub="pg-diary-mutation-sub",
        )
        db.session.add(user)
        db.session.flush()
        row = MealLog(
            user_id=user.id,
            ogun="KahvaltÄ±",
            yemekler="Race meal",
            kalori=320.0,
            protein=12.0,
            karb=40.0,
            yag=9.0,
            tarih=DAY_KEY,
            source="manual",
            created_at=datetime(2026, 8, 11, 5, 24),
        )
        db.session.add(row)
        db.session.commit()
        user_id = user.id
    try:
        yield app, user_id
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _target(app, user_id):
    from app.services.mobile_nutrition import build_diary_day

    with app.app_context():
        return build_diary_day(
            user_id, app.config["SECRET_KEY"], day=DAY)["meals"][0]


def _race(app, operations):
    from app.extensions import db
    from app.services.mobile_diary_mutation import (
        EntryNotFound,
        StaleDiaryEntry,
        StoredObjectNotReleased,
    )

    barrier = threading.Barrier(len(operations))
    outcomes = {}

    def contender(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                result = operations[index]()
                outcomes[index] = ("ok", result)
            except StaleDiaryEntry:
                outcomes[index] = ("stale",)
            except EntryNotFound:
                outcomes[index] = ("missing",)
            except StoredObjectNotReleased:
                # A domain outcome of the shared authority, not a surprise: the
                # ledger correction committed and the object release is durably
                # pending. Classified here so a race can assert on it.
                outcomes[index] = ("pending",)
            except Exception as error:  # pragma: no cover - surfaced below
                outcomes[index] = ("unexpected", type(error).__name__)
            finally:
                db.session.remove()

    threads = [
        threading.Thread(target=contender, args=(index,), daemon=True)
        for index in range(len(operations))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), outcomes
    assert not any(outcome[0] == "unexpected" for outcome in outcomes.values())
    return outcomes


def _set_slot(app, user_id, target, slot):
    from app.services.mobile_diary_mutation import SetSlotCommand, set_slot

    return lambda: set_slot(
        user_id,
        DAY_KEY,
        target["id"],
        target["revision"],
        SetSlotCommand(slot),
        app.config["SECRET_KEY"],
    )


def _delete(app, user_id, target):
    from app.services.mobile_diary_mutation import delete_entry

    return lambda: delete_entry(
        user_id,
        DAY_KEY,
        target["id"],
        target["revision"],
        app.config["SECRET_KEY"],
    )


def test_same_revision_slot_moves_have_exactly_one_winner(pg_mutation_app):
    from app.models import MealLog
    from app.services.mobile_nutrition.serialization import SLOT_BY_MEAL_LABEL

    app, user_id = pg_mutation_app
    target = _target(app, user_id)
    outcomes = _race(app, [
        _set_slot(app, user_id, target, "ogle"),
        _set_slot(app, user_id, target, "aksam"),
    ])

    assert sorted(outcome[0] for outcome in outcomes.values()) == ["ok", "stale"]
    with app.app_context():
        assert MealLog.query.one().ogun in {
            label for label, slot in SLOT_BY_MEAL_LABEL.items()
            if slot in {"ogle", "aksam"}
        }


def test_same_revision_slot_move_and_delete_cannot_resurrect(pg_mutation_app):
    from app.models import MealLog

    app, user_id = pg_mutation_app
    target = _target(app, user_id)
    outcomes = _race(app, [
        _set_slot(app, user_id, target, "ogle"),
        _delete(app, user_id, target),
    ])

    kinds = sorted(outcome[0] for outcome in outcomes.values())
    assert kinds in (["ok", "stale"], ["missing", "ok"])
    assert sum(outcome[0] == "ok" for outcome in outcomes.values()) == 1
    with app.app_context():
        assert MealLog.query.count() in {0, 1}


def test_same_revision_deletes_remove_at_most_one_row(pg_mutation_app):
    from app.models import MealLog

    app, user_id = pg_mutation_app
    target = _target(app, user_id)
    outcomes = _race(app, [
        _delete(app, user_id, target),
        _delete(app, user_id, target),
    ])

    assert sorted(outcome[0] for outcome in outcomes.values()) == ["missing", "ok"]
    with app.app_context():
        assert MealLog.query.count() == 0


def test_returned_fresh_revision_allows_the_next_slot_move(pg_mutation_app):
    from app.services.mobile_diary_mutation import (
        SetSlotCommand,
        StaleDiaryEntry,
        set_slot,
    )

    app, user_id = pg_mutation_app
    target = _target(app, user_id)
    with app.app_context():
        first = set_slot(
            user_id, DAY_KEY, target["id"], target["revision"],
            SetSlotCommand("ogle"), app.config["SECRET_KEY"])
        with pytest.raises(StaleDiaryEntry):
            set_slot(
                user_id, DAY_KEY, target["id"], target["revision"],
                SetSlotCommand("aksam"), app.config["SECRET_KEY"])
        second = set_slot(
            user_id, DAY_KEY, target["id"], first["revision"],
            SetSlotCommand("aksam"), app.config["SECRET_KEY"])

    assert second["slot"] == "aksam"


# ---------------------------------------------------------------------------
# F14 (Sprint 13 PR4) - the stored-object lifecycle under a real row-lock race
# ---------------------------------------------------------------------------

PHOTO_KEY = "meals/{user_id}/2026/08/" + "a1" * 16 + ".jpg"


@pytest.fixture
def photo_backed_row(pg_mutation_app):
    """Give the seeded row a stored photo and a faked S3 network boundary."""
    from app.extensions import db
    from app.models import MealLog
    import s3_helper

    app, user_id = pg_mutation_app
    key = PHOTO_KEY.format(user_id=user_id)
    with app.app_context():
        row = MealLog.query.one()
        row.photo_key = key
        db.session.commit()

    lock = threading.Lock()
    deleted = []

    class RacingS3Client:
        def delete_object(self, **kwargs):
            with lock:
                deleted.append(kwargs)
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    original = (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
                s3_helper._client)
    s3_helper._BOTO3_AVAILABLE = True
    s3_helper.S3_BUCKET_NAME = "pg-race-bucket"
    s3_helper._client = RacingS3Client()
    try:
        yield app, user_id, key, deleted
    finally:
        (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
         s3_helper._client) = original


def test_concurrent_deletes_release_the_photo_exactly_once(photo_backed_row):
    """Two racing corrections converge: one row, one object, no leftover intent.

    The `SELECT ... FOR UPDATE` row lock still admits exactly one committer, so
    exactly one cleanup intent is written. After that commit the loser no
    longer sees the row; the remediation's retry path may then issue the same
    idempotent DeleteObject if the winner has not yet retired the intent.
    Duplicate release of the *same* key is therefore allowed. What must never
    happen is a second intent, a surviving row, or a forgotten object.
    """
    from app.models import MealLog, MealPhotoCleanup

    app, user_id, key, deleted = photo_backed_row
    target = _target(app, user_id)

    outcomes = _race(app, [
        _delete(app, user_id, target),
        _delete(app, user_id, target),
    ])

    statuses = sorted(outcome[0] for outcome in outcomes.values())
    assert statuses in (["missing", "ok"], ["ok", "ok"]), outcomes
    with app.app_context():
        assert MealLog.query.count() == 0
        assert MealPhotoCleanup.query.count() == 0
    assert deleted, "the owned object was never released"
    assert {item["Bucket"] for item in deleted} == {"pg-race-bucket"}
    assert {item["Key"] for item in deleted} == {key}
    assert len(deleted) in (1, 2)


def test_a_slot_move_racing_a_delete_never_orphans_a_surviving_row(
        photo_backed_row):
    """The surviving-row-points-at-a-deleted-object state must be unreachable.

    Whichever contender wins, the durable end state is one of exactly two: the
    row survives WITH its object, or the row is gone AND its object was
    released. A photo-bearing row that outlives its own photo is the failure
    Sprint 13's rollback plan rules out, and this race is where it would appear.
    """
    from app.models import MealLog

    app, user_id, key, deleted = photo_backed_row
    target = _target(app, user_id)

    _race(app, [
        _set_slot(app, user_id, target, "ogle"),
        _delete(app, user_id, target),
    ])

    with app.app_context():
        survivors = MealLog.query.all()
        if survivors:
            assert survivors[0].photo_key == key
            assert deleted == [], (
                "A row survived while its stored object was released.")
        else:
            assert deleted == [{"Bucket": "pg-race-bucket", "Key": key}]


# ---------------------------------------------------------------------------
# F14 durability (PR4 remediation) - the cleanup intent under real transactions
# ---------------------------------------------------------------------------

def test_the_cleanup_intent_and_the_row_delete_commit_atomically(
        photo_backed_row):
    """One transaction, two state transitions - proved on the real engine.

    SQLite can show the happy result; only PostgreSQL can show that the intent
    INSERT and the row DELETE are the same unit of work under a row lock that
    another connection is contending for. If they could ever land apart, the
    losing shape is a committed deletion whose object identity was never
    recorded - the defect this remediation removes.
    """
    from app.extensions import db
    from app.models import MealLog, MealPhotoCleanup

    app, user_id, key, deleted = photo_backed_row
    target = _target(app, user_id)

    _race(app, [
        _delete(app, user_id, target),
        _delete(app, user_id, target),
    ])

    with app.app_context():
        assert MealLog.query.count() == 0
        # The release succeeded, so the intent settled with it. What must never
        # exist is the converse: a committed delete with no intent AND no
        # released object.
        assert MealPhotoCleanup.query.count() == 0
        assert db.session.query(MealPhotoCleanup).count() == 0
    assert deleted == [{"Bucket": "pg-race-bucket", "Key": key}]


def test_a_failed_release_leaves_one_durable_intent_under_a_delete_race(
        pg_mutation_app, monkeypatch):
    """Two racing deletes, S3 down: exactly one intent, naming the exact object.

    The row lock admits one committer, so a lost race must not also write an
    intent - two intents for one object would mean two drains chasing it, and
    the uniqueness constraint would surface as a spurious 500 on the loser.
    """
    import s3_helper
    from app.extensions import db
    from app.models import MealLog, MealPhotoCleanup

    app, user_id = pg_mutation_app
    key = PHOTO_KEY.format(user_id=user_id)
    with app.app_context():
        row = MealLog.query.one()
        row.photo_key = key
        db.session.commit()

    class BrokenS3Client:
        def delete_object(self, **kwargs):
            raise s3_helper.BotoCoreError()

    original = (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
                s3_helper._client)
    s3_helper._BOTO3_AVAILABLE = True
    s3_helper.S3_BUCKET_NAME = "pg-race-bucket"
    s3_helper._client = BrokenS3Client()
    try:
        target = _target(app, user_id)
        outcomes = _race(app, [
            _delete(app, user_id, target),
            _delete(app, user_id, target),
        ])
    finally:
        (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
         s3_helper._client) = original

    statuses = sorted(outcome[0] for outcome in outcomes.values())
    # The winner commits the intent and then fails the release. The loser, no
    # longer able to lock the row, takes the retry path and fails the same
    # release — both report the pending lifecycle. A "missing" here would mean
    # the retry path was skipped, which is the defect again.
    assert statuses == ["pending", "pending"], outcomes

    with app.app_context():
        assert MealLog.query.count() == 0
        pending = MealPhotoCleanup.query.all()
        assert len(pending) == 1, (
            "The loser of the row-lock race wrote a second intent for the same "
            "object.")
        assert pending[0].photo_key == key
        assert pending[0].user_id == user_id


def test_concurrent_drains_converge_without_corrupting_intents(
        pg_mutation_app):
    """Two operator drains may run at the same moment.

    Both may issue the same idempotent DeleteObject; neither may clear an
    intent that belongs to a different record. The primary-key delete is what
    makes that structural rather than lucky, and the ORM unit of work is
    avoided precisely because its zero-row DELETE would raise on the loser.
    """
    import s3_helper
    from app.extensions import db
    from app.models import MealPhotoCleanup
    from app.services import mobile_diary_mutation

    app, user_id = pg_mutation_app
    mine = PHOTO_KEY.format(user_id=user_id)
    other = f"meals/{user_id}/2026/08/" + "b2" * 16 + ".jpg"

    lock = threading.Lock()
    deleted = []

    class RacingS3Client:
        def delete_object(self, **kwargs):
            with lock:
                deleted.append(kwargs["Key"])
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    original = (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
                s3_helper._client)
    s3_helper._BOTO3_AVAILABLE = True
    s3_helper.S3_BUCKET_NAME = "pg-race-bucket"
    s3_helper._client = RacingS3Client()
    try:
        with app.app_context():
            for entry_id, key in ((4242, mine), (4243, other)):
                db.session.add(MealPhotoCleanup(
                    user_id=user_id, entry_id=entry_id, photo_key=key,
                    entry_revision="pg-race-revision", diary_date=DAY_KEY,
                    created_at=datetime(2026, 8, 11, 6, 0)))
            db.session.commit()

        def drain():
            return mobile_diary_mutation.drain_meal_photo_cleanups(limit=10)

        outcomes = _race(app, [drain, drain])
    finally:
        (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
         s3_helper._client) = original

    assert all(outcome[0] == "ok" for outcome in outcomes.values()), outcomes
    assert sorted(set(deleted)) == sorted([mine, other])
    with app.app_context():
        assert MealPhotoCleanup.query.count() == 0, (
            "Concurrent drains must converge on an empty table, not deadlock "
            "or leave a record behind.")


def test_a_retry_racing_the_operator_drain_converges(pg_mutation_app):
    """A user retry and the operator drain may run at the same moment.

    Both name the same exact key. Duplicate DeleteObject is tolerated;
    neither may resurrect the ledger row, invent a second intent, or leave
    the original intent behind after a successful release.
    """
    import s3_helper
    from app.extensions import db
    from app.models import MealLog, MealPhotoCleanup
    from app.services import mobile_diary_mutation

    app, user_id = pg_mutation_app
    key = PHOTO_KEY.format(user_id=user_id)

    lock = threading.Lock()
    deleted = []

    class RacingS3Client:
        def delete_object(self, **kwargs):
            with lock:
                deleted.append(kwargs["Key"])
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    original = (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
                s3_helper._client)
    s3_helper._BOTO3_AVAILABLE = True
    s3_helper.S3_BUCKET_NAME = "pg-race-bucket"
    s3_helper._client = RacingS3Client()
    try:
        with app.app_context():
            row = MealLog.query.one()
            row.photo_key = key
            db.session.commit()
        target = _target(app, user_id)
        with app.app_context():
            row = MealLog.query.one()
            db.session.add(MealPhotoCleanup(
                user_id=user_id, entry_id=row.id, photo_key=key,
                entry_revision=target["revision"], diary_date=DAY_KEY,
                created_at=datetime(2026, 8, 11, 6, 0)))
            db.session.delete(row)
            db.session.commit()

        outcomes = _race(app, [
            _delete(app, user_id, target),
            lambda: mobile_diary_mutation.drain_meal_photo_cleanups(limit=10),
        ])
    finally:
        (s3_helper._BOTO3_AVAILABLE, s3_helper.S3_BUCKET_NAME,
         s3_helper._client) = original

    statuses = sorted(outcome[0] for outcome in outcomes.values())
    assert statuses in (["missing", "ok"], ["ok", "ok"]), outcomes
    assert key in deleted
    with app.app_context():
        assert MealLog.query.count() == 0
        assert MealPhotoCleanup.query.count() == 0
