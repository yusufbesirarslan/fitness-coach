"""Original QR operation through the real D1/PG door; SDKs stay synthetic."""
from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import update
from app.extensions import db
from app.models import User
from app.services import ai_consent as consent, ai_consent_disclosures as disclosures, ai, ai_provider_call as calls, ai_spend_guard, ai_launch
from test_ai_consent_pg import pg_app, command
from subscription_support import adult_account
from test_d1_launch_integration import approved_cohort

pytestmark = pytest.mark.pg_concurrency


def call_nutrition(sdk):
    with calls.admit(feature="nutrition", provider="openai", payload={
        "model": ai.OPENAI_MODEL, "messages": [{"role": "user", "content": "SYNTHETIC_MENU_ONLY"}],
        "max_tokens": 100}, gate=False, charge=False) as admitted:
        return admitted.create(sdk)


def test_original_qr_authorizes_nested_nutrition_and_revocation_is_terminal(pg_app):
    sdk = Mock(return_value=object())
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        consent.mutate(1, command("menu_extract"))
        authority = consent.begin_operation(1, "menu_extract", "openai", ai.OPENAI_MODEL)
        with consent.operation_scope(authority):
            assert consent.begin_operation(1, "menu_extract", "openai", ai.OPENAI_MODEL) is authority
            call_nutrition(sdk)
            consent.authorize_operation(authority)
        consent.mutate(1, command("menu_extract", 1, "revoke", "revoke-qr"))
        consent.mutate(1, command("menu_extract", 2, key="regrant-qr"))
        with consent.operation_scope(authority), pytest.raises(consent.ConsentDenied):
            call_nutrition(sdk)
        with consent.operation_scope(authority), pytest.raises(consent.ConsentDenied):
            consent.begin_operation(1, "menu_extract", "openai", ai.OPENAI_MODEL)
        with pytest.raises(consent.ConsentDenied):
            consent.authorize_operation(authority)
    assert sdk.call_count == 1
    assert "SYNTHETIC_MENU_ONLY" in repr(sdk.call_args)


@pytest.mark.parametrize("case", ["model", "categories", "publication", "owner", "cross_purpose"])
def test_qr_nested_denial_never_calls_sdk(pg_app, monkeypatch, case):
    sdk = Mock()
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        consent.mutate(1, command("menu_extract"))
        authority = consent.begin_operation(1, "menu_extract", "openai", ai.OPENAI_MODEL)
        policy = consent.POLICIES[authority.version]
        if case == "model":
            monkeypatch.setitem(consent.POLICIES, policy.version, replace(policy, recipients=()))
        elif case == "categories":
            monkeypatch.setitem(consent.POLICIES, policy.version, replace(policy, categories=("health_targets",)))
        elif case == "publication":
            monkeypatch.setattr(disclosures, "PUBLISHED_TEXTS", {})
        elif case == "owner":
            authority = replace(authority, owner=2)
        else:
            authority = replace(authority, feature="coach")
        with consent.operation_scope(authority), pytest.raises(consent.ConsentDenied):
            call_nutrition(sdk)
    sdk.assert_not_called()


def test_launch_evidence_rechecked_from_database_before_provider_and_publication(pg_app, monkeypatch, approved_cohort):
    from app.services.ai_allowances import launch_policy
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        user = adult_account(db.session.get(User, 1))
        db.session.commit()
        monkeypatch.setattr(disclosures, "LAUNCH_VERSIONS", {p.purpose: p.version for p in consent.POLICIES.values()})
        # Isolate runtime installation from the real PG cohort/D1 authority.
        monkeypatch.setattr(launch_policy, "installed_policy", lambda: object())
        monkeypatch.setattr(ai_launch, "require_runtime", lambda: None)
        consent.mutate(1, command("coach"))
        authority = consent.begin_operation(1, "coach", "openai", ai.OPENAI_MODEL)
        consent.authorize_operation(authority)
        # Keep stale ORM identity in the request; final authority reads the DB.
        with db.engine.begin() as conn:
            conn.execute(update(User).where(User.id == 1).values(user_metadata={}))
        sdk = Mock()
        with consent.operation_scope(authority), pytest.raises(consent.ConsentDenied) as error:
            with calls.admit(feature="coach", provider="openai", payload={"model": ai.OPENAI_MODEL,
                    "max_tokens": 100, "messages": [{"role": "user", "content": "SYNTHETIC"}]},
                    gate=False, charge=False) as admitted:
                admitted.create(sdk)
        assert error.value.code == "AI_SERVICE_ELIGIBILITY_REQUIRED"
        sdk.assert_not_called()
        with pytest.raises(consent.ConsentDenied):
            consent.authorize_operation(authority)


def test_qr_nested_provider_calls_debit_one_original_finite_hold(pg_app, monkeypatch, approved_cohort):
    from datetime import datetime, timezone
    from app.services.ai_allowances import launch_policy, execution, service as ledger
    from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
    from app.models import AiUsageReservation
    from ai_allowance_support import enable_subscriptions
    units = {f: 1 for f in SUPPORTED_FEATURES}
    policy = AllowancePolicy("synthetic-integration", ScopePolicy("NEVER", units, 0),
        datetime(2026, 1, 1, tzinfo=timezone.utc), ScopePolicy("MONTHLY", units, 0))
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        adult_account(db.session.get(User, 1))
        db.session.commit()
        qr_policy = consent.POLICIES["synthetic-menu_extract"]
        monkeypatch.setitem(consent.POLICIES, qr_policy.version,
                            replace(qr_policy, external_scopes=("public_https_menu_source",)))
        monkeypatch.setattr(disclosures, "LAUNCH_VERSIONS", {"qr_menu.analysis": qr_policy.version})
        monkeypatch.setattr(launch_policy, "APPROVED_POLICY", policy)
        monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())
        monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
        pg_app.config.update(AI_ALLOWANCE_POLICY=policy, AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
            AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True)
        enable_subscriptions(pg_app, monkeypatch)
        consent.mutate(1, command("menu_extract"))
        with execution.logical_action(1, "qr_menu", {"url": "https://menu.invalid"}, key="original-qr-0001") as action:
            sdk = Mock(return_value=object())
            for feature in ("menu_extract", "nutrition", "nutrition"):
                with calls.admit(feature=feature, provider="openai", payload={"model": ai.OPENAI_MODEL,
                        "messages": [{"role": "user", "content": "SYNTHETIC_MENU"}], "max_tokens": 100},
                        gate=False, charge=False) as admission:
                    admission.create(sdk)
            action.success()
        hold = db.session.query(AiUsageReservation).one()
        assert hold.feature == "qr_menu" and hold.physical_attempts == 3
        assert hold.state == ledger.COMMITTED
        assert hold.physical_input_reserved > 0
        assert sdk.call_count == 3
        with pytest.raises(execution.ExecutionRefused):
            with execution.logical_action(1, "qr_menu", {"url": "https://menu.invalid"}, key="original-qr-0001"):
                pytest.fail("terminal replay must not redispatch")
        assert sdk.call_count == 3
