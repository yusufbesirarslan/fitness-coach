"""PD2 training start on real PostgreSQL: Accept races, exactly-once activation.

Synthetic providers only; disposable database only (PG_TEST_DATABASE_URL).
"""
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tests.test_mobile_training_generation_pg import (  # noqa: F401
    pg_generation_app, _request, _document,
)

pytestmark = pytest.mark.pg_concurrency


@pytest.fixture(autouse=True)
def _lp21_synthetic_operation_authority(monkeypatch):
    from consent_projection_support import allow_operation_fences
    allow_operation_fences(monkeypatch)


def _stage_preview(app, monkeypatch, user_id, key):
    from app.extensions import db
    from app.models import User
    from app.services.mobile_training_generation import service
    calls = []
    monkeypatch.setattr(service, "generate_training_plan_candidate",
                        lambda *a, **k: calls.append(1) or SimpleNamespace(
                            document=_document(), overall_score=8.5))
    with app.app_context():
        staged = service.generate_preview(
            db.session.get(User, user_id), _request(), key,
            chat_fn=lambda **kwargs: "unused", provider_guard=nullcontext)
        db.session.remove()
    assert calls == [1]
    return staged.candidate_fingerprint


def _race(app, user_id, actions):
    """Run each action(user) in its own thread, released together."""
    from app.extensions import db
    from app.models import User
    barrier = threading.Barrier(len(actions))
    outcomes = {}

    def run(index, action):
        try:
            with app.app_context():
                user = db.session.get(User, user_id)
                barrier.wait(timeout=20)
                try:
                    outcomes[index] = ("ok", action(user))
                except Exception as error:  # classified by the assertions
                    outcomes[index] = (type(error).__name__,
                                       getattr(error, "public_code", None))
                finally:
                    db.session.remove()
        except Exception as error:  # pragma: no cover
            outcomes[index] = ("unexpected", repr(error))

    threads = [threading.Thread(target=run, args=(index, action), daemon=True)
               for index, action in enumerate(actions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    return [outcomes[index] for index in range(len(actions))]


def _plans(app, user_id):
    from app.extensions import db
    from app.models import TrainingPlan
    with app.app_context():
        plans = TrainingPlan.query.filter_by(user_id=user_id).all()
        result = [(plan.id, plan.plan_data) for plan in plans]
        db.session.remove()
    return result


def test_concurrent_accepts_activate_exactly_one_plan(pg_generation_app):
    from app.services.mobile_training_generation import service
    app, user_ids, monkeypatch = pg_generation_app
    owner = user_ids[0]
    fingerprint = _stage_preview(app, monkeypatch, owner, "pd2-pg-accept-1")

    def accept(user):
        result = service.accept_preview(user, "pd2-pg-accept-1", fingerprint)
        return (result.replayed, result.plan.id)

    outcomes = _race(app, owner, [accept] * 6)

    fresh = [o for o in outcomes if o[0] == "ok" and o[1][0] is False]
    busy = [o for o in outcomes if o[0] == "GenerationInProgress"]
    replays = [o for o in outcomes if o[0] == "ok" and o[1][0] is True]
    assert len(fresh) == 1, outcomes
    assert len(fresh) + len(busy) + len(replays) == 6, outcomes
    plans = _plans(app, owner)
    assert len(plans) == 1 and all(o[1][1] == plans[0][0] for o in fresh + replays)
    # A retry after the race replays the same plan.
    from app.extensions import db
    from app.models import User
    with app.app_context():
        again = service.accept_preview(db.session.get(User, owner), "pd2-pg-accept-1", fingerprint)
        assert again.replayed is True and again.plan.id == plans[0][0]
        db.session.remove()


def test_accept_racing_discard_has_one_coherent_winner(pg_generation_app):
    from app.extensions import db
    from app.models import TrainingPlanGenerationOperation
    from app.services.mobile_training_generation import service
    app, user_ids, monkeypatch = pg_generation_app
    owner = user_ids[0]
    fingerprint = _stage_preview(app, monkeypatch, owner, "pd2-pg-discard-1")

    outcomes = _race(app, owner, [
        lambda user: service.accept_preview(user, "pd2-pg-discard-1", fingerprint).plan.id,
        lambda user: service.discard_preview(user, "pd2-pg-discard-1"),
    ])

    plans = _plans(app, owner)
    with app.app_context():
        status = TrainingPlanGenerationOperation.query.filter_by(user_id=owner).one().status
        db.session.remove()
    if plans:
        assert status == "SUCCEEDED" and outcomes[0][0] == "ok"
        assert outcomes[1][0] in ("PreviewAlreadyAccepted", "GenerationInProgress"), outcomes
    else:
        assert status in ("FAILED", "GENERATED"), outcomes
        assert outcomes[0][0] in ("PreviewDiscarded", "GenerationInProgress"), outcomes


def test_accept_racing_a_general_first_plan_leaves_one_plan(pg_generation_app):
    from app.services.general_training_plan import (
        TEMPLATE_ID, create_general_plan, parse_general_request,
    )
    from app.services.mobile_training_generation import service, store
    app, user_ids, monkeypatch = pg_generation_app
    owner = user_ids[0]
    fingerprint = _stage_preview(app, monkeypatch, owner, "pd2-pg-general-1")
    # Make the draft look older than the live window so General is not
    # refused up front: the owner lock alone must arbitrate.
    monkeypatch.setattr(store, "_generation_live", lambda user_id: False)
    request = parse_general_request({"template": TEMPLATE_ID, "gun_sayisi": 3,
                                     "ekipman": "ev"})

    outcomes = _race(app, owner, [
        lambda user: service.accept_preview(user, "pd2-pg-general-1", fingerprint).plan.id,
        lambda user: create_general_plan(user, request, "pd2-pg-general-2").plan.id,
    ])

    plans = _plans(app, owner)
    assert len(plans) == 1, outcomes
    winners = [o for o in outcomes if o[0] == "ok"]
    assert len(winners) == 1, outcomes
    assert {o[0] for o in outcomes if o[0] != "ok"} <= {
        "ExistingPlanRefused", "GenerationInProgress"}, outcomes


def test_skip_racing_accept_never_hides_the_plan(pg_generation_app):
    from app.extensions import db
    from app.services import training_start
    from app.services.mobile_training_generation import service
    app, user_ids, monkeypatch = pg_generation_app
    owner = user_ids[0]
    fingerprint = _stage_preview(app, monkeypatch, owner, "pd2-pg-skip-1")
    monkeypatch.setattr(training_start, "onboarding_state",
                        lambda user: SimpleNamespace(complete=True))

    outcomes = _race(app, owner, [
        lambda user: service.accept_preview(user, "pd2-pg-skip-1", fingerprint).plan.id,
        lambda user: training_start.record_skip(user)["decision"],
    ])

    assert outcomes[0][0] == "ok", outcomes
    assert len(_plans(app, owner)) == 1
    with app.app_context():
        state = training_start.read_start(owner)
        db.session.remove()
    assert state["has_plan"] is True and state["decision"] is None
    assert outcomes[1] in (("ok", "skip"), ("TrainingStartPlanExists", "TRAINING_START_PLAN_EXISTS"))
