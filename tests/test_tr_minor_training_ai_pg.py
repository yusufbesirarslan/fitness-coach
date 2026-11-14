"""TR 13-17 native AI Training first plan through the REAL F01, D1 fence,
signed cohort, allowance ledger and publication authorities on PostgreSQL.

Synthetic only where a source test must be: Cognito/JWT identity and the
Bedrock SDK answer. A pass is source evidence, NOT a real-provider
qualification. Shipping pins stay None; every opening here is a monkeypatch.
"""
import json
import os
from types import SimpleNamespace

import fakeredis
import pytest

from app.extensions import db
from app.models import (AIConsentEvent, AiUsageReservation, TrainingPlan,
                        TrainingPlanGenerationOperation, User)
from app.services import account_eligibility as policy, ai_launch
from app.services.training_generation.preference_contract import CODE_UNSUPPORTED
from tests.test_mobile_training_generation_api import CANONICAL, _provider_document

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("explicit disposable PostgreSQL required", allow_module_level=True)

NOTICE = policy.TR_MINOR_NOTICE_VERSION
SONNET = "eu.anthropic.claude-sonnet-5-5"
HAIKU = "eu.anthropic.claude-haiku-5-5"


@pytest.fixture
def pins(monkeypatch):
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", NOTICE)
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_TRAINING_AI_VERSION",
                        policy.TR_MINOR_TRAINING_AI_VERSION)
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", policy.VERSION)


@pytest.fixture
def app(monkeypatch):
    from app import create_app
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    assert url.startswith(("postgresql://", "postgresql+psycopg2://"))
    monkeypatch.setenv("DATABASE_URL", url)
    application = create_app()
    application.config.update(TESTING=True, ACCOUNT_ELIGIBILITY_ENABLED=True)
    with application.app_context():
        db.create_all()
    try:
        with application.app_context():
            yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


@pytest.fixture
def teen_ai(app, monkeypatch, pins):
    from app import config, extensions
    from app.blueprints import mobile_registration
    from app.services import (ai, ai_consent as consent, ai_spend_guard, cognito_service,
                              health_disclosure, mobile_auth)
    from app.services.ai_allowances import launch_policy
    from tests.consent_projection_support import publish_synthetic_texts  # noqa: I001

    from app.services import ai_consent_disclosures as disclosures
    purpose, categories = consent.BINDINGS["training_plan"]
    installed = disclosures.LAUNCH_VERSIONS.get(purpose)
    if installed is not None:
        # Composed with the F01 owner's reviewed Training source (R1/R2 text):
        # reuse it unchanged, never a parallel teen contract.
        training = consent.POLICIES[installed]
        assert ("bedrock", SONNET) in training.recipients
    else:
        training = consent.Disclosure("teen-ai-fixture", purpose, categories,
                                      (("bedrock", SONNET), ("bedrock", HAIKU)), ())
        policies = publish_synthetic_texts(monkeypatch, {"teen-ai-fixture": training})
        monkeypatch.setattr(consent, "POLICIES", policies)
        monkeypatch.setattr(disclosures, "LAUNCH_VERSIONS", {purpose: "teen-ai-fixture"})
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", health_disclosure.VERSION)
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", launch_policy.LAUNCH_V1_POLICY)
    for name, value in (("AI_MODEL_ROUTING", "claude55"), ("BEDROCK_ENABLED", True),
                        ("BEDROCK_SONNET55_MODEL", SONNET), ("BEDROCK_HAIKU55_MODEL", HAIKU)):
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(extensions, "redis_client", fakeredis.FakeRedis())
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    app.config.update(AI_ALLOWANCE_LEDGER_ENABLED=True,
                      AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
                      AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
                      APPLE_SUBSCRIPTIONS_ENABLED=False,
                      AI_ALLOWANCE_POLICY=launch_policy.LAUNCH_V1_POLICY)
    sdk = []

    def create(**kwargs):
        sdk.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", usage=None, content=[SimpleNamespace(
            type="text", text=json.dumps(_provider_document("ex_barbell_back_squat")))])
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    client = app.test_client()

    def register(age, name):
        monkeypatch.setattr(cognito_service, "sign_up", lambda **_: f"sub-{name}")
        body = {"username": name, "email": f"{name}@example.invalid", "password": "Sifre123",
                "eligibility_token": client.post("/api/v1/auth/eligibility", json={
                    "age": age, "country": "TR"}).json["eligibility"]["registration_token"]}
        if age < 18:
            body["minor_notice_version"] = NOTICE
        assert client.post("/api/v1/auth/register", json=body).status_code == 201
        owner = User.query.filter_by(username=name).one().id

        def authenticate(_):
            row = db.session.get(User, owner)
            return mobile_auth.MobilePrincipal(row, SimpleNamespace(id=1), {"sub": row.cognito_sub})
        monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)
        return owner, {"Authorization": "Bearer fixture-access"}

    def call(method, path, status, headers, **kwargs):
        response = getattr(client, method)(path, headers=headers, **kwargs)
        try:
            assert response.status_code == status, response.get_json()
            return response.get_json()
        finally:
            response.close()

    return SimpleNamespace(register=register, call=call, sdk=sdk, policy=training)


def _onboard(teen_ai, age, name, locale="tr"):
    from app.services import health_disclosure
    owner, headers = teen_ai.register(age, name)
    status = teen_ai.call("get", "/api/v1/health-consent", 200, headers)
    teen_ai.call("post", "/api/v1/health-consent", 200, headers, json=dict(
        purpose=health_disclosure.PURPOSE, version=health_disclosure.VERSION, locale=locale,
        text_digest=health_disclosure.DIGESTS[locale], action="affirmative_grant",
        expected_generation=status["generation"], request_id=f"{name}-f01"))
    teen_ai.call("put", "/api/v1/account/profile", 200, headers, json={
        "weight_kg": 52.0, "height_cm": 160.0, "age": age, "gender": "female",
        "goal": "build_muscle", "fitness_level": "beginner", "activity_level": "active"})
    return owner, headers


def _grant_training(teen_ai, headers, name, locale="tr"):
    status = teen_ai.call("get", "/api/v1/ai-consent", 200, headers)
    # The catalog publishes every registered D1 purpose; select Training's.
    disclosure, = [d for d in status["available_disclosures"]
                   if d["purpose"] == "training.plan"]
    grant = dict(action="affirmative_grant", purpose="training.plan",
                 version=disclosure["version"], locale=locale,
                 text_digest=disclosure["texts"][locale],
                 contract_digest=disclosure["contract_digest"],
                 expected_generation=status["generation"], request_id=f"{name}-d1")
    teen_ai.call("post", "/api/v1/ai-consent", 200, headers, json=grant)
    return grant


@pytest.mark.parametrize("age", [13, 15, 17])
def test_tr_minor_generates_a_first_ai_training_plan(teen_ai, age):
    name = f"teenplan{age}"
    owner, headers = _onboard(teen_ai, age, name)
    blocked = teen_ai.call("post", "/api/v1/training/plans", 403,
                           {**headers, "Idempotency-Key": f"{name}-before-grant"}, json=CANONICAL)
    assert blocked["error"]["code"] == "AI_CONSENT_REQUIRED" and teen_ai.sdk == []
    _grant_training(teen_ai, headers, name)

    key = {**headers, "Idempotency-Key": f"{name}-first-plan"}
    created = teen_ai.call("post", "/api/v1/training/plans", 201, key, json=CANONICAL)
    assert created["plan"] is not None
    replay = teen_ai.call("post", "/api/v1/training/plans", 201, key, json=CANONICAL)
    assert replay["plan"] == created["plan"]
    assert len(teen_ai.sdk) == 1 and teen_ai.sdk[0]["model"] == SONNET
    sent = json.dumps(teen_ai.sdk[0], default=str, ensure_ascii=False)
    assert "GENÇ KULLANICI GÜVENLİK SINIRI" in sent and f"Age: {age}" in sent
    assert TrainingPlan.query.filter_by(user_id=owner).count() == 1
    assert TrainingPlanGenerationOperation.query.filter_by(user_id=owner).one().status == "SUCCEEDED"
    assert AiUsageReservation.query.filter_by(user_id=owner).one().state == "COMMITTED"
    assert AIConsentEvent.query.filter_by(owner_id=owner).count() == 1


def test_teen_refused_preference_spends_nothing(teen_ai):
    owner, headers = _onboard(teen_ai, 15, "teenpref")
    _grant_training(teen_ai, headers, "teenpref")
    refused = teen_ai.call("post", "/api/v1/training/plans", 422,
                           {**headers, "Idempotency-Key": "teenpref-fat-loss"},
                           json={**CANONICAL, "odak_hedef": "yag_yakimi"})
    assert refused["error"]["code"] == CODE_UNSUPPORTED
    assert teen_ai.sdk == []
    assert AiUsageReservation.query.filter_by(user_id=owner).count() == 0
    assert TrainingPlanGenerationOperation.query.filter_by(user_id=owner).count() == 0


def test_teen_revoke_and_closed_pin_stop_new_generation(teen_ai, monkeypatch):
    owner, headers = _onboard(teen_ai, 16, "teenstop")
    grant = _grant_training(teen_ai, headers, "teenstop")
    with monkeypatch.context() as closed:
        closed.setattr(policy, "APPROVED_TR_MINOR_TRAINING_AI_VERSION", None)
        denied = teen_ai.call("post", "/api/v1/training/plans", 403,
                              {**headers, "Idempotency-Key": "teenstop-pin-closed"}, json=CANONICAL)
        assert denied["error"]["code"] == "AI_SERVICE_ELIGIBILITY_REQUIRED"
    assert teen_ai.sdk == []
    teen_ai.call("post", "/api/v1/ai-consent", 200, headers, json={
        **grant, "action": "revoke", "expected_generation": 1, "request_id": "teenstop-revoke"})
    revoked = teen_ai.call("post", "/api/v1/training/plans", 403,
                           {**headers, "Idempotency-Key": "teenstop-after-revoke"}, json=CANONICAL)
    assert revoked["error"]["code"] == "AI_CONSENT_REQUIRED"
    assert teen_ai.sdk == [] and TrainingPlan.query.filter_by(user_id=owner).count() == 0


@pytest.mark.parametrize("path,body", [
    ("/api/v1/coach/messages", {"message": "hi"}),
    ("/api/v1/nutrition/plan/generate", {}),
    ("/api/v1/nutrition/menu/analyze", {"url": "https://example.invalid/menu"}),
])
def test_teen_other_ai_purposes_stay_closed(teen_ai, path, body):
    owner, headers = _onboard(teen_ai, 15, "teenother")
    _grant_training(teen_ai, headers, "teenother")
    response = teen_ai.call("post", path, 403, headers, json=body)
    # The cohort refuses before any disclosure/grant lookup.
    assert response["error"]["code"] == "AI_SERVICE_ELIGIBILITY_REQUIRED"
    assert teen_ai.sdk == []


@pytest.mark.parametrize("purpose,path,code", [
    ("coach.conversation", "/api/v1/coach/messages", "AI_SERVICE_ELIGIBILITY_REQUIRED"),
    ("nutrition.plan", "/api/v1/nutrition/plan/generate", "AI_SERVICE_ELIGIBILITY_REQUIRED"),
    # Adult-only body_photos capability refuses a minor before AI admission.
    ("pump_check.analysis", "/api/v1/pump-checks", "ELIGIBILITY_FEATURE_UNAVAILABLE"),
    ("qr_menu.analysis", "/api/v1/nutrition/menu/analyze", "AI_SERVICE_ELIGIBILITY_REQUIRED"),
])
def test_teen_grant_of_another_published_purpose_never_opens_it(teen_ai, monkeypatch, purpose, path, code):
    # F-QR-W1: the consent WRITE refuses a minor's non-Training grant before
    # any event/generation change. A grant recorded by the pre-fix write path
    # (historical, e.g. staging window 1) still never opens admission: the
    # cohort decides that, independently of the write gate.
    from app.services import ai_consent as consent
    owner, headers = _onboard(teen_ai, 15, "teengrant")
    status = teen_ai.call("get", "/api/v1/ai-consent", 200, headers)
    disclosure, = [d for d in status["available_disclosures"] if d["purpose"] == purpose]
    grant = dict(
        action="affirmative_grant", purpose=purpose, version=disclosure["version"],
        locale="tr", text_digest=disclosure["texts"]["tr"],
        contract_digest=disclosure["contract_digest"],
        expected_generation=status["generation"], request_id="teengrant-other")
    events = AIConsentEvent.query.filter_by(owner_id=owner).count()
    refused = teen_ai.call("post", "/api/v1/ai-consent", 403, headers, json=grant)
    assert refused["error"]["code"] == "AI_CONSENT_PURPOSE_UNAVAILABLE_FOR_ACCOUNT"
    assert teen_ai.call("get", "/api/v1/ai-consent", 200, headers)["generation"] == status["generation"]
    db.session.remove()
    assert AIConsentEvent.query.filter_by(owner_id=owner).count() == events
    with monkeypatch.context() as legacy:
        legacy.setattr(consent, "_require_grant_cohort", lambda *a, **k: None)
        teen_ai.call("post", "/api/v1/ai-consent", 200, headers, json=grant)
    response = teen_ai.call("post", path, 403, headers, json={})
    assert response["error"]["code"] == code
    assert teen_ai.sdk == []


def test_stale_adult_session_age_never_lifts_the_youth_boundary(teen_ai):
    from app.models import UserSession
    owner, headers = _onboard(teen_ai, 15, "teenstale")
    _grant_training(teen_ai, headers, "teenstale")
    UserSession.query.filter_by(user_id=owner).update({"age": 30})
    db.session.commit()
    refused = teen_ai.call("post", "/api/v1/training/plans", 422,
                           {**headers, "Idempotency-Key": "teenstale-fat-loss"},
                           json={**CANONICAL, "odak_hedef": "yag_yakimi"})
    assert refused["error"]["code"] == CODE_UNSUPPORTED and teen_ai.sdk == []


def test_adult_tr_18_journey_has_no_youth_directive(teen_ai):
    owner, headers = teen_ai.register(18, "adult18ai")
    from app.services import health_disclosure
    status = teen_ai.call("get", "/api/v1/health-consent", 200, headers)
    teen_ai.call("post", "/api/v1/health-consent", 200, headers, json=dict(
        purpose=health_disclosure.PURPOSE, version=health_disclosure.VERSION, locale="en",
        text_digest=health_disclosure.DIGESTS["en"], action="affirmative_grant",
        expected_generation=status["generation"], request_id="adult18-f01"))
    teen_ai.call("put", "/api/v1/account/profile", 200, headers, json={
        "weight_kg": 70.0, "height_cm": 175.0, "age": 18, "gender": "male",
        "goal": "lose_weight", "target_weight_kg": 65.0, "fitness_level": "beginner",
        "activity_level": "active"})
    _grant_training(teen_ai, headers, "adult18", locale="en")
    teen_ai.call("post", "/api/v1/training/plans", 201,
                 {**headers, "Idempotency-Key": "adult18-first-plan"},
                 json={**CANONICAL, "odak_hedef": "yag_yakimi"})
    sent = json.dumps(teen_ai.sdk[0], default=str, ensure_ascii=False)
    assert "YOUTH SAFETY" not in sent and "GENÇ KULLANICI" not in sent
