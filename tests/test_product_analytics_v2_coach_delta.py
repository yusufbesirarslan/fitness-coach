"""DELTA-1 (pending catalog review): Coach Nutrition success and the proposal denominator.

Server-authoritative only, emitted after the canonical commit. A chat message,
tool call, refusal, reuse, cancellation, stale proposal or undo never counts.
"""
import json

import pytest

from app.extensions import db
from app.models import ProductAnalyticsOutbox
from app.services.plan_confirmation import service as confirmation
from app.services.product_analytics import FLAG, consent
from app.services.product_analytics.catalog import NOTICE_VERSION
from test_nutrition_plan_edit_service import edit_rig, prepare  # noqa: F401
from app.services.nutrition_plan_mutation import service as nutrition


@pytest.fixture
def on(app):
    app.config[FLAG] = True
    yield app
    app.config[FLAG] = False


def _events(owner):
    db.session.expire_all()
    return [(row.name, json.loads(row.properties)) for row in
            ProductAnalyticsOutbox.query.filter_by(user_id=owner)
            .filter(ProductAnalyticsOutbox.name != "axis_v2_analytics_enabled")
            .order_by(ProductAnalyticsOutbox.id)]


def _propose(owner, *, command_type="add_exercise", fingerprint="f" * 64):
    return confirmation.create_or_reuse_pending(
        owner, lineage_id="lineage-1", mutation_version=1, snapshot_fingerprint="s" * 64,
        command_type=command_type, command_payload={"synthetic": True},
        command_fingerprint=fingerprint, reason_codes=["synthetic"], summary="Synthetic change")


def test_training_proposal_and_confirmation_are_counted_once(on, make_user):
    owner = make_user("delta-training").id
    consent.grant(owner, NOTICE_VERSION)
    row, reused = _propose(owner)
    assert not reused
    assert _propose(owner)[1] is True                       # identical reuse: not a new proposal
    assert _events(owner) == [("axis_v2_coach_change_proposed", {"domain": "training"})]
    confirmation.mark_applied(owner, row.public_id)
    confirmation.mark_applied(owner, row.public_id)         # converging replay: no second count
    assert _events(owner)[1:] == [("axis_v2_coach_change_confirmed", {"domain": "training"})]


@pytest.mark.parametrize("resolve", ["cancel", "stale"])
def test_cancelled_or_stale_training_proposals_are_never_confirmations(on, make_user, resolve):
    owner = make_user(f"delta-{resolve}").id
    consent.grant(owner, NOTICE_VERSION)
    row, _ = _propose(owner)
    if resolve == "cancel":
        confirmation.cancel_pending(owner)
    else:
        confirmation.mark_stale(owner, row.public_id)
    confirmation.mark_applied(owner, row.public_id)         # resolved row: no transition
    assert [name for name, _ in _events(owner)] == ["axis_v2_coach_change_proposed"]


def test_undo_proposals_are_not_mutation_attempts(on, make_user):
    owner = make_user("delta-undo").id
    consent.grant(owner, NOTICE_VERSION)
    row, _ = _propose(owner, command_type="undo_last_change")
    confirmation.mark_applied(owner, row.public_id)
    assert _events(owner) == []


def test_nutrition_proposal_and_confirmed_edit_success(on, edit_rig):  # noqa: F811
    owner = edit_rig.owner
    consent.grant(owner, NOTICE_VERSION)
    quote = prepare(edit_rig)
    assert prepare(edit_rig).proposal_id == quote.proposal_id       # replayed quote
    assert _events(owner) == [("axis_v2_coach_change_proposed", {"domain": "nutrition"})]
    nutrition.resolve_edit(owner, quote.proposal_id, "confirm")
    nutrition.resolve_edit(owner, quote.proposal_id, "confirm")     # replay
    assert _events(owner)[1:] == [("axis_v2_coach_mutation_applied", {"domain": "nutrition"})]
    raw = json.dumps([props for _, props in _events(owner)])
    for content in ("Chicken", "Eggs", "kahvalti", "kalori", "450"):
        assert content not in raw


def test_cancelled_nutrition_edit_is_only_a_proposal(on, edit_rig):  # noqa: F811
    owner = edit_rig.owner
    consent.grant(owner, NOTICE_VERSION)
    quote = prepare(edit_rig)
    nutrition.resolve_edit(owner, quote.proposal_id, "cancel")
    assert [name for name, _ in _events(owner)] == ["axis_v2_coach_change_proposed"]


def test_flag_off_delta_emitters_touch_no_analytics_state(edit_rig):  # noqa: F811
    quote = prepare(edit_rig)
    nutrition.resolve_edit(edit_rig.owner, quote.proposal_id, "confirm")
    assert ProductAnalyticsOutbox.query.count() == 0


def test_flag_off_delta_hooks_issue_no_sql(app, make_user):
    """Review T-1: OFF, the hooks return before any statement or attribute refresh."""
    from sqlalchemy import event
    from app.services.plan_confirmation import service as confirmation_service
    owner = make_user("delta-off-sql").id
    row, _ = _propose(owner)
    db.session.commit()
    statements = []

    def count(*args, **kwargs):
        statements.append(1)

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        confirmation_service._analytics_training_proposal(
            owner, "axis_v2_coach_change_proposed", row)
        nutrition._analytics(owner, "axis_v2_coach_change_proposed", "plan_confirmation:x")
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    assert statements == []
