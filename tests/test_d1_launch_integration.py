"""Launch gates with synthetic contracts; no runtime/legal approval or network."""
from dataclasses import replace
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from app.extensions import db
from app.services import ai_consent as consent, ai_consent_disclosures as disclosures, ai_launch
from app.services.ai_allowances import launch_policy, execution, service as ledger
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
from consent_projection_support import publish_synthetic_texts
from subscription_support import adult_account
from test_mobile_coach_api import bearer


@pytest.fixture
def contracts(monkeypatch):
    policies = {f: consent.Disclosure(f, purpose, categories, (("openai", "synthetic"),), (),
        ("public_https_menu_source",) if f == "menu_extract" else ())
        for f, (purpose, categories) in consent.BINDINGS.items()}
    policies = publish_synthetic_texts(monkeypatch, policies)
    monkeypatch.setattr(consent, "POLICIES", policies)
    monkeypatch.setattr(disclosures, "LAUNCH_VERSIONS", {p.purpose: p.version for p in policies.values()})
    state = {"generation": 5, "grants": {p.purpose: dict(version=p.version, locale="en",
        text_digest=dict(p.texts)["en"], contract_digest=consent.contract_digest(p),
        action="affirmative_grant", state="granted", granted_at=datetime.utcnow().isoformat(), revoked_at=None)
        for p in policies.values()}}
    return state


@pytest.fixture
def source_policy(monkeypatch):
    units = {f: 1 for f in SUPPORTED_FEATURES}
    policy = AllowancePolicy("synthetic-launch", ScopePolicy("NEVER", units, 0),
        datetime(2026, 1, 1, tzinfo=timezone.utc), ScopePolicy("MONTHLY", units, 0))
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", policy)
    return policy


@pytest.mark.parametrize("feature", list(consent.BINDINGS))
def test_selected_exact_contract_and_publication_required(contracts, monkeypatch, feature):
    ai_launch.require_selected(contracts, feature)
    policy = consent.POLICIES[feature]
    consent._authorize(contracts, consent.BINDINGS[feature], "openai", "synthetic")
    for field, value in [("version", "obsolete"), ("state", "revoked"),
                         ("contract_digest", "0" * 64), ("action", "implicit_grant")]:
        old = contracts["grants"][policy.purpose][field]
        contracts["grants"][policy.purpose][field] = value
        with pytest.raises(consent.ConsentDenied):
            ai_launch.require_selected(contracts, feature)
        contracts["grants"][policy.purpose][field] = old
    with pytest.raises(consent.ConsentDenied):
        consent._authorize(contracts, consent.BINDINGS[feature], "openai", "other")
    with pytest.raises(consent.ConsentDenied):
        consent._authorize(contracts, (policy.purpose, ("extra",)), "openai", "synthetic")
    monkeypatch.setattr(disclosures, "PUBLISHED_TEXTS", {})
    with pytest.raises(consent.ConsentDenied):
        consent._authorize(contracts, consent.BINDINGS[feature], "openai", "synthetic")
    with pytest.raises(consent.ConsentDenied):
        consent._check_operation(contracts, consent.OperationAuthority(1, feature, 5, feature))


def test_qr_composition_uses_qr_contract_not_nutrition_grant(contracts):
    contracts["grants"].pop("nutrition.plan")
    authority = consent.OperationAuthority(1, "menu_extract", 5, "menu_extract")
    with consent.operation_scope(authority):
        assert consent.admission_generation(1, "nutrition") == 5
        binding = consent.provider_binding(1, "nutrition")
        assert binding == consent.BINDINGS["menu_extract"]
        consent._authorize(contracts, binding, "openai", "synthetic")
        with pytest.raises(consent.ConsentDenied):
            consent.provider_binding(2, "nutrition")
        with pytest.raises(consent.ConsentDenied):
            consent.provider_binding(1, "nutrition_plan")
    assert consent.provider_binding(1, "nutrition") is None
    contracts["generation"] += 2  # Revoke/regrant never revives the old operation.
    with pytest.raises(consent.ConsentDenied):
        consent._check_operation(contracts, authority)


def test_coach_unqualified_tools_absent_and_forged_calls_have_no_effect(app, make_user, monkeypatch):
    from app.services import ai_coach
    user = make_user("launch-tools")
    sinks = [Mock(side_effect=AssertionError("unqualified sink")) for _ in range(3)]
    for name, sink in zip(("_tool_fetch_nutrition_and_stage_log", "_tool_analyze_gym_photo", "_tool_manage_user_memory"), sinks):
        monkeypatch.setattr(ai_coach, name, sink)
    from app.services.ai_spend_guard import subject_scope
    with subject_scope(user.id):
        names = {t["name"] for t in ai_coach._coach_tool_defs_for_call(user.id)}
        assert "stage_workout_log" in names
        for tool in ai_coach._UNQUALIFIED_D1_TOOLS:
            assert tool not in names
            assert "unavailable" in ai_coach._dispatch_coach_tool(user.id, tool, '{}')
    for sink in sinks:
        sink.assert_not_called()


@pytest.mark.parametrize("case", ["missing", "unsigned", "tampered", "other_subject", "no_subject", "contradictory",
    "teen", "tr_teen", "country", "future", "naive", "invalid_time", "review_required"])
def test_signed_launch_cohort_reuses_canonical_evidence(app, make_user, case):
    from test_purchase_eligibility import invalidate
    user = adult_account(make_user("launch-cohort"))
    db.session.commit()
    ai_launch.require_cohort(user)
    invalidate(user.id, case)
    with pytest.raises(consent.ConsentDenied) as error:
        ai_launch.require_cohort(db.session.get(type(user), user.id, populate_existing=True))
    assert error.value.code == "AI_SERVICE_ELIGIBILITY_REQUIRED"


def test_starter_cannot_allocate_with_unverified_service_cohort(app, make_user, source_policy):
    user = make_user("launch-no-starter")
    with pytest.raises(consent.ConsentDenied):
        ledger._starter_locked(user, source_policy, datetime.now(timezone.utc))
    from app.models import AiStarterGrant, AiAllowanceBucket
    assert db.session.query(AiStarterGrant).count() == 0
    assert db.session.query(AiAllowanceBucket).count() == 0


def test_runtime_needs_source_policy_flags_and_redis(app, source_policy, monkeypatch):
    from app.services import ai_spend_guard
    app.config.update(AI_ALLOWANCE_POLICY=source_policy, AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        APPLE_SUBSCRIPTIONS_ENABLED=True)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())
    ai_launch.require_runtime()
    for flag in (execution.FLAG, "AI_ALLOWANCE_LEDGER_ENABLED", "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED", "APPLE_SUBSCRIPTIONS_ENABLED"):
        app.config[flag] = False
        with pytest.raises((consent.ConsentDenied, execution.ExecutionRefused)):
            ai_launch.require_runtime()
        app.config[flag] = True
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    with pytest.raises(consent.ConsentDenied):
        ai_launch.require_runtime()


def test_menu_network_requires_original_qr_scope_and_generation(contracts, monkeypatch):
    @contextmanager
    def transaction(owner):
        assert owner == 1
        yield None
    monkeypatch.setattr(consent, "_transaction", transaction)
    monkeypatch.setattr(consent, "_state", lambda *a: contracts)
    sink = Mock()
    with consent.operation_scope(consent.OperationAuthority(1, "menu_extract", 5, "menu_extract")):
        with consent.menu_source_dispatch():
            sink()
        policy = consent.POLICIES["menu_extract"]
        modified = replace(policy, external_scopes=())
        monkeypatch.setitem(consent.POLICIES, "menu_extract", modified)
        record = contracts["grants"][policy.purpose]
        record["contract_digest"] = consent.contract_digest(modified)
        with pytest.raises(consent.ConsentDenied):
            with consent.menu_source_dispatch():
                sink()
    assert sink.call_count == 1


@pytest.mark.parametrize("endpoint", list(ai_launch.NATIVE_ENDPOINTS))
def test_only_five_native_views_can_pass_source_gate(client, app, make_user, bearer, contracts, source_policy, monkeypatch, endpoint):
    from flask import jsonify
    from app.services import ai_spend_guard
    user = adult_account(make_user("launch-native"))
    db.session.commit()
    app.config.update(AI_ALLOWANCE_POLICY=source_policy, AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        APPLE_SUBSCRIPTIONS_ENABLED=True)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())
    @contextmanager
    def transaction(owner):
        assert owner == user.id
        yield db.session.connection()  # Locking is separately qualified on PG.
    monkeypatch.setattr(consent, "_transaction", transaction)
    monkeypatch.setattr(consent, "_state", lambda *a: contracts)
    sink = Mock(return_value=jsonify(qualified_view=True))
    monkeypatch.setitem(app.view_functions, endpoint, sink)
    rule = next(r for r in app.url_map.iter_rules() if r.endpoint == endpoint)
    response = client.post(str(rule), headers=bearer(user), json={})
    assert response.status_code == 200
    assert sink.call_count == 1
    user.user_metadata = {}
    db.session.commit()
    response = client.post(str(rule), headers=bearer(user), json={})
    assert response.status_code == 403
    assert response.json["error"]["code"] == "AI_SERVICE_ELIGIBILITY_REQUIRED"
    assert sink.call_count == 1
