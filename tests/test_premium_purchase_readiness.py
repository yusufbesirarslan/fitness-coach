"""Fail-closed Premium purchase readiness (founder decision, PRELAUNCH-02).

A Production deployment issues no new purchase context while any promised
native AI benefit is closed. Paid customers keep verify/restore/entitlement.
Synthetic launch inputs below are test choices, never approvals.
"""
from types import SimpleNamespace as NS

import pytest
from flask import g

from app.extensions import db
from app.models import AppleAccountAssociation, User
from app.services import ai, ai_launch, ai_consent_disclosures as disclosures, mobile_auth, subscriptions as s
from app.services.ai_allowances import launch_policy
from subscription_support import adult_account, evidence
from test_d1_launch_integration import approved_cohort, contracts, source_policy  # noqa: F401

PATH = "/api/v1/subscription/"
HEADERS = {"Authorization": "Bearer fixture"}


@pytest.fixture
def production(app, make_user, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
                      APPLE_SANDBOX_FALLBACK_ENABLED=True, ACCOUNT_ELIGIBILITY_ENABLED=False)
    user = adult_account(make_user("readiness-adult"))
    db.session.commit()
    uid = user.id
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: NS(
        user=db.session.get(User, uid), family=None, claims={"age": 99, "eligibility": "screen_passed"}))
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Production",
        environments=("Production", "Sandbox"), verify=lambda _, token: evidence(token, environment="Production")))
    return uid


@pytest.fixture
def launch_ready(app, contracts, approved_cohort, source_policy, monkeypatch):  # noqa: F811
    from app.services import ai_spend_guard
    app.config.update(AI_ALLOWANCE_POLICY=source_policy, AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
                      AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())
    # The synthetic contracts name ("openai", "synthetic"); the live route must match.
    monkeypatch.setattr(ai, "heavy_primary_route", lambda: ("openai", "synthetic"))


def test_shipping_source_refuses_new_purchase_without_association(client, production):
    assert launch_policy.APPROVED_POLICY is None and ai_launch.APPROVED_SERVICE_COHORT_VERSION is None
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    assert response.json["error"]["retryable"] is False
    assert db.session.get(AppleAccountAssociation, production) is None


def test_full_launch_closure_issues_context(client, production, launch_ready):
    assert ai_launch.premium_ready() is True
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.status_code == 200 and response.json["environment"] == "Production"


@pytest.mark.parametrize("gap", ["policy", "cohort", "flag", "enforcement", "spend_guard", "redis",
                                 "disclosure", "launch_version", "route", "malformed_policy", "error"])
def test_any_launch_gap_closes_new_purchases(app, client, production, launch_ready, monkeypatch, gap):
    from app.services import ai_spend_guard
    if gap == "policy":
        monkeypatch.setattr(launch_policy, "APPROVED_POLICY", None)
    elif gap == "cohort":
        monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", None)
    elif gap == "flag":
        app.config["AI_ALLOWANCE_LEDGER_ENABLED"] = False
    elif gap == "enforcement":
        app.config["AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED"] = False
    elif gap == "spend_guard":
        monkeypatch.setattr(ai_spend_guard, "ENABLED", False)
    elif gap == "redis":
        monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    elif gap == "route":
        # e.g. AI_MODEL_ROUTING switched, or Bedrock off -> OpenAI light route.
        monkeypatch.setattr(ai, "heavy_primary_route", lambda: ("bedrock", "unpublished-model"))
    elif gap == "malformed_policy":
        monkeypatch.setattr(launch_policy, "APPROVED_POLICY", object())
    elif gap == "error":
        def broken():
            raise RuntimeError("unexpected")
        monkeypatch.setattr(ai, "heavy_primary_route", broken)
    elif gap == "disclosure":
        monkeypatch.setattr(disclosures, "PUBLISHED_TEXTS", {})
    else:
        versions = dict(disclosures.LAUNCH_VERSIONS)
        versions.pop(next(iter(versions)))  # One of five benefits unpublished.
        monkeypatch.setattr(disclosures, "LAUNCH_VERSIONS", versions)
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    assert db.session.get(AppleAccountAssociation, production) is None


def test_readiness_is_decided_before_eligibility(client, production):
    user = db.session.get(User, production)
    user.user_metadata = {}
    db.session.commit()
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"


def test_probe_does_not_turn_the_response_into_an_allowance_refusal(app, client, production, launch_ready):
    # Execution ON with a missing companion flag makes require_ready() record a
    # typed allowance refusal on g; the after_request hook must not replace ours.
    app.config["AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"] = False
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    with app.test_request_context():
        g.allowance_refusal = sentinel = object()
        assert ai_launch.premium_ready() is False
        assert g.allowance_refusal is sentinel


def test_paid_customer_keeps_verify_restore_and_entitlement(client, production, monkeypatch):
    # Purchased while ready: the association already exists.
    token = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
    db.session.add(AppleAccountAssociation(user_id=production, token=token))
    db.session.commit()
    assert ai_launch.premium_ready() is False
    verified = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS)
    assert verified.status_code == 200 and verified.json["entitlement"]["effective_premium"] is True
    entitlement = client.get(PATH + "entitlement", headers=HEADERS)
    assert entitlement.json["entitlement"]["tier"] == "PREMIUM"
    assert client.get(PATH + "purchase-context", headers=HEADERS).json["error"]["code"] == \
        "SUBSCRIPTION_PURCHASE_UNAVAILABLE"


def test_sandbox_only_deployment_keeps_its_qualification_path(app, client, production, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTION_ENVIRONMENT="Sandbox", APPLE_SANDBOX_FALLBACK_ENABLED=False)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Sandbox", environments=("Sandbox",)))
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.status_code == 200 and response.json["environment"] == "Sandbox"


def test_ineligible_account_still_gets_its_own_answer_once_ready(client, production, launch_ready):
    user = db.session.get(User, production)
    user.user_metadata = {}
    db.session.commit()
    response = client.get(PATH + "purchase-context", headers=HEADERS)
    assert response.json["error"]["code"] == "SUBSCRIPTION_ELIGIBILITY_REQUIRED"

