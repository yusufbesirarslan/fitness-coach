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
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.extensions import db
from app.models import User, UserSession
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
    """

    weight: float
    height: float
    age: int
    gender: str
    goal: str
    fitness_level: str
    current_activity: str
    target_weight: Optional[float] = None

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
    }


def _lock_owner(user_id):
    """`SELECT … FOR UPDATE` the owner row for its serializing effect.

    Two first submissions would otherwise both see "no session" and both
    insert one. A column query, so the identity-mapped user is not handed back
    stale (see `plan_owner_lock.lock_plan_owner`). Position 2 of the
    repository lock order (`user` row); nothing further is locked here. No-op
    on SQLite, which admits one writer at a time.
    """
    db.session.query(User.id).filter_by(id=user_id).with_for_update().first()


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
    if not isinstance(profile, OnboardingProfile):
        raise TypeError("profile must be an OnboardingProfile")

    stored_goal = _GOAL_STORED[profile.goal]
    bmr = calculate_bmr(profile.weight, profile.height, profile.age,
                        profile.gender)
    tdee = calculate_tdee(bmr, profile.current_activity)
    target_calories = calculate_target(tdee, stored_goal)
    training_plan = generate_training_plan(stored_goal, profile.fitness_level)
    nutrition_plan = generate_nutrition_plan(stored_goal, target_calories)

    try:
        _lock_owner(user.id)
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
