"""Adaptive Coach C2 on real PostgreSQL: real D1 grants, generations and
transaction fences; synthetic estimator only (no SDK call).

Covers the C2 seams C1's own PG suite cannot: capturing the separate
``nutrition.plan`` authority inside a Coach operation, and the Coach-level
confirm/cancel entrypoint under real row locks.
"""
import json
import os
import threading
from types import SimpleNamespace

import pytest

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PG_TEST_DATABASE_URL", allow_module_level=True)

from app.extensions import db
from app.models import NutritionPlan, NutritionPlanMutationRecord as Record
from app.models import TrainingPlanConfirmationProposal as Proposal
from app.observability import assign_request_id
from app.services import ai_consent, coach_plan_tools, nutrition_plan_store as store
from app.services.ai_spend_guard import subject_scope
from app.services.coach_plan_tools import nutrition
from app.services.nutrition_plan_mutation import access, service
from tests.test_mobile_training_generation_pg import pg_generation_app  # noqa: F401
from tests.test_lp21_d1_operation_fencing_pg import disclose, consent, regrant
from tests.test_nutrition_plan_edit_service import DOCUMENT, ESTIMATE

pytestmark = pytest.mark.pg_concurrency

REPLACE = {"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Chicken - 100 g"}


@pytest.fixture
def rig(pg_generation_app, monkeypatch):
    app, owners, _ = pg_generation_app
    app.config[coach_plan_tools.FLAG_KEY] = True
    policies = disclose(monkeypatch)
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    monkeypatch.setattr(access, "APPROVED_COACH_EDIT_DISCLOSURES",
                        frozenset({policies["coach"].version}))
    from d1_screened_support import screen_adult_tr
    with app.app_context():
        screen_adult_tr(*owners)
        for owner in owners:
            consent(owner, policies["coach"], "affirmative_grant", "c2-coach-grant")
            consent(owner, policies["nutrition_plan"], "affirmative_grant", "c2-nutrition-grant")
        db.session.add(NutritionPlan(user_id=owners[0], plan_data=json.dumps(DOCUMENT, indent=2)))
        db.session.commit()
    r = SimpleNamespace(app=app, owner=owners[0], other=owners[1], calls=[], policies=policies)

    def estimate(items):
        assert not db.session().in_transaction()
        assert ai_consent.current_operation().feature == "nutrition_plan"
        r.calls.append(tuple(items))
        return dict(ESTIMATE)

    monkeypatch.setattr(service.estimation, "estimate_meal", estimate)
    return r


def coach_authority(r, owner=None):
    owner = owner or r.owner
    return ai_consent.OperationAuthority(
        owner, "coach", ai_consent.generation(owner), r.policies["coach"].version)


def in_turn(r, fn, owner=None):
    """Run ``fn`` inside one synthetic Coach turn of ``owner``."""
    owner = owner or r.owner
    with r.app.test_request_context("/ask", method="POST"), subject_scope(owner):
        assign_request_id()
        nutrition.reset_turn()
        with ai_consent.operation_scope(coach_authority(r, owner)):
            try:
                return fn()
            finally:
                db.session.remove()


def resolve(r, decision, proposal_id, owner=None):
    owner = owner or r.owner
    with r.app.test_request_context("/api/v1/coach/plan-confirmation", method="POST"), subject_scope(owner):
        try:
            return nutrition.execute_resolution(owner, proposal_id, decision)
        finally:
            db.session.remove()


def stage(r):
    result = in_turn(r, lambda: nutrition.execute(r.owner, nutrition.REPLACE_TOOL, REPLACE))
    assert result["status"] == "confirmation_required", result
    with r.app.app_context():
        return Proposal.query.filter_by(user_id=r.owner).one().public_id


def run(r, calls):
    barrier = threading.Barrier(len(calls))
    out = [None] * len(calls)

    def worker(index, call):
        try:
            barrier.wait(timeout=15)
            out[index] = call()
        except Exception as error:  # ConsentDenied is a legitimate outcome
            out[index] = error

    threads = [threading.Thread(target=worker, args=(i, c), daemon=True) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), out
    unexpected = [x for x in out if isinstance(x, Exception) and not isinstance(x, ai_consent.ConsentDenied)]
    assert not unexpected, out
    return out


def state(r):
    with r.app.app_context():
        row = store.newest_plan(r.owner)
        return (row.plan_data, row.mutation_version, Record.query.filter_by(user_id=r.owner).count(),
                [p.status for p in Proposal.query.filter_by(user_id=r.owner)])


def test_real_grants_publish_tools_and_a_missing_grant_withholds_them(rig):
    r = rig
    names = in_turn(r, lambda: [d["name"] for d in nutrition.published_tool_defs(r.owner)])
    assert set(names) == nutrition.TOOL_NAMES
    read = in_turn(r, lambda: nutrition.execute(r.owner, nutrition.READ_TOOL, {}))
    assert read["status"] == "ok" and read["meals"][0]["items"][0] == "Eggs - 2 adet"
    with r.app.app_context():
        consent(r.owner, r.policies["nutrition_plan"], "revoke", "c2-revoke-nutrition")
    assert in_turn(r, lambda: nutrition.published_tool_defs(r.owner)) == ()
    refused = in_turn(r, lambda: nutrition.execute(r.owner, nutrition.READ_TOOL, {}))
    assert refused["error"] == "PERMISSION_UNAVAILABLE" and "meals" not in refused
    assert not r.calls


def test_independent_capture_leaves_the_coach_operation_active(rig):
    r = rig

    def capture():
        authorities = nutrition.turn_authorities(r.owner)
        assert ai_consent.current_operation().feature == "coach"
        return authorities

    authorities = in_turn(r, capture)
    assert authorities.coach.feature == "coach"
    assert authorities.nutrition.feature == "nutrition_plan"
    assert authorities.nutrition.version == r.policies["nutrition_plan"].version


def test_parallel_confirm_and_cancel_resolve_exactly_once(rig):
    r = rig
    proposal_id = stage(r)
    out = run(r, [lambda: resolve(r, "confirm", proposal_id),
                  lambda: resolve(r, "cancel", proposal_id)])
    statuses = sorted(x.get("status") or x.get("error") for x in out)
    raw, version, records, proposals = state(r)
    if version == 1:
        assert records == 1 and proposals == ["applied"]
        assert "applied" in statuses and "cancelled" not in statuses
        assert json.loads(raw)["kahvalti"]["yemekler"][0] == "Chicken - 100 g"
    else:
        assert version == 0 and records == 0 and proposals == ["cancelled"]
        assert "cancelled" in statuses and "applied" not in statuses
    assert len(r.calls) == 1


def test_parallel_double_confirm_applies_once(rig):
    r = rig
    proposal_id = stage(r)
    out = run(r, [lambda: resolve(r, "confirm", proposal_id),
                  lambda: resolve(r, "confirm", proposal_id)])
    assert sorted(x["status"] for x in out) == ["applied", "replayed"], out
    _, version, records, proposals = state(r)
    assert version == 1 and records == 1 and proposals == ["applied"] and len(r.calls) == 1


def test_withdrawal_after_quote_refuses_and_regrant_cannot_revive(rig):
    r = rig
    proposal_id = stage(r)
    with r.app.app_context():
        consent(r.owner, r.policies["nutrition_plan"], "revoke", "c2-withdraw")
    with pytest.raises(ai_consent.ConsentDenied):
        resolve(r, "confirm", proposal_id)
    with r.app.app_context():
        consent(r.owner, r.policies["nutrition_plan"], "affirmative_grant", "c2-regrant")
    with pytest.raises(ai_consent.ConsentDenied):
        resolve(r, "confirm", proposal_id)  # the stored generation stays stale
    raw, version, records, proposals = state(r)
    assert version == 0 and records == 0 and proposals == ["stale"]
    assert raw == json.dumps(DOCUMENT, indent=2)


def test_coach_regrant_between_quote_and_confirm_is_stale(rig):
    r = rig
    proposal_id = stage(r)
    with r.app.app_context():
        regrant(r.owner, r.policies["coach"], "c2-coach")
    with pytest.raises(ai_consent.ConsentDenied):
        resolve(r, "confirm", proposal_id)
    assert state(r)[1:3] == (0, 0)


def test_other_account_cannot_resolve_or_see_the_quote(rig):
    r = rig
    proposal_id = stage(r)
    foreign = resolve(r, "confirm", proposal_id, owner=r.other)
    assert foreign["error"] == "NO_PENDING_CONFIRMATION"
    with r.app.app_context():
        assert nutrition.owns_proposal(r.other, proposal_id) is False
        assert service.find_quote(r.other) is None
    assert state(r)[1:] == (0, 0, ["pending"])
