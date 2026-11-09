"""O2-B on disposable PostgreSQL, including the installed completion fence."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.health_consent_models import HealthErasure, HealthLegalHold
from app.models import MealLogRetainedTotal, User, WorkoutCompletionDay, CompletionClaimTransaction
from app.services import health_consent as consent, health_erasure, completion_fence
from test_health_consent import command
from test_health_consent_pg import pg_app  # noqa: F401
from test_completion_fence_pg import _drop_fence_objects, _install_fence
from test_mobile_training_allowance_pg import _module, _run
from account_deletion_support import install_fakes, seed_account, rows_exist

pytestmark = pytest.mark.pg_concurrency
MIGRATION = 'f01e2a5e0002_health_withdrawal_erasure.py'


@pytest.fixture
def world(pg_app, monkeypatch):
    _drop_fence_objects()
    _install_fence()
    with pg_app.app_context():
        with db.engine.begin() as conn:
            _run(conn, _module(MIGRATION).upgrade)
        _, s3 = install_fakes(monkeypatch)
        # No replacement of consent authority, SQL locks, migrations or row erasure.
        consent.mutate(1, command())
        db.session.add(User(id=2, username='other-erasure', email='other@example.invalid'))
        db.session.commit()
        consent.mutate(2, command())
        a, ak = seed_account(db.session.get(User, 1), s3, 'A')
        b, bk = seed_account(db.session.get(User, 2), s3, 'B')
        for owner in (1, 2):
            db.session.add(MealLogRetainedTotal(user_id=owner, tarih='2026-11-01',
                energy_kcal=500, protein_g=20, carbohydrate_g=50, fat_g=15, item_count=2))
        db.session.commit()
        with db.engine.connect() as conn:
            completion_fence.raise_floor(conn, 'c' * 40)
        for owner in (1, 2):
            db.session.add(WorkoutCompletionDay(user_id=owner, date_key='2026-11-01', proof='none'))
        db.session.commit()
        yield pg_app, s3, a, b, ak, bk
        db.session.remove()
    _drop_fence_objects()


def stop(owner=1):
    return consent.mutate(owner, command(1, 'withdraw', f'pg-o2b-stop-{owner}'))


def test_complete_health_inventory_and_fenced_claims_are_erased_only_for_owner(world):
    app, s3, a, b, ak, bk = world
    stop()
    assert health_erasure.run(1) == 'completed'
    assert WorkoutCompletionDay.query.filter_by(user_id=1).count() == 0
    assert WorkoutCompletionDay.query.filter_by(user_id=2).count() == 1
    assert CompletionClaimTransaction.query.count() == 1
    assert MealLogRetainedTotal.query.filter_by(user_id=1).count() == 0
    assert MealLogRetainedTotal.query.filter_by(user_id=2).one().energy_kcal == 500
    assert rows_exist(b) == []
    assert not set(bk.values()) & set(s3.deleted)
    assert ak['avatar'] in s3.objects
    assert HealthErasure.query.filter_by(owner_id=1).one().completed_at is not None
    with db.engine.connect() as conn:
        completion_fence.verify_fence(conn)
    assert consent.mutate(1, command(2, request_id='pg-o2b-regrant'))['state'] == 'granted'
    assert health_erasure.run(1) == 'none'


@pytest.mark.parametrize('mode', ['granted', 'withdrawn_no_debt', 'pending_granted', 'workout_hold', 'other_owner'])
def test_retention_exception_cannot_bypass_health_evidence_or_holds(world, mode):
    if mode != 'granted':
        stop()
    if mode == 'withdrawn_no_debt':
        HealthErasure.query.delete()
    if mode == 'pending_granted':
        from app.health_consent_models import HealthConsentState
        state = db.session.get(HealthConsentState, 1)
        state.record = dict(state.record, state='granted')
    if mode == 'workout_hold':
        db.session.add(HealthLegalHold(owner_id=1, categories=['workouts'], matter_ref='SYNTHETIC',
            created_at=datetime.utcnow(), review_at=datetime.utcnow() + timedelta(days=1)))
    db.session.commit()
    owner = 2 if mode == 'other_owner' else 1
    with pytest.raises(sa.exc.DBAPIError) as error:
        with db.engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM workout_completion_day WHERE user_id=:owner'), {'owner': owner})
    assert error.value.orig.pgcode == 'AXF05'
    assert WorkoutCompletionDay.query.filter_by(user_id=owner).count() == 1


def test_direct_binding_delete_remains_forbidden_even_during_erasure(world):
    stop()
    with pytest.raises(sa.exc.DBAPIError) as error:
        with db.engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM completion_claim_transaction'))
    assert error.value.orig.pgcode == 'AXF05'


def test_duplicate_retry_waits_and_cannot_erase_newly_granted_records(world, monkeypatch):
    app, *_ = world
    stop()
    entered, finish, waiting = threading.Event(), threading.Event(), threading.Event()
    original = health_erasure._release
    first = [True]
    def release(*args):
        if first[0]:
            first[0] = False
            entered.set()
            assert finish.wait(5)
        return original(*args)
    monkeypatch.setattr(health_erasure, '_release', release)
    def erase():
        with app.app_context():
            return health_erasure.run(1)
    def duplicate():
        with app.app_context():
            waiting.set()
            return health_erasure.run(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(erase)
        assert entered.wait(5)
        two = pool.submit(duplicate)
        assert waiting.wait(5)
        assert not two.done()
        finish.set()
        assert one.result(5) == 'completed'
        assert two.result(5) == 'none'
    consent.mutate(1, command(2, request_id='pg-o2b-fresh-grant'))
    user = db.session.get(User, 1)
    user.weight = 65
    db.session.commit()
    assert health_erasure.run(1) == 'none'
    assert db.session.get(User, 1).weight == 65


def test_committed_hold_keeps_photos_until_release_on_real_authority(world):
    app, s3, a, b, ak, bk = world
    from app.cli import health_legal_hold_add_cmd, health_legal_hold_release_cmd
    # Register only the actual controller commands on the minimal PG app.
    app.cli.command('hold-add')(health_legal_hold_add_cmd)
    app.cli.command('hold-release')(health_legal_hold_release_cmd)
    review = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d')
    result = app.test_cli_runner().invoke(args=['hold-add', '--user-id', '1', '--matter', 'SYNTHETIC',
        '--review-date', review, '--category', 'pump'])
    assert result.exit_code == 0, result.output
    stop()
    assert health_erasure.run(1) == 'held'
    assert ak['pump'] in s3.objects and ak['meal'] not in s3.objects
    assert consent.status(1)['erasure']['state'] == 'pending'
    hold = HealthLegalHold.query.one()
    result = app.test_cli_runner().invoke(args=['hold-release', '--hold-id', str(hold.id)])
    assert result.exit_code == 0, result.output
    assert consent.status(1)['erasure']['state'] == 'completed'


def test_reviewed_fence_verifier_still_rejects_function_tampering(world):
    with db.engine.begin() as conn:
        conn.execute(sa.text("ALTER FUNCTION public.completion_fence_marker() SECURITY DEFINER"))
        with pytest.raises(RuntimeError):
            completion_fence.verify_fence(conn)
        conn.execute(sa.text("ALTER FUNCTION public.completion_fence_marker() SECURITY INVOKER"))


def test_hold_creation_waits_for_inflight_storage_erasure(world, monkeypatch):
    app, *_ = world
    from app.cli import health_legal_hold_add_cmd
    app.cli.command('hold-add')(health_legal_hold_add_cmd)
    stop()
    entered, finish, adding = threading.Event(), threading.Event(), threading.Event()
    original = health_erasure._release
    first = [True]
    def release(*args):
        if first[0]:
            first[0] = False
            entered.set()
            assert finish.wait(5)
        return original(*args)
    monkeypatch.setattr(health_erasure, '_release', release)
    def erase():
        with app.app_context():
            return health_erasure.run(1)
    def add():
        with app.app_context():
            adding.set()
            return app.test_cli_runner().invoke(args=['hold-add', '--user-id', '1', '--matter', 'SYNTHETIC',
                '--review-date', (datetime.utcnow() + timedelta(days=2)).strftime('%Y-%m-%d'), '--category', 'pump'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(erase)
        assert entered.wait(5)
        two = pool.submit(add)
        assert adding.wait(5)
        assert not two.done()
        finish.set()
        assert one.result(5) == 'completed'
        result = two.result(5)
        assert result.exit_code == 0, result.output
