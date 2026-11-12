"""RC2 named cohort: one account can purchase, nothing else changes.

ACCOUNT_ELIGIBILITY_ENABLED stays off in every test here, as it does on the
qualification deployment. Remove this file with the seam in the D1 launch commit.
"""
import logging
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.extensions import db
from app.models import AIConsentState, AppleAccountAssociation, User
from app.services import account_eligibility as policy
from app.services import account_registration, ai_consent, ai_consent_disclosures, ai_launch
from app.services import mobile_auth, subscriptions as s
from app.services.apple_subscriptions import SubscriptionError
from subscription_support import adult_account, evidence

NAMED_SUBJECT = "11111111-2222-3333-4444-555555555555"
OTHER_SUBJECT = "99999999-8888-7777-6666-555555555555"
ME = "/api/v1/account/me"
REVIEW = "/api/v1/account/eligibility"
PREVIEW = "/api/v1/auth/eligibility"
CONTEXT = "/api/v1/subscription/purchase-context"
NAMED = {"Authorization": "Bearer named"}
OTHER = {"Authorization": "Bearer other"}


@pytest.fixture
def window(app, make_user, monkeypatch):
    """The deployment RC2 runs on: the list set, the global flag off."""
    app.config.update(
        APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
        APPLE_SANDBOX_FALLBACK_ENABLED=True, ACCOUNT_ELIGIBILITY_ENABLED=False,
        APPLE_PURCHASE_QUALIFICATION_SUBJECTS=frozenset({NAMED_SUBJECT}))
    named, other = make_user("rc2-named"), make_user("rc2-other")
    named.cognito_sub, other.cognito_sub = NAMED_SUBJECT, OTHER_SUBJECT
    db.session.commit()
    ids = {"named": named.id, "other": other.id}
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda token: NS(
        user=db.session.get(User, ids[token]), family=None, claims={}))
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(
        environment="Production", environments=("Production", "Sandbox"),
        verify=lambda _, token: evidence(token, environment="Sandbox")))
    return NS(named=ids["named"], other=ids["other"])


def _ticket(client, age=30, country="TR"):
    response = client.post(PREVIEW, json={"age": age, "country": country})
    assert response.status_code == 200
    return response.json["eligibility"]["registration_token"]


def _declare(client, age=30, country="TR"):
    return client.post(REVIEW, headers=NAMED, json={"eligibility_token": _ticket(client, age, country)})


def test_named_account_declares_then_gets_a_production_context(app, client, window):
    assert policy.enabled() is False

    # This is what makes the shipped client ask for the declaration.
    assert client.get(ME, headers=NAMED).json["user"]["eligibility"]["status"] == "review_required"
    assert client.get(REVIEW, headers=NAMED).json["eligibility"]["status"] == "review_required"
    assert client.get(CONTEXT, headers=NAMED).json["error"]["code"] == "SUBSCRIPTION_ELIGIBILITY_REQUIRED"
    assert db.session.get(AppleAccountAssociation, window.named) is None

    response = _declare(client)
    assert response.status_code == 200
    assert response.json["eligibility"]["status"] == "screen_passed"
    assert response.json["eligibility"]["country"] == "TR"

    response = client.get(CONTEXT, headers=NAMED)
    assert response.status_code == 200 and response.json["environment"] == "Production"
    assert db.session.get(AppleAccountAssociation, window.named) is not None
    assert app.config["ACCOUNT_ELIGIBILITY_ENABLED"] is False


def test_named_minor_or_foreign_declaration_earns_no_purchase(client, window):
    assert _ticket(client, age=17) is None  # TR minor: no ticket exists to present.

    assert _declare(client, country="GB").status_code == 200
    assert client.get(CONTEXT, headers=NAMED).json["error"]["code"] == "SUBSCRIPTION_ELIGIBILITY_REQUIRED"
    assert db.session.get(AppleAccountAssociation, window.named) is None


def test_named_declaration_is_one_time_and_never_promotes(client, window):
    assert _declare(client, country="GB", age=30).status_code == 200
    response = _declare(client, age=30, country="TR")
    assert response.status_code == 409
    assert response.json["error"]["code"] == "ELIGIBILITY_REVIEW_REQUIRED"


def test_other_account_sees_exactly_the_closed_deployment(client, window):
    assert "eligibility" not in client.get(ME, headers=OTHER).json["user"]
    assert client.get(REVIEW, headers=OTHER).status_code == 503
    # Not even with a genuine ticket from the open preview.
    response = client.post(REVIEW, headers=OTHER, json={"eligibility_token": _ticket(client)})
    assert response.status_code == 503
    assert response.json["error"]["code"] == "AUTH_ELIGIBILITY_UNAVAILABLE"
    assert policy.projection(db.session.get(User, window.other))["status"] == "review_required"

    response = client.get(CONTEXT, headers=OTHER)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    assert db.session.get(AppleAccountAssociation, window.other) is None


def test_other_account_with_a_valid_adult_record_is_still_refused(client, window):
    adult_account(db.session.get(User, window.other))
    db.session.commit()
    response = client.get(CONTEXT, headers=OTHER)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    assert db.session.get(AppleAccountAssociation, window.other) is None


def test_other_account_cannot_take_over_the_named_association(client, window):
    assert _declare(client).status_code == 200
    token = client.get(CONTEXT, headers=NAMED).json["app_account_token"]
    with pytest.raises(SubscriptionError) as caught:
        s.persist_verified(window.other, evidence(token, environment="Sandbox"), token)
    assert caught.value.code == "SUBSCRIPTION_OWNERSHIP_CONFLICT"
    db.session.rollback()


def test_control_account_declares_but_is_refused_by_the_purchase_gate(app, client, window):
    """Row 2 of the RC2 acceptance: the gate refuses it, not the eligibility check."""
    app.config["APPLE_QUALIFICATION_CONTROL_SUBJECTS"] = frozenset({OTHER_SUBJECT})
    assert client.get(ME, headers=OTHER).json["user"]["eligibility"]["status"] == "review_required"
    response = client.post(REVIEW, headers=OTHER, json={"eligibility_token": _ticket(client)})
    assert response.status_code == 200
    assert client.get(REVIEW, headers=OTHER).json["eligibility"]["status"] == "screen_passed"

    response = client.get(CONTEXT, headers=OTHER)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    assert db.session.get(AppleAccountAssociation, window.other) is None
    policy.require_capability(db.session.get(User, window.other), "body_photos")


def test_subject_in_both_lists_is_a_control(app, client, window):
    """A pasted control must never widen who can purchase."""
    app.config.update(
        APPLE_PURCHASE_QUALIFICATION_SUBJECTS=frozenset({NAMED_SUBJECT, OTHER_SUBJECT}),
        APPLE_QUALIFICATION_CONTROL_SUBJECTS=frozenset({OTHER_SUBJECT}))
    assert client.post(REVIEW, headers=OTHER, json={"eligibility_token": _ticket(client)}).status_code == 200
    response = client.get(CONTEXT, headers=OTHER)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "SUBSCRIPTION_PURCHASE_UNAVAILABLE"
    assert db.session.get(AppleAccountAssociation, window.other) is None
    assert _declare(client).status_code == 200
    assert client.get(CONTEXT, headers=NAMED).status_code == 200


@pytest.mark.parametrize("age,country", [(13, "US"), (17, "GB"), (14, "CA"), (15, "AU")])
def test_window_opens_no_sign_up_for_a_declared_minor(app, client, window, age, country):
    """With the flag off nothing would record the declaration or restrict the account."""
    response = client.post(PREVIEW, json={"age": age, "country": country})
    assert response.status_code == 503
    assert response.json["error"]["code"] == "AUTH_ELIGIBILITY_UNAVAILABLE"

    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True
    assert _ticket(client, age, country)


@pytest.mark.parametrize("age,country,status", [
    (12, "US", "ineligible"), (30, "ZZ", "ineligible"), (17, "TR", "qualification_required")])
def test_window_keeps_the_ticketless_answers(client, window, age, country, status):
    response = client.post(PREVIEW, json={"age": age, "country": country})
    assert response.status_code == 200
    assert response.json["eligibility"]["status"] == status
    assert response.json["eligibility"]["registration_token"] is None


def test_control_list_alone_opens_nothing(app, client, window):
    app.config.update(APPLE_PURCHASE_QUALIFICATION_SUBJECTS=frozenset(),
                      APPLE_QUALIFICATION_CONTROL_SUBJECTS=frozenset({OTHER_SUBJECT}))
    assert client.get(REVIEW, headers=OTHER).status_code == 503
    assert client.post(PREVIEW, json={"age": 30, "country": "TR"}).status_code == 503


def test_list_does_not_restrict_any_account(client, window):
    """Unknown cohorts keep body photos and weight targets: the global gate is off."""
    for uid in (window.named, window.other):
        user = db.session.get(User, uid)
        policy.require_capability(user, "body_photos")
        policy.require_capability(user, "automated_weight_targets")
    for headers in (NAMED, OTHER):
        response = client.post("/api/v1/pump-checks", headers=headers, data=b"x",
                               content_type="multipart/form-data; boundary=x")
        assert (response.json or {}).get("error", {}).get("code") != "ELIGIBILITY_FEATURE_UNAVAILABLE"


def test_ticket_stays_inert_at_registration(client, window, monkeypatch):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: OTHER_SUBJECT.replace("9", "7"))
    account_registration.register_account(
        "rc2-new", "rc2-new@example.invalid", "Sifre123", eligibility_token=_ticket(client))
    user = User.query.filter_by(username="rc2-new").one()
    assert policy.METADATA_KEY not in (user.user_metadata or {})
    assert user.age is None
    assert policy.projection(user)["status"] == "review_required"


def test_web_preview_stays_closed(client, window):
    assert client.post("/eligibility", json={"age": 30, "country": "TR"}).status_code == 503


@pytest.mark.parametrize("change", [
    {"APPLE_PURCHASE_QUALIFICATION_SUBJECTS": frozenset()},
    {"APPLE_PURCHASE_QUALIFICATION_SUBJECTS": frozenset({OTHER_SUBJECT})},
    {"APPLE_SANDBOX_FALLBACK_ENABLED": False},
    {"APPLE_SUBSCRIPTIONS_ENABLED": False},
    {"APPLE_SUBSCRIPTION_ENVIRONMENT": "Sandbox"},
])
def test_any_missing_window_condition_closes_the_named_account(app, client, window, change):
    app.config.update(change)
    assert "eligibility" not in client.get(ME, headers=NAMED).json["user"]
    assert client.get(REVIEW, headers=NAMED).status_code == 503


@pytest.mark.parametrize("change", [
    {"APPLE_PURCHASE_QUALIFICATION_SUBJECTS": frozenset()},
    {"APPLE_SANDBOX_FALLBACK_ENABLED": False},
    {"APPLE_SUBSCRIPTIONS_ENABLED": False},
    {"APPLE_SUBSCRIPTION_ENVIRONMENT": "Sandbox"},
])
def test_preview_is_closed_without_a_complete_window(app, client, window, change):
    app.config.update(change)
    assert client.post(PREVIEW, json={"age": 30, "country": "TR"}).status_code == 503


def test_flag_on_ignores_the_list_for_other_accounts(app, client, window):
    """The seam only ever adds the named account. Flag behaviour is the existing one."""
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True
    assert client.get(ME, headers=OTHER).json["user"]["eligibility"]["status"] == "review_required"
    assert client.post("/eligibility", json={"age": 30, "country": "TR"}).status_code == 200
    with pytest.raises(policy.EligibilityDenied):
        policy.require_capability(db.session.get(User, window.other), "body_photos")


def test_ai_stays_closed_for_the_named_account_after_purchase_setup(client, window, monkeypatch):
    assert _declare(client).status_code == 200
    assert client.get(CONTEXT, headers=NAMED).status_code == 200

    assert ai_launch.premium_ready() is False
    assert ai_launch.APPROVED_SERVICE_COHORT_VERSION == "age-screen-v1"
    assert dict(ai_consent_disclosures.LAUNCH_VERSIONS) == {
        "training.plan": "d1-closure01-training-candidate",
        "coach.conversation": "d1-closure02-coach-candidate-r2",
        "nutrition.plan": "d1-closure02-nutrition-plan-candidate",
        "pump_check.analysis": "d1-closure02-pump-check-candidate",
        "qr_menu.analysis": "d1-closure02-qr-menu-candidate",
    }
    with pytest.raises(ai_consent.ConsentDenied):
        ai_launch.require_runtime()
    # The accepted signed adult TR record passes cohort only; runtime and the
    # Coach grant remain independently closed even with Coach text published.
    ai_launch.require_cohort(db.session.get(User, window.named))

    response = client.post("/api/v1/coach/messages", headers=NAMED, json={"message": "merhaba"})
    assert response.status_code in (403, 409, 503)
    assert response.json["error"]["code"].startswith("AI_")
    # The declaration and the purchase context wrote no consent state.
    assert AIConsentState.query.count() == 0


def test_no_subject_reaches_the_logs(client, window, caplog):
    with caplog.at_level(logging.DEBUG):
        assert _declare(client).status_code == 200
        assert client.get(CONTEXT, headers=NAMED).status_code == 200
    assert "event=qualification_purchase_context" in caplog.text
    assert NAMED_SUBJECT not in caplog.text


def test_seam_is_confined_to_named_modules():
    root = Path(__file__).resolve().parents[1]
    seam = ("APPLE_PURCHASE_QUALIFICATION_SUBJECTS", "APPLE_QUALIFICATION_CONTROL_SUBJECTS", "qualification_subject",
            "qualification_open", "served_to(")
    found = set()
    for folder in ("app", "fitx_mcp"):
        for path in (root / folder).rglob("*.py"):
            if any(name in path.read_text(encoding="utf-8") for name in seam):
                found.add(path.relative_to(root).as_posix())
    assert found == {
        "app/config.py", "app/services/account_eligibility.py", "app/services/subscriptions.py",
        "app/blueprints/account_eligibility.py", "app/blueprints/mobile_api.py",
    }
