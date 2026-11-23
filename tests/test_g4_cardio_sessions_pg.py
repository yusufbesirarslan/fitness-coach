"""G4 (#530) cardio-session outcomes on real PostgreSQL (16 and 18 in CI)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock

import pytest
import sqlalchemy as sa

from tests.test_plan_mutation_history_pg import pg_plan_app  # noqa: F401
from tests.test_g4_cardio_sessions import as_owner, document, save  # noqa: F401


pytestmark = pytest.mark.pg_concurrency

MONDAY = date(2026, 11, 23)


def _serve(app, owner_id, monkeypatch):
    from app.blueprints.mobile_api import bp
    from app.extensions import db
    from app.health_boundary import HealthProcessingBoundary
    from app.models import TrainingPlan
    from app.services import health_consent, health_disclosure, mobile_training
    from app.services import training_session_outcomes

    app.register_blueprint(bp)
    app.wsgi_app = HealthProcessingBoundary(app.wsgi_app)
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", health_disclosure.VERSION)
    monkeypatch.setattr(mobile_training, "app_today", lambda: MONDAY)
    monkeypatch.setattr(training_session_outcomes, "app_today", lambda: MONDAY)
    with app.app_context():
        TrainingPlan.query.delete()
        db.session.commit()
        save(owner_id, document())
        health_consent.mutate(owner_id, {
            "action": "affirmative_grant", "purpose": health_disclosure.PURPOSE,
            "version": health_disclosure.VERSION, "locale": "en",
            "text_digest": health_disclosure.DIGESTS["en"], "expected_generation": 0,
            "request_id": "synthetic-g4-grant",
        })


def _session_ref(app, headers):
    with app.test_client().get("/api/v1/training/plans/current?include_sessions=1",
                               headers=headers) as response:
        assert response.status_code == 200, response.json
        return response.json["plan"]["days"][0]["cardio_session"]["workout_ref"]


@pytest.mark.parametrize("answers", [("complete", "complete"), ("complete", "skip")],
                         ids=["same-answer", "different-answers"])
def test_concurrent_answers_claim_one_outcome(pg_plan_app, as_owner, monkeypatch, answers):
    from app.extensions import db
    from app.models import TrainingSessionOutcome, User, WorkoutCompletionDay
    from app.services import training_session_outcomes

    app, owner_id = pg_plan_app
    _serve(app, owner_id, monkeypatch)
    with app.app_context():
        headers = as_owner(db.session.get(User, owner_id))
    ref = _session_ref(app, headers)
    barrier = Barrier(2)
    existing = training_session_outcomes._existing
    first_reads = []
    guard = Lock()

    def contend(user_id, date_key):
        # Both requests read "no outcome yet"; PostgreSQL's unique claim
        # decides. Only each request's first read waits; the loser's re-read
        # after the IntegrityError passes straight through.
        row = existing(user_id, date_key)
        with guard:
            first = len(first_reads) < 2
            first_reads.append(1)
        if first:
            barrier.wait(timeout=10)
        return row

    monkeypatch.setattr(training_session_outcomes, "_existing", contend)

    def contender(answer):
        with app.test_client().post(f"/api/v1/training/sessions/{ref}/{answer}",
                                    headers=headers) as response:
            return response.status_code, response.json

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(contender, answers))
    monkeypatch.setattr(training_session_outcomes, "_existing", existing)

    statuses = sorted(status for status, _ in outcomes)
    if answers[0] == answers[1]:
        assert statuses == [200, 200], outcomes
        assert sorted(body["replayed"] for _, body in outcomes) == [False, True]
    else:
        assert statuses == [200, 409], outcomes
        loser = next(body for status, body in outcomes if status == 409)
        assert loser["error"]["code"] == "TRAINING_SESSION_OUTCOME_CONFLICT"
    with app.app_context():
        assert TrainingSessionOutcome.query.filter_by(user_id=owner_id).count() == 1
        # The day claim, and everything that hangs off it, is untouched.
        assert WorkoutCompletionDay.query.filter_by(user_id=owner_id).count() == 0


def test_postgres_enforces_the_outcome_vocabulary_and_owner_cascade(pg_plan_app):
    from app.extensions import db

    app, owner_id = pg_plan_app
    insert = sa.text(
        "INSERT INTO training_session_outcome (user_id, date_key, session_kind, outcome, "
        "plan_lineage_id, mutation_version, created_at) VALUES "
        "(:user, '2026-11-23', :kind, :outcome, 'lineage', :version, now())")
    with app.app_context():
        for kind, outcome, version in (("strength", "completed", 0),
                                       ("cardio", "started", 0),
                                       ("cardio", "completed", -1)):
            with pytest.raises(sa.exc.IntegrityError):
                with db.engine.begin() as connection:
                    connection.execute(insert, {"user": owner_id, "kind": kind,
                                                "outcome": outcome, "version": version})
        with db.engine.begin() as connection:
            connection.execute(insert, {"user": owner_id, "kind": "cardio",
                                        "outcome": "skipped", "version": 0})
        with db.engine.begin() as connection:
            # The account's own rows go with it (FK ON DELETE CASCADE).
            connection.execute(sa.text("DELETE FROM training_plan WHERE user_id = :user"),
                               {"user": owner_id})
            connection.execute(sa.text('DELETE FROM "user" WHERE id = :user'),
                               {"user": owner_id})
        with db.engine.connect() as connection:
            assert connection.execute(sa.text(
                "SELECT count(*) FROM training_session_outcome")).scalar_one() == 0
