"""Exact text boundary, offline only. No legal approval or provider acceptance."""
from dataclasses import replace
from hashlib import sha256

import pytest

from app.services import ai_consent as consent, ai_consent_disclosures as copy


@pytest.fixture
def synthetic_policy(monkeypatch):
    texts = {"en": "SYNTHETIC disclosure", "tr": "SENTETİK açıklama"}
    policy = consent.Disclosure("synthetic-coach", "coach.conversation",
        ("personal_free_text",), (("bedrock", "synthetic-model"),),
        tuple((locale, sha256(text.encode()).hexdigest()) for locale, text in texts.items()))
    monkeypatch.setattr(consent, "POLICIES", {policy.version: policy})
    monkeypatch.setattr(copy, "PUBLISHED_TEXTS", {
        (policy.version, locale): text for locale, text in texts.items()})
    return policy, texts


def test_shipping_registry_is_empty_and_does_not_open_coach():
    assert dict(copy.PUBLISHED_TEXTS) == {}
    assert dict(consent.POLICIES) == {}
    assert "coach" not in consent.BINDINGS
    assert copy.available_disclosures() == []


def test_both_exact_locales_and_full_contract_are_projected(synthetic_policy):
    policy, texts = synthetic_policy
    projected, = copy.available_disclosures()
    assert projected["localized_texts"] == texts
    assert projected["texts"] == dict(policy.texts)
    assert projected["contract_digest"] == consent.contract_digest(policy)
    assert projected["recipients"] == [["bedrock", "synthetic-model"]]
    projected["localized_texts"]["en"] = "client-edited"
    assert copy.localized_texts(policy) == texts


@pytest.mark.parametrize("value", [None, "", " ", "edited", "SYNTHETIC disclosure\n"])
def test_missing_or_edited_copy_denies_entire_disclosure(monkeypatch, synthetic_policy, value):
    policy, _ = synthetic_policy
    monkeypatch.setattr(copy, "PUBLISHED_TEXTS", {
        **copy.PUBLISHED_TEXTS, (policy.version, "en"): value})
    assert copy.localized_texts(policy) is None
    assert copy.available_disclosures() == []


def test_orphan_text_and_non_authoritative_contract_cannot_publish(monkeypatch, synthetic_policy):
    policy, _ = synthetic_policy
    assert copy.localized_texts(replace(policy, recipients=(("openai", "other"),))) is None
    monkeypatch.setattr(consent, "POLICIES", {})
    assert copy.available_disclosures() == []


@pytest.mark.parametrize("locales", [("en",), ("en", "tr", "de"), ("en", "tr", "en")])
def test_partial_extra_and_duplicate_locale_contracts_fail_closed(monkeypatch, synthetic_policy, locales):
    policy, _ = synthetic_policy
    digests = dict(policy.texts)
    broken = replace(policy, texts=tuple((locale, digests.get(locale, "a" * 64)) for locale in locales))
    monkeypatch.setattr(consent, "POLICIES", {broken.version: broken})
    assert copy.available_disclosures() == []
