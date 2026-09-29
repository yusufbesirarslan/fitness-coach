"""The shared onboarding-profile authority (LP-03): app/services/account_profile.py.

Vocabulary and validation (fail closed), the ONE readiness rule, the atomic
write, repeat-safety and owner isolation — independent of any transport.

    python -m pytest tests/test_account_profile_service.py -v
"""
import math

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import User, UserSession
from app.services import account_profile
from app.services.account_profile import (
    OnboardingProfile, ProfilePersistenceFailed, ProfileRejected,
    complete_onboarding, onboarding_state,
)

VALID = dict(weight=80, height=180, age=30, gender="male", goal="lose_weight",
             fitness_level="beginner", current_activity="active")


def _profile(**overrides):
    return OnboardingProfile(**{**VALID, **overrides})


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _sessions(user_id):
    db.session.expire_all()
    return UserSession.query.filter_by(user_id=user_id).all()


@pytest.fixture
def refuse_session_rows():
    """Fail any flush that carries a UserSession — between the profile
    mutation and the session becoming durable."""
    def listener(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.new | session.dirty):
            raise RuntimeError("injected UserSession persistence failure")

    event.listen(db.session, "before_flush", listener)
    yield
    event.remove(db.session, "before_flush", listener)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def test_goal_tokens_map_to_the_unchanged_stored_literals():
    assert account_profile.GOALS == ("lose_weight", "build_muscle")
    assert account_profile.goal_token("kilo verme") == "lose_weight"
    assert account_profile.goal_token("kas kazanma") == "build_muscle"


@pytest.mark.parametrize("stored", [None, "", "fit", "Kilo Verme", 1, "lose_weight"])
def test_non_canonical_stored_goal_has_no_token(stored):
    assert account_profile.goal_token(stored) is None


@pytest.mark.parametrize(("field", "value"), [
    ("goal", "kilo verme"),          # the stored literal is not a native token
    ("goal", "maintain"),
    ("goal", "LOSE_WEIGHT"),
    ("gender", "other"),
    ("fitness_level", "elite"),
    ("current_activity", "extreme"),
    ("current_activity", ""),
])
def test_unknown_tokens_fail_closed(field, value):
    with pytest.raises(ProfileRejected) as caught:
        _profile(**{field: value})
    assert (caught.value.field, caught.value.reason) == (field, "vocabulary")


@pytest.mark.parametrize("field", [
    "gender", "goal", "fitness_level", "current_activity"])
@pytest.mark.parametrize("value", [None, 1, True, ["male"]])
def test_non_string_tokens_are_type_errors(field, value):
    with pytest.raises(ProfileRejected) as caught:
        _profile(**{field: value})
    assert (caught.value.field, caught.value.reason) == (field, "type")


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["weight", "height", "target_weight"])
@pytest.mark.parametrize("value", [
    "80", True, False, math.nan, math.inf, -math.inf, [80]])
def test_numbers_must_be_finite_json_numbers(field, value):
    with pytest.raises(ProfileRejected) as caught:
        _profile(**{field: value})
    assert (caught.value.field, caught.value.reason) == (field, "type")


@pytest.mark.parametrize("field", ["weight", "target_weight"])
@pytest.mark.parametrize(("value", "ok"), [
    (19.99, False), (20, True), (500, True), (500.01, False), (-80, False)])
def test_body_weight_reuses_the_canonical_weight_log_range(field, value, ok):
    if ok:
        assert getattr(_profile(**{field: value}), field) == float(value)
    else:
        with pytest.raises(ProfileRejected) as caught:
            _profile(**{field: value})
        assert caught.value.reason == "range"


def test_weight_range_is_the_same_constant_the_weight_log_enforces():
    from app.blueprints import tracking
    assert tracking._parse_weight(19.99) == (None, "route.weight_range")
    assert tracking._parse_weight(20) == (20.0, None)
    assert tracking._parse_weight(500.01) == (None, "route.weight_range")
    assert (account_profile.BODY_WEIGHT_MIN_KG,
            account_profile.BODY_WEIGHT_MAX_KG) == (20.0, 500.0)


@pytest.mark.parametrize("value", [0, -1, 0.0, -0.5])
def test_height_must_be_positive_but_has_no_invented_range(value):
    with pytest.raises(ProfileRejected) as caught:
        _profile(height=value)
    assert caught.value.reason == "range"
    # No product-policy bound exists for height: anything positive is accepted.
    assert _profile(height=0.5).height == 0.5
    assert _profile(height=999).height == 999.0


@pytest.mark.parametrize(("value", "reason"), [
    (0, "range"), (-3, "range"), (30.0, "type"), (30.5, "type"),
    ("30", "type"), (True, "type"), (None, "type")])
def test_age_must_be_a_positive_whole_number(value, reason):
    with pytest.raises(ProfileRejected) as caught:
        _profile(age=value)
    assert (caught.value.field, caught.value.reason) == ("age", reason)
    # No product-policy bound exists for age either.
    assert _profile(age=1).age == 1
    assert _profile(age=150).age == 150


def test_rejection_never_carries_the_submitted_value():
    with pytest.raises(ProfileRejected) as caught:
        _profile(weight=487654.25)
    assert "487654" not in str(caught.value)
    assert "487654" not in repr(caught.value)


def test_profile_has_no_owner_field():
    fields = set(OnboardingProfile.__dataclass_fields__)
    assert fields == {"weight", "height", "age", "gender", "goal",
                      "fitness_level", "current_activity", "target_weight"}
    with pytest.raises(TypeError):
        OnboardingProfile(**VALID, user_id=1)


# ---------------------------------------------------------------------------
# Readiness — the ONE rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("flag", "with_session", "complete"), [
    (False, False, False),
    (True, False, False),   # legacy false-complete: flag committed, session lost
    (False, True, False),   # legacy /chat row without onboarding
    (True, True, True),
])
def test_readiness_requires_both_persisted_facts(make_user, flag, with_session, complete):
    user = make_user("readiness")
    user.profile_complete = flag
    if with_session:
        db.session.add(UserSession(user_id=user.id))
    db.session.commit()
    state = onboarding_state(user)
    assert state.complete is complete
    assert (state.session is not None) is complete


def test_canonical_session_is_newest_then_highest_id(make_user):
    from datetime import datetime
    user = make_user("canonical")
    stamp = datetime(2026, 9, 1, 12)
    older = UserSession(user_id=user.id, created_at=datetime(2026, 8, 1))
    tie_a = UserSession(user_id=user.id, created_at=stamp)
    tie_b = UserSession(user_id=user.id, created_at=stamp)
    db.session.add_all([older, tie_a, tie_b])
    db.session.commit()
    assert account_profile.canonical_session(user.id).id == tie_b.id


def test_readiness_is_owner_scoped(make_user):
    owner = make_user("owner", profile_complete=False)
    other = make_user("other")
    other.profile_complete = True
    db.session.add(UserSession(user_id=other.id))
    owner.profile_complete = True
    db.session.commit()
    assert onboarding_state(other).complete is True
    assert onboarding_state(owner).complete is False


# ---------------------------------------------------------------------------
# complete_onboarding
# ---------------------------------------------------------------------------

def test_first_submission_writes_profile_session_and_completeness(make_user):
    user = make_user("first")
    result = complete_onboarding(user, _profile(target_weight=75))
    assert result.session_created is True
    assert (round(result.bmr), round(result.tdee), round(result.target_calories)) == (
        1780, 2759, 2359)

    fresh = _fresh(user.id)
    assert (fresh.weight, fresh.height, fresh.age, fresh.gender) == (80.0, 180.0, 30, "male")
    assert (fresh.goal, fresh.goal_type) == ("kilo verme", "loss")
    assert (fresh.fitness_level, fresh.current_activity) == ("beginner", "active")
    assert fresh.target_weight == 75.0
    assert fresh.profile_complete is True

    [session] = _sessions(user.id)
    assert session.name == "first"
    assert session.goal == "kilo verme"
    assert session.coach_reply == ""
    assert session.training_plan == "Haftada 3 gün 30 dk yürüyüş + 2 gün hafif kardiyo"
    assert onboarding_state(fresh).complete is True
    assert onboarding_state(fresh).session.id == session.id


def test_build_muscle_stores_the_existing_gain_literal(make_user):
    user = make_user("gain")
    result = complete_onboarding(user, _profile(goal="build_muscle"))
    fresh = _fresh(user.id)
    assert (fresh.goal, fresh.goal_type) == ("kas kazanma", "gain")
    assert round(result.target_calories) == 3059


def test_repeat_submission_reuses_the_canonical_session(make_user):
    user = make_user("repeat")
    complete_onboarding(user, _profile())
    [first] = _sessions(user.id)
    first_id, first_created = first.id, first.created_at

    again = complete_onboarding(user, _profile())
    revised = complete_onboarding(
        user, _profile(weight=70, goal="build_muscle", fitness_level="advanced"))

    assert again.session_created is False and revised.session_created is False
    [session] = _sessions(user.id)
    assert session.id == first_id
    assert session.created_at == first_created
    assert (session.weight, session.goal, session.fitness_level) == (
        70.0, "kas kazanma", "advanced")
    fresh = _fresh(user.id)
    assert (fresh.weight, fresh.goal, fresh.goal_type) == (70.0, "kas kazanma", "gain")
    assert onboarding_state(fresh).complete is True


def test_repeat_submission_preserves_an_existing_coach_reply(make_user):
    user = make_user("coach-reply")
    db.session.add(UserSession(user_id=user.id, coach_reply="earlier advice"))
    db.session.commit()
    complete_onboarding(user, _profile())
    [session] = _sessions(user.id)
    assert session.coach_reply == "earlier advice"
    assert session.bmr == pytest.approx(1780)


def test_absent_target_weight_keeps_the_stored_value(make_user):
    user = make_user("target", target_weight=68.0)
    complete_onboarding(user, _profile())
    assert _fresh(user.id).target_weight == 68.0


def test_failure_before_session_is_durable_rolls_back_everything(
        make_user, refuse_session_rows):
    user = make_user("atomic")
    with pytest.raises(ProfilePersistenceFailed) as caught:
        complete_onboarding(user, _profile(target_weight=75))
    assert isinstance(caught.value.__cause__, RuntimeError)
    fresh = _fresh(user.id)
    assert fresh.profile_complete is not True
    assert (fresh.weight, fresh.goal, fresh.target_weight) == (None, None, None)
    assert _sessions(user.id) == []
    assert onboarding_state(fresh).complete is False


def test_failed_resubmission_keeps_the_prior_valid_state(make_user):
    user = make_user("atomic-repeat")
    complete_onboarding(user, _profile())

    def refuse(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.dirty):
            raise RuntimeError("injected")

    event.listen(db.session, "before_flush", refuse)
    try:
        with pytest.raises(ProfilePersistenceFailed):
            complete_onboarding(user, _profile(weight=60, goal="build_muscle"))
    finally:
        event.remove(db.session, "before_flush", refuse)

    fresh = _fresh(user.id)
    assert (fresh.weight, fresh.goal) == (80.0, "kilo verme")
    [session] = _sessions(user.id)
    assert (session.weight, session.goal) == (80.0, "kilo verme")
    assert onboarding_state(fresh).complete is True


def test_commit_failure_rolls_back(make_user, monkeypatch):
    user = make_user("commit-failure")

    def failing_commit():
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(db.session, "commit", failing_commit)
    with pytest.raises(ProfilePersistenceFailed):
        complete_onboarding(user, _profile())
    monkeypatch.undo()
    assert _fresh(user.id).profile_complete is not True
    assert _sessions(user.id) == []


def test_onboarding_touches_only_the_owner(make_user):
    owner = make_user("iso-owner")
    other = make_user("iso-other", weight=91.0, goal="kas kazanma")
    db.session.add(UserSession(user_id=other.id, weight=91.0, goal="kas kazanma"))
    db.session.commit()

    complete_onboarding(owner, _profile())

    fresh_other = _fresh(other.id)
    assert (fresh_other.weight, fresh_other.goal) == (91.0, "kas kazanma")
    assert fresh_other.profile_complete is not True
    [other_session] = _sessions(other.id)
    assert (other_session.weight, other_session.goal) == (91.0, "kas kazanma")
    assert len(_sessions(owner.id)) == 1


def test_only_a_validated_profile_is_accepted(make_user):
    user = make_user("typed")
    with pytest.raises(TypeError):
        complete_onboarding(user, dict(VALID))
    assert _sessions(user.id) == []
