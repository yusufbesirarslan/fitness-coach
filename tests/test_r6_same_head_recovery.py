"""Offline faults through the protected adult transaction's two boundaries."""
import json
import subprocess
from dataclasses import replace

import pytest

from tests.test_r6_adult_cutover import adult, assert_no_old_recovery
from tests.test_r6_deploy_transaction import CAND, IMG, OTHER, PREV, Box, FakeOps, names, run, tx


@pytest.fixture
def same_head(adult):
    adult.source_revisions['previous'] = adult.source_revisions['candidate'].copy()
    adult.db_heads = adult.candidate_heads = frozenset({tx.ADULT_MIGRATION})
    adult.faults['delta'] = {}
    return adult


def interrupt(world, fault):
    world.faults[fault] = True
    code, transaction = run(world)
    world.faults.pop(fault)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert transaction.registration_closure_attempted
    assert not world.same_head_receipt.committed
    assert_no_old_recovery(world, world.same_head_receipt.old_slot)
    world.calls.clear()


def assert_recovered(world, transaction):
    old = world.same_head_receipt.old_slot
    assert world.route == tx.CANDIDATE_SLOT[old] and world.head == CAND and world.registration_open
    assert old not in world.slots
    assert world.worker.image_id == world.slots[world.route].image_id == IMG[CAND]
    assert world.same_head_receipt.committed
    events = [event for event, _ in transaction.events]
    assert events.index('adult-reopen-ready') < events.index('adult-registration-open')
    assert events.index('adult-registration-open') < events.index('release-committed')
    assert ('route_switch', old) not in world.calls
    assert not any(c[:2] == ('worker_up', 'previous') for c in world.calls)


@pytest.mark.parametrize('adult', ['blue', 'green'], indirect=True)
def test_original_prepare_failure_and_same_head_protected_retry(same_head):
    original_route = same_head.route
    interrupt(same_head, 'release_prepare')
    assert same_head.route == original_route and same_head.head == PREV
    assert not same_head.slots and same_head.worker is None
    assert same_head.db_heads == frozenset({tx.ADULT_MIGRATION})
    assert not same_head.same_head_receipt.prepared

    code, _ = run(same_head)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert 'release_prepare' not in names(same_head)
    same_head.calls.clear()
    # Rebuilding an already-proven candidate could change its image ID.
    same_head.faults['build'] = True
    same_head.images[f'axisai-web:{CAND}'] = IMG[OTHER]
    code, transaction = run(same_head, mode='forward-recovery')
    assert code == tx.EXIT_OK
    assert names(same_head).count('release_prepare') == 1
    assert 'build_image' not in names(same_head)
    assert_recovered(same_head, transaction)


@pytest.mark.parametrize('fault', ['worker_close', 'migration_timeout', 'boot', 'acl',
                                   'public', 'worker_unhealthy', 'checkout', 'open_readback'])
def test_precommit_interruption_recovers_same_candidate(same_head, fault):
    interrupt(same_head, fault)
    prepared = same_head.same_head_receipt.prepared
    code, transaction = run(same_head, mode='forward-recovery')
    assert code == tx.EXIT_OK
    assert names(same_head).count('release_prepare') == (0 if prepared else 1)
    assert_recovered(same_head, transaction)


def test_preclosure_failure_preserves_serving_pair_and_has_no_receipt(same_head):
    old_web, old_worker = same_head.slots['blue'], same_head.worker
    same_head.faults['direct_origin'] = True
    code, transaction = run(same_head)
    assert code == tx.EXIT_ROLLED_BACK
    assert not transaction.registration_closure_attempted
    assert same_head.slots == {'blue': old_web} and same_head.worker is old_worker
    assert same_head.same_head_receipt is None
    assert 'release_prepare' not in names(same_head)
    same_head.faults.pop('direct_origin')
    same_head.calls.clear()
    code, transaction = run(same_head)
    assert code == tx.EXIT_OK
    assert_recovered(same_head, transaction)


def test_foreign_stopped_target_refuses_before_arming_receipt(same_head):
    old_web, old_worker = same_head.slots['blue'], same_head.worker
    foreign = Box('foreign', IMG[OTHER], OTHER, OTHER, status='exited', health=None)
    same_head.slots['green'] = foreign
    code, transaction = run(same_head)
    assert code == tx.EXIT_ROLLED_BACK
    assert not transaction.registration_closure_attempted
    assert same_head.slots == {'blue': old_web, 'green': foreign}
    assert same_head.worker is old_worker and same_head.same_head_receipt is None
    assert 'release_prepare' not in names(same_head)


def test_hard_stop_after_checkout_and_open_recovers_before_durable_commit(same_head, monkeypatch):
    class HardStop(BaseException):
        pass

    latch = FakeOps.registration_latch

    def stop_after_open(self, slot, revision, action, timeout):
        latch(self, slot, revision, action, timeout)
        if action == 'open':
            raise HardStop()

    with monkeypatch.context() as patch:
        patch.setattr(FakeOps, 'registration_latch', stop_after_open)
        with pytest.raises(HardStop):
            run(same_head)
    assert same_head.head == CAND and same_head.registration_open
    assert same_head.same_head_receipt.prepared and not same_head.same_head_receipt.committed
    same_head.calls.clear()
    code, transaction = run(same_head, previous_commit=CAND, mode='forward-recovery')
    assert code == tx.EXIT_OK
    assert same_head.calls.count(('registration_latch', 'close')) == 1
    assert 'reload_generation' in names(same_head)
    assert 'release_prepare' not in names(same_head)
    assert_recovered(same_head, transaction)


def test_postcommit_floor_failure_keeps_pair_and_refuses_same_release(same_head):
    same_head.floor.update(floor='absent', claims_present=False, raised_by_revision=None)
    same_head.faults['floor_raise'] = True
    code, transaction = run(same_head)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert_recovered(same_head, transaction)
    pair = same_head.slots['green'], same_head.worker
    for mode in ('normal', 'forward-recovery'):
        same_head.calls.clear()
        code, declined = run(same_head, previous_commit=CAND, mode=mode)
        assert code == tx.EXIT_ROLLBACK_INCOMPLETE
        assert any('already committed' in event.get('reason', '') for _, event in declined.events)
        assert (same_head.slots['green'], same_head.worker) == pair
        assert same_head.head == CAND and same_head.registration_open
        assert 'registration_latch' not in names(same_head)
        assert 'checkout_reset' not in names(same_head)


@pytest.mark.parametrize('fault', ['release_prepare', 'boot', 'eligibility', 'acl',
                                   'worker_unhealthy', 'open_readback', 'buffered'])
def test_persistent_fault_contains_each_retry_and_keeps_pending_identity(same_head, fault):
    interrupt(same_head, fault)
    record = same_head.same_head_receipt
    same_head.faults[fault] = True
    for _ in range(2):
        same_head.calls.clear()
        code, transaction = run(same_head, previous_commit=same_head.head, mode='forward-recovery')
        assert code == tx.EXIT_ROLLBACK_INCOMPLETE
        assert same_head.same_head_receipt == record
        assert 'build_image' not in names(same_head)
        assert not {'release-committed', 'same-head-runtime-proven'} & dict(transaction.events).keys()
        assert_no_old_recovery(same_head, record.old_slot)

    same_head.calls.clear()
    code, _ = run(same_head, previous_commit=same_head.head, deploy_sha=OTHER, mode='forward-recovery')
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert same_head.same_head_receipt == record
    assert not {'slot_start', 'slot_remove', 'worker_up', 'release_prepare'} & set(names(same_head))
    same_head.faults.pop(fault)
    same_head.calls.clear()
    code, transaction = run(same_head, previous_commit=same_head.head, mode='forward-recovery')
    assert code == tx.EXIT_OK
    assert_recovered(same_head, transaction)


def test_docker_healthy_routed_candidate_with_bad_deep_health_is_preserved(same_head):
    interrupt(same_head, 'acl')
    web = same_head.serving_box()
    web.deep_ok = False
    assert web.health == 'healthy'
    for _ in range(2):
        same_head.calls.clear()
        code, _ = run(same_head, previous_commit=same_head.head, mode='forward-recovery')
        assert code == tx.EXIT_ROLLBACK_INCOMPLETE
        assert same_head.serving_box() is web and not same_head.registration_open
        assert ('slot_remove', same_head.route, CAND) not in same_head.calls
        assert not same_head.same_head_receipt.committed


@pytest.mark.parametrize('adult', ['blue', 'green'], indirect=True)
@pytest.mark.parametrize('after', ['recovered', 'committed-residue'])
def test_next_normal_release_starts_from_actual_committed_slot(same_head, monkeypatch, after):
    original = same_head.route
    if after == 'recovered':
        interrupt(same_head, 'acl')
        code, _ = run(same_head, mode='forward-recovery')
        assert code == tx.EXIT_OK
    else:
        same_head.floor.update(floor='absent', claims_present=False, raised_by_revision=None)
        same_head.faults['floor_raise'] = True
        assert run(same_head)[0] == tx.EXIT_COMMITTED_RESIDUE
        same_head.faults.pop('floor_raise')
    committed = same_head.route
    prior = same_head.same_head_receipt
    assert committed != original and prior.committed
    assert same_head.registration_open and same_head.head == CAND
    same_head.manifests[IMG[OTHER]] = same_head.manifests[IMG[CAND]]

    def ingress(self, route, candidate, timeout):
        assert route == same_head.route
        return {'configuration': 'd' * 64, 'master': [1, 10],
                'workers': [[3, 30] if route == original else [2, 20]]}

    def latch(self, slot, revision, action, timeout):
        assert (slot, revision, action) == (original, OTHER, 'open')
        assert same_head.head == OTHER and same_head.worker.image_id == IMG[OTHER]
        same_head.registration_open = True
        return "a" * 64

    def prepare(self, revision, timeout):
        assert committed not in same_head.slots and same_head.worker is None
        same_head.registration_open = False
        assert revision == OTHER
        self._rec('release_prepare', revision, timeout=timeout)

    monkeypatch.setattr(FakeOps, 'ingress_snapshot', ingress)
    monkeypatch.setattr(FakeOps, 'registration_latch', latch)
    monkeypatch.setattr(FakeOps, 'release_prepare', prepare)
    same_head.calls.clear()
    code, transaction = run(same_head, previous_commit=CAND, deploy_sha=OTHER)
    assert code == tx.EXIT_OK
    assert same_head.registration_open and same_head.head == OTHER
    assert same_head.route == original and committed not in same_head.slots
    assert same_head.same_head_receipt == tx.SameHeadReceipt(CAND, OTHER, committed, IMG[OTHER], True, True)
    assert 'recovery-baseline' not in dict(transaction.events)


@pytest.mark.parametrize('reason', ['untracked', 'wrong-candidate', 'wrong-image', 'wrong-head',
                                    'changed-graph', 'migrator-present', 'below-floor',
                                    'unprepared-route', 'unprepared-checkout'])
def test_unproven_recovery_refuses_before_cutover(same_head, monkeypatch, reason):
    interrupt(same_head, 'release_prepare')
    if reason == 'untracked':
        same_head.same_head_receipt = None
    elif reason == 'wrong-candidate':
        same_head.same_head_receipt = replace(same_head.same_head_receipt, candidate=OTHER)
    elif reason == 'wrong-image':
        same_head.same_head_receipt = replace(same_head.same_head_receipt, image=IMG[OTHER])
    elif reason == 'wrong-head':
        historical = next(iter(same_head.source_revisions['previous'] - {tx.ADULT_MIGRATION}))
        same_head.db_heads = frozenset({historical})
    elif reason == 'changed-graph':
        same_head.faults['delta'] = {'c1': 'expand'}
    elif reason == 'below-floor':
        same_head.manifests.pop(IMG[CAND])
        same_head.source_manifests['candidate'] = None
    elif reason == 'unprepared-route':
        same_head.route = 'green'
    elif reason == 'unprepared-checkout':
        same_head.head = CAND
    elif reason == 'migrator-present':
        original = FakeOps.container

        def inspect(self, container_id, timeout):
            if container_id == tx.release_prepare_container_name(CAND):
                return Box(container_id, IMG[CAND], CAND, CAND)
            return original(self, container_id, timeout)

        monkeypatch.setattr(FakeOps, 'container', inspect)
    code, transaction = run(same_head, previous_commit=same_head.head, mode='forward-recovery')
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert not transaction.registration_closure_attempted
    assert not ({'release_prepare', 'slot_start', 'slot_remove', 'registration_latch',
                 'route_switch', 'checkout_reset', 'worker_up'} & set(names(same_head)))


def test_real_receipt_survives_new_engine_and_rejects_unsafe_file(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    args = dict(deploy_dir=tmp_path, context_dir=tmp_path / 'build', work_dir=tmp_path / 'work',
                main_project='offline', deploy_sha=CAND, previous_commit=PREV)
    record = tx.SameHeadReceipt(PREV, CAND, 'blue', IMG[CAND])
    ops = tx.HostOps(**args)
    assert ops.read_same_head_receipt(10) is None
    ops.write_same_head_receipt(record, 10)
    path = tmp_path / '.git' / tx.SAME_HEAD_RECEIPT
    assert (path.stat().st_mode & 0o777) == 0o600
    assert tx.HostOps(**args).read_same_head_receipt(10) == record
    committed = replace(record, prepared=True, committed=True)
    ops.write_same_head_receipt(committed, 10)
    assert tx.HostOps(**args).read_same_head_receipt(10) == committed
    path.rename(path.with_suffix('.saved'))
    path.symlink_to(path.with_suffix('.saved'))
    with pytest.raises(tx.OpError, match='receipt unreadable'):
        ops.read_same_head_receipt(10)


# The source graph's single head. It is the adult migration's descendant once
# later migrations land (owner-private exercises, the re-landed C1, then G4's
# session outcomes), so a database still at the adult migration, at
# owner-private exercises or at C1 is
# BEHIND head and must not pass --require-at-head. The retired C1 stamp
# c1a9b7e5d301 is not in the graph at all (app/retired_revisions.py).
SOURCE_HEAD = 'c8e1f3a5b702'


@pytest.mark.parametrize('current, success', [(None, False), ([], False),
                                             ([SOURCE_HEAD], True),
                                             ([tx.ADULT_MIGRATION], False),
                                             (['e5b27aa4101c'], False),
                                             (['19cb998acd2f'], False),
                                             (['c1a9b7e5d301'], False),
                                             (['d7e3a9c1f5b8'], False),
                                             ([SOURCE_HEAD, 'unexpected'], False)])
def test_read_only_schema_command_requires_all_expected_heads(app, current, success):
    from sqlalchemy import text
    from alembic.script import ScriptDirectory
    from app import schema_safety
    from app.extensions import db

    expected = schema_safety.expected_heads(app)
    assert expected == {SOURCE_HEAD}
    graph = ScriptDirectory('migrations')
    assert tx.ADULT_MIGRATION in {rev.revision for rev in graph.iterate_revisions(SOURCE_HEAD, 'base')}
    assert db.engine.dialect.name == 'sqlite'
    if current is not None:
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            for revision in current:
                connection.execute(text('INSERT INTO alembic_version VALUES (:revision)'),
                                   {'revision': revision})
    before = schema_safety._read_current_heads(db.engine)
    try:
        result = app.test_cli_runner().invoke(args=['schema-heads', '--require-at-head'])
        payload = json.loads(result.output.split('schema-heads: ')[-1])
        assert result.exit_code == (0 if success else 1)
        assert payload['result'] == ('ok' if success else 'error')
        assert schema_safety._read_current_heads(db.engine) == before
    finally:
        if current is not None:
            with db.engine.begin() as connection:
                connection.execute(text('DROP TABLE alembic_version'))
