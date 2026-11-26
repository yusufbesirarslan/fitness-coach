"""PD1 (#547): goal pace and the canonical, server-owned daily calorie target.

    PUT /api/v1/account/profile        optional write-only `goal_pace`
    GET /api/v1/account/calorie-target canonical read (PD2 handoff contract)
    PUT /api/v1/account/goal-pace      pace-only edit

Acceptance map: A distinct bounded targets · B returned == canonical read ·
C pace persisted (survives a new request/session) · D edit without duplicate
rows · E goal change / maintenance / unsuitable / incomplete · F legacy and
old clients · G Nutrition reads the same value · H invalid input, rollback ·
J contract shape. Minors (I) are in tests/test_tr_minor_full_onboarding.py.

    python -m pytest tests/test_pd1_goal_pace.py -v
"""
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import User, UserSession
from app.services import account_profile, goal_pace, mobile_auth
from app.services.calculations import (
    calculate_bmr, calculate_target, calculate_tdee,
)

PROFILE = "/api/v1/account/profile"
TARGET = "/api/v1/account/calorie-target"
PACE = "/api/v1/account/goal-pace"
DAY_VIEW = "/api/v1/nutrition/day-view"
DIARY = "/api/v1/nutrition/diary/today"

BODY = {"weight_kg": 80, "height_cm": 180, "age": 30, "gender": "male",
        "goal": "lose_weight", "fitness_level": "beginner",
        "activity_level": "active"}
# 80 kg / 180 cm / 30 y / male / active: BMR 1780, maintenance 2759.
TDEE = calculate_tdee(calculate_bmr(80, 180, 30, "male"), "active")

VIEW_KEYS = {"state", "daily_kcal", "maintenance_kcal", "goal", "goal_pace",
             "basis", "bounded", "reason", "pace_options"}
OPTION_KEYS = {"goal_pace", "daily_kcal", "basis", "bounded", "reason",
               "nominal_weekly_change_g"}


@pytest.fixture
def principal(monkeypatch):
    current = {}

    def authenticate(raw):
        user = db.session.get(User, current["id"])
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def as_user(user):
        current["id"] = user.id
        return {"Authorization": "Bearer opaque-pd1-access"}
    return as_user


@pytest.fixture
def owner(make_user, principal):
    user = make_user("pd1-owner")
    return SimpleNamespace(id=user.id, headers=principal(user))


def _sessions(user_id):
    db.session.expire_all()
    return UserSession.query.filter_by(user_id=user_id).all()


def _user(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _view(client, headers):
    response = client.get(TARGET, headers=headers)
    assert response.status_code == 200, response.json
    assert set(response.json) == {"contract_version", "calorie_target"}
    assert response.json["contract_version"] == 1
    return response.json["calorie_target"]


def _onboard(client, owner, **extra):
    response = client.put(PROFILE, json={**BODY, **extra}, headers=owner.headers)
    assert response.status_code == 200, response.json
    return response


# ---------------------------------------------------------------------------
# Pure pace rules
# ---------------------------------------------------------------------------

def _pace(pace, *, tdee=TDEE, weight=80, height=180, gender="male", target=None):
    return goal_pace.pace_target(pace, tdee=tdee, weight=weight, height=height,
                                 gender=gender, target_weight=target)


def test_a_and_b_give_distinct_bounded_targets():
    slower, faster = _pace("lose_slower"), _pace("lose_faster")
    # 200 g/week = 220 kcal/day, 500 g/week = 550 kcal/day, rounded to 10.
    assert (slower.target_calories, faster.target_calories) == (2540.0, 2210.0)
    assert slower.basis == faster.basis == "pace"
    assert not slower.bounded and not faster.bounded
    assert slower.target_calories > faster.target_calories


def test_deficit_cap_and_calorie_floor_bound_the_faster_pace():
    # female 70 kg / 165 cm / 40 y / sedentary: maintenance 1644.3; 25 % cap.
    capped = _pace("lose_faster", tdee=1644.3, weight=70, height=165, gender="female")
    assert (capped.target_calories, capped.basis, capped.bounded) == (1230.0, "pace", True)
    # female 60 kg / 165 cm / 30 y / sedentary: maintenance 1584.3; floor 1200.
    floored = _pace("lose_faster", tdee=1584.3, weight=60, height=165, gender="female")
    assert (floored.target_calories, floored.bounded) == (1200.0, True)
    # unknown sex takes the higher floor
    unknown = _pace("lose_faster", tdee=1700.0, weight=70, height=165, gender=None)
    assert unknown.target_calories >= 1500.0


@pytest.mark.parametrize("kwargs, reason", [
    (dict(weight=55, height=180), "low_bmi"),                       # BMI 17.0
    (dict(target=80), "target_weight_reached"),
    (dict(target=85), "target_weight_reached"),
    (dict(tdee=1246.8, weight=50, height=160, gender="female"), "calorie_floor"),
])
def test_unsuitable_loss_falls_back_to_maintenance(kwargs, reason):
    for pace in goal_pace.LOSS_PACES:
        outcome = _pace(pace, **kwargs)
        tdee = kwargs.get("tdee", TDEE)
        assert (outcome.basis, outcome.reason, outcome.bounded) == (
            "maintenance", reason, False)
        assert outcome.target_calories == goal_pace.round_kcal(tdee)


def test_gain_options_are_distinct_individual_surpluses():
    lean, steady = _pace("gain_lean"), _pace("gain_steady")
    assert (lean.target_calories, steady.target_calories) == (2900.0, 3030.0)
    # clamped bands keep a small body's options distinct and a large body's capped
    small_lean = _pace("gain_lean", tdee=1500.0)
    small_steady = _pace("gain_steady", tdee=1500.0)
    assert (small_lean.target_calories, small_steady.target_calories) == (1600.0, 1700.0)
    assert _pace("gain_steady", tdee=6000.0).target_calories == 6450.0


@pytest.mark.parametrize("bad", ["", "LOSE_SLOWER", "slower", "A", None, 1])
def test_unknown_pace_is_refused(bad):
    with pytest.raises(ValueError):
        _pace(bad)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_non_positive_or_non_finite_inputs_are_refused(value):
    with pytest.raises(ValueError):
        _pace("lose_slower", tdee=value)


def test_rounding_is_half_up_to_ten():
    assert goal_pace.round_kcal(2535.0) == 2540.0
    assert goal_pace.round_kcal(2534.99) == 2530.0
    assert goal_pace.round_kcal(2545.0) == 2550.0  # not banker's rounding


# ---------------------------------------------------------------------------
# Onboarding, readback and parity (A, B, C, G)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pace, expected", [
    ("lose_slower", 2540.0), ("lose_faster", 2210.0),
])
def test_onboarding_with_a_pace_persists_one_canonical_target(
        raw_client, owner, pace, expected):
    response = _onboard(raw_client, owner, goal_pace=pace)
    # PUT profile answer is the unchanged account projection (old DTOs).
    assert set(response.json) == {"user"}
    assert "goal_pace" not in response.json["user"]

    (session,) = _sessions(owner.id)
    assert session.target_calories == expected
    assert _user(owner.id).goal_pace == pace

    view = _view(raw_client, owner.headers)
    assert view["state"] == "available"
    assert view["daily_kcal"] == int(expected)
    assert (view["goal"], view["goal_pace"], view["basis"]) == (
        "lose_weight", pace, "pace")
    assert view["maintenance_kcal"] == 2760


def test_returned_target_equals_nutrition_and_diary_reads(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_faster")
    view = _view(raw_client, owner.headers)
    day = raw_client.get(DAY_VIEW, headers=owner.headers)
    assert day.status_code == 200, day.json
    assert day.json["target"]["state"] == "available"
    assert day.json["target"]["value"] == view["daily_kcal"]
    diary = raw_client.get(DIARY, headers=owner.headers)
    assert diary.status_code == 200, diary.json
    assert diary.json["goal"]["target_energy_kcal"] == view["daily_kcal"]


def test_pace_options_are_the_values_an_edit_would_persist(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower")
    options = _view(raw_client, owner.headers)["pace_options"]
    assert [o["goal_pace"] for o in options] == ["lose_slower", "lose_faster"]
    assert all(set(o) == OPTION_KEYS for o in options)
    assert [o["nominal_weekly_change_g"] for o in options] == [-200, -500]
    for option in options:
        edited = raw_client.put(PACE, json={"goal_pace": option["goal_pace"]},
                                headers=owner.headers)
        assert edited.status_code == 200, edited.json
        assert edited.json["calorie_target"]["daily_kcal"] == option["daily_kcal"]
        assert _sessions(owner.id)[0].target_calories == option["daily_kcal"]


def test_view_contract_keys_are_closed(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower")
    assert set(_view(raw_client, owner.headers)) == VIEW_KEYS


def test_pace_survives_a_new_request_and_principal(raw_client, make_user, principal):
    user = make_user("pd1-restart")
    headers = principal(user)
    raw_client.put(PROFILE, json={**BODY, "goal_pace": "lose_faster"}, headers=headers)
    db.session.remove()  # nothing cached between "sessions"
    again = principal(db.session.get(User, user.id))
    view = _view(raw_client, again)
    assert (view["goal_pace"], view["daily_kcal"]) == ("lose_faster", 2210)


# ---------------------------------------------------------------------------
# Pace edit (D)
# ---------------------------------------------------------------------------

def test_edit_updates_the_target_in_place_and_repeats_as_a_no_op(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower")
    (before,) = _sessions(owner.id)
    first = raw_client.put(PACE, json={"goal_pace": "lose_faster"}, headers=owner.headers)
    assert first.status_code == 200, first.json
    assert first.headers["Cache-Control"] == "no-store"
    assert first.json == {"contract_version": 1,
                          "calorie_target": _view(raw_client, owner.headers)}
    second = raw_client.put(PACE, json={"goal_pace": "lose_faster"}, headers=owner.headers)
    assert second.json == first.json
    (after,) = _sessions(owner.id)
    assert after.id == before.id
    assert after.target_calories == 2210.0
    assert UserSession.query.count() == 1


def test_edit_keeps_every_other_profile_value(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower", target_weight_kg=72)
    before = account_profile.current_profile(_user(owner.id))
    raw_client.put(PACE, json={"goal_pace": "lose_faster"}, headers=owner.headers)
    after = account_profile.current_profile(_user(owner.id))
    assert {**before, "goal_pace": "lose_faster"} == after


# ---------------------------------------------------------------------------
# Goal change, maintenance, unsuitable, incomplete (E)
# ---------------------------------------------------------------------------

def test_goal_change_without_a_pace_clears_it_and_uses_the_legacy_formula(
        raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_faster")
    _onboard(raw_client, owner, goal="build_muscle")
    assert _user(owner.id).goal_pace is None
    assert _sessions(owner.id)[0].target_calories == calculate_target(TDEE, "kas kazanma")
    view = _view(raw_client, owner.headers)
    assert (view["goal"], view["goal_pace"], view["basis"]) == (
        "build_muscle", None, "legacy")
    assert [o["goal_pace"] for o in view["pace_options"]] == ["gain_lean", "gain_steady"]
    assert all(o["nominal_weekly_change_g"] is None for o in view["pace_options"])


def test_goal_change_with_a_gain_pace(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower")
    _onboard(raw_client, owner, goal="build_muscle", goal_pace="gain_steady")
    assert _sessions(owner.id)[0].target_calories == 3030.0


def test_unsuitable_pace_is_stored_but_targets_maintenance(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower", target_weight_kg=85)
    view = _view(raw_client, owner.headers)
    assert (view["basis"], view["reason"], view["daily_kcal"]) == (
        "maintenance", "target_weight_reached", 2760)
    assert view["goal_pace"] == "lose_slower"


def test_edit_without_a_complete_profile_is_409_and_writes_nothing(raw_client, owner):
    response = raw_client.put(PACE, json={"goal_pace": "lose_slower"},
                              headers=owner.headers)
    assert response.status_code == 409
    assert response.json["error"]["code"] == "PROFILE_INCOMPLETE"
    assert _user(owner.id).goal_pace is None and _sessions(owner.id) == []
    view = _view(raw_client, owner.headers)
    assert (view["state"], view["daily_kcal"], view["pace_options"]) == (
        "not_set", None, [])


def test_edit_with_a_legacy_out_of_range_stored_value_is_409(raw_client, owner):
    _onboard(raw_client, owner)
    user = _user(owner.id)
    user.weight = 900.0  # a legacy row outside today's canonical range
    db.session.commit()
    response = raw_client.put(PACE, json={"goal_pace": "lose_slower"},
                              headers=owner.headers)
    assert response.status_code == 409
    assert _user(owner.id).goal_pace is None


# ---------------------------------------------------------------------------
# Legacy accounts and old clients (F)
# ---------------------------------------------------------------------------

def test_legacy_account_is_not_retargeted(raw_client, owner):
    _onboard(raw_client, owner)  # an old client: no goal_pace key at all
    (session,) = _sessions(owner.id)
    assert session.target_calories == TDEE - 400
    assert _user(owner.id).goal_pace is None
    view = _view(raw_client, owner.headers)
    assert (view["basis"], view["goal_pace"], view["daily_kcal"]) == (
        "legacy", None, round(TDEE - 400))
    # reading never writes
    assert _sessions(owner.id)[0].target_calories == TDEE - 400


def test_old_client_resubmission_keeps_the_stored_pace(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_faster")
    _onboard(raw_client, owner)  # same goal, no goal_pace key
    assert _user(owner.id).goal_pace == "lose_faster"
    assert _sessions(owner.id)[0].target_calories == 2210.0


def test_profile_read_contract_is_unchanged(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_faster")
    profile = raw_client.get(PROFILE, headers=owner.headers).json["profile"]
    assert "goal_pace" not in profile
    me = raw_client.get("/api/v1/account/me", headers=owner.headers).json["user"]
    assert "goal_pace" not in me


def test_a_pace_from_another_goal_reads_as_none_and_basis_stored(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_faster")
    user = _user(owner.id)
    user.goal = "kas kazanma"  # web /edit-profile changes the goal only
    db.session.commit()
    view = _view(raw_client, owner.headers)
    assert view["goal_pace"] is None
    assert view["basis"] == "stored"           # stored value is not re-derived
    assert view["daily_kcal"] == 2210          # still the canonical stored target


def test_body_weight_update_keeps_the_pace(app, raw_client, owner):
    from app.services.weekly_checkin import service as checkin_service
    _onboard(raw_client, owner, goal_pace="lose_slower")
    user, (session,) = _user(owner.id), _sessions(owner.id)
    update = checkin_service.apply_body_weight(user, 78.0, session)
    db.session.commit()
    tdee = calculate_tdee(calculate_bmr(78.0, 180, 30, "male"), "active")
    assert update.target_calories == goal_pace.round_kcal(tdee - 220)
    assert _view(raw_client, owner.headers)["basis"] == "pace"


def test_body_weight_update_without_a_pace_is_the_legacy_formula(app, raw_client, owner):
    from app.services.weekly_checkin import service as checkin_service
    _onboard(raw_client, owner)
    user, (session,) = _user(owner.id), _sessions(owner.id)
    update = checkin_service.apply_body_weight(user, 78.0, session)
    tdee = calculate_tdee(calculate_bmr(78.0, 180, 30, "male"), "active")
    assert update.target_calories == tdee - 400


# ---------------------------------------------------------------------------
# Invalid input, unknown vocabulary, failure (H)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body, status", [
    ({}, 400),
    ({"goal_pace": "lose_slower", "user_id": 1}, 400),
    ({"goal_pace": "lose_slower", "daily_kcal": 1200}, 400),
    ({"goal_pace": 1}, 400),
    ({"goal_pace": None}, 400),
    ({"goal_pace": "A"}, 422),
    ({"goal_pace": "LOSE_SLOWER"}, 422),
    ({"goal_pace": "gain_lean"}, 422),       # a pace of another goal
])
def test_invalid_edit_bodies_are_refused_without_a_write(raw_client, owner, body, status):
    _onboard(raw_client, owner, goal_pace="lose_slower")
    response = raw_client.put(PACE, json=body, headers=owner.headers)
    assert response.status_code == status
    assert _user(owner.id).goal_pace == "lose_slower"
    assert _sessions(owner.id)[0].target_calories == 2540.0


def test_edit_body_must_be_a_json_object(raw_client, owner):
    _onboard(raw_client, owner)
    for data in ('["lose_slower"]', '"lose_slower"', "not json"):
        response = raw_client.put(PACE, data=data, content_type="application/json",
                                  headers=owner.headers)
        assert response.status_code == 400


@pytest.mark.parametrize("value, status", [
    ("A", 422), ("gain_steady", 422), (5, 400), (["lose_slower"], 400),
])
def test_profile_write_refuses_an_invalid_pace(raw_client, owner, value, status):
    response = raw_client.put(PROFILE, json={**BODY, "goal_pace": value},
                              headers=owner.headers)
    assert response.status_code == status
    assert _sessions(owner.id) == []


def test_a_commit_failure_rolls_back_pace_and_target(raw_client, owner, monkeypatch):
    _onboard(raw_client, owner, goal_pace="lose_slower")

    def boom():
        raise RuntimeError("commit fault")
    monkeypatch.setattr(db.session, "commit", boom)
    response = raw_client.put(PACE, json={"goal_pace": "lose_faster"},
                              headers=owner.headers)
    monkeypatch.undo()
    assert response.status_code == 503
    assert response.json["error"]["code"] == "PROFILE_TEMPORARILY_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert _user(owner.id).goal_pace == "lose_slower"
    assert _sessions(owner.id)[0].target_calories == 2540.0


def test_a_read_fault_is_a_typed_503(raw_client, owner, monkeypatch):
    _onboard(raw_client, owner)

    def boom(_user):
        raise RuntimeError("read fault")
    monkeypatch.setattr(account_profile, "calorie_target_view", boom)
    response = raw_client.get(TARGET, headers=owner.headers)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "PROFILE_TEMPORARILY_UNAVAILABLE"


def test_accounts_are_isolated(raw_client, make_user, principal):
    a, b = make_user("pd1-a"), make_user("pd1-b")
    a_id, b_id = a.id, b.id
    raw_client.put(PROFILE, json={**BODY, "goal_pace": "lose_slower"}, headers=principal(a))
    headers_b = principal(db.session.get(User, b_id))
    assert raw_client.put(PACE, json={"goal_pace": "lose_faster"},
                          headers=headers_b).status_code == 409
    assert _user(a_id).goal_pace == "lose_slower"
    assert _view(raw_client, headers_b)["state"] == "not_set"


def test_goal_pace_is_erased_with_the_health_profile():
    from app.services import health_erasure
    assert "goal_pace" in health_erasure.PROFILE_FIELDS


# ---------------------------------------------------------------------------
# Review fixes (P2-2, P3-2)
# ---------------------------------------------------------------------------

def test_a_non_positive_maintenance_never_raises(raw_client, make_user, principal):
    # Height typed in metres: BMR and maintenance go negative.
    body = {**BODY, "weight_kg": 45, "height_cm": 1.6, "age": 80,
            "gender": "female", "goal": "build_muscle", "goal_pace": "gain_lean"}
    user = make_user("pd1-metres")
    headers = principal(user)
    response = raw_client.put(PROFILE, json=body, headers=headers)
    assert response.status_code == 200, response.json
    tdee = calculate_tdee(calculate_bmr(45, 1.6, 80, "female"), "active")
    assert tdee <= 0
    assert _sessions(user.id)[0].target_calories == calculate_target(
        tdee, "kas kazanma")
    view = _view(raw_client, headers)
    assert view["pace_options"] == [] and view["maintenance_kcal"] is None
    assert account_profile.derive_targets(
        weight=45, height=1.6, age=80, gender="female",
        current_activity="active", stored_goal="kas kazanma",
        goal_pace="gain_lean").basis == "legacy"


def test_edit_with_a_stored_age_under_18_is_409(raw_client, owner):
    _onboard(raw_client, owner, goal_pace="lose_slower")
    user = _user(owner.id)
    user.age = 16  # a contradictory legacy value on an adult account
    db.session.commit()
    response = raw_client.put(PACE, json={"goal_pace": "lose_faster"},
                              headers=owner.headers)
    assert (response.status_code, response.json["error"]["code"]) == (
        409, "PROFILE_INCOMPLETE")
    assert _user(owner.id).goal_pace == "lose_slower"
