"""Coach capability request + disclosure availability on disposable PostgreSQL.

#533 companion D-1/D-2. Real F01/D1/screened-cohort grant path through
``/api/v1/ai-consent`` and the real owner-locked consent read; capability
contracts are SYNTHETIC and registered by monkeypatch (nothing is approved,
registered or published in source). No provider call is made.
"""
import os
from hashlib import sha256
from types import MappingProxyType

import pytest

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("explicit disposable PostgreSQL required", allow_module_level=True)

from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.services import ai, ai_consent, ai_consent_disclosures, coach_capabilities as caps  # noqa: E402
from tests.test_d1_four_purpose_registration_pg import _act, _adult  # noqa: E402
from tests.test_f01_training_source_candidate_pg import candidate, call  # noqa: E402, F401

pytestmark = pytest.mark.pg_concurrency

R2 = "d1-closure02-coach-candidate-r2"
VERSIONS = {caps.TRAINING_PLAN_WRITE: "test-pg-request-training-edit",
            caps.NUTRITION_PLAN_ACCESS: "test-pg-request-nutrition-plan"}


def _register(monkeypatch, *capabilities, approve=True):
    policies, published = dict(ai_consent.POLICIES), dict(ai_consent_disclosures.PUBLISHED_TEXTS)
    launched, approved = dict(ai_consent_disclosures.LAUNCH_VERSIONS), {}
    for capability in capabilities:
        spec, version = caps.SPECS[capability], VERSIONS[capability]
        texts = {locale: f"synthetic {capability} {locale}\n" for locale in ("en", "tr")}
        policies[version] = ai_consent.Disclosure(
            version=version, purpose=spec.purpose, categories=spec.categories,
            recipients=ai_consent.POLICIES[R2].recipients,
            texts=tuple((k, sha256(v.encode()).hexdigest()) for k, v in sorted(texts.items())))
        published.update({(version, k): v for k, v in texts.items()})
        launched[spec.purpose] = version
        approved[capability] = frozenset({version})
    monkeypatch.setattr(ai_consent, "POLICIES", MappingProxyType(policies))
    monkeypatch.setattr(ai_consent_disclosures, "PUBLISHED_TEXTS", MappingProxyType(published))
    monkeypatch.setattr(ai_consent_disclosures, "LAUNCH_VERSIONS", MappingProxyType(launched))
    if approve:
        monkeypatch.setattr(caps, "APPROVED_VERSIONS", MappingProxyType(approved))


def _owner(app):
    with app.app_context():
        return db.session.query(User).one().id


def _operation(app, owner):
    with app.app_context():
        return ai_consent.begin_operation(owner, "coach", *ai.heavy_primary_route())


def _open(app, operation, owner, capability):
    with app.app_context():
        with ai_consent.operation_scope(operation):
            return ai_consent.capability_request_open(owner, capability)


def _purposes(client, headers):
    status = call(client, "get", "/api/v1/ai-consent", headers=headers)
    return {d["purpose"] for d in status["available_disclosures"]}, status


def test_d2_unapproved_capability_is_not_published_and_cannot_be_forged(
        candidate, monkeypatch):  # noqa: F811
    app, client, sdk = candidate
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE, approve=False)
    headers = _adult(client)
    offered, status = _purposes(client, headers)
    assert offered == {"training.plan", "coach.conversation", "nutrition.plan",
                       "pump_check.analysis", "qr_menu.analysis"}  # core five unchanged
    policy = ai_consent.POLICIES[VERSIONS[caps.TRAINING_PLAN_WRITE]]
    response = client.post("/api/v1/ai-consent", headers=headers, json=dict(
        action="affirmative_grant", purpose=policy.purpose, version=policy.version,
        locale="en", text_digest=dict(policy.texts)["en"],
        contract_digest=ai_consent.contract_digest(policy),
        expected_generation=status["generation"], request_id="req-forged-grant"))
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "AI_CONSENT_DISCLOSURE_UNAVAILABLE"
    _act(client, headers, "coach.conversation", "affirmative_grant", "req-d2-coach")
    owner = _owner(app)
    assert _open(app, _operation(app, owner), owner, caps.TRAINING_PLAN_WRITE) is False
    assert sdk == []


def test_training_request_lifecycle_on_the_real_owner_lock(candidate, monkeypatch):  # noqa: F811
    app, client, sdk = candidate
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE)
    headers = _adult(client)
    assert "coach.training_plan_edit" in _purposes(client, headers)[0]
    _act(client, headers, "coach.conversation", "affirmative_grant", "req-coach")
    owner = _owner(app)

    # r2 only: the missing capability is requestable; another owner never is.
    operation = _operation(app, owner)
    assert _open(app, operation, owner, caps.TRAINING_PLAN_WRITE) is True
    assert _open(app, operation, owner + 1, caps.TRAINING_PLAN_WRITE) is False

    # Granted elsewhere after the operation was captured: the old operation's
    # request is retired (generation moved) and a new turn asks nothing.
    _act(client, headers, "coach.training_plan_edit", "affirmative_grant", "req-grant")
    assert _open(app, operation, owner, caps.TRAINING_PLAN_WRITE) is False
    assert _open(app, _operation(app, owner), owner, caps.TRAINING_PLAN_WRITE) is False

    # Withdrawn → requestable again; regranted → never asked again.
    _act(client, headers, "coach.training_plan_edit", "revoke", "req-revoke")
    assert _open(app, _operation(app, owner), owner, caps.TRAINING_PLAN_WRITE) is True
    _act(client, headers, "coach.training_plan_edit", "affirmative_grant", "req-regrant")
    assert _open(app, _operation(app, owner), owner, caps.TRAINING_PLAN_WRITE) is False
    assert sdk == []


def test_nutrition_request_follows_both_separate_grants(candidate, monkeypatch):  # noqa: F811
    app, client, sdk = candidate
    _register(monkeypatch, caps.NUTRITION_PLAN_ACCESS)
    headers = _adult(client)
    _act(client, headers, "coach.conversation", "affirmative_grant", "req-n-coach")
    owner = _owner(app)
    assert _open(app, _operation(app, owner), owner, caps.NUTRITION_PLAN_ACCESS) is True
    _act(client, headers, "nutrition.plan", "affirmative_grant", "req-n-plan")
    assert _open(app, _operation(app, owner), owner, caps.NUTRITION_PLAN_ACCESS) is True
    _act(client, headers, "coach.nutrition_plan_access", "affirmative_grant", "req-n-cap")
    assert _open(app, _operation(app, owner), owner, caps.NUTRITION_PLAN_ACCESS) is False
    # The Training capability is independent: still missing, still requestable
    # only if its own contract is open (it is not registered here).
    assert _open(app, _operation(app, owner), owner, caps.TRAINING_PLAN_WRITE) is False
    _act(client, headers, "nutrition.plan", "revoke", "req-n-plan-revoke")
    assert _open(app, _operation(app, owner), owner, caps.NUTRITION_PLAN_ACCESS) is True
    assert sdk == []


def test_unscreened_account_is_never_offered_a_capability(candidate, monkeypatch):  # noqa: F811
    """The same cohort gate ``mutate`` applies: a request is only raised where
    the client's Allow can succeed."""
    from app.services import account_eligibility
    app, client, sdk = candidate
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE)
    headers = _adult(client)
    _act(client, headers, "coach.conversation", "affirmative_grant", "req-u-coach")
    owner = _owner(app)
    operation = _operation(app, owner)
    assert _open(app, operation, owner, caps.TRAINING_PLAN_WRITE) is True
    monkeypatch.setattr(account_eligibility, "d1_grant_cohort", lambda *a, **k: None)
    assert _open(app, operation, owner, caps.TRAINING_PLAN_WRITE) is False
    assert sdk == []
