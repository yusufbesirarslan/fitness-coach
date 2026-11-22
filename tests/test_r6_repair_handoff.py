"""Faults at the exact repair-SHA authority and durable cutover boundaries."""

from dataclasses import asdict, replace
from pathlib import Path
import subprocess

import pytest

from scripts import deploy_control, r6_recovery_acceptance as checks
from tests.test_deploy_control import VALID_ENV
from tests.test_r6_adult_cutover import adult
from tests.test_r6_deploy_transaction import CAND, IMG, OTHER, PREV, Box, FakeOps, names, run, tx
from tests.test_r6_same_head_recovery import interrupt, same_head


REPAIR = OTHER
NEXT = 'e1' * 20
TREE = '1' * 40
FAILED_TREE = '2' * 40
STAGE_RUN = '500'
FAILED_RUN = '400'
STAMP = '2026-11-20T03:01:00Z'


def snapshot(revision, digit):
    return f'predeploy-{revision}-' + digit * 32


def stage_auth(original):
    return ':'.join(('repair-stage', REPAIR, STAGE_RUN, '1', TREE, CAND, FAILED_TREE,
                     tx.repair.fingerprint(asdict(original)), FAILED_RUN, '3', '450',
                     snapshot(CAND, '0'), '3' * 64))


def apply_auth(record, attempt, *, image=None, revision=REPAIR):
    return (f'repair-apply:{revision}:{STAGE_RUN}:{attempt}:{record.staged_identity}:'
            f'{image or record.image}:{record.stage.failed_snapshot}')


def attempt(w, mode, authorization, available, *, revision=REPAIR):
    w.calls.clear()
    w.events.clear()
    return run(w, deploy_sha=revision, previous_commit=w.head, mode=mode,
               repair_authorization=authorization, repair_snapshot=available)


@pytest.fixture
def failed(same_head, monkeypatch):
    w = same_head
    old = w.route
    target = tx.CANDIDATE_SLOT[old]
    interrupt(w, 'acl')
    w.serving_box().deep_ok = False
    original = w.same_head_receipt
    # The same image is fundamentally bad. A fresh container ID does not help.
    w.faults['deep'] = True
    for _ in range(2):
        broken = w.serving_box()
        broken.status, broken.health = 'exited', None
        assert run(w, mode='forward-recovery')[0] == tx.EXIT_ROLLBACK_INCOMPLETE
        assert w.serving_box().id != broken.id and w.serving_box().health == 'healthy'
        assert not w.serving_box().deep_ok and w.same_head_receipt == original
        assert not w.registration_open and old not in w.slots
    w.faults.pop('deep')
    assert run(w, deploy_sha=REPAIR, mode='forward-recovery')[0] == tx.EXIT_ROLLBACK_INCOMPLETE
    # External dual-lock quarantine is separately authorized. Model its proven
    # postconditions. The stage refuses unless it independently observes them.
    del w.slots[target]
    w.worker = None
    w.route = old
    w.head = CAND  # also supports the original previous checkout
    w.manifests[IMG[REPAIR]] = w.manifests[IMG[CAND]]
    monkeypatch.setitem(IMG, NEXT, 'sha256:' + 'f' * 64)
    w.manifests[IMG[NEXT]] = w.manifests[IMG[CAND]]
    w.baked[IMG[NEXT]] = NEXT

    def source(self, stage, previous, timeout):
        self._rec('repair_source_identity', timeout=timeout)
        if (stage.tree, stage.failed_tree, previous, stage.failed) != (TREE, FAILED_TREE, PREV, CAND):
            raise tx.OpError('unapproved source tree')

    def archive(self, record, timeout):
        self._rec('archive_repair_receipt', record, timeout=timeout)

    def ingress(self, state, slot, timeout):
        assert state == w.route
        if w.faults.get('obsolete_listener') and slot == "empty":
            raise tx.OpError('OBSOLETE_APPLICATION_LISTENER')
        self._rec('ingress_snapshot', state, timeout=timeout)
        generation = (1 if w.route == old else 2) + (0 if w.faults.get('no_retirement') else w.reloads * 2)
        if w.head == REPAIR and w.route == old:
            generation += 6
        return {'configuration': 'd' * 64, 'master': [1, 10], 'workers': [[generation, generation * 10]]}

    def reload(self, slot, allowed_slot, timeout):
        assert w.route == slot
        assert allowed_slot in ("empty", target)
        self._rec('reload_repair_generation', slot, timeout=timeout)
        w.reloads += 1

    def latch(self, slot, revision, action, timeout):
        self._rec('registration_latch', action, timeout=timeout)
        if action == 'open':
            assert w.head == revision and w.worker.revision == revision and w.route == slot
            assert w.slots[slot].revision == revision
        w.registration_open = action == 'open'
        if action == 'open':
            return 'a' * 64

    def close_worker(self, previous, timeout):
        self._rec('close_old_worker', timeout=timeout)
        if w.worker is not None and w.worker.revision == previous:
            w.worker = None

    def inventory(self, prior_slot, _, slot, revision, closed, timeout):
        if closed:
            assert prior_slot not in w.slots

    def prepare(self, revision, timeout):
        assert revision == NEXT and w.worker is None and not w.slots
        w.registration_open = False
        self._rec('release_prepare', revision, timeout=timeout)

    def new_id(prefix):
        w.counter += 1
        return f'{w.counter:064x}'

    orig_container = FakeOps._container

    def container(self, box, service):
        return replace(orig_container(self, box, service), started_at=STAMP)

    monkeypatch.setattr(w, 'new_id', new_id)
    monkeypatch.setattr(FakeOps, '_container', container)
    monkeypatch.setattr(FakeOps, 'repair_source_identity', source, raising=False)
    monkeypatch.setattr(FakeOps, 'archive_repair_receipt', archive, raising=False)
    monkeypatch.setattr(FakeOps, 'reload_repair_generation', reload, raising=False)
    monkeypatch.setattr(FakeOps, 'ingress_snapshot', ingress)
    monkeypatch.setattr(FakeOps, 'registration_latch', latch)
    monkeypatch.setattr(FakeOps, 'adult_web_inventory', inventory)
    monkeypatch.setattr(FakeOps, 'close_old_worker', close_worker)
    monkeypatch.setattr(FakeOps, 'release_prepare', prepare)
    return w, original


@pytest.fixture
def staged(failed):
    w, original = failed
    assert attempt(w, 'repair-stage', stage_auth(original), snapshot(REPAIR, '1'))[0] == tx.EXIT_OK
    assert 'build_image' in names(w)
    assert not {'slot_start', 'route_switch', 'worker_up', 'release_prepare'} & set(names(w))
    return w, w.same_head_receipt


@pytest.mark.parametrize('adult', ['blue', 'green'], indirect=True)
def test_authorized_repair_preserves_fence_and_allows_next_normal_release(staged):
    w, record = staged
    old = w.route
    code, tr = attempt(w, 'repair-apply', apply_auth(record, 2), snapshot(REPAIR, '2'))
    assert code == tx.EXIT_OK
    committed = w.same_head_receipt
    assert committed.committed and committed.original == record.original and committed.last_apply == 2
    assert w.route == tx.CANDIDATE_SLOT[old] and w.head == REPAIR and w.registration_open
    assert w.serving_box().image_id == w.worker.image_id == IMG[REPAIR]
    assert old not in w.slots and w.db_heads == set(record.heads)
    assert 'release_prepare' not in names(w) and 'build_image' not in names(w)
    calls = names(w)
    assert calls.index('reload_repair_generation') < calls.index('slot_start') < calls.index('route_switch')
    assert w.calls.index(('registration_latch', 'open')) > calls.index('checkout_reset')
    assert 'repair-handoff' in dict(tr.events)
    assert attempt(w, 'repair-apply', apply_auth(record, 3), snapshot(REPAIR, '3'))[0] == tx.EXIT_ROLLBACK_INCOMPLETE
    w.events.clear()
    code, _ = run(w, previous_commit=REPAIR, deploy_sha=NEXT)
    assert code == tx.EXIT_OK, '\n'.join(w.events)
    assert ('archive_repair_receipt', committed) in w.calls
    assert w.same_head_receipt == tx.SameHeadReceipt(REPAIR, NEXT, tx.CANDIDATE_SLOT[old], IMG[NEXT], True, True)


@pytest.mark.parametrize('bad', ['sha', 'stage-snapshot', 'image', 'attempt', 'tree', 'schema',
                                 'route', 'old-actor', 'failed-actor', 'failed-migrator',
                                 'generation', 'obsolete-listener'])
def test_unproven_repair_never_starts_or_changes_writers(staged, bad, monkeypatch):
    w, record = staged
    authorization = apply_auth(record, 2)
    offered_snapshot = snapshot(REPAIR, '2')
    if bad == 'sha':
        authorization = apply_auth(record, 2, revision=CAND)
    elif bad == 'stage-snapshot':
        offered_snapshot = record.stage_snapshot
    elif bad == 'obsolete-listener':
        w.faults['obsolete_listener'] = True
    elif bad == 'image':
        authorization = apply_auth(record, 2, image=IMG[CAND])
    elif bad == 'attempt':
        authorization = apply_auth(record, 1)
    elif bad == 'tree':
        altered = replace(record, stage_authorization=record.stage_authorization.replace(TREE, '8' * 40))
        w.same_head_receipt = record = altered
        authorization = apply_auth(altered, 2)
    elif bad == 'schema':
        w.db_heads = frozenset({'unexpected'})
    elif bad == 'route':
        w.route = tx.CANDIDATE_SLOT[w.route]
    elif bad == 'old-actor':
        w.worker = Box('old-actor', IMG[PREV], PREV, PREV)
    elif bad == 'failed-actor':
        w.slots[tx.CANDIDATE_SLOT[w.route]] = Box('failed', IMG[CAND], CAND, CAND)
    elif bad == 'failed-migrator':
        orig = FakeOps.container

        def inspect(self, identity, timeout):
            if identity == tx.release_prepare_container_name(CAND):
                return Box(identity, IMG[CAND], CAND, CAND)
            return orig(self, identity, timeout)

        monkeypatch.setattr(FakeOps, 'container', inspect)
    elif bad == 'generation':
        w.faults['no_retirement'] = True
    assert attempt(w, 'repair-apply', authorization, offered_snapshot)[0] == tx.EXIT_ROLLBACK_INCOMPLETE
    assert w.same_head_receipt == record and not w.registration_open
    assert not {'slot_start', 'slot_remove', 'route_switch', 'worker_up', 'release_prepare'} & set(names(w))


@pytest.mark.parametrize('point', ['stage', 'claim', 'open'])
def test_interrupted_handoff_resumes_only_with_distinct_apply_authority(failed, monkeypatch, point):
    w, original = failed
    write = FakeOps.write_same_head_receipt
    latch = FakeOps.registration_latch

    class LostHost(BaseException):
        pass

    def interrupt_write(self, record, timeout):
        write(self, record, timeout)
        if isinstance(record, tx.repair.RepairReceipt) and bool(record.last_apply) == (point == 'claim'):
            raise LostHost()

    def interrupt_open(self, slot, revision, action, timeout):
        latch(self, slot, revision, action, timeout)
        if action == 'open':
            raise LostHost()

    if point == 'stage':
        with monkeypatch.context() as patch, pytest.raises(LostHost):
            patch.setattr(FakeOps, 'write_same_head_receipt', interrupt_write)
            attempt(w, 'repair-stage', stage_auth(original), snapshot(REPAIR, '1'))
        record = w.same_head_receipt
        assert not record.last_apply
        assert attempt(w, 'repair-stage', stage_auth(original), snapshot(REPAIR, '1'))[0] != tx.EXIT_OK
    else:
        assert attempt(w, 'repair-stage', stage_auth(original), snapshot(REPAIR, '1'))[0] == tx.EXIT_OK
        record = w.same_head_receipt
        with monkeypatch.context() as patch, pytest.raises(LostHost):
            if point == 'claim':
                patch.setattr(FakeOps, 'write_same_head_receipt', interrupt_write)
            else:
                patch.setattr(FakeOps, 'registration_latch', interrupt_open)
            attempt(w, 'repair-apply', apply_auth(record, 2), snapshot(REPAIR, '2'))
        assert w.same_head_receipt.last_apply == 2 and not w.same_head_receipt.committed
        assert attempt(w, 'repair-apply', apply_auth(record, 2), snapshot(REPAIR, '2'))[0] != tx.EXIT_OK
    assert attempt(w, 'normal', '', '', revision=NEXT)[0] != tx.EXIT_OK
    assert attempt(w, 'forward-recovery', '', '', revision=CAND)[0] != tx.EXIT_OK
    assert attempt(w, 'repair-apply', apply_auth(record, 3), snapshot(REPAIR, '3'))[0] == tx.EXIT_OK
    assert w.same_head_receipt.committed and w.registration_open and w.head == REPAIR


def test_repeated_repair_application_failure_does_not_admit_a_different_revision(staged):
    w, record = staged
    w.faults['deep'] = True
    for n in (2, 3):
        assert attempt(w, 'repair-apply', apply_auth(record, n), snapshot(REPAIR, str(n)))[0] != tx.EXIT_OK
        assert w.same_head_receipt.last_apply == n and not w.same_head_receipt.committed
        assert not w.registration_open and w.route == record.original['old_slot']
        assert 'build_image' not in names(w) and 'release_prepare' not in names(w)
    assert attempt(w, 'normal', '', '', revision=NEXT)[0] != tx.EXIT_OK


def test_receipt_roundtrip_and_archival_uses_durable_private_metadata(staged, tmp_path):
    _, record = staged
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    ops = tx.HostOps(deploy_dir=tmp_path, context_dir=tmp_path, work_dir=tmp_path,
                     main_project='fixture', deploy_sha=REPAIR, previous_commit=PREV)
    active = tmp_path / '.git' / tx.SAME_HEAD_RECEIPT
    ops.write_same_head_receipt(record, 10)
    assert active.stat().st_mode & 0o777 == 0o600
    assert ops.read_same_head_receipt(10) == record
    terminal = replace(record, committed=True, last_apply=2, last_snapshot=snapshot(REPAIR, '2'))
    ops.archive_repair_receipt(terminal, 10)
    ops.archive_repair_receipt(terminal, 10)
    with pytest.raises(tx.OpError, match='archive differs'):
        ops.archive_repair_receipt(record, 10)


def test_controller_repair_authority_requires_actual_run_and_attempt(staged):
    _, record = staged
    env = VALID_ENV | {'DEPLOY_SHA': REPAIR, 'GITHUB_RUN_ID': STAGE_RUN, 'GITHUB_RUN_ATTEMPT': '2',
                       deploy_control.REPAIR_AUTHORIZATION_ENV: apply_auth(record, 2)}
    cfg = deploy_control.DeployConfig.from_environ(env)
    assert cfg.transaction_mode == 'repair-apply'
    with pytest.raises(deploy_control.ConfigError):
        deploy_control.DeployConfig.from_environ(env | {'GITHUB_RUN_ATTEMPT': '3'})
    with pytest.raises(deploy_control.ConfigError):
        deploy_control.DeployConfig.from_environ(env | {deploy_control.FORWARD_RECOVERY_AUTHORIZATION_ENV: 'both'})
    with pytest.raises(deploy_control.ConfigError):
        deploy_control.build_remote_command(cfg, Path('scripts/production_deploy.sh').read_bytes())
    command = deploy_control.build_remote_command(replace(cfg, repair_snapshot=snapshot(REPAIR, '2')),
                                                  Path('scripts/production_deploy.sh').read_bytes())
    assert 0 < len(command) <= deploy_control.LOCAL_RUN_SHELL_COMMAND_MAX_CHARS


@pytest.mark.parametrize('phase', ['post', 'post2', 'watch1', 'watch2'])
def test_post_watch_accepts_only_the_exact_repair_attempt_and_live_actors(staged, phase):
    w, record = staged
    current = snapshot(REPAIR, '2')
    assert attempt(w, 'repair-apply', apply_auth(record, 2), current)[0] == tx.EXIT_OK
    log = '\n'.join((f'{STAMP} repair-apply authorized for this exact run attempt',
                    f'{STAMP} Retained failed-candidate snapshot Available: {record.stage.failed_snapshot}',
                    f'{STAMP} Recovery snapshot Available for candidate {REPAIR}: {current}',
                    *(f'{STAMP} r6-deploy: {event}' for event in w.events),
                    f'{STAMP} deployment verified at {REPAIR}'))
    base = dict(previous=PREV, candidate=REPAIR, old_slot=record.original['old_slot'], db_heads=record.heads)
    review = checks.RepairProof(CAND, IMG[CAND], record.stage.failed_snapshot, TREE, record.image,
                                record.staged_identity, STAGE_RUN, 1, 2)
    for proof in (None, replace(review, attempt=3), replace(review, tree=FAILED_TREE),
                  replace(review, receipt='0' * 64), replace(review, image=IMG[CAND])):
        with pytest.raises(ValueError):
            checks.parse_recovery_log(log, **base, repair=proof)

    root = 'repos/example/fixture'
    deploy = f'{root}/actions/runs/{STAGE_RUN}'
    ok = dict(head_sha=REPAIR, head_branch='main', status='completed', conclusion='success')
    job = ok | dict(id=502, run_id=int(STAGE_RUN), run_attempt=2, name='deploy',
                    started_at=STAMP, completed_at=STAMP)
    responses = {
        deploy: dict(run_attempt=2),
        f'{deploy}/attempts/2': ok | dict(id=int(STAGE_RUN), run_attempt=2, path=checks.DEPLOY_PATH,
                                        event='workflow_run'),
        f'{deploy}/attempts/2/jobs?per_page=100': dict(total_count=1, jobs=[job]),
        f'{root}/commits/main': dict(sha=REPAIR),
        f'{root}/git/commits/{REPAIR}': dict(tree=dict(sha=TREE)),
        f'{root}/actions/runs/501': ok | dict(id=501, path=checks.CI_PATH, event='push'),
        f'{root}/compare/{PREV}...{REPAIR}': dict(status='ahead', behind_by=0, files=[dict(filename='reviewed')]),
        f'{root}/actions/jobs/502/logs': log,
    }
    binding = dict(candidate=REPAIR, serving=PREV, candidate_tree=TREE, run=STAGE_RUN, ci_run='501',
                   recovery_attempt=2, transaction_mode='repair-apply', reviewed_diff=('reviewed',),
                   serving_slot=record.original['old_slot'], db_heads=record.heads,
                   repair={k: v for k, v in asdict(review).items() if k not in ('tree', 'run', 'attempt')})

    def gh(path, raw=False):
        return responses[path]

    _, proof = checks.github_recovery(phase, binding, gh, 'example/fixture', tuple)
    assert proof.snapshot == current and proof.image == IMG[REPAIR]

    def actor(box):
        return dict(container_id=box.id, started_at=STAMP, image_id=box.image_id, app_revision=REPAIR,
                    build_revision=REPAIR, state='running', health='healthy', restart_count=0)

    checks.verify_runtime(proof, actor(w.serving_box()), actor(w.worker))
    for changed in (dict(image_id=IMG[CAND]), dict(container_id='0' * 64), dict(restart_count=1)):
        with pytest.raises(ValueError, match='LIVE_PAIR_CHANGED'):
            checks.verify_runtime(proof, actor(w.serving_box()) | changed, actor(w.worker))
