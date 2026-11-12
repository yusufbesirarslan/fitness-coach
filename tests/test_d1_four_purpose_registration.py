"""Founder-content-approved (2026-11-12) Coach, Nutrition plan, Pump Check and
QR Menu D1 contracts: exact bytes, exact contract shape, and no widening of any
existing boundary. Registration is source only; runtime/activation stay closed.

Founder content approval is not legal counsel sign-off.
"""
from hashlib import sha256

import pytest

from app.services import ai_consent as consent
from app.services import ai_consent_disclosures as copy
from app.services import ai_launch
from app.services.ai_allowances import launch_policy
from app.services.ai_allowances.policy import SUPPORTED_FEATURES

SONNET = ("bedrock", "eu.anthropic.claude-sonnet-5-5")
HAIKU = ("bedrock", "eu.anthropic.claude-haiku-5-5")

# version -> (purpose, binding feature, en sha256, tr sha256)
APPROVED = {
    "d1-closure02-coach-candidate-r2": (
        "coach.conversation", "coach",
        "4666f7fc249f0916ed9f861f7114193407fa2f394cec1556209fdadeb04650e7",
        "1c93b22650a21c80e6b28a87eec41492cef9dfd307c6312bf387f58db85b9b9b"),
    "d1-closure02-nutrition-plan-candidate": (
        "nutrition.plan", "nutrition_plan",
        "6b51fe2dd8177ec2369aed2131da057850aad159b1b0e05a06c5bf2794017b75",
        "0c1f84a3ecac79382d81dc223327f9fc5e01687585db8a1d1df2e0974b2e6bb8"),
    "d1-closure02-pump-check-candidate": (
        "pump_check.analysis", "vision",
        "816f7a3af11fa48edebcac266278e821963bad59670510f503aebd1e5a4b9089",
        "c67fbdfac82ba21fd57cf1b000ff46bac79e0668c6c3119d446cd4cb21bb0637"),
    "d1-closure02-qr-menu-candidate": (
        "qr_menu.analysis", "menu_extract",
        "c143131cb0ac8ef8e82d795dff3b50d3260a7c646959f3d0ca57bd818c6d589c",
        "fd2f0d2eb34a3d328aa4a12091b9e7b2f917a9d74657ab3ee28bcf9100e5a74f"),
}
TRAINING = "d1-closure01-training-candidate"
# Shared final paragraph: byte-identical to the approved Training text.
SHARED_TAIL_DIGEST = {
    "en": sha256(copy.PUBLISHED_TEXTS[(TRAINING, "en")].split("\n\n")[-1].encode()).hexdigest(),
    "tr": sha256(copy.PUBLISHED_TEXTS[(TRAINING, "tr")].split("\n\n")[-1].encode()).hexdigest(),
}


@pytest.mark.parametrize("version", sorted(APPROVED))
def test_exact_approved_bytes_and_digests(version):
    purpose, _, en, tr = APPROVED[version]
    policy = consent.POLICIES[version]
    assert dict(policy.texts) == {"en": en, "tr": tr}
    texts = copy.localized_texts(policy)
    assert texts is not None
    for locale, expected in (("en", en), ("tr", tr)):
        body = copy.PUBLISHED_TEXTS[(version, locale)]
        assert sha256(body.encode("utf-8")).hexdigest() == expected == sha256(
            texts[locale].encode("utf-8")).hexdigest()
        assert body.endswith("\n") and not body.endswith("\n\n")
        paragraphs = body.split("\n\n")
        assert len(paragraphs) == 4
        assert sha256(paragraphs[-1].encode()).hexdigest() == SHARED_TAIL_DIGEST[locale]
    assert copy.LAUNCH_VERSIONS[purpose] == version


@pytest.mark.parametrize("version", sorted(APPROVED))
def test_contract_shape_matches_bindings_and_routes(version):
    purpose, feature, _, _ = APPROVED[version]
    policy = consent.POLICIES[version]
    assert policy.version == version and policy.purpose == purpose
    assert consent.BINDINGS[feature] == (purpose, policy.categories)
    assert policy.recipients == (SONNET, HAIKU)
    assert policy.external_scopes == (
        ("public_https_menu_source",) if feature == "menu_extract" else ())
    assert policy.nested_features == (("nutrition", "vision") if feature == "coach" else ())
    assert ai_launch._published(feature) == (purpose, version)


def test_training_contract_and_text_are_untouched():
    policy = consent.POLICIES[TRAINING]
    assert consent.contract_digest(policy) == (
        "2d14b846a5e2b273ca1bb724f507336989998b81cbcafbd7360ba4ea28b05968")
    assert copy.LAUNCH_VERSIONS["training.plan"] == TRAINING


def test_contract_digests_are_pinned():
    # Any byte/recipient/category/scope change must update this pin deliberately.
    assert {v: consent.contract_digest(consent.POLICIES[v])[:16] for v in APPROVED} == {
        "d1-closure02-coach-candidate-r2": "262bee6825e4e059",
        "d1-closure02-nutrition-plan-candidate": "0d5c9795fa16e410",
        "d1-closure02-pump-check-candidate": "df764f2b0015d313",
        "d1-closure02-qr-menu-candidate": "e99d3b3adaecf2e0",
    }


def test_available_disclosures_publish_exactly_the_five_purposes():
    published = {d["purpose"]: d["version"] for d in copy.available_disclosures()}
    assert published == dict(copy.LAUNCH_VERSIONS)
    assert len(published) == 5


@pytest.mark.parametrize("feature", ["coach", "nutrition_plan", "vision", "training_plan"])
def test_only_menu_may_carry_the_public_menu_scope(monkeypatch, feature):
    purpose = consent.BINDINGS[feature][0]
    version = copy.LAUNCH_VERSIONS[purpose]
    widened = consent.Disclosure(**{**consent.POLICIES[version].__dict__,
                                    "external_scopes": ("public_https_menu_source",)})
    monkeypatch.setattr(consent, "POLICIES", {**consent.POLICIES, version: widened})
    with pytest.raises(consent.ConsentDenied):
        ai_launch._published(feature)


def test_menu_without_its_scope_is_refused(monkeypatch):
    version = copy.LAUNCH_VERSIONS["qr_menu.analysis"]
    narrowed = consent.Disclosure(**{**consent.POLICIES[version].__dict__, "external_scopes": ()})
    monkeypatch.setattr(consent, "POLICIES", {**consent.POLICIES, version: narrowed})
    with pytest.raises(consent.ConsentDenied):
        ai_launch._published("menu_extract")


def test_coach_helpers_are_exactly_nutrition_and_vision_and_never_memory():
    version = copy.LAUNCH_VERSIONS["coach.conversation"]
    authority = consent.OperationAuthority(owner=1, feature="coach", generation=1, version=version)
    assert consent.qualified_coach_helpers(authority) == ("nutrition", "vision")
    other = consent.OperationAuthority(owner=1, feature="vision", generation=1,
                                       version=copy.LAUNCH_VERSIONS["pump_check.analysis"])
    assert consent.qualified_coach_helpers(other) == ()
    assert "summary" not in consent.BINDINGS  # AI Memory stays unqualified


@pytest.mark.parametrize("feature", sorted(consent.NATIVE_OPERATION_FEATURES))
def test_native_families_still_need_an_admitted_operation(feature):
    # e.g. Pump Check comparison (not a native endpoint) stays refused.
    with pytest.raises(consent.ConsentDenied) as error:
        consent.admission_generation(1, feature)
    assert error.value.code == "AI_CONSENT_PATH_UNAVAILABLE"
    assert "mobile_api.create_pump_check_comparison" not in ai_launch.NATIVE_ENDPOINTS


def test_endpoints_cohort_and_allowances_are_unchanged():
    assert ai_launch.NATIVE_ENDPOINTS == {
        "mobile_api.send_coach_message": "coach",
        "mobile_api.create_training_plan": "training_plan",
        "mobile_api.nutrition_plan_generate_native": "nutrition_plan",
        "mobile_api.create_pump_check": "vision",
        "mobile_api.nutrition_menu_analyze": "menu_extract",
    }
    assert ai_launch.APPROVED_SERVICE_COHORT_VERSION == "age-screen-v1"
    assert SUPPORTED_FEATURES == ("coach", "training_generation", "nutrition_generation",
                                  "pump_check", "qr_menu")
    assert dict(launch_policy.LAUNCH_V1_POLICY.starter.allowances) == dict(
        zip(SUPPORTED_FEATURES, (10, 1, 1, 1, 1)))
    assert dict(launch_policy.LAUNCH_FREE_CAMPAIGN.allowances.allowances) == dict(
        zip(SUPPORTED_FEATURES, (30, 2, 2, 20, 5)))


def test_registration_does_not_open_runtime():
    # Text publication is not activation: the runtime gate still refuses here.
    with pytest.raises(consent.ConsentDenied):
        ai_launch.require_runtime()
