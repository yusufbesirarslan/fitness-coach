"""TR 13-17 full V1 journey (source pin on): register -> own F01 grant ->
teen profile (no weight-target maths) -> general first plan.

Real HTTP surface, real eligibility record, real F01 mutation and real profile
and plan persistence. Adult-only lanes stay refused; minors never appear on a
public ranking. Acceptance matrix: TR 12 / 13 / 15 / 17 / 18.
"""
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import TrainingPlan, User, UserSession
from app.services import (
    account_eligibility as policy, health_disclosure, mobile_auth,
)

NOTICE = policy.TR_MINOR_NOTICE_VERSION
TEEN_PROFILE = {"weight_kg": 52.0, "height_cm": 160.0, "gender": "female",
                "goal": "build_muscle", "fitness_level": "beginner",
                "activity_level": "active"}
GENERAL = {"template": "general_full_body_v1", "gun_sayisi": 3, "ekipman": "ev"}


@pytest.fixture(autouse=True)
def pinned(app, monkeypatch):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", NOTICE)


@pytest.fixture
def journey(app, monkeypatch):
    from app.blueprints import mobile_registration
    from app.services import cognito_service
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    client = app.test_client()

    def register(age, name):
        monkeypatch.setattr(cognito_service, "sign_up", lambda **_: f"sub-{name}")
        token = client.post("/api/v1/auth/eligibility", json={
            "age": age, "country": "TR"}).json["eligibility"]["registration_token"]
        body = {"username": name, "email": f"{name}@example.invalid",
                "password": "Sifre123", "eligibility_token": token}
        if age < 18:
            body["minor_notice_version"] = NOTICE
        assert client.post("/api/v1/auth/register", json=body).status_code == 201
        user = User.query.filter_by(username=name).one()
        owner = user.id

        def authenticate(_):
            row = db.session.get(User, owner)
            return mobile_auth.MobilePrincipal(row, SimpleNamespace(id=1),
                                               {"sub": row.cognito_sub})
        monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)
        return user, {"Authorization": "Bearer fixture-access"}

    def grant(headers, generation=0):
        return client.post("/api/v1/health-consent", headers=headers, json={
            "action": "affirmative_grant", "purpose": health_disclosure.PURPOSE,
            "version": health_disclosure.VERSION, "locale": "tr",
            "text_digest": health_disclosure.DIGESTS["tr"],
            "expected_generation": generation, "request_id": "teen-own-f01-grant"})

    return SimpleNamespace(client=client, register=register, grant=grant)


def test_tr_12_is_refused_before_any_ticket(journey):
    result = journey.client.post("/api/v1/auth/eligibility", json={
        "age": 12, "country": "TR"}).json["eligibility"]
    assert (result["status"], result["reason"], result["registration_token"]) == (
        "ineligible", "UNDER_MINIMUM_AGE", None)


@pytest.mark.parametrize("age", [13, 15, 17])
def test_tr_minor_reaches_a_first_personal_plan(journey, monkeypatch, age):
    from app.services import account_profile
    monkeypatch.setattr(account_profile, "calculate_bmr", lambda *_: pytest.fail("adult math"))
    monkeypatch.setattr(account_profile, "calculate_target", lambda *_: pytest.fail("adult math"))
    user, headers = journey.register(age, f"trteen{age}")
    client = journey.client

    # Nothing health-related before the minor's own F01 decision.
    profile = dict(TEEN_PROFILE, age=age)
    assert client.put("/api/v1/account/profile", headers=headers,
                      json=profile).status_code == 403
    assert journey.grant(headers).status_code == 200

    response = client.put("/api/v1/account/profile", headers=headers, json=profile)
    assert response.status_code == 200, response.json
    assert response.json["user"]["profile_complete"] is True
    session = UserSession.query.filter_by(user_id=user.id).one()
    assert (session.bmr, session.tdee, session.target_calories,
            session.nutrition_plan) == (None, None, None, None)
    assert session.training_plan  # goal/level text only, no calorie number

    plan = client.post("/api/v1/training/plans/general", json=GENERAL,
                       headers={**headers, "Idempotency-Key": f"teen-plan-{age}-0001"})
    assert plan.status_code == 201, plan.json
    assert TrainingPlan.query.filter_by(user_id=user.id).count() == 1
    today = client.get("/api/v1/today", headers=headers)
    assert today.status_code == 200 and today.json["today"]["status"] != "no_plan"


@pytest.mark.parametrize("change", [
    {"goal": "lose_weight"}, {"target_weight_kg": 45.0}, {"age": 18}, {"age": 12},
])
def test_teen_profile_refuses_adult_only_values(journey, change):
    user, headers = journey.register(15, "trteenrefuse")
    assert journey.grant(headers).status_code == 200
    body = {**TEEN_PROFILE, "age": 15, **change}
    response = journey.client.put("/api/v1/account/profile", headers=headers, json=body)
    assert (response.status_code, response.json["error"]["code"]) == (
        422, "PROFILE_INVALID_VALUE")
    assert User.query.get(user.id).profile_complete is False
    assert UserSession.query.count() == 0


def test_teen_adult_only_lanes_stay_closed(journey):
    user, headers = journey.register(16, "trteenclosed")
    assert journey.grant(headers).status_code == 200
    client = journey.client
    assert client.put("/api/v1/account/profile", headers=headers,
                      json=dict(TEEN_PROFILE, age=16)).status_code == 200
    checkin = client.post("/api/v1/progress/check-ins", headers={
        **headers, "Idempotency-Key": "teen-checkin-0001"}, json={})
    assert checkin.json["error"]["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"
    pump = client.post("/api/v1/pump-checks", headers=headers, json={})
    assert pump.json["error"]["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"
    ai = client.post("/api/v1/training/plans", headers={
        **headers, "Idempotency-Key": "teen-ai-plan-0001"}, json={})
    assert ai.status_code in {400, 403, 503}
    assert TrainingPlan.query.filter_by(user_id=user.id).count() == 0
    from datetime import datetime, timezone
    assert policy.purchase_eligible(User.query.get(user.id), datetime.now(timezone.utc)) is False


def test_tr_18_adult_flow_is_unchanged(journey):
    user, headers = journey.register(18, "tradult18")
    assert journey.grant(headers).status_code == 200
    body = dict(TEEN_PROFILE, age=18, goal="lose_weight", target_weight_kg=48.0)
    response = journey.client.put("/api/v1/account/profile", headers=headers, json=body)
    assert response.status_code == 200, response.json
    session = UserSession.query.filter_by(user_id=user.id).one()
    assert session.bmr and session.tdee and session.target_calories
    assert User.query.get(user.id).target_weight == 48.0


def test_unpinned_teen_profile_falls_back_to_closed(journey, monkeypatch):
    user, headers = journey.register(15, "trteenunpinned")
    assert journey.grant(headers).status_code == 200
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", None)
    response = journey.client.put("/api/v1/account/profile", headers=headers,
                                  json=dict(TEEN_PROFILE, age=15))
    assert response.json["error"]["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"


# ---- public ranking guard -------------------------------------------------

def test_minors_never_appear_on_public_rankings(app, make_user, monkeypatch):
    from app.models import Challenge, UserChallengeProgress
    from app.services import challenges, gamification
    adult = make_user("rankadult", age=30, rank_points=10, weekly_xp=10)
    teen = make_user("rankteen", age=15, rank_points=999, weekly_xp=999)
    legacy = make_user("ranklegacy", rank_points=5, weekly_xp=5)
    db.session.commit()

    with app.test_request_context():
        from flask_login import login_user
        login_user(adult)
        for timeframe in ("all_time", "weekly"):
            board = gamification._leaderboard_via_postgres("global", timeframe).get_json()
            names = [e["username"] for e in board["entries"]]
            assert "rankteen" not in names and {"rankadult", "ranklegacy"} <= set(names)
        login_user(teen)
        own = gamification._leaderboard_via_postgres("global", "all_time").get_json()
        assert all(e["username"] != "rankteen" for e in own["entries"])

    challenge = Challenge(code="teen_rank_guard", title="X", metric="workout_logged",
                          target_value=3, xp_reward=100, challenge_type="global",
                          period_type="weekly")
    db.session.add(challenge)
    db.session.flush()
    for user in (adult, teen):
        db.session.add(UserChallengeProgress(user_id=user.id, challenge_id=challenge.id,
                                             period_key="2026-W46", progress=2))
    db.session.commit()
    board = challenges.challenge_board(challenge.id, "2026-W46", "global", adult.id)
    assert [e["user_id"] for e in board["entries"]] == [adult.id]

    awarded = []
    monkeypatch.setattr(gamification, "award_xp", lambda uid, *a, **k: awarded.append(uid))
    gamification.run_weekly_rollover(force_week="2026-W45")
    assert teen.id not in awarded and adult.id in awarded


def test_ranking_publication_skips_and_evicts_minors(app, make_user, monkeypatch):
    from app.services import gamification
    teen = make_user("pubteen", age=16, rank_points=50)
    adult = make_user("pubadult", age=40, rank_points=40)
    db.session.commit()

    class FakeRedis:
        def __init__(self):
            self.sets, self.removed = {}, []
        def zadd(self, key, mapping):
            self.sets.setdefault(key, {}).update(mapping)
        def zrem(self, key, member):
            self.removed.append(member)
            self.sets.get(key, {}).pop(member, None)
    fake = FakeRedis()
    monkeypatch.setattr(gamification, "redis_client", fake)
    gamification.lb_sync_user(teen)
    gamification.lb_sync_user(adult)
    published = {member for members in fake.sets.values() for member in members}
    assert published == {str(adult.id)}
    assert str(teen.id) in fake.removed


# ---- rollout flag is not a channel (review P2-2) ---------------------------

@pytest.mark.parametrize("pin", [True, False])
def test_flag_off_keeps_a_registered_minor_out_of_adult_lanes(journey, app, monkeypatch, pin):
    from app.services import account_profile
    user, headers = journey.register(15, f"trteenflag{int(pin)}")
    assert journey.grant(headers).status_code == 200
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = False
    if not pin:
        monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", None)
    monkeypatch.setattr(account_profile, "calculate_bmr", lambda *_: pytest.fail("adult math"))
    adult_values = dict(TEEN_PROFILE, age=30, goal="lose_weight", target_weight_kg=45.0)
    response = journey.client.put("/api/v1/account/profile", headers=headers, json=adult_values)
    assert response.status_code in {403, 422}
    assert User.query.get(user.id).age == 15
    pump = journey.client.post("/api/v1/pump-checks", headers=headers, json={})
    assert pump.json["error"]["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"
    if pin:
        teen = journey.client.put("/api/v1/account/profile", headers=headers,
                                  json=dict(TEEN_PROFILE, age=15))
        assert teen.status_code == 200
        assert UserSession.query.filter_by(user_id=user.id).one().target_calories is None


def test_minor_viewer_sees_own_xp_without_a_rank(app, make_user):
    from flask_login import login_user
    from app.services import gamification
    make_user("rankadult2", age=30, rank_points=10)
    teen = make_user("rankteen2", age=14, rank_points=5)
    db.session.commit()
    with app.test_request_context():
        login_user(teen)
        board = gamification._leaderboard_via_postgres("global", "all_time").get_json()
    assert board["in_list"] is False and board["me"]["rank"] is None
