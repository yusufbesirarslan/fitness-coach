"""Characterization of the browser onboarding route `/setup` (LP-03).

Pins what `/setup` answers and persists BEFORE its domain logic moves into the
shared onboarding service, so the extraction can be checked against the old
behaviour instead of against a description of it. Exact bodies, statuses,
messages and persisted columns are pinned on purpose.

The `CURRENT DEFECT` tests pin the behaviour LP-03 is authorized to change
(duplicate `UserSession` per submission; a profile marked complete before the
session that makes it usable is persisted). They assert today's behaviour so
the change is visible as a deliberate diff in the PR, not as silent drift.

    python -m pytest tests/test_web_setup_characterization.py -v
"""
import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import User, UserSession

PAYLOAD = {
    "weight": 80, "height": 180, "age": 30, "gender": "male",
    "goal": "kilo verme", "fitness_level": "beginner",
    "current_activity": "active", "target_weight": "75",
}
REQUIRED = ["weight", "height", "age", "gender", "goal", "fitness_level",
            "current_activity"]


def _user(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _sessions(user_id):
    db.session.expire_all()
    return UserSession.query.filter_by(user_id=user_id).all()


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def test_get_renders_wizard_for_incomplete_profile(client, auth_user):
    response = client.get("/setup")
    assert response.status_code == 200
    assert 'id="s-weight"' in response.get_data(as_text=True)


def test_get_redirects_completed_profile_home(client, auth_user):
    client.post("/setup", json=PAYLOAD)
    response = client.get("/setup")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_get_reopens_wizard_with_yeniden(client, auth_user):
    client.post("/setup", json=PAYLOAD)
    assert client.get("/setup?yeniden=1").status_code == 200


def test_anonymous_get_and_post_do_not_reach_the_route(client):
    assert client.get("/setup").status_code in (302, 401)
    assert client.post("/setup", json=PAYLOAD).status_code in (302, 401)
    assert UserSession.query.count() == 0


# ---------------------------------------------------------------------------
# POST — success
# ---------------------------------------------------------------------------

def test_success_exact_body(client, auth_user):
    response = client.post("/setup", json=PAYLOAD)
    assert response.status_code == 200
    assert response.get_json() == {
        "message": "Profil kaydedildi.",
        "bmr": 1780,
        "tdee": 2759,
        "target_calories": 2359,
    }


def test_success_persists_user_columns(client, auth_user):
    client.post("/setup", json=PAYLOAD)
    user = _user(auth_user.id)
    assert (user.weight, user.height, user.age) == (80.0, 180.0, 30)
    assert (user.gender, user.goal, user.fitness_level, user.current_activity) == (
        "male", "kilo verme", "beginner", "active")
    assert user.profile_complete is True
    assert user.goal_type == "loss"
    assert user.target_weight == 75.0


def test_success_persists_one_session_row(client, auth_user):
    client.post("/setup", json=PAYLOAD)
    [session] = _sessions(auth_user.id)
    assert session.name == "testuser"
    assert (session.age, session.gender, session.weight, session.height) == (
        30, "male", 80.0, 180.0)
    assert (session.goal, session.fitness_level, session.current_activity) == (
        "kilo verme", "beginner", "active")
    assert session.bmr == 1780
    assert session.tdee == pytest.approx(1780 * 1.55)
    assert session.target_calories == pytest.approx(1780 * 1.55 - 400)
    assert session.training_plan == (
        "Haftada 3 gün 30 dk yürüyüş + 2 gün hafif kardiyo")
    assert session.nutrition_plan == (
        "Günlük 2359 kcal — yüksek protein, düşük işlenmiş karbonhidrat")
    assert session.coach_reply == ""


@pytest.mark.parametrize(("goal", "goal_type", "target"), [
    ("kilo verme", "loss", 2359),
    ("kas kazanma", "gain", 3059),
])
def test_goal_drives_goal_type_and_target(client, auth_user, goal, goal_type, target):
    response = client.post("/setup", json={**PAYLOAD, "goal": goal})
    assert response.get_json()["target_calories"] == target
    assert _user(auth_user.id).goal_type == goal_type


@pytest.mark.parametrize(("gender", "activity", "bmr", "tdee"), [
    ("female", "sedentary", 1614, 1937),
    ("male", "very_active", 1780, 3115),
])
def test_gender_and_activity_drive_bmr_and_tdee(
        client, auth_user, gender, activity, bmr, tdee):
    body = client.post("/setup", json={
        **PAYLOAD, "gender": gender, "current_activity": activity}).get_json()
    assert (body["bmr"], body["tdee"]) == (bmr, tdee)


def test_numeric_strings_are_accepted(client, auth_user):
    response = client.post("/setup", json={
        **PAYLOAD, "weight": "80", "height": "180", "age": "30"})
    assert response.status_code == 200
    assert _user(auth_user.id).age == 30


def test_fractional_json_age_is_truncated(client, auth_user):
    assert client.post("/setup", json={**PAYLOAD, "age": 30.9}).status_code == 200
    assert _user(auth_user.id).age == 30


def test_absent_target_weight_keeps_existing_value(client, auth_user):
    auth_user.target_weight = 70.0
    db.session.commit()
    payload = {k: v for k, v in PAYLOAD.items() if k != "target_weight"}
    assert client.post("/setup", json=payload).status_code == 200
    assert _user(auth_user.id).target_weight == 70.0


@pytest.mark.parametrize("value", ["bilmem", [], {}])
def test_unparseable_target_weight_is_ignored(client, auth_user, value):
    response = client.post("/setup", json={**PAYLOAD, "target_weight": value})
    assert response.status_code == 200
    assert _user(auth_user.id).target_weight is None


# ---------------------------------------------------------------------------
# POST — rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", REQUIRED)
@pytest.mark.parametrize("value", ["<absent>", "", None, 0])
def test_missing_or_falsy_required_field(client, auth_user, field, value):
    payload = dict(PAYLOAD)
    if value == "<absent>":
        del payload[field]
    else:
        payload[field] = value
    response = client.post("/setup", json=payload)
    assert response.status_code == 400
    assert response.get_json() == {"error": f"{field} alanı eksik"}
    assert _user(auth_user.id).profile_complete is not True
    assert _sessions(auth_user.id) == []


def test_first_missing_field_in_declared_order_is_reported(client, auth_user):
    response = client.post("/setup", json={"goal": "kilo verme"})
    assert response.get_json() == {"error": "weight alanı eksik"}


@pytest.mark.parametrize("body", [None, "not json", [1, 2]])
def test_non_object_body_reports_first_field_missing(app, client, auth_user, body):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    if body is None:
        response = client.post("/setup", data="x", content_type="text/plain")
    elif isinstance(body, str):
        response = client.post("/setup", data=body, content_type="application/json")
    else:
        response = client.post("/setup", json=body)
    if isinstance(body, list):
        # A JSON array survives `or {}` and then fails `.get` — a crash, not
        # a validation answer. Recorded, not endorsed.
        assert response.status_code == 500
    else:
        assert response.status_code == 400
        assert response.get_json() == {"error": "weight alanı eksik"}


@pytest.mark.parametrize(("field", "value"), [
    ("weight", "seksen"), ("height", "abc"), ("age", "otuz"), ("age", "30.5"),
])
def test_non_numeric_values_rejected(client, auth_user, field, value):
    response = client.post("/setup", json={**PAYLOAD, field: value})
    assert response.status_code == 400
    assert response.get_json() == {"error": "Kilo, boy ve yaş sayısal olmalıdır"}
    assert _user(auth_user.id).profile_complete is not True
    assert _sessions(auth_user.id) == []


# ---------------------------------------------------------------------------
# CURRENT DEFECTS — pinned here, changed deliberately by LP-03
# ---------------------------------------------------------------------------

def test_current_defect_repeat_submission_appends_a_session(client, auth_user):
    client.post("/setup", json=PAYLOAD)
    client.post("/setup", json={**PAYLOAD, "weight": 78})
    assert len(_sessions(auth_user.id)) == 2


def test_current_defect_session_failure_leaves_profile_complete(app, client, auth_user):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    def refuse_session_rows(session, _flush_context, _instances):
        if any(isinstance(obj, UserSession) for obj in session.new | session.dirty):
            raise RuntimeError("injected UserSession persistence failure")

    event.listen(db.session, "before_flush", refuse_session_rows)
    try:
        response = client.post("/setup", json=PAYLOAD)
    finally:
        event.remove(db.session, "before_flush", refuse_session_rows)
    assert response.status_code == 500
    assert _sessions(auth_user.id) == []
    assert _user(auth_user.id).profile_complete is True


@pytest.mark.parametrize(("field", "value"), [
    ("goal", "tamamen uydurma"), ("gender", "robot"),
    ("fitness_level", "olympian"), ("current_activity", "couch"),
])
def test_current_defect_unknown_vocabulary_is_persisted(client, auth_user, field, value):
    assert client.post("/setup", json={**PAYLOAD, field: value}).status_code == 200
    assert getattr(_user(auth_user.id), field) == value


@pytest.mark.parametrize(("field", "value"), [
    ("weight", -80), ("height", 0.0001), ("weight", 5), ("age", -3),
])
def test_current_defect_physically_invalid_numbers_are_persisted(
        client, auth_user, field, value):
    assert client.post("/setup", json={**PAYLOAD, field: value}).status_code == 200
    assert _user(auth_user.id).profile_complete is True


def test_current_defect_nan_weight_crashes_after_marking_complete(
        app, client, auth_user):
    # SQLite stores NaN as NULL; the reloaded NULL weight then crashes the BMR
    # formula after the first commit already marked the profile complete.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.post("/setup", json={**PAYLOAD, "weight": "nan"})
    assert response.status_code == 500
    assert _user(auth_user.id).profile_complete is True
    assert _sessions(auth_user.id) == []


def test_current_defect_infinite_height_persists_then_crashes(
        app, client, auth_user):
    # Both commits succeed with an infinite height; `round(inf)` then turns the
    # already-persisted onboarding into a 500 for the caller.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.post("/setup", json={**PAYLOAD, "height": "inf"})
    assert response.status_code == 500
    assert _user(auth_user.id).profile_complete is True
    assert len(_sessions(auth_user.id)) == 1


# ---------------------------------------------------------------------------
# CURRENT DEFECT — two readiness rules
#
# `profile_complete` (account/me, `/` gate, `/setup` GET) and "a UserSession
# exists" (both first-plan generators) answer the same question differently.
# ---------------------------------------------------------------------------

def _divergent_user(make_user, *, flag, with_session):
    user = make_user("divergent", profile_complete=flag)
    if with_session:
        db.session.add(UserSession(
            user_id=user.id, goal="kilo verme", fitness_level="beginner",
            current_activity="active", tdee=2400))
        db.session.commit()
    return user


def _mobile_me(raw_client, monkeypatch, user):
    from types import SimpleNamespace
    from app.services import mobile_auth
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    return raw_client.get(
        "/api/v1/account/me", headers={"Authorization": "Bearer opaque"})


def _first_plan_prerequisite_met(user):
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.errors import (
        GenerationPrerequisiteMissing)
    try:
        service._required_session(user.id)
    except GenerationPrerequisiteMissing:
        return False
    return True


def test_current_defect_complete_flag_without_session(raw_client, make_user, monkeypatch):
    user = _divergent_user(make_user, flag=True, with_session=False)
    assert _mobile_me(raw_client, monkeypatch, user).json["user"]["profile_complete"] is True
    assert _first_plan_prerequisite_met(user) is False


def test_current_defect_session_without_complete_flag(raw_client, make_user, monkeypatch):
    user = _divergent_user(make_user, flag=False, with_session=True)
    assert _mobile_me(raw_client, monkeypatch, user).json["user"]["profile_complete"] is False
    assert _first_plan_prerequisite_met(user) is True
