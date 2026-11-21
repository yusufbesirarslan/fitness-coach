"""Real PostgreSQL contention through the native editor HTTP boundary."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.test_plan_mutation_history_pg import pg_plan_app  # noqa: F401
from tests.test_mobile_training_editor import (
    FACE_PULL, PLANK, as_owner, mutate, program, revision, save_plan,  # noqa: F401
)


pytestmark = pytest.mark.pg_concurrency


@pytest.mark.parametrize("same_key", [True, False], ids=["retry", "different-writes"])
def test_competing_editor_requests_serialize_and_keep_one_journal_record(
    pg_plan_app, same_key, as_owner, monkeypatch,
):
    from app.blueprints.mobile_api import bp
    from app.extensions import db
    from app.health_boundary import HealthProcessingBoundary
    from app.models import PlanMutationRecord, TrainingPlan, User
    from app.services import health_consent, health_disclosure
    from app.services.plan_mutation import service

    app, owner_id = pg_plan_app
    app.register_blueprint(bp)
    app.wsgi_app = HealthProcessingBoundary(app.wsgi_app)
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", health_disclosure.VERSION)
    with app.app_context():
        TrainingPlan.query.delete()
        save_plan(owner_id, program())
        headers = as_owner(db.session.get(User, owner_id))
        health_consent.mutate(owner_id, {
            "action": "affirmative_grant", "purpose": health_disclosure.PURPOSE,
            "version": health_disclosure.VERSION, "locale": "en",
            "text_digest": health_disclosure.DIGESTS["en"], "expected_generation": 0,
            "request_id": "synthetic-editor-grant",
        })
    binding = revision(app.test_client(), headers)
    barrier = Barrier(2)
    lock_for_mutation = service._lock_for_mutation

    def contend_for_lock(user_id, *, editor):
        # Both requests have already observed an unused key. Let the real
        # PostgreSQL transaction locks decide which one gets the plan first.
        barrier.wait(timeout=10)
        return lock_for_mutation(user_id, editor=editor)

    monkeypatch.setattr(service, "_lock_for_mutation", contend_for_lock)

    def contender(index):
        client = app.test_client()
        key = f"editor-race-{0 if same_key else index}"
        identity = PLANK if same_key or index == 0 else FACE_PULL
        with mutate(client, headers, "add", binding, key,
                    exercise_id=identity, sets=3, reps="12") as response:
            return response.status_code, response.json, response.headers.get("Idempotency-Replayed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(contender, range(2)))
    statuses = sorted(status for status, _, _ in outcomes)
    if same_key:
        assert statuses == [200, 200], outcomes
        assert outcomes[0][1] == outcomes[1][1]
        assert {replayed for _, _, replayed in outcomes} == {"false", "true"}
    else:
        assert statuses == [200, 409], outcomes
        assert next(body for status, body, _ in outcomes if status == 409)["error"]["code"] == (
            "TRAINING_EDITOR_STALE_PLAN"
        )

    with app.app_context():
        row = TrainingPlan.query.filter_by(user_id=owner_id).one()
        assert row.mutation_version == 1
        record = PlanMutationRecord.query.filter_by(user_id=owner_id).one()
        assert record.after_snapshot == row.plan_data
        assert record.before_version == 0
        assert record.after_version == 1
