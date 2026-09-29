"""LP-03 intentional web behaviour changes, asserted as the NEW invariants.

Each test here FAILS against the pre-LP-03 `/setup` (the old behaviour was
pinned first in tests/test_web_setup_characterization.py, then moved here as
its corrected form). Everything not listed here is unchanged and still pinned
by the characterization file.

    A. Atomic onboarding — profile, canonical UserSession and completeness
       commit together or not at all.
    B. Repeat-safe onboarding — a repeat updates the canonical session instead
       of appending another.
    C. One readiness rule — `/`, `GET /setup`, `account/me` and both first-plan
       prerequisites answer from `account_profile.onboarding_state`.
    D. Fail-closed crafted input — values the wizard can never post (unknown
       vocabulary, non-finite / non-positive numbers, weight outside the
       canonical range, a JSON array body) are refused instead of persisted
       or crashed on.

    python -m pytest tests/test_web_setup_lp03_deltas.py -v
"""
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import User, UserSession
from app.services import mobile_auth

PAYLOAD = {
    "weight": 80, "height": 180, "age": 30, "gender": "male",
    "goal": "kilo verme", "fitness_level": "beginner",
    "current_activity": "active", "target_weight": "75",
}


def _user(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _sessions(user_id):
    db.session.expire_all()
    return UserSession.query.filter_by(user_id=user_id).all()


@pytest.fixture
def refuse_session_rows():
    def listener(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.new | session.dirty):
            raise RuntimeError("injected UserSession persistence failure")

    event.listen(db.session, "before_flush", listener)
    yield
    event.remove(db.session, "before_flush", listener)


# ---------------------------------------------------------------------------
# A. Atomic
# ---------------------------------------------------------------------------

def test_a_session_failure_leaves_the_account_incomplete(
        app, client, auth_user, refuse_session_rows):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.post("/setup", json=PAYLOAD)
    assert response.status_code == 500
    user = _user(auth_user.id)
    assert user.profile_complete is not True
    assert (user.weight, user.goal, user.target_weight) == (None, None, None)
    assert _sessions(auth_user.id) == []
    # The account is still sent to onboarding, not to a Today it cannot use.
    assert client.get("/").status_code == 302
    assert client.get("/setup").status_code == 200


def test_a_failed_resubmission_keeps_the_prior_valid_profile(app, client, auth_user):
    client.post("/setup", json=PAYLOAD)
    app.config["PROPAGATE_EXCEPTIONS"] = False

    def refuse(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.dirty | session.new):
            raise RuntimeError("injected")

    event.listen(db.session, "before_flush", refuse)
    try:
        response = client.post("/setup", json={**PAYLOAD, "weight": 60,
                                               "goal": "kas kazanma"})
    finally:
        event.remove(db.session, "before_flush", refuse)
    assert response.status_code == 500
    user = _user(auth_user.id)
    assert (user.weight, user.goal, user.profile_complete) == (80.0, "kilo verme", True)
    [session] = _sessions(auth_user.id)
    assert (session.weight, session.goal) == (80.0, "kilo verme")


# ---------------------------------------------------------------------------
# B. Repeat-safe
# ---------------------------------------------------------------------------

def test_b_repeat_submission_updates_the_one_canonical_session(client, auth_user):
    first = client.post("/setup", json=PAYLOAD)
    [session] = _sessions(auth_user.id)
    same = client.post("/setup", json=PAYLOAD)
    revised = client.post("/setup", json={**PAYLOAD, "weight": 78,
                                          "goal": "kas kazanma"})

    assert first.get_json() == same.get_json()
    assert revised.status_code == 200
    [updated] = _sessions(auth_user.id)
    assert updated.id == session.id
    assert updated.created_at == session.created_at
    assert (updated.weight, updated.goal) == (78.0, "kas kazanma")
    assert (_user(auth_user.id).weight, _user(auth_user.id).goal_type) == (78.0, "gain")


# ---------------------------------------------------------------------------
# C. One readiness rule
# ---------------------------------------------------------------------------

def _mobile_me(raw_client, monkeypatch, user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    return raw_client.get(
        "/api/v1/account/me", headers={"Authorization": "Bearer opaque"}
    ).json["user"]["profile_complete"]


def _first_plan_prerequisite_met(user):
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.errors import (
        GenerationPrerequisiteMissing)
    # The pre-LP-03 prerequisite took a user id; the canonical one takes the
    # user. Resolve by signature so this file runs against both.
    param = next(iter(inspect.signature(service._required_session).parameters))
    try:
        service._required_session(user if param == "user" else user.id)
    except GenerationPrerequisiteMissing:
        return False
    return True


def test_c_complete_flag_without_session_is_incomplete_everywhere(
        client, raw_client, auth_user, monkeypatch):
    auth_user.profile_complete = True
    db.session.commit()
    assert _mobile_me(raw_client, monkeypatch, auth_user) is False
    assert _first_plan_prerequisite_met(auth_user) is False
    home = client.get("/")
    assert home.status_code == 302 and home.headers["Location"].endswith("/setup")
    assert client.get("/setup").status_code == 200     # no redirect loop


def test_c_session_without_flag_is_incomplete_everywhere(
        client, raw_client, auth_user, monkeypatch):
    db.session.add(UserSession(user_id=auth_user.id, goal="kilo verme",
                               fitness_level="beginner", current_activity="active",
                               tdee=2400))
    db.session.commit()
    assert _mobile_me(raw_client, monkeypatch, auth_user) is False
    assert _first_plan_prerequisite_met(auth_user) is False
    response = client.post("/training-plan", json={})
    assert response.status_code == 400
    assert response.get_json()["code"] == "TRAINING_PLAN_NO_SESSION"


def test_c_onboarded_account_is_complete_everywhere(
        client, raw_client, auth_user, monkeypatch):
    client.post("/setup", json=PAYLOAD)
    assert _mobile_me(raw_client, monkeypatch, _user(auth_user.id)) is True
    assert _first_plan_prerequisite_met(_user(auth_user.id)) is True
    assert client.get("/").status_code == 200
    assert client.get("/setup").status_code == 302


# ---------------------------------------------------------------------------
# D. Fail-closed crafted input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("field", "value", "message"), [
    ("goal", "tamamen uydurma", "Geçersiz hedef seçimi."),
    ("goal", "lose_weight", "Geçersiz hedef seçimi."),
    ("gender", "robot", "Geçersiz değer."),
    ("fitness_level", "olympian", "Geçersiz değer."),
    ("current_activity", "couch", "Geçersiz değer."),
    ("gender", 7, "Geçersiz değer."),
])
def test_d_unknown_vocabulary_is_refused(client, auth_user, field, value, message):
    response = client.post("/setup", json={**PAYLOAD, field: value})
    assert response.status_code == 400
    assert response.get_json() == {"error": message}
    assert _user(auth_user.id).profile_complete is not True
    assert _sessions(auth_user.id) == []


@pytest.mark.parametrize(("field", "value", "message"), [
    ("weight", -80, "Kilo 20 ile 500 kg arasında olmalıdır"),
    ("weight", 5, "Kilo 20 ile 500 kg arasında olmalıdır"),
    ("weight", 501, "Kilo 20 ile 500 kg arasında olmalıdır"),
    ("height", -1, "Kilo, boy ve yaş sayısal olmalıdır"),
    ("age", -3, "Kilo, boy ve yaş sayısal olmalıdır"),
    ("weight", "nan", "Kilo, boy ve yaş sayısal olmalıdır"),
    ("height", "inf", "Kilo, boy ve yaş sayısal olmalıdır"),
    ("age", [30], "Kilo, boy ve yaş sayısal olmalıdır"),
])
def test_d_physically_invalid_numbers_are_refused(
        client, auth_user, field, value, message):
    response = client.post("/setup", json={**PAYLOAD, field: value})
    assert response.status_code == 400
    assert response.get_json() == {"error": message}
    assert _user(auth_user.id).profile_complete is not True
    assert _sessions(auth_user.id) == []


def test_d_json_array_body_is_a_validation_answer(client, auth_user):
    response = client.post("/setup", json=[1, 2])
    assert response.status_code == 400
    assert response.get_json() == {"error": "weight alanı eksik"}


@pytest.mark.parametrize("value", ["5", "600", "inf", "nan"])
def test_d_out_of_rule_target_weight_is_dropped_like_an_unparseable_one(
        client, auth_user, value):
    response = client.post("/setup", json={**PAYLOAD, "target_weight": value})
    assert response.status_code == 200
    assert _user(auth_user.id).target_weight is None
