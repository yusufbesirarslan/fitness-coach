"""LP16-A canonical WeeklyCheckIn service — direct, transport-free tests.

The service is called with explicit owners (no request, no ``current_user``)
exactly as the native LP16-B transport will call it.

    python -m pytest tests/test_lp16a_weekly_checkin_service.py -q
"""
import json
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import User, UserSession, WeeklyCheckIn
from app.services import weekly_checkin
from app.services.weekly_checkin import FullCheckIn, SubmissionOutcome
from app.timeutil import app_today, utc_day_bounds


VALUES = FullCheckIn(weight=78.0, intensity=4, fatigue=2,
                     progressive_overload="evet", sleep_quality=5,
                     nutrition_adherence=4, note="")


@pytest.fixture
def commits():
    seen = []
    listener = lambda _session: seen.append(1)  # noqa: E731
    sa.event.listen(Session, "after_commit", listener)
    try:
        yield seen
    finally:
        sa.event.remove(Session, "after_commit", listener)


def _owner(make_user, name="svcowner", *, complete=True, session=True):
    user = make_user(name)
    user.weight, user.target_weight = 80.0, 70.0
    if complete:
        user.height, user.age, user.gender = 180.0, 30, "male"
        user.current_activity, user.goal = "active", "kilo verme"
    if session:
        db.session.add(UserSession(user_id=user.id, weight=80.0, bmr=1.0,
                                   tdee=2.0, target_calories=3.0))
    db.session.commit()
    return user


def _durable(user_id):
    db.session.rollback()
    db.session.expire_all()
    user = db.session.get(User, user_id)
    session = (UserSession.query.filter_by(user_id=user_id)
               .order_by(UserSession.created_at.desc(), UserSession.id.desc())
               .first())
    rows = WeeklyCheckIn.query.filter_by(user_id=user_id).order_by(WeeklyCheckIn.id).all()
    return user, session, rows


# -- shared weight primitive ------------------------------------------------

def test_apply_body_weight_recalculates_eligible_session_without_commit(
        make_user, commits):
    owner = _owner(make_user)
    session = weekly_checkin.load_context(owner.id).session
    commits.clear()

    update = weekly_checkin.apply_body_weight(owner, 78.0, session)

    assert update == weekly_checkin.BodyWeightUpdate(1760.0, 2728.0, 2328.0, True)
    assert (owner.weight, owner.target_weight) == (78.0, 70.0)
    assert (session.weight, session.bmr, session.tdee, session.target_calories) == (
        78.0, 1760.0, 2728.0, 2328.0)
    assert commits == []
    # Staged only: a rollback leaves the stored state.
    user, stored, _rows = _durable(owner.id)
    assert (user.weight, stored.target_calories) == (80.0, 3.0)


def test_apply_body_weight_incomplete_profile_keeps_derived_targets(make_user):
    owner = _owner(make_user, complete=False)
    session = weekly_checkin.load_context(owner.id).session
    update = weekly_checkin.apply_body_weight(owner, 77.0, session)
    assert update == weekly_checkin.BodyWeightUpdate(1.0, 2.0, 3.0, False)
    assert (session.weight, session.bmr) == (77.0, 1.0)


def test_apply_body_weight_without_session(make_user):
    owner = _owner(make_user, session=False)
    update = weekly_checkin.apply_body_weight(owner, 77.0, None)
    # Eligible profile, no session: targets are computed and answered, nothing
    # is created to hold them.
    assert update == weekly_checkin.BodyWeightUpdate(1750.0, 2712.5, 2312.5, True)
    assert UserSession.query.filter_by(user_id=owner.id).count() == 0
    assert owner.weight == 77.0


def test_apply_body_weight_refuses_another_owners_session(make_user):
    owner = _owner(make_user, "svcowner")
    other = _owner(make_user, "svcother")
    foreign = weekly_checkin.load_context(other.id).session
    with pytest.raises(ValueError):
        weekly_checkin.apply_body_weight(owner, 70.0, foreign)
    assert owner.weight == 80.0


# -- full check-in ---------------------------------------------------------

def test_full_checkin_is_one_commit_of_row_weight_and_session(make_user, commits):
    owner = _owner(make_user)
    context = weekly_checkin.load_context(owner.id)
    commits.clear()

    entry = weekly_checkin.stage_full_checkin(owner, VALUES, context)
    assert commits == []
    result = weekly_checkin.commit_full_checkin(entry)

    assert result.outcome is SubmissionOutcome.COMMITTED
    assert len(commits) == 1
    user, session, [row] = _durable(owner.id)
    assert (row.weight, row.yogunluk, row.fatigue, row.progressive_overload,
            row.uyku_kalitesi, row.beslenme_uyumu, row.note, row.coach_feedback) == (
        78.0, 4, 2, "evet", 5, 4, "", None)
    assert (user.weight, user.target_weight) == (78.0, 70.0)
    assert session.target_calories == 2328.0


def test_staged_full_checkin_rolls_back_as_a_whole(make_user):
    owner = _owner(make_user)
    context = weekly_checkin.load_context(owner.id)
    weekly_checkin.stage_full_checkin(owner, VALUES, context)
    db.session.flush()  # even flushed, nothing is durable before the commit
    user, session, rows = _durable(owner.id)
    assert (user.weight, session.target_calories, rows) == (80.0, 3.0, [])


def test_full_checkin_requires_intensity(make_user):
    owner = _owner(make_user)
    context = weekly_checkin.load_context(owner.id)
    sparse = FullCheckIn(weight=78.0, intensity=None, fatigue=2,
                         progressive_overload="evet", sleep_quality=5,
                         nutrition_adherence=4)
    with pytest.raises(ValueError):
        weekly_checkin.stage_full_checkin(owner, sparse, context)
    assert owner.weight == 80.0
    assert not [o for o in db.session.new if isinstance(o, WeeklyCheckIn)]


def test_full_checkin_refuses_foreign_context_and_half_idempotency(make_user):
    owner = _owner(make_user, "svcowner")
    other = _owner(make_user, "svcother")
    with pytest.raises(ValueError):
        weekly_checkin.stage_full_checkin(
            owner, VALUES, weekly_checkin.load_context(other.id))
    with pytest.raises(ValueError):
        weekly_checkin.stage_full_checkin(
            owner, VALUES, weekly_checkin.load_context(owner.id),
            idempotency_key="k" * 16)
    with pytest.raises(TypeError):
        weekly_checkin.stage_full_checkin(owner, {"weight": 1},
                                          weekly_checkin.load_context(owner.id))
    assert owner.weight == 80.0


def test_load_context_reads_latest_full_checkin_and_canonical_session(make_user):
    owner = _owner(make_user)
    now = datetime.utcnow()
    older = WeeklyCheckIn(user_id=owner.id, weight=82.0, yogunluk=3,
                          created_at=now - timedelta(days=3))
    tie_a = WeeklyCheckIn(user_id=owner.id, weight=81.0, yogunluk=3,
                          created_at=now - timedelta(days=1))
    tie_b = WeeklyCheckIn(user_id=owner.id, weight=80.5, yogunluk=3,
                          created_at=now - timedelta(days=1))
    sparse = WeeklyCheckIn(user_id=owner.id, weight=79.0, created_at=now)
    db.session.add_all([older, tie_a, tie_b, sparse])
    db.session.commit()

    context = weekly_checkin.load_context(owner.id)

    assert context.previous.id == tie_b.id  # created_at DESC, id DESC; sparse skipped
    assert context.session.user_id == owner.id


def test_no_ai_or_provider_is_reachable_from_a_full_checkin(make_user, monkeypatch):
    from app.services import ai_coach

    def _explode(*_a, **_k):
        raise AssertionError("provider reached from the persistence service")

    monkeypatch.setattr(ai_coach, "generate_checkin_feedback", _explode)
    owner = _owner(make_user)
    entry = weekly_checkin.stage_full_checkin(
        owner, VALUES, weekly_checkin.load_context(owner.id))
    weekly_checkin.commit_full_checkin(entry)
    assert entry.coach_feedback is None


# -- idempotency primitives ------------------------------------------------

def _keyed(owner, key, fingerprint, response):
    claim = weekly_checkin.claim_submission(owner.id, key, fingerprint)
    assert claim.outcome is SubmissionOutcome.FRESH
    entry = weekly_checkin.stage_full_checkin(
        owner, VALUES, weekly_checkin.load_context(owner.id),
        idempotency_key=key, request_fingerprint=fingerprint)
    return weekly_checkin.commit_full_checkin(entry, response=response)


def test_keyed_commit_then_replay_then_conflict(make_user):
    owner = _owner(make_user)
    response = {"message": "ok", "coach_feedback": ""}
    first = _keyed(owner, "svc-key-000001", "a" * 64, response)
    assert first.outcome is SubmissionOutcome.COMMITTED
    assert first.response == response

    replay = weekly_checkin.claim_submission(owner.id, "svc-key-000001", "a" * 64)
    assert replay.outcome is SubmissionOutcome.REPLAYED
    assert replay.response == response

    conflict = weekly_checkin.claim_submission(owner.id, "svc-key-000001", "b" * 64)
    assert conflict.outcome is SubmissionOutcome.CONFLICT
    assert conflict.response is None
    _user, _session, [row] = _durable(owner.id)
    assert json.loads(row.response_snapshot) == response


def test_same_key_is_independent_per_owner(make_user):
    first = _owner(make_user, "svcowner")
    second = _owner(make_user, "svcother")
    _keyed(first, "svc-key-shared", "a" * 64, {"n": 1})
    claim = weekly_checkin.claim_submission(second.id, "svc-key-shared", "b" * 64)
    assert claim.outcome is SubmissionOutcome.FRESH
    db.session.rollback()


def test_commit_arbitrates_a_lost_uniqueness_race(make_user):
    """The winner's committed row decides: same intent replays, else conflict."""
    owner = _owner(make_user)
    _keyed(owner, "svc-key-race01", "a" * 64, {"winner": True})
    owner_id = owner.id

    for fingerprint, expected in (("a" * 64, SubmissionOutcome.REPLAYED),
                                  ("b" * 64, SubmissionOutcome.CONFLICT)):
        owner = db.session.get(User, owner_id)
        loser = weekly_checkin.stage_full_checkin(
            owner, VALUES, weekly_checkin.load_context(owner_id),
            idempotency_key="svc-key-race01", request_fingerprint=fingerprint)
        result = weekly_checkin.commit_full_checkin(loser, response={"winner": False})
        assert result.outcome is expected
        if expected is SubmissionOutcome.REPLAYED:
            assert result.response == {"winner": True}
    _user, _session, rows = _durable(owner_id)
    assert len(rows) == 1


def test_commit_response_contract(make_user):
    owner = _owner(make_user)
    context = weekly_checkin.load_context(owner.id)
    unkeyed = weekly_checkin.stage_full_checkin(owner, VALUES, context)
    with pytest.raises(ValueError):
        weekly_checkin.commit_full_checkin(unkeyed, response={"x": 1})
    db.session.rollback()
    owner = db.session.get(User, owner.id)
    keyed = weekly_checkin.stage_full_checkin(
        owner, VALUES, weekly_checkin.load_context(owner.id),
        idempotency_key="svc-key-000002", request_fingerprint="c" * 64)
    with pytest.raises(ValueError):
        weekly_checkin.commit_full_checkin(keyed)
    db.session.rollback()


# -- legacy /update-weight write -------------------------------------------

def test_legacy_weight_update_inserts_weight_only_row(make_user, commits):
    owner = _owner(make_user)
    commits.clear()
    update = weekly_checkin.record_legacy_weight_update(owner, 77.0)
    assert update.profile_ready is True
    assert len(commits) == 1
    user, session, [row] = _durable(owner.id)
    assert (row.weight, row.yogunluk) == (77.0, None)
    assert (user.weight, user.target_weight) == (77.0, 70.0)
    assert session.weight == 77.0
    # A weight-only row never becomes the "latest full check-in".
    assert weekly_checkin.load_context(owner.id).previous is None


def test_legacy_weight_update_uses_the_istanbul_day(make_user):
    owner = _owner(make_user)
    start, end = utc_day_bounds(app_today())
    inside = WeeklyCheckIn(user_id=owner.id, weight=81.0,
                           created_at=start + timedelta(minutes=1))
    outside = WeeklyCheckIn(user_id=owner.id, weight=82.0,
                            created_at=start - timedelta(minutes=1))
    after = WeeklyCheckIn(user_id=owner.id, weight=83.0,
                          created_at=end + timedelta(minutes=1))
    db.session.add_all([inside, outside, after])
    db.session.commit()
    weekly_checkin.record_legacy_weight_update(owner, 77.0)
    _user, _session, rows = _durable(owner.id)
    assert [r.weight for r in rows] == [77.0, 82.0, 83.0]


def test_legacy_weight_update_overwrites_same_day_full_checkin(make_user):
    """Preserved defect: the full row keeps its metrics but loses its weight."""
    owner = _owner(make_user)
    entry = weekly_checkin.stage_full_checkin(
        owner, VALUES, weekly_checkin.load_context(owner.id))
    weekly_checkin.commit_full_checkin(entry)
    weekly_checkin.record_legacy_weight_update(db.session.get(User, owner.id), 75.0)
    _user, _session, [row] = _durable(owner.id)
    assert (row.weight, row.yogunluk) == (75.0, 4)
