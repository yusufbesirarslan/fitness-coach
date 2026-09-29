"""LP-03 acceptance: native onboarding clears the first-plan prerequisite.

Drives the real native surface end to end — register, verify, login (a real
opaque Bearer credential, not a principal stub), `GET /account/me`,
`PUT /account/profile`, `POST /training/plans` — with only the identity
provider and the model provider faked. The proof is that the SAME persisted
state answers "complete" on the account surface and "eligible" at the
first-plan prerequisite, and that a failed onboarding answers "no" at both.

    python -m pytest tests/test_mobile_onboarding_first_plan.py -v
"""
import calendar
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.blueprints import mobile_registration
from app.extensions import db
from app.models import TrainingPlan, TrainingPlanGenerationOperation, User, UserSession
from app.services import cognito_jwt, cognito_service
from app.services.account_profile import onboarding_state
from app.services.mobile_training_generation import service as generation_service
from app.services.mobile_training_generation.errors import GenerationPrerequisiteMissing
from tests.test_mobile_training_generation_api import CANONICAL, _document

USERNAME = "onboardnative"
PASSWORD = "Sifre123"
PROFILE = {
    "weight_kg": 80, "height_cm": 180, "age": 30, "gender": "male",
    "goal": "build_muscle", "fitness_level": "intermediate",
    "activity_level": "active",
}


@pytest.fixture
def identity_provider(monkeypatch):
    """Cognito, faked at the provider boundary only."""
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    monkeypatch.setattr(cognito_service, "sign_up",
                        lambda username, password, email, name: f"sub-{username}")
    monkeypatch.setattr(cognito_service, "confirm_sign_up",
                        lambda username, code: None)
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, password: {
        "tokens": {"access_token": "provider-access", "id_token": "provider-id",
                   "refresh_token": "provider-refresh", "expires_in": 3600},
        "claims": {"sub": f"sub-{username}"},
    })
    expiry = calendar.timegm((datetime.utcnow() + timedelta(hours=1)).timetuple())

    def validate(token, expected_use, leeway_seconds=0):
        claims = {"sub": f"sub-{USERNAME}", "exp": expiry}
        if expected_use == "id":
            claims.update(email=f"{USERNAME}@example.com", email_verified=True)
        return claims

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)


@pytest.fixture
def model_provider(monkeypatch):
    calls = []

    def candidate(user, last_session, preferences, chat_fn, **kwargs):
        calls.append(last_session.id)
        return SimpleNamespace(document=_document(), overall_score=8.5)

    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", candidate)
    monkeypatch.setattr(
        "app.blueprints.mobile_training._heavy_chat", lambda **kwargs: "unused")
    return calls


def _signed_in(client):
    assert client.post("/api/v1/auth/register", json={
        "username": USERNAME, "email": f"{USERNAME}@example.com",
        "password": PASSWORD}).status_code == 201
    assert client.post("/api/v1/auth/verify", json={
        "username": USERNAME, "code": "123456"}).status_code == 200
    login = client.post("/api/v1/auth/login",
                        json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": "Bearer " + login.json["session"]["access_credential"]}


def _user():
    db.session.expire_all()
    return User.query.filter_by(username=USERNAME).one()


def _prerequisite_accepts(user):
    try:
        return generation_service._required_session(user).id
    except GenerationPrerequisiteMissing:
        return None


def _sessions(user_id):
    db.session.expire_all()
    return UserSession.query.filter_by(user_id=user_id).all()


def test_native_onboarding_clears_the_first_plan_prerequisite(
        raw_client, identity_provider, model_provider):
    headers = _signed_in(raw_client)
    user = _user()

    # Verified + signed in, not onboarded: both authorities say "no".
    assert raw_client.get("/api/v1/account/me", headers=headers).json["user"][
        "profile_complete"] is False
    assert _prerequisite_accepts(user) is None
    refused = raw_client.post("/api/v1/training/plans", json=CANONICAL,
                              headers={**headers, "Idempotency-Key": "first-plan-early"})
    assert refused.status_code == 422
    assert refused.json["error"]["code"] == "TRAINING_PLAN_PREREQUISITE_MISSING"
    assert model_provider == []

    # Onboard natively.
    saved = raw_client.put("/api/v1/account/profile", json=PROFILE, headers=headers)
    assert saved.status_code == 200
    assert saved.json["user"]["profile_complete"] is True
    assert saved.json["user"]["goal"] == "build_muscle"

    [session] = _sessions(user.id)
    user = _user()
    assert raw_client.get("/api/v1/account/me", headers=headers).json["user"][
        "profile_complete"] is True
    assert onboarding_state(user).complete is True
    assert _prerequisite_accepts(user) == session.id

    # The real first-plan command now accepts the account and reads the
    # canonical onboarding session.
    created = raw_client.post("/api/v1/training/plans", json=CANONICAL,
                              headers={**headers, "Idempotency-Key": "first-plan-key"})
    assert created.status_code == 201
    assert model_provider == [session.id]
    assert TrainingPlan.query.filter_by(user_id=user.id).count() == 1

    # Repeat onboarding: still one canonical session, still eligible.
    again = raw_client.put("/api/v1/account/profile",
                           json={**PROFILE, "weight_kg": 78}, headers=headers)
    assert again.status_code == 200
    assert [row.id for row in _sessions(user.id)] == [session.id]
    assert _prerequisite_accepts(_user()) == session.id
    assert raw_client.get("/api/v1/account/me", headers=headers).json["user"][
        "profile_complete"] is True


def test_failed_onboarding_leaves_both_authorities_incomplete(
        raw_client, identity_provider, model_provider):
    headers = _signed_in(raw_client)

    def refuse(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.new | session.dirty):
            raise RuntimeError("injected between profile mutation and session")

    event.listen(db.session, "before_flush", refuse)
    try:
        failed = raw_client.put("/api/v1/account/profile", json=PROFILE,
                                headers=headers)
    finally:
        event.remove(db.session, "before_flush", refuse)

    assert failed.status_code == 503
    assert failed.json["error"]["code"] == "PROFILE_TEMPORARILY_UNAVAILABLE"
    user = _user()
    assert user.profile_complete is not True
    assert user.weight is None and user.goal is None
    assert _sessions(user.id) == []
    assert raw_client.get("/api/v1/account/me", headers=headers).json["user"][
        "profile_complete"] is False
    assert _prerequisite_accepts(user) is None

    refused = raw_client.post("/api/v1/training/plans", json=CANONICAL,
                              headers={**headers, "Idempotency-Key": "after-failure"})
    assert refused.status_code == 422
    assert refused.json["error"]["code"] == "TRAINING_PLAN_PREREQUISITE_MISSING"
    assert model_provider == []
    assert TrainingPlanGenerationOperation.query.count() == 0


@pytest.mark.parametrize(("flag", "with_session"), [
    (True, False), (False, True), (False, False), (True, True)])
def test_account_surface_and_prerequisite_never_disagree(
        raw_client, identity_provider, flag, with_session):
    """Every persisted combination, including legacy divergent rows."""
    headers = _signed_in(raw_client)
    user = _user()
    user.profile_complete = flag
    if with_session:
        db.session.add(UserSession(user_id=user.id, goal="kilo verme",
                                   fitness_level="beginner", current_activity="active"))
    db.session.commit()

    reported = raw_client.get("/api/v1/account/me", headers=headers).json["user"][
        "profile_complete"]
    eligible = _prerequisite_accepts(_user()) is not None
    assert reported is eligible is (flag and with_session)
