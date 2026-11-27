"""Coach capability authority (#533 / #547 F3): default closed, no expansion.

Pure/SQLite layer. Real owner-lock fencing of capability grants needs
PostgreSQL (``ai_consent._transaction`` refuses other dialects); the
state-level predicates are exercised directly here.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from types import MappingProxyType, SimpleNamespace

import pytest

from app.services import ai_consent, ai_consent_disclosures, coach_capabilities as caps
from app.services.coach_plan_policy import ImpactFacts, is_direct_apply_eligible
from app.services.plan_mutation import (
    AddExerciseCommand,
    RemoveExerciseCommand,
    UpdateExercisePrescriptionCommand,
)

R2 = "d1-closure02-coach-candidate-r2"
NUTRITION_PLAN = "d1-closure02-nutrition-plan-candidate"
CAP_VERSION = "test-capability-training-edit"
NCAP_VERSION = "test-capability-nutrition-plan"


def _record(version, locale="en"):
    policy = ai_consent.POLICIES[version]
    return {"version": version, "locale": locale,
            "text_digest": dict(policy.texts)[locale],
            "contract_digest": ai_consent.contract_digest(policy),
            "action": "affirmative_grant", "state": "granted",
            "granted_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
            "revoked_at": None}


def _register(monkeypatch, capability, version, *, approve=True, launch=True, **overrides):
    spec = caps.SPECS[capability]
    coach = ai_consent.POLICIES[R2]
    texts = {locale: f"synthetic {capability} {locale}\n" for locale in ("en", "tr")}
    fields = dict(version=version, purpose=spec.purpose, categories=spec.categories,
                  recipients=coach.recipients,
                  texts=tuple((k, sha256(v.encode()).hexdigest()) for k, v in sorted(texts.items())))
    fields.update(overrides)
    policy = ai_consent.Disclosure(**fields)
    policies = dict(ai_consent.POLICIES)
    policies[version] = policy
    monkeypatch.setattr(ai_consent, "POLICIES", MappingProxyType(policies))
    published = dict(ai_consent_disclosures.PUBLISHED_TEXTS)
    published.update({(version, k): v for k, v in texts.items()})
    monkeypatch.setattr(ai_consent_disclosures, "PUBLISHED_TEXTS", MappingProxyType(published))
    if launch:
        launched = dict(ai_consent_disclosures.LAUNCH_VERSIONS)
        launched[policy.purpose] = version
        monkeypatch.setattr(ai_consent_disclosures, "LAUNCH_VERSIONS", MappingProxyType(launched))
    if approve:
        approved = dict(caps.APPROVED_VERSIONS)
        approved[capability] = frozenset({version})
        monkeypatch.setattr(caps, "APPROVED_VERSIONS", MappingProxyType(approved))
    return policy


def _state(generation=5, **grants):
    return {"owner_id": 7, "generation": generation, "grants": grants}


# ── Registry: default closed ──────────────────────────────────────────────

def test_registry_is_default_closed_and_unpublished():
    assert caps.APPROVED_VERSIONS == {}
    assert caps.DIRECT_APPLY_VERSIONS == frozenset()
    bound = {purpose for purpose, _ in ai_consent.BINDINGS.values()}
    for spec in caps.SPECS.values():
        assert spec.purpose not in bound  # never a provider dispatch of its own
        assert spec.purpose not in ai_consent_disclosures.LAUNCH_VERSIONS
        assert all(p.purpose != spec.purpose for p in ai_consent.POLICIES.values())
        assert not caps.source_open(spec.name)
    offered = {d["purpose"] for d in ai_consent_disclosures.available_disclosures()}
    assert offered.isdisjoint(caps.CAPABILITY_PURPOSES)
    # The r2 ordinary-chat contract and its launch selection are unchanged.
    assert ai_consent_disclosures.LAUNCH_VERSIONS["coach.conversation"] == R2


def test_capture_refuses_before_any_database_access_when_closed(monkeypatch):
    monkeypatch.setattr(ai_consent, "_transaction", lambda *a, **k: pytest.fail("no DB read"))
    for capability in caps.SPECS:
        with pytest.raises(ai_consent.ConsentDenied) as denied:
            ai_consent.capture_capability(7, capability)
        assert denied.value.code == "AI_CAPABILITY_UNAVAILABLE"
    with pytest.raises(ai_consent.ConsentDenied):
        ai_consent.capture_capability(7, "coach.conversation")


def test_version_open_requires_every_condition(monkeypatch):
    policy = _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION)
    assert caps.version_open(caps.TRAINING_PLAN_WRITE, policy)
    assert not caps.version_open(caps.NUTRITION_PLAN_ACCESS, policy)


@pytest.mark.parametrize("mutation", [
    {"approve": False}, {"launch": False},
    {"categories": ("training_plan_content",)},
    {"recipients": (("bedrock", "eu.anthropic.claude-sonnet-5-5"),)},
    {"external_scopes": ("public_https_menu_source",)},
    {"nested_features": ("nutrition",)},
])
def test_version_open_refuses_any_deviation(monkeypatch, mutation):
    policy = _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION, **mutation)
    assert not caps.version_open(caps.TRAINING_PLAN_WRITE, policy)
    assert not caps.grant_open(policy)


def test_version_open_refuses_unpinned_text(monkeypatch):
    policy = _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION)
    published = dict(ai_consent_disclosures.PUBLISHED_TEXTS)
    published[(CAP_VERSION, "tr")] = "edited after review\n"
    monkeypatch.setattr(ai_consent_disclosures, "PUBLISHED_TEXTS", MappingProxyType(published))
    assert not caps.version_open(caps.TRAINING_PLAN_WRITE, policy)


def test_grant_open_leaves_existing_purposes_untouched():
    for policy in ai_consent.POLICIES.values():
        assert caps.grant_open(policy)


# ── No historical grant expansion ─────────────────────────────────────────

def test_r2_coach_and_nutrition_grants_never_imply_a_capability(monkeypatch):
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION)
    _register(monkeypatch, caps.NUTRITION_PLAN_ACCESS, NCAP_VERSION)
    state = _state(**{"coach.conversation": _record(R2), "nutrition.plan": _record(NUTRITION_PLAN),
                      "training.plan": _record("d1-closure01-training-candidate")})
    for capability in caps.SPECS:
        with pytest.raises(ai_consent.ConsentDenied) as denied:
            ai_consent._capability_version(state, capability)
        assert denied.value.code == "AI_CAPABILITY_CONSENT_REQUIRED"


def test_valid_capability_grant_opens_only_its_capability(monkeypatch):
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION)
    state = _state(**{"coach.conversation": _record(R2),
                      "coach.training_plan_edit": _record(CAP_VERSION, "tr")})
    assert ai_consent._capability_version(state, caps.TRAINING_PLAN_WRITE) == CAP_VERSION
    with pytest.raises(ai_consent.ConsentDenied):
        ai_consent._capability_version(state, caps.NUTRITION_PLAN_ACCESS)


def test_nutrition_capability_also_needs_the_separate_nutrition_plan_grant(monkeypatch):
    _register(monkeypatch, caps.NUTRITION_PLAN_ACCESS, NCAP_VERSION)
    grants = {"coach.conversation": _record(R2),
              "coach.nutrition_plan_access": _record(NCAP_VERSION)}
    with pytest.raises(ai_consent.ConsentDenied) as denied:
        ai_consent._capability_version(_state(**grants), caps.NUTRITION_PLAN_ACCESS)
    assert denied.value.code == "AI_CONSENT_REQUIRED"
    grants["nutrition.plan"] = _record(NUTRITION_PLAN)
    assert ai_consent._capability_version(_state(**grants), caps.NUTRITION_PLAN_ACCESS) == NCAP_VERSION


def test_revoked_or_unapproved_capability_is_refused(monkeypatch):
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION)
    revoked = dict(_record(CAP_VERSION), state="revoked", revoked_at=datetime.utcnow().isoformat())
    with pytest.raises(ai_consent.ConsentDenied):
        ai_consent._capability_version(_state(**{"coach.training_plan_edit": revoked}),
                                       caps.TRAINING_PLAN_WRITE)
    record = _record(CAP_VERSION)
    monkeypatch.setattr(caps, "APPROVED_VERSIONS", MappingProxyType({}))
    with pytest.raises(ai_consent.ConsentDenied) as denied:
        ai_consent._capability_version(_state(**{"coach.training_plan_edit": record}),
                                       caps.TRAINING_PLAN_WRITE)
    assert denied.value.code == "AI_CAPABILITY_UNAVAILABLE"


def test_captured_capability_is_stale_after_any_consent_change(monkeypatch):
    _register(monkeypatch, caps.TRAINING_PLAN_WRITE, CAP_VERSION)
    state = _state(generation=5, **{"coach.training_plan_edit": _record(CAP_VERSION)})
    authority = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                               "coach.training_plan_edit", CAP_VERSION, 5)
    ai_consent._check_capability(state, authority)
    with pytest.raises(ai_consent.ConsentDenied) as denied:
        ai_consent._check_capability(dict(state, generation=7), authority)  # revoke -> regrant
    assert denied.value.code == "AI_CONSENT_CHANGED"
    other = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                           "coach.training_plan_edit", "older-version", 5)
    with pytest.raises(ai_consent.ConsentDenied):
        ai_consent._check_capability(state, other)


# ── Durable form and scope ────────────────────────────────────────────────

def test_capability_context_round_trip_is_owner_and_capability_bound():
    authority = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                               "coach.training_plan_edit", CAP_VERSION, 5)
    context = authority.to_context()
    assert ai_consent.CapabilityAuthority.from_context(7, context, caps.TRAINING_PLAN_WRITE) == authority
    bad = [(8, context, caps.TRAINING_PLAN_WRITE),
           (7, context, caps.NUTRITION_PLAN_ACCESS),
           (7, dict(context, purpose="coach.conversation"), caps.TRAINING_PLAN_WRITE),
           (7, dict(context, generation=0), caps.TRAINING_PLAN_WRITE),
           (7, dict(context, extra=1), caps.TRAINING_PLAN_WRITE),
           (7, None, caps.TRAINING_PLAN_WRITE)]
    for owner, value, capability in bad:
        with pytest.raises(ai_consent.ConsentDenied):
            ai_consent.CapabilityAuthority.from_context(owner, value, capability)


def test_capability_scope_requires_the_same_operation_generation():
    authority = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                               "coach.training_plan_edit", CAP_VERSION, 5)
    with pytest.raises(ai_consent.ConsentDenied):
        with ai_consent.capability_scope(authority):
            pass  # no operation
    with ai_consent.operation_scope(ai_consent.OperationAuthority(7, "coach", 6, R2)):
        with pytest.raises(ai_consent.ConsentDenied):
            with ai_consent.capability_scope(authority):
                pass
    with ai_consent.operation_scope(ai_consent.OperationAuthority(7, "coach", 5, R2)):
        with ai_consent.capability_scope(authority):
            assert ai_consent.active_capability(7, caps.TRAINING_PLAN_WRITE) == authority
            assert ai_consent.active_capability(8, caps.TRAINING_PLAN_WRITE) is None
        assert ai_consent.active_capabilities() == ()


def test_fence_active_fences_active_capabilities(monkeypatch):
    seen = []
    monkeypatch.setattr(ai_consent, "fence", lambda *a, **k: seen.append("operation"))
    monkeypatch.setattr(ai_consent, "_fence_capabilities",
                        lambda owner, authorities: seen.append(("capabilities", owner, authorities)))
    authority = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                               "coach.training_plan_edit", CAP_VERSION, 5)
    with ai_consent.operation_scope(ai_consent.OperationAuthority(7, "coach", 5, R2)):
        ai_consent.fence_active(7)
        with ai_consent.capability_scope(authority):
            ai_consent.fence_active(7)
    assert seen == ["operation", "operation", ("capabilities", 7, (authority,))]


# ── Training WRITE wiring ─────────────────────────────────────────────────

def test_training_write_is_absent_and_refused_without_capability(app, monkeypatch):
    from app.services import coach_plan_tools
    from app.services.coach_plan_tools import executor
    monkeypatch.setattr(executor, "plan_mutation_tools_enabled", lambda: True)
    with app.test_request_context():
        with ai_consent.operation_scope(ai_consent.OperationAuthority(7, "coach", 5, R2)):
            assert executor.training_write_authority(7) is None
            assert coach_plan_tools.published_plan_tool_defs(7) == ()
            payload = coach_plan_tools.execute_plan_tool(
                7, "update_training_plan_prescription", {"day": "Pazartesi", "exercise": "Squat", "sets": 4})
            assert payload["error"] == "CAPABILITY_DISABLED"


def test_outside_an_ai_operation_the_capability_does_not_apply(app):
    from app.services.coach_plan_tools import executor
    with app.test_request_context():
        assert executor.training_write_authority(7) is executor.NOT_IN_AI_OPERATION
        assert executor.training_write_authorized(7)


def test_proposal_without_its_capability_never_applies():
    from app.services.coach_plan_tools import executor
    pending = SimpleNamespace(command_payload={ai_consent.PAYLOAD_AUTHORITY_KEY: {}})
    with pytest.raises(ai_consent.ConsentDenied) as denied:
        executor.stored_training_capability(7, pending)
    assert denied.value.status == 403
    authority = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                               "coach.training_plan_edit", CAP_VERSION, 5)
    pending.command_payload[ai_consent.PAYLOAD_CAPABILITY_KEY] = [authority.to_context()]
    assert executor.stored_training_capability(7, pending) == authority
    with pytest.raises(ai_consent.ConsentDenied):
        executor.stored_training_capability(8, pending)
    pending.command_payload[ai_consent.PAYLOAD_CAPABILITY_KEY] = [authority.to_context()] * 2
    with pytest.raises(ai_consent.ConsentDenied):
        executor.stored_training_capability(7, pending)


# ── Risk-based direct apply ───────────────────────────────────────────────

SAFE = ImpactFacts(touches_active_session=False)


def _sets(before, after):
    return [(("program", 0, "egzersizler", 0, "set"), before, after)]


def test_low_impact_rule_is_a_single_small_set_change():
    command = UpdateExercisePrescriptionCommand(day="Pazartesi", exercise="Squat", sets=4)
    assert is_direct_apply_eligible(command, SAFE, _sets(3, 4))
    assert is_direct_apply_eligible(command, SAFE, _sets(4, 3))
    assert not is_direct_apply_eligible(command, SAFE, _sets(3, 5))
    assert not is_direct_apply_eligible(command, SAFE, _sets(3, 3))
    assert not is_direct_apply_eligible(command, SAFE, _sets(True, 2))
    assert not is_direct_apply_eligible(command, SAFE, _sets(3, 4) * 2)
    assert not is_direct_apply_eligible(command, SAFE, [(("x", "tekrar"), "8", "10")])
    assert not is_direct_apply_eligible(command, SAFE, None)
    assert not is_direct_apply_eligible(
        command, ImpactFacts(touches_active_session=True), _sets(3, 4))
    assert not is_direct_apply_eligible(
        command, ImpactFacts(touches_active_session=False, impact_undeterminable=True), _sets(3, 4))
    with_reps = UpdateExercisePrescriptionCommand(day="Pazartesi", exercise="Squat", sets=4, reps="8")
    assert not is_direct_apply_eligible(with_reps, SAFE, _sets(3, 4))
    for other in (AddExerciseCommand(day="Pazartesi", exercise="Squat", sets=3, reps="8"),
                  RemoveExerciseCommand(day="Pazartesi", exercise="Squat")):
        assert not is_direct_apply_eligible(other, SAFE, _sets(3, 4))


def test_direct_apply_is_closed_without_an_approved_direct_apply_disclosure(monkeypatch):
    authority = ai_consent.CapabilityAuthority(7, caps.TRAINING_PLAN_WRITE,
                                               "coach.training_plan_edit", CAP_VERSION, 5)
    assert not caps.direct_apply_allowed(authority)
    assert not caps.direct_apply_allowed(None)
    monkeypatch.setattr(caps, "DIRECT_APPLY_VERSIONS", frozenset({CAP_VERSION}))
    assert not caps.direct_apply_allowed(authority)  # still needs the approval list
    monkeypatch.setattr(caps, "APPROVED_VERSIONS",
                        MappingProxyType({caps.TRAINING_PLAN_WRITE: frozenset({CAP_VERSION})}))
    assert caps.direct_apply_allowed(authority)


def test_changed_document_leaves_reports_structure_changes_as_non_set():
    from app.services.coach_plan_tools import proposals
    plan = SimpleNamespace(plan_data="not json")
    assert proposals.changed_document_leaves(plan, None) is None


# ── Nutrition: Coach + nutrition.plan alone never write ───────────────────

def test_nutrition_fence_requires_the_capability(monkeypatch):
    from app.services.nutrition_plan_mutation import access
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    monkeypatch.setattr(access, "APPROVED_COACH_EDIT_DISCLOSURES", frozenset({R2}))
    monkeypatch.setattr(ai_consent, "fence", lambda *a, **k: None)
    coach = ai_consent.OperationAuthority(7, "coach", 5, R2)
    nutrition = ai_consent.OperationAuthority(7, "nutrition_plan", 5, NUTRITION_PLAN)
    with pytest.raises(ai_consent.ConsentDenied) as denied:
        access.fence(7, access.Authorities(coach, nutrition))
    assert denied.value.code == "AI_CAPABILITY_CONSENT_REQUIRED"
    stale = ai_consent.CapabilityAuthority(7, caps.NUTRITION_PLAN_ACCESS,
                                           "coach.nutrition_plan_access", NCAP_VERSION, 4)
    with pytest.raises(ai_consent.ConsentDenied):
        access.fence(7, access.Authorities(coach, nutrition, stale))
    fenced = []
    monkeypatch.setattr(ai_consent, "fence_capabilities", lambda owner, *a: fenced.append(a))
    current = ai_consent.CapabilityAuthority(7, caps.NUTRITION_PLAN_ACCESS,
                                             "coach.nutrition_plan_access", NCAP_VERSION, 5)
    access.fence(7, access.Authorities(coach, nutrition, current))
    assert fenced == [(current,)]


def test_nutrition_authorities_context_carries_the_capability():
    from app.services.nutrition_plan_mutation import access
    coach = ai_consent.OperationAuthority(7, "coach", 5, R2)
    nutrition = ai_consent.OperationAuthority(7, "nutrition_plan", 5, NUTRITION_PLAN)
    capability = ai_consent.CapabilityAuthority(7, caps.NUTRITION_PLAN_ACCESS,
                                                "coach.nutrition_plan_access", NCAP_VERSION, 5)
    full = access.Authorities(coach, nutrition, capability)
    assert access.Authorities.from_context(7, full.to_context()) == full
    legacy = access.Authorities(coach, nutrition)
    assert access.Authorities.from_context(7, legacy.to_context()).capability is None
