"""Exercise the post/watch contract against the transaction's emitted sequence."""

from dataclasses import replace

import pytest

from scripts import r6_recovery_acceptance as check
from tests.test_r6_adult_cutover import adult  # noqa: F401
from tests.test_r6_deploy_transaction import CAND, IMG, OTHER, PREV, FakeOps, run, tx
from tests.test_r6_same_head_recovery import interrupt, same_head  # noqa: F401


STAMP = '2026-11-19T01:02:03.123456789Z'
WEB_START = '2026-11-19T00:58:00.123456789Z'
WORKER_START = '2026-11-19T01:01:00.987654321Z'
SNAPSHOT = f'predeploy-{CAND}-' + '0' * 32


@pytest.fixture(params=['release_prepare', 'acl'])
def release(request, same_head, monkeypatch):
    def new_id(prefix):
        same_head.counter += 1
        return f'{same_head.counter:064x}'

    container = FakeOps._container

    def recorded(self, box, service):
        started = WORKER_START if service == 'worker' else WEB_START
        return replace(container(self, box, service), started_at=started)

    monkeypatch.setattr(same_head, 'new_id', new_id)
    monkeypatch.setattr(FakeOps, '_container', recorded)
    interrupt(same_head, request.param)
    retained_web = same_head.serving_box()
    retained_worker = same_head.worker
    if retained_worker is not None:
        def reuse_worker(self, kind, image_tag, revision, timeout):
            assert kind == 'candidate' and revision == CAND and same_head.worker is retained_worker
            self._rec('worker_up', kind, image_tag, revision, timeout=timeout)

        monkeypatch.setattr(FakeOps, 'worker_up', reuse_worker)
    same_head.events.clear()
    code, _ = run(same_head, mode='forward-recovery')
    assert code == tx.EXIT_OK
    if retained_web is not None:
        assert same_head.serving_box() is retained_web
        assert same_head.worker is retained_worker
    lines = [
        f'{STAMP} forward recovery authorized for this exact run attempt; delete the attempt binding',
        f'{STAMP} Recovery snapshot Available for candidate {CAND}: {SNAPSHOT}',
        *(f'{STAMP} r6-deploy: {event}' for event in same_head.events),
        f'{STAMP} deployment verified at {CAND}',
    ]
    return '\n'.join(lines), same_head


def parse(log, **overrides):
    binding = dict(previous=PREV, candidate=CAND, old_slot='blue', db_heads=[tx.ADULT_MIGRATION])
    return check.parse_recovery_log(log, **(binding | overrides))


def live_service(box, started):
    return dict(container_id=box.id, started_at=started, image_id=box.image_id, app_revision=box.app_revision,
                build_revision=box.revision, state='running', health='healthy', restart_count=0)


def test_recovery_proves_new_or_retained_prepare_route_and_exact_runtime(release):
    log, world = release
    proof = parse(log)
    assert proof.image == IMG[CAND] and proof.revision == CAND and proof.snapshot == SNAPSHOT
    check.verify_runtime(proof, live_service(world.serving_box(), WEB_START), live_service(world.worker, WORKER_START))


@pytest.mark.parametrize('mutation', [
    lambda log: log.replace('same-head-schema-evidence', 'recovery-schema-evidence'),
    lambda log: log.replace(tx.ADULT_MIGRATION, 'd7e3a9c1f5b8'),
    lambda log: log.replace(f'deployment verified at {CAND}', f'deployment verified at {OTHER}'),
    lambda log: log.replace(f'worker-updated revision={CAND}', f'worker-updated revision={OTHER}'),
    lambda log: log.replace('same-head-runtime-proven', 'missing-runtime-proof'),
    lambda log: log.replace('release-committed', 'retirement-incomplete'),
    lambda log: log.replace('release-complete', 'release-committed'),
    lambda log: log.replace('adult-registration-open', 'release-committed').replace(
        'release-committed revision=' + CAND + ' route=green', 'adult-registration-open revision=' + CAND),
    lambda log: log.replace(SNAPSHOT, 'predeploy-' + OTHER + '-' + '0' * 32),
    lambda log: '\n'.join([*log.splitlines()[1:2], *log.splitlines()[:1], *log.splitlines()[2:]]),
    lambda log: '\n'.join([*log.splitlines()[:-2], log.splitlines()[-1], log.splitlines()[-2]]),
])
def test_tampered_or_partial_deploy_evidence_refuses(release, mutation):
    with pytest.raises(ValueError, match='R6_RECOVERY_'):
        parse(mutation(release[0]))


@pytest.mark.parametrize('edit', [dict(image_id=IMG[OTHER]), dict(container_id='f' * 64),
                                  dict(started_at=WORKER_START), dict(restart_count=1),
                                  dict(build_revision=OTHER), dict(health='unhealthy')])
def test_a_different_boot_or_container_cannot_satisfy_watch(release, edit):
    log, world = release
    worker = live_service(world.worker, WORKER_START)
    with pytest.raises(ValueError, match='R6_RECOVERY_LIVE_PAIR_CHANGED'):
        check.verify_runtime(parse(log), live_service(world.serving_box(), WEB_START) | edit, worker)


@pytest.fixture
def identity():
    completed = dict(head_sha=CAND, status='completed', conclusion='success')
    return dict(candidate=CAND, tree='1' * 40, main=CAND, actual_tree='1' * 40, run_id=10, attempt=2, ci_id=11,
                run=completed | dict(id=10, run_attempt=2, head_branch='main', event='workflow_run',
                                     path=check.DEPLOY_PATH),
                job=completed | dict(id=12, run_id=10, run_attempt=2, name='deploy',
                                     started_at='2026-11-19T01:00:00Z', completed_at='2026-11-19T01:03:00Z'),
                ci=completed | dict(id=11, head_branch='main', path=check.CI_PATH, event='push'))


@pytest.mark.parametrize('phase', ['post', 'post2', 'watch1', 'watch2'])
def test_exact_attempt_and_main_push_ci_for_each_post_and_watch_phase(identity, phase):
    check.verify_identity(phase=phase, **identity)


@pytest.mark.parametrize('phase', ['pre', 'readback', 'readback2', 'readback3', None])
def test_profile_excludes_every_other_phase(identity, phase):
    with pytest.raises(ValueError, match='R6_RECOVERY_PHASE_INVALID'):
        check.verify_identity(phase=phase, **identity)


@pytest.mark.parametrize('key,edit', [
    ('main', OTHER), ('actual_tree', '2' * 40), ('candidate', CAND[:8]),
    ('run', {'id': 12}), ('run', {'head_sha': OTHER}), ('run', {'run_attempt': 1}),
    ('job', {'run_attempt': 1}), ('job', {'run_id': 11}), ('job', {'head_sha': OTHER}),
    ('job', {'conclusion': 'skipped'}), ('job', {'completed_at': '2026-11-19T00:00:00Z'}),
    ('ci', {'event': 'pull_request'}), ('ci', {'head_sha': OTHER}), ('ci', {'conclusion': 'failure'}),
])
def test_source_attempt_job_and_ci_mismatch_refuse(identity, key, edit):
    identity[key] = identity[key] | edit if isinstance(edit, dict) else edit
    with pytest.raises(ValueError, match='R6_RECOVERY_'):
        check.verify_identity(phase='post', **identity)


@pytest.mark.parametrize('fault', [None, 'latest', 'job', 'compare', 'tree'])
def test_github_callback_uses_bound_attempt_and_refuses_before_log_on_mismatch(release, identity, fault):
    root = 'repos/example/fixture'
    deploy = f'{root}/actions/runs/10'
    scope = f'{deploy}/attempts/2'
    log_path = f'{root}/actions/jobs/12/logs'
    responses = {
        deploy: dict(run_attempt=1 if fault == 'latest' else 2),
        scope: identity['run'],
        f'{scope}/jobs?per_page=100': dict(total_count=1, jobs=[identity['job'] | (
            {'run_attempt': 1} if fault == 'job' else {})]),
        f'{root}/commits/main': dict(sha=CAND),
        f'{root}/git/commits/{CAND}': dict(tree=dict(sha='2' * 40 if fault == 'tree' else identity['tree'])),
        f'{root}/actions/runs/11': identity['ci'],
        f'{root}/compare/{PREV}...{CAND}': dict(status='ahead', behind_by=0,
                                               files=[dict(filename='wrong' if fault == 'compare' else 'reviewed')]),
        log_path: release[0],
    }
    reads = []

    def read(path, raw=False):
        reads.append(path)
        assert raw == (path == log_path)
        return responses[path]

    binding = dict(serving=PREV, candidate=CAND, candidate_tree=identity['tree'], run='10', ci_run='11',
                   recovery_attempt=2, reviewed_diff=('reviewed',), serving_slot='blue',
                   db_heads=[tx.ADULT_MIGRATION])
    if fault is None:
        job, proof = check.github_recovery('watch2', binding, read, 'example/fixture', tuple)
        assert job == identity['job'] and proof.snapshot == SNAPSHOT and log_path in reads
    else:
        with pytest.raises(ValueError, match='R6_RECOVERY_'):
            check.github_recovery('watch2', binding, read, 'example/fixture', tuple)
        assert log_path not in reads
