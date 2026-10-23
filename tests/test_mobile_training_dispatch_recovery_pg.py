"""Real PostgreSQL crash boundaries; providers are synthetic, never networked."""
import json
from contextlib import nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from consent_projection_support import synthetic_authority

from tests.test_mobile_training_generation_pg import (  # noqa: F401
    pg_generation_app, _request, _document, _run,
)

pytestmark = pytest.mark.pg_concurrency


@pytest.fixture(autouse=True)
def _lp21_synthetic_operation_authority(monkeypatch):
    # LP21: these races use injected synthetic providers/writers; ALLOW at the
    # operation-fencing decision points only. Real D1 operation fencing of the
    # same writers is qualified in tests/test_lp21_d1_operation_fencing_pg.py.
    from consent_projection_support import allow_operation_fences
    allow_operation_fences(monkeypatch)


class Crash(BaseException):
    """Bypass application exception handling, like abrupt worker death."""


@pytest.mark.parametrize('boundary,status,calls', [
    ('before_claim_commit', None, 0),
    ('after_claim_commit', 'IN_PROGRESS', 0),
    ('after_marker_commit', 'IN_PROGRESS', 0),
    ('in_provider', 'IN_PROGRESS', 1),
    ('before_stage', 'IN_PROGRESS', 1),
    ('during_stage_commit', 'IN_PROGRESS', 1),
    ('during_marker_commit', 'IN_PROGRESS', 0),
    ('after_stage', 'GENERATED', 1),
    ('during_plan_insert', 'GENERATED', 1),
    ('after_plan_commit', 'SUCCEEDED', 1),
])
def test_crash_boundary_never_repeats_possible_dispatch(
        pg_generation_app, boundary, status, calls):
    from app.extensions import db
    from app.models import User, TrainingPlanGenerationOperation, TrainingPlan
    from app.services.mobile_training_generation import service, store
    from app.services.mobile_training_generation.errors import GenerationInProgress
    app, owners, monkeypatch = pg_generation_app
    executions = []

    def candidate(*args, **kwargs):
        executions.append(1)
        if boundary == 'in_provider':
            raise Crash()
        return SimpleNamespace(document=_document(), overall_score=8.5)

    monkeypatch.setattr(service, 'generate_training_plan_candidate', candidate)
    target = {
        'after_claim_commit': 'claim',
        'after_marker_commit': 'authorize_dispatch',
        'before_stage': 'stage_candidate',
        'after_stage': 'stage_candidate',
        'during_plan_insert': '_insert_plan',
        'after_plan_commit': 'commit_plan',
        'during_stage_commit': 'stage_candidate',
        'during_marker_commit': 'authorize_dispatch',
    }.get(boundary)
    original = getattr(store, target) if target else None

    def inject(*args, **kwargs):
        if boundary in ('during_stage_commit', 'during_marker_commit'):
            original_commit = db.session.commit
            def fail_commit():
                db.session.flush()
                raise Crash()
            monkeypatch.setattr(db.session, 'commit', fail_commit)
            try:
                original(*args, **kwargs)
            finally:
                monkeypatch.setattr(db.session, 'commit', original_commit)
        if boundary not in ('before_stage', 'during_plan_insert'):
            original(*args, **kwargs)
        raise Crash()

    with app.app_context():
        if boundary == 'before_claim_commit':
            original_commit = db.session.commit
            def crash_commit():
                db.session.flush()
                raise Crash()
            monkeypatch.setattr(db.session, 'commit', crash_commit)
        elif target:
            monkeypatch.setattr(store, target, inject)
        with pytest.raises(Crash):
            service.generate_and_persist(
                db.session.get(User, owners[0]), _request(), 'crash-boundary-key',
                chat_fn=None, provider_guard=nullcontext)
        db.session.remove()  # roll back uncommitted changes / release connections
        op = TrainingPlanGenerationOperation.query.one_or_none()
        assert (op.status if op else None) == status
        assert len(executions) == calls
        if op and boundary not in ('after_claim_commit', 'during_marker_commit'):
            assert op.provider_dispatch_possible is True
        if target:
            monkeypatch.setattr(store, target, original)
        if boundary == 'before_claim_commit':
            monkeypatch.setattr(db.session, 'commit', original_commit)
        monkeypatch.setattr(service, 'generate_training_plan_candidate', lambda *a, **k: (
            executions.append(1) or SimpleNamespace(document=_document(), overall_score=8.5)))
        command = lambda: service.generate_and_persist(
            db.session.get(User, owners[0]), _request(), 'crash-boundary-key',
            chat_fn=None, provider_guard=nullcontext)
        if status == 'IN_PROGRESS' and boundary not in ('after_claim_commit', 'during_marker_commit'):
            with pytest.raises(GenerationInProgress):
                command()
            assert len(executions) == calls
            assert TrainingPlan.query.count() == 0
        else:
            command()
            assert len(executions) == (1 if calls == 0 else calls)
            assert TrainingPlan.query.count() == 1


def test_provider_has_no_transaction_owner_or_operation_lock(pg_generation_app):
    from app.extensions import db
    from app.models import User
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.locking import _advisory_key
    app, owners, monkeypatch = pg_generation_app
    def candidate(*args, **kwargs):
        assert not db.session().in_transaction()
        with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            assert conn.exec_driver_sql('SELECT pg_try_advisory_lock(%s)',
                                        (_advisory_key(owners[0]),)).scalar()
            conn.exec_driver_sql('SELECT pg_advisory_unlock(%s)', (_advisory_key(owners[0]),))
        with db.engine.begin() as conn:
            conn.exec_driver_sql('SELECT id FROM "user" WHERE id=%s FOR UPDATE NOWAIT',
                                 (owners[0],))
            conn.exec_driver_sql('SELECT id FROM training_plan_generation_operation '
                                 'WHERE user_id=%s FOR UPDATE NOWAIT', (owners[0],))
        return SimpleNamespace(document=_document(), overall_score=8.5)
    monkeypatch.setattr(service, 'generate_training_plan_candidate', candidate)
    outcomes = {}
    _run(app, owners[0], _request(), 'no-provider-lock-key', outcomes, 0)
    assert outcomes[0][0] == 'ok', outcomes


def test_staging_failure_and_abandonment_fence_late_worker(pg_generation_app):
    from app.extensions import db
    from app.models import User, TrainingPlanGenerationOperation
    from app.services.mobile_training_generation import service, store
    from app.services.mobile_training_generation.errors import (
        GenerationPersistenceUnavailable, GenerationInProgress, StoredGenerationFailure)
    app, owners, monkeypatch = pg_generation_app
    executions = []
    monkeypatch.setattr(service, 'generate_training_plan_candidate', lambda *a, **k: (
        executions.append(1) or SimpleNamespace(document=_document(), overall_score=8.5)))
    real_stage = store.stage_candidate
    monkeypatch.setattr(store, 'stage_candidate', lambda *a, **k: (
        (_ for _ in ()).throw(GenerationPersistenceUnavailable())))
    with app.app_context():
        def command(key):
            return service.generate_and_persist(db.session.get(User, owners[0]),
                _request(), key, chat_fn=None, provider_guard=nullcontext)
        with pytest.raises(GenerationPersistenceUnavailable):
            command('stage-failure-original')
        monkeypatch.setattr(store, 'stage_candidate', real_stage)
        for key in ('stage-failure-original', 'competing-new-key'):
            with pytest.raises(GenerationInProgress):
                command(key)
        assert executions == [1]
        old = TrainingPlanGenerationOperation.query.one()
        old_id = old.id
        old.updated_at = datetime.utcnow() - timedelta(minutes=16)
        db.session.commit()
        with pytest.raises(StoredGenerationFailure) as failure:
            command('stage-failure-original')
        assert failure.value.public_code == 'TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN'
        assert failure.value.retryable is False
        with pytest.raises(GenerationPersistenceUnavailable):
            real_stage(old_id, json.dumps(_document()), 8.5, synthetic_authority(owners[0]))
        # A genuinely new attempt is separately admitted, never same-key recovery.
        command('explicit-new-attempt')
        assert executions == [1, 1]
        db.session.expire_all()
        old = db.session.get(TrainingPlanGenerationOperation, old_id)
        assert old.status == 'FAILED'
        assert old.provider_dispatch_possible is True


def test_historical_in_progress_is_unknown_not_redispatchable(pg_generation_app):
    from app.extensions import db
    from app.models import TrainingPlanGenerationOperation
    app, owners, monkeypatch = pg_generation_app
    with app.app_context():
        db.session.add(TrainingPlanGenerationOperation(user_id=owners[0],
            idempotency_key='historical-unknown-key', request_fingerprint=_request().fingerprint,
            status='IN_PROGRESS'))
        db.session.commit()
    from app.services.mobile_training_generation import service
    monkeypatch.setattr(service, 'generate_training_plan_candidate',
                        lambda *a, **k: pytest.fail('historical redispatch'))
    outcomes = {}
    _run(app, owners[0], _request(), 'historical-unknown-key', outcomes, 0)
    assert outcomes[0] == ('in_progress',)


def test_recovery_observes_candidate_committed_between_key_lookups(pg_generation_app):
    """The second read must refresh the first read's ORM identity-map object."""
    import threading
    from app.extensions import db
    from app.models import User
    from app.services.mobile_training_generation import service, store
    app, owners, monkeypatch = pg_generation_app
    request = _request()
    with app.app_context():
        operation = store.claim(owners[0], 'candidate-between-lookups', request.fingerprint)
        operation_id = operation.id
        store.authorize_dispatch(operation_id)
        db.session.remove()
    real_find = store.find_by_key
    lookups = []
    worker_errors = []
    def find_and_stage(*args):
        operation = real_find(*args)
        lookups.append(1)
        if len(lookups) == 1:
            def stage_on_separate_connection():
                with app.app_context():
                    try:
                        store.stage_candidate(operation_id, json.dumps(_document()), 8.5,
                                              synthetic_authority(owners[0]))
                    except Exception as error:
                        worker_errors.append(type(error).__name__)
                    finally:
                        db.session.remove()
            worker = threading.Thread(target=stage_on_separate_connection)
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive()
            assert worker_errors == []
        return operation
    monkeypatch.setattr(store, 'find_by_key', find_and_stage)
    monkeypatch.setattr(service, 'generate_training_plan_candidate',
                        lambda *a, **k: pytest.fail('recovered candidate redispatched'))
    outcomes = {}
    _run(app, owners[0], request, 'candidate-between-lookups', outcomes, 0)
    assert outcomes[0][0] == 'ok', outcomes
