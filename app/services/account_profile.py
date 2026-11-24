"""The ONE onboarding-profile authority (LP-03).

Browser `/setup` and native `PUT /api/v1/account/profile` both complete
onboarding through this module, and every reader that asks "has this account
finished onboarding / may it generate a first plan?" asks `onboarding_state`.
It is transport-independent: no request, response, redirect, flash, JSON,
i18n or logging lives here. Adapters parse their own wire format, build an
`OnboardingProfile` and render the typed failures.

Readiness — the ONE rule
------------------------
An account is onboarded iff `User.profile_complete` is true AND the canonical
`UserSession` (the owner's newest row, the one every consumer already reads)
exists. Before LP-03 two rules answered the same question: `account/me`, the
`/` gate and `/setup` read the flag, while both first-plan generators only
asked whether a session existed. Each could say yes while the other said no.
The flag alone is not enough because the old `/setup` committed it before the
session (a failure in between left "complete" accounts that cannot generate a
plan); a session alone is not enough because the legacy `/chat` form appends
sessions without ever running onboarding. Requiring both fails closed on
every divergent legacy row, and `complete_onboarding` writes both in one
transaction, so no new divergent row can be produced.

Vocabulary
----------
Native clients speak locale-independent tokens. Gender, fitness level and
activity level already are stable English tokens in the domain, so they are
their own tokens. The goal is not: calculations, plan text and the nutrition
split branch on the stored Turkish literals, so the goal is a token at the
boundary and mapped to the unchanged stored value here — the only place that
knows both spellings. Unknown values fail closed; nothing falls back.

Numbers
-------
Body weight (and target weight) reuse the backend's existing canonical range,
the one the weight log already enforces (`BODY_WEIGHT_MIN_KG`..`MAX`). There
is no canonical backend range for height or age, and LP-03 does not invent
one: they must be finite and positive (age a whole number), which is the
structural minimum that keeps the BMR formula and the stored row meaningful.

Reading
-------
`current_profile` is the one read of the stored profile values, in the same
domain names and tokens the write takes, so a native client can prefill the
full-replace write from it.

Goal pace and the daily calorie target (PD1)
--------------------------------------------
`User.goal_pace` is the owner's chosen pace token (`goal_pace.PACES`), valid
only for its goal (`PACES_FOR_GOAL`). `derive_targets` is the ONE place the
daily target is computed — onboarding, a pace edit and every body-weight
update call it — and `UserSession.target_calories` stays the ONE stored
target every reader (Nutrition, Today, Coach, Weekly Check-in) uses.

Compatibility: NULL pace means "never chosen" and keeps the legacy formula
(`calculations.calculate_target`) byte-for-byte, so no existing account is
re-targeted until its owner picks a pace. A pace that does not belong to the
stored goal (a goal changed elsewhere) is treated as NULL, never re-mapped.
A write that omits the pace keeps the stored one when it still fits the
submitted goal and clears it otherwise. A minor never has a pace or a target.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import User, UserSession
from app.services import goal_pace as pace_rules
from app.services.calculations import (
    calculate_bmr,
    calculate_target,
    calculate_tdee,
    generate_nutrition_plan,
    generate_training_plan,
)

# Canonical body-weight bounds, in kg. Owned here; the weight log
# (`tracking._parse_weight`) enforces the same range from this constant.
BODY_WEIGHT_MIN_KG = 20.0
BODY_WEIGHT_MAX_KG = 500.0

GOAL_LOSE_WEIGHT = "lose_weight"
GOAL_BUILD_MUSCLE = "build_muscle"

# Token -> stored domain value. The stored values are what calculations,
# plan text and nutrition_targets branch on; they are not changed by LP-03.
_GOAL_STORED = {
    GOAL_LOSE_WEIGHT: "kilo verme",
    GOAL_BUILD_MUSCLE: "kas kazanma",
}
_GOAL_TOKEN = {stored: token for token, stored in _GOAL_STORED.items()}
_GOAL_TYPE = {GOAL_LOSE_WEIGHT: "loss", GOAL_BUILD_MUSCLE: "gain"}

GOALS = tuple(_GOAL_STORED)
GENDERS = ("male", "female")
FITNESS_LEVELS = ("beginner", "intermediate", "advanced")
ACTIVITY_LEVELS = ("sedentary", "active", "very_active")

# Field names are the domain's (`User` columns), not any transport's.
WEIGHT = "weight"
HEIGHT = "height"
AGE = "age"
GENDER = "gender"
GOAL = "goal"
FITNESS_LEVEL = "fitness_level"
CURRENT_ACTIVITY = "current_activity"
TARGET_WEIGHT = "target_weight"
GOAL_PACE = "goal_pace"

# The paces each goal admits. A pace outside its goal's tuple is refused on
# write and read back as "no pace".
PACES_FOR_GOAL = {
    GOAL_LOSE_WEIGHT: pace_rules.LOSS_PACES,
    GOAL_BUILD_MUSCLE: pace_rules.GAIN_PACES,
}

# Closed rejection reasons.
REASON_TYPE = "type"
REASON_RANGE = "range"
REASON_VOCABULARY = "vocabulary"


class ProfileRejected(ValueError):
    """A profile value the domain refuses. Carries no submitted value."""

    def __init__(self, field, reason):
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class ProfilePersistenceFailed(RuntimeError):
    """The onboarding transaction did not commit and was rolled back."""


def goal_token(stored_goal):
    """The native token for a stored goal, or None when it is not canonical."""
    return _GOAL_TOKEN.get(stored_goal) if isinstance(stored_goal, str) else None


def _number(field, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileRejected(field, REASON_TYPE)
    value = float(value)
    if not math.isfinite(value):
        raise ProfileRejected(field, REASON_TYPE)
    return value


def _body_weight(field, value):
    value = _number(field, value)
    if not BODY_WEIGHT_MIN_KG <= value <= BODY_WEIGHT_MAX_KG:
        raise ProfileRejected(field, REASON_RANGE)
    return value


def _positive(field, value):
    value = _number(field, value)
    if value <= 0:
        raise ProfileRejected(field, REASON_RANGE)
    return value


def _whole_positive(field, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileRejected(field, REASON_TYPE)
    if value <= 0:
        raise ProfileRejected(field, REASON_RANGE)
    return value


def _token(field, value, vocabulary):
    if not isinstance(value, str):
        raise ProfileRejected(field, REASON_TYPE)
    if value not in vocabulary:
        raise ProfileRejected(field, REASON_VOCABULARY)
    return value


@dataclass(frozen=True)
class OnboardingProfile:
    """A validated onboarding submission, in domain tokens.

    Construction IS validation: an instance cannot hold an unknown token, a
    non-finite number or a value outside the canonical ranges. There is no
    owner field — the owner is whoever the caller authenticated.
    `target_weight=None` means "not submitted" and leaves the stored value.
    `goal_pace=None` means "not submitted": the stored pace is kept when it
    belongs to the submitted goal and cleared when it does not.
    """

    weight: float
    height: float
    age: int
    gender: str
    goal: str
    fitness_level: str
    current_activity: str
    target_weight: Optional[float] = None
    goal_pace: Optional[str] = None

    def __post_init__(self):
        set_ = object.__setattr__
        set_(self, WEIGHT, _body_weight(WEIGHT, self.weight))
        set_(self, HEIGHT, _positive(HEIGHT, self.height))
        set_(self, AGE, _whole_positive(AGE, self.age))
        _token(GENDER, self.gender, GENDERS)
        _token(GOAL, self.goal, GOALS)
        _token(FITNESS_LEVEL, self.fitness_level, FITNESS_LEVELS)
        _token(CURRENT_ACTIVITY, self.current_activity, ACTIVITY_LEVELS)
        if self.target_weight is not None:
            set_(self, TARGET_WEIGHT,
                 _body_weight(TARGET_WEIGHT, self.target_weight))
        if self.goal_pace is not None:
            _token(GOAL_PACE, self.goal_pace, pace_rules.PACES)
            if self.goal_pace not in PACES_FOR_GOAL[self.goal]:
                raise ProfileRejected(GOAL_PACE, REASON_VOCABULARY)


@dataclass(frozen=True)
class DerivedTargets:
    """BMR, maintenance and the daily target of one profile, and why.

    `basis` is `pace` / `maintenance` (from `goal_pace`) or `legacy` (no
    usable pace: the pre-PD1 formula). `goal_pace` is the pace that was
    applied, never one that did not fit the goal.
    """

    bmr: float
    tdee: float
    target_calories: float
    basis: str
    goal_pace: Optional[str]
    bounded: bool
    reason: Optional[str]


BASIS_LEGACY = "legacy"


def pace_for_goal(pace, goal):
    """`pace` when it is a pace of goal token `goal`, else None."""
    return pace if pace in PACES_FOR_GOAL.get(goal, ()) else None


def maintenance_usable(tdee):
    """Whether `tdee` is a maintenance estimate a pace can size from."""
    return (isinstance(tdee, (int, float)) and not isinstance(tdee, bool)
            and math.isfinite(tdee) and tdee > 0)


def derive_targets(*, weight, height, age, gender, current_activity,
                   stored_goal, goal_pace=None, target_weight=None):
    """The ONE daily-target computation. Pure; the caller persists it.

    The inputs must already be a complete adult profile (the caller decides
    that); `stored_goal` is the stored literal, `goal_pace` the stored or
    submitted token. A pace that does not belong to the goal is ignored.
    """
    bmr = calculate_bmr(weight, height, age, gender)
    tdee = calculate_tdee(bmr, current_activity)
    pace = pace_for_goal(goal_pace, goal_token(stored_goal))
    if pace is None or not maintenance_usable(tdee):
        # No pace, or a maintenance estimate a pace cannot size (a profile
        # whose values give a non-positive TDEE, e.g. height in metres): the
        # legacy formula, unchanged, never a raise. Readers already treat a
        # non-positive stored target as "no target".
        return DerivedTargets(bmr, tdee, calculate_target(tdee, stored_goal),
                              BASIS_LEGACY, None, False, None)
    outcome = pace_rules.pace_target(
        pace, tdee=tdee, weight=weight, height=height, gender=gender,
        target_weight=target_weight)
    return DerivedTargets(bmr, tdee, outcome.target_calories, outcome.basis,
                          pace, outcome.bounded, outcome.reason)


def derive_targets_for_weight(owner, weight):
    """`derive_targets` for `owner`'s stored profile at a new body `weight`.

    The weight primitive (`weekly_checkin.apply_body_weight`) calls this so a
    body-weight update keeps the owner's stored pace and target weight
    without that package reading either column. The caller has already
    checked that the stored profile is complete.
    """
    return derive_targets(
        weight=weight, height=owner.height, age=owner.age,
        gender=owner.gender, current_activity=owner.current_activity,
        stored_goal=owner.goal, goal_pace=owner.goal_pace,
        target_weight=_stored_number(owner.target_weight))


@dataclass(frozen=True)
class OnboardingResult:
    bmr: float
    tdee: float
    target_calories: float
    session_created: bool


@dataclass(frozen=True)
class OnboardingState:
    """The canonical readiness answer for one account.

    `complete` is the ONE completeness/first-plan-readiness rule. `session` is
    the canonical session when `complete`, else None — a caller cannot reach
    a session through an account this rule calls incomplete.
    """

    complete: bool
    session: Optional[UserSession]


def canonical_session(user_id):
    """The owner's canonical `UserSession`: newest by `created_at`, then id."""
    return (UserSession.query.filter_by(user_id=user_id)
            .order_by(UserSession.created_at.desc(), UserSession.id.desc())
            .first())


def onboarding_state(user):
    session = canonical_session(user.id)
    complete = bool(user.profile_complete) and session is not None
    return OnboardingState(complete=complete,
                           session=session if complete else None)


def _stored_number(value):
    """A stored finite number as it is, else None (NULL, NaN, ±Inf)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _stored_whole(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _stored_token(value, vocabulary):
    return value if isinstance(value, str) and value in vocabulary else None


def invalidate_health_onboarding(user):
    """Reset readiness inside the caller's locked health-erasure transaction."""
    user.profile_complete = False


def current_profile(user):
    """The canonical stored profile of `user`, in domain field names.

    Read-only: no write, no commit, no lock. The `User` columns are the
    profile — `complete_onboarding`, the weight log and the web profile edit
    all write them — so the canonical `UserSession`, a derived copy that can
    lag them, is not read.

    A value is returned as stored, without the onboarding ranges applied: a
    legacy number outside them is still the truth, and a caller must correct
    it before writing it back. Only what is not a usable value at all is
    None — NULL, a non-finite number, a token outside the vocabulary. The goal
    is its token, never the stored literal.
    """
    return {
        WEIGHT: _stored_number(user.weight),
        HEIGHT: _stored_number(user.height),
        AGE: _stored_whole(user.age),
        GENDER: _stored_token(user.gender, GENDERS),
        GOAL: goal_token(user.goal),
        FITNESS_LEVEL: _stored_token(user.fitness_level, FITNESS_LEVELS),
        CURRENT_ACTIVITY: _stored_token(user.current_activity, ACTIVITY_LEVELS),
        TARGET_WEIGHT: _stored_number(user.target_weight),
        GOAL_PACE: pace_for_goal(user.goal_pace, goal_token(user.goal)),
    }


def _lock_owner(user_id):
    """`SELECT … FOR UPDATE` the owner row; returns its committed `goal_pace`.

    Two first submissions would otherwise both see "no session" and both
    insert one. A column query, so the identity-mapped user is not handed back
    stale (see `plan_owner_lock.lock_plan_owner`) — and so the stored pace a
    write keeps is the one read under the lock, not a value loaded earlier.
    Position 2 of the repository lock order (`user` row); nothing further is
    locked here. No-op lock on SQLite, which admits one writer at a time.
    """
    row = (db.session.query(User.id, User.goal_pace).filter_by(id=user_id)
           .with_for_update().first())
    return row.goal_pace if row is not None else None


# TR 13-17 profile (account_eligibility.tr_minor_profile): no BMR/TDEE/calorie
# target, no target weight, no nutrition plan text, strength/fitness goal only.
MINOR_AGE_MIN, MINOR_AGE_MAX = 13, 17
ADULT_AGE_MIN = 18
MINOR_GOALS = (GOAL_BUILD_MUSCLE,)


def _require_minor_profile(profile):
    """The teen branch's own refusals; values are never named."""
    if not MINOR_AGE_MIN <= profile.age <= MINOR_AGE_MAX:
        raise ProfileRejected(AGE, REASON_RANGE)
    if profile.goal not in MINOR_GOALS:
        raise ProfileRejected(GOAL, REASON_VOCABULARY)
    if profile.target_weight is not None:
        raise ProfileRejected(TARGET_WEIGHT, REASON_VOCABULARY)
    if profile.goal_pace is not None:
        raise ProfileRejected(GOAL_PACE, REASON_VOCABULARY)


def require_weight_target_eligibility(user):
    """Require the canonical negative boundary before adult target work.

    Shared by onboarding and Weekly Check-in through their existing profile
    dependency. No transaction, transport handling, readiness or consent grant.
    """
    from app.services.account_eligibility import require_capability
    require_capability(user, "automated_weight_targets")


def complete_onboarding(user, profile):
    """Persist `profile` for `user` and make the account onboarded, atomically.

    One transaction writes the profile columns, the canonical `UserSession`
    (updated in place when one exists, created once when none does) and
    `profile_complete`. A failure anywhere rolls all of it back and raises
    `ProfilePersistenceFailed`; the account keeps its prior state. Repeating a
    submission updates the same session row and never appends another.

    On update, `created_at` and `coach_reply` of the reused row are left as
    they are: the row keeps its place in history and nothing a user already
    saw is erased.
    """
    from app.services.account_eligibility import tr_minor_profile
    minor = tr_minor_profile(user)
    if not minor:
        require_weight_target_eligibility(user)
    if not isinstance(profile, OnboardingProfile):
        raise TypeError("profile must be an OnboardingProfile")
    if minor:
        # No adult weight-target maths for a minor: absence stays NULL, which
        # every target reader already treats as "no target" (never 0).
        _require_minor_profile(profile)
    elif profile.age < ADULT_AGE_MIN:
        # V1 is adult-only (FDO-AGE18). An adult account's editable profile age
        # is never authority, and a value under 18 would only contradict its
        # signed record and lock the account out; refuse it as out of range.
        raise ProfileRejected(AGE, REASON_RANGE)

    stored_goal = _GOAL_STORED[profile.goal]
    training_plan = generate_training_plan(stored_goal, profile.fitness_level)

    try:
        locked_pace = _lock_owner(user.id)
        if minor:
            pace = None
            bmr = tdee = target_calories = nutrition_plan = None
        else:
            pace = profile.goal_pace or pace_for_goal(locked_pace, profile.goal)
            target_weight = (profile.target_weight
                             if profile.target_weight is not None
                             else _stored_number(user.target_weight))
            derived = derive_targets(
                weight=profile.weight, height=profile.height, age=profile.age,
                gender=profile.gender,
                current_activity=profile.current_activity,
                stored_goal=stored_goal, goal_pace=pace,
                target_weight=target_weight)
            bmr, tdee = derived.bmr, derived.tdee
            target_calories = derived.target_calories
            nutrition_plan = generate_nutrition_plan(stored_goal,
                                                     target_calories)
        session = canonical_session(user.id)
        created = session is None
        if created:
            session = UserSession(user_id=user.id, coach_reply="")
            db.session.add(session)

        user.weight = profile.weight
        user.height = profile.height
        user.age = profile.age
        user.gender = profile.gender
        user.goal = stored_goal
        user.goal_type = _GOAL_TYPE[profile.goal]
        user.goal_pace = pace
        if locked_pace != pace:
            # The in-memory row may hold a value loaded before the lock that
            # equals `pace`; force the UPDATE so the committed pace always
            # matches the target derived from it.
            flag_modified(user, "goal_pace")
        user.fitness_level = profile.fitness_level
        user.current_activity = profile.current_activity
        if profile.target_weight is not None:
            user.target_weight = profile.target_weight

        session.name = user.username
        session.age = profile.age
        session.gender = profile.gender
        session.weight = profile.weight
        session.height = profile.height
        session.goal = stored_goal
        session.fitness_level = profile.fitness_level
        session.current_activity = profile.current_activity
        session.bmr = bmr
        session.tdee = tdee
        session.target_calories = target_calories
        session.training_plan = training_plan
        session.nutrition_plan = nutrition_plan

        user.profile_complete = True
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        raise ProfilePersistenceFailed(type(error).__name__) from error

    return OnboardingResult(bmr=bmr, tdee=tdee,
                            target_calories=target_calories,
                            session_created=created)


class ProfileIncomplete(ValueError):
    """The account has no complete adult profile to apply a pace to."""


def set_goal_pace(user, pace):
    """Change only the owner's goal pace and re-derive the canonical target.

    `pace` is validated first (unknown token → `ProfileRejected(goal_pace,
    vocabulary)`). Then, under the owner-row lock, the stored profile is
    re-read and must be a complete adult profile (else `ProfileIncomplete`);
    the pace must belong to the stored goal (else `ProfileRejected`). The
    write itself is `complete_onboarding` with that same profile: one
    transaction updates `User.goal_pace` and the canonical session in place,
    so a repeated call is a no-op and nothing is appended.
    """
    if not isinstance(pace, str):
        raise ProfileRejected(GOAL_PACE, REASON_TYPE)
    _token(GOAL_PACE, pace, pace_rules.PACES)
    from app.services.account_eligibility import tr_minor_profile
    if tr_minor_profile(user):
        raise ProfileRejected(GOAL_PACE, REASON_VOCABULARY)
    require_weight_target_eligibility(user)
    try:
        _lock_owner(user.id)
        # The profile the pace is applied to is the one committed now, not a
        # copy loaded before the lock (a concurrent profile write wins whole).
        db.session.refresh(user)
        if not onboarding_state(user).complete:
            raise ProfileIncomplete("profile")
        stored = current_profile(user)
        stored.pop(GOAL_PACE)
        if any(stored[field] is None for field in (
                WEIGHT, HEIGHT, AGE, GENDER, GOAL, FITNESS_LEVEL,
                CURRENT_ACTIVITY)):
            raise ProfileIncomplete("profile")
        if stored[AGE] < ADULT_AGE_MIN:
            # A stored adult-account age under 18 contradicts the account; the
            # profile must be corrected first, the pace alone cannot apply.
            raise ProfileIncomplete(AGE)
        try:
            profile = OnboardingProfile(**{**stored, GOAL_PACE: pace})
        except ProfileRejected as rejection:
            if rejection.field == GOAL_PACE:
                raise
            # A stored legacy value outside today's ranges: the owner must
            # correct the profile first; the pace alone cannot be applied.
            raise ProfileIncomplete(rejection.field) from None
    except Exception:
        db.session.rollback()
        raise
    return complete_onboarding(user, profile)


# Read projection states.
TARGET_AVAILABLE = "available"
TARGET_NOT_SET = "not_set"
BASIS_STORED = "stored"


def _complete_adult_inputs(user):
    """The stored profile as `derive_targets` inputs, or None."""
    values = current_profile(user)
    if any(values[field] is None for field in (
            WEIGHT, HEIGHT, AGE, GENDER, GOAL, CURRENT_ACTIVITY)):
        return None
    if values[AGE] < ADULT_AGE_MIN:
        return None
    try:
        _body_weight(WEIGHT, values[WEIGHT])
        _positive(HEIGHT, values[HEIGHT])
    except ProfileRejected:
        return None
    return values


def _kcal(value):
    return int(math.floor(value + 0.5))


def calorie_target_view(user):
    """The canonical daily calorie target of `user`, for every client.

    Read-only: no write, no commit, no lock. `daily_kcal` is the canonical
    `UserSession.target_calories` — the value Nutrition, Today and Coach
    read — rounded half-up to a whole kcal, never recomputed. `basis`
    explains it by re-deriving from the stored profile and pace; when that
    does not reproduce the stored value (a profile changed by a path that
    does not re-derive), `basis` is `stored` and nothing is guessed.
    `pace_options` lists, per pace of the stored goal, the target a pace
    edit would persist — the server's numbers, so no client computes one.
    """
    from app.services.account_eligibility import tr_minor_profile
    goal = goal_token(user.goal)
    stored_pace = pace_for_goal(user.goal_pace, goal)
    view = {
        "state": TARGET_NOT_SET,
        "daily_kcal": None,
        "maintenance_kcal": None,
        "goal": goal,
        "goal_pace": stored_pace,
        "basis": None,
        "bounded": False,
        "reason": None,
        "pace_options": [],
    }
    state = onboarding_state(user)
    if not state.complete or tr_minor_profile(user):
        return view
    stored_target = _stored_number(state.session.target_calories)
    if stored_target is None or stored_target <= 0:
        return view
    view["state"] = TARGET_AVAILABLE
    view["daily_kcal"] = _kcal(stored_target)
    view["basis"] = BASIS_STORED

    values = _complete_adult_inputs(user)
    if values is None:
        return view

    def derive(pace):
        return derive_targets(
            weight=values[WEIGHT], height=values[HEIGHT], age=values[AGE],
            gender=values[GENDER], current_activity=values[CURRENT_ACTIVITY],
            stored_goal=user.goal, goal_pace=pace,
            target_weight=values[TARGET_WEIGHT])

    current = derive(stored_pace)
    if abs(current.target_calories - stored_target) < 0.5:
        view.update(basis=current.basis, bounded=current.bounded,
                    reason=current.reason)
    if not maintenance_usable(current.tdee):
        # No maintenance estimate a pace can size: no options are offered.
        return view
    view["maintenance_kcal"] = _kcal(pace_rules.round_kcal(current.tdee))
    for pace in PACES_FOR_GOAL.get(goal, ()):
        option = derive(pace)
        view["pace_options"].append({
            "goal_pace": pace,
            "daily_kcal": _kcal(option.target_calories),
            "basis": option.basis,
            "bounded": option.bounded,
            "reason": option.reason,
            "nominal_weekly_change_g":
                pace_rules.NOMINAL_WEEKLY_CHANGE_G.get(pace),
        })
    return view
