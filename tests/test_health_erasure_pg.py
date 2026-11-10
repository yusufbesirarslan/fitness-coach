"""O2-B on disposable PostgreSQL, including the installed completion fence."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from contextlib import contextmanager

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


def test_cache_failure_rolls_back_fenced_claim_erasure_until_retry(world, monkeypatch):
    from app.services import gamification
    class Cache:
        def zrem(self, key, owner):
            raise RuntimeError('synthetic cache unavailable')
    monkeypatch.setattr(gamification, 'redis_client', Cache())
    stop()
    with pytest.raises(health_erasure.ErasureUnavailable):
        health_erasure.run(1)
    assert WorkoutCompletionDay.query.filter_by(user_id=1).count() == 1
    assert CompletionClaimTransaction.query.count() == 2
    assert HealthErasure.query.filter_by(owner_id=1).one().completed_at is None
    monkeypatch.setattr(Cache, 'zrem', lambda *args: 0)
    assert health_erasure.run(1) == 'completed'
    assert WorkoutCompletionDay.query.filter_by(user_id=1).count() == 0
    assert WorkoutCompletionDay.query.filter_by(user_id=2).count() == 1


def _ranking_cache(world, monkeypatch):
    from app.services import gamification as gam
    from test_leaderboard_redis import FakeRedis
    class Cache(FakeRedis):
        def zrem(self, key, owner):
            return int(self.zsets.get(key, {}).pop(str(owner), None) is not None)
    cache = Cache()
    monkeypatch.setattr(gam, 'redis_client', cache)
    for owner in (1, 2):
        user = db.session.get(User, owner)
        user.rank_points, user.weekly_xp, user.streak_count = owner * 100, owner * 20, owner
    db.session.commit()
    return gam, cache


@pytest.mark.parametrize('publisher', ['rebuild', 'delayed_commit'])
def test_pre_erasure_ranking_snapshot_cannot_restore_erased_scores(world, monkeypatch, publisher):
    app, *_ = world
    gam, cache = _ranking_cache(world, monkeypatch)
    entered, finish = threading.Event(), threading.Event()
    original = gam._ranking_snapshots
    @contextmanager
    def paused(payloads):
        if 1 in payloads:
            entered.set()
            assert finish.wait(5)
        with original(payloads) as snapshots:
            yield snapshots
    monkeypatch.setattr(gam, '_ranking_snapshots', paused)
    def publish():
        with app.app_context():
            if publisher == 'rebuild':
                gam.lb_rebuild(batch_size=2)
            else:
                gam._sync_leaderboard_snapshot(1, {'rank_points': 100, 'weekly_xp': 20, 'streak_count': 1})
                gam.lb_sync_user(db.session.get(User, 2))
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(publish)
        assert entered.wait(5)
        stop()
        assert health_erasure.run(1) == 'completed'
        finish.set()
        work.result(timeout=5)
    assert cache.zscore(gam.LB_ALLTIME_KEY, '1') == 0
    assert cache.zscore(gam.LB_WEEKLY_KEY, '1') == 0
    assert cache.zscore(gam.LB_ALLTIME_KEY, '2') == gam._lb_score(200, 2)
    assert cache.zscore(gam.LB_WEEKLY_KEY, '2') == gam._lb_score(40, 2)


def test_inflight_ranking_publication_finishes_before_erasure_removal(world, monkeypatch):
    app, *_ = world
    gam, cache = _ranking_cache(world, monkeypatch)
    stop()
    entered, finish, erasing, done = (threading.Event() for _ in range(4))
    original = cache.zadd
    def paused(key, mapping):
        if '1' in mapping and key == gam.LB_ALLTIME_KEY:
            entered.set()
            assert finish.wait(5)
        return original(key, mapping)
    monkeypatch.setattr(cache, 'zadd', paused)
    def publish():
        with app.app_context():
            gam.lb_rebuild(batch_size=2)
    def erase():
        with app.app_context():
            erasing.set()
            result = health_erasure.run(1)
            done.set()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(publish)
        assert entered.wait(5)
        erasure = pool.submit(erase)
        assert erasing.wait(5)
        assert not done.wait(0.15)
        finish.set()
        work.result(timeout=5)
        assert erasure.result(timeout=5) == 'completed'
    assert cache.zscore(gam.LB_ALLTIME_KEY, '1') is None
    assert cache.zscore(gam.LB_WEEKLY_KEY, '1') is None
    assert cache.zscore(gam.LB_ALLTIME_KEY, '2') == gam._lb_score(200, 2)


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


@pytest.fixture
def clap_world(world):
    from account_deletion_support import (add_activity, add_clap, drop_legacy_activity_clap,
                                          install_legacy_activity_clap)
    install_legacy_activity_clap()
    try:
        own, other = add_activity(1), add_activity(2)
        add_clap(own, 2)      # someone else's clap on the erased owner's activity
        add_clap(other, 1)    # the erased owner's clap elsewhere: social, not health
        add_clap(other, 2)
        db.session.commit()
        yield world, own, other
    finally:
        drop_legacy_activity_clap()


def test_legacy_activity_clap_no_longer_blocks_workout_erasure(clap_world):
    from app.models import Activity
    from account_deletion_support import claps
    _, own, other = clap_world
    stop()
    assert health_erasure.run(1) == 'completed'
    assert db.session.get(Activity, own) is None
    assert db.session.get(Activity, other) is not None
    assert claps() == sorted([(other, 1), (other, 2)])
    assert HealthErasure.query.filter_by(owner_id=1).one().completed_at is not None


def test_without_the_cleanup_legacy_clap_keeps_workout_erasure_pending(clap_world, monkeypatch):
    """Non-vacuity: the same erasure with the cleanup disabled cannot complete."""
    from app.models import Activity
    from app.services import legacy_activity_clap
    from account_deletion_support import claps
    _, own, other = clap_world
    monkeypatch.setattr(legacy_activity_clap, 'delete_on_activities_of', lambda uid: 0)
    stop()
    before = claps()
    with pytest.raises(health_erasure.ErasureUnavailable) as error:
        health_erasure.run(1)
    assert error.value.__cause__.orig.pgcode == '23503'
    assert error.value.__cause__.orig.diag.constraint_name == 'activity_clap_activity_id_fkey'
    db.session.rollback()
    assert db.session.get(Activity, own) is not None
    assert claps() == before
    assert HealthErasure.query.filter_by(owner_id=1).one().completed_at is None
