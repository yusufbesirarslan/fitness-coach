"""Inactive integration: no publication, provider or transaction changes."""
from datetime import datetime, timezone
import pytest
from app.services import ai_consent as consent
from app.services.ai_allowances import launch_policy
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES, PolicyError


def test_five_independent_purposes_remain_unpublished():
    assert {value[0] for value in consent.BINDINGS.values()} == {
        "coach.conversation", "training.plan", "nutrition.plan", "pump_check.analysis", "qr_menu.analysis"}
    assert len(consent.BINDINGS) == 5
    assert not consent.POLICIES
    assert "nutrition" not in consent.BINDINGS
    from app.services.ai_consent_disclosures import PUBLISHED_TEXTS
    assert not PUBLISHED_TEXTS


@pytest.mark.parametrize("original,nested", [("coach", "nutrition"), ("coach", "vision"),
    ("vision", "coach"), ("training_plan", "nutrition_plan")])
def test_accounting_taxonomy_never_permits_nested_consent(original, nested):
    authority = consent.OperationAuthority(1, original, 7, "synthetic")
    with consent.operation_scope(authority):
        with pytest.raises(consent.ConsentDenied) as error:
            consent.admission_generation(1, nested)
        assert error.value.code == "AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE"
        assert consent.admission_generation(1, original) == 7


def test_no_runtime_policy_channel(app, monkeypatch):
    monkeypatch.setenv("AI_ALLOWANCE_POLICY", '{"premium":"unlimited"}')
    assert launch_policy.installed_policy() is None
    assert app.config["AI_ALLOWANCE_POLICY"] is None


def test_source_installer_requires_both_complete_scopes(monkeypatch):
    incomplete = AllowancePolicy("synthetic", premium=ScopePolicy("MONTHLY", {"coach": 1}, 0))
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", incomplete)
    with pytest.raises(PolicyError):
        launch_policy.installed_policy()
    # Synthetic finite values qualify shape only, never installed source facts.
    units = {feature: 1 for feature in SUPPORTED_FEATURES}
    complete = AllowancePolicy("synthetic", ScopePolicy("NEVER", units, 0),
        datetime(2026, 1, 1, tzinfo=timezone.utc), ScopePolicy("MONTHLY", units, 0))
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", complete)
    assert launch_policy.installed_policy() is complete
    with pytest.raises(TypeError):
        complete.premium.allowances["coach"] = 2


def test_allowance_refusal_exposes_only_customer_metadata(app):
    from app.services.ai_allowances import execution
    from flask import current_app
    with app.test_request_context():
        error = execution.ExecutionRefused("AI_ALLOWANCE_EXHAUSTED", feature="coach",
            scope="PREMIUM", reset="MONTHLY", resets_at="2026-11-01T00:00:00+03:00",
            reservation_id="private", request_digest="private")
        response = current_app.handle_user_exception(error)
        body = response.get_json()["error"]
        assert body["details"] == {"feature": "coach", "scope": "PREMIUM", "reset": "MONTHLY",
            "resets_at": "2026-11-01T00:00:00+03:00"}
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("feature", ["coach", "vision", "menu_extract"])
def test_new_native_bindings_cannot_authorize_legacy_provider_calls(feature):
    with pytest.raises(consent.ConsentDenied) as error:
        consent.admission_generation(1, feature)
    assert error.value.code == "AI_CONSENT_PATH_UNAVAILABLE"
