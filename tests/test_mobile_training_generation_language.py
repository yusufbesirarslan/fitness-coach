"""LP-14 PR1 — native first-plan generation reads the persisted User.language.

The native command used to read a ``preferred_language`` attribute the User
model does not have, so every mobile account (registered with ``language``)
got a Turkish plan. ``preferred_language`` is only the account/me
serialization name for ``User.language``; it must never be a second source.

Hermetic: the provider is always a spy.
"""
import json

import pytest

from app.extensions import db
from app.models import User
from app.services.mobile_training_generation import service as generation_service
from app.services.training_generation.prompt_builder import build_system_prompt
from tests.test_mobile_training_generation_api import (  # noqa: F401 - fixtures
    CANONICAL,
    POST_PATH,
    _provider_document,
    as_mobile,
    mobile_user,
)


@pytest.fixture
def candidate_language(monkeypatch):
    """Spy on the generator boundary; record the language the command passes."""
    real = generation_service.generate_training_plan_candidate
    seen = []

    def spy(user, last_session, preferences, chat_fn, **kwargs):
        seen.append(kwargs["language"])
        return real(user, last_session, preferences, chat_fn, **kwargs)

    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", spy)
    return seen


@pytest.fixture
def provider_calls(monkeypatch):
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return json.dumps(_provider_document("ex_barbell_back_squat"))

    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", complete)
    return calls


def _set_language(user, value):
    user.language = value
    db.session.commit()


def test_user_model_has_no_preferred_language_attribute():
    """The fix must not add a second persisted language source."""
    assert "preferred_language" not in User.__table__.columns
    assert not hasattr(User, "preferred_language")


@pytest.mark.parametrize("language", ["en", "tr"])
def test_generation_language_is_the_persisted_user_language(
        client, mobile_user, as_mobile, candidate_language, provider_calls,
        language):
    _set_language(mobile_user, language)

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, f"lang-param-{language}"))

    assert created.status_code == 201, created.json
    assert candidate_language == [language]
    assert len(provider_calls) == 1
    assert provider_calls[0]["system_prompt"] == build_system_prompt(language)


def test_english_user_gets_the_english_prompt_path(
        client, mobile_user, as_mobile, provider_calls):
    _set_language(mobile_user, "en")

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "lang-en-prompt"))

    assert created.status_code == 201, created.json
    call = provider_calls[0]
    assert call["system_prompt"].startswith("You are an experienced personal trainer.")
    assert "İçerik dili: ENGLISH" in call["messages"][0]["content"]
    assert "İçerik dili: TÜRKÇE" not in call["messages"][0]["content"]


def test_turkish_user_keeps_the_turkish_prompt_path(
        client, mobile_user, as_mobile, provider_calls):
    _set_language(mobile_user, "tr")

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "lang-tr-prompt"))

    assert created.status_code == 201, created.json
    call = provider_calls[0]
    assert call["system_prompt"].startswith("Sen deneyimli bir kişisel antrenörsün.")
    assert "İçerik dili: TÜRKÇE" in call["messages"][0]["content"]
    assert "İçerik dili: ENGLISH" not in call["messages"][0]["content"]


def test_a_transient_preferred_language_attribute_is_ignored(
        client, mobile_user, as_mobile, candidate_language, provider_calls):
    """The pre-fix bug only passed tests that attached this non-model
    attribute; a contradicting one must have no effect now."""
    _set_language(mobile_user, "en")
    mobile_user.preferred_language = "tr"

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "lang-transient"))

    assert created.status_code == 201, created.json
    assert candidate_language == ["en"]
    assert provider_calls[0]["system_prompt"] == build_system_prompt("en")


def test_missing_legacy_language_keeps_the_turkish_fallback(
        client, mobile_user, as_mobile, candidate_language, provider_calls):
    _set_language(mobile_user, None)

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "lang-missing"))

    assert created.status_code == 201, created.json
    assert candidate_language == ["tr"]
    assert provider_calls[0]["system_prompt"] == build_system_prompt("tr")
