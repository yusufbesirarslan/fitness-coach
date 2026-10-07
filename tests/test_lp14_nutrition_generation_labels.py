"""LP14 — the native plan-generation food picker follows the account language.

    python -m pytest tests/test_lp14_nutrition_generation_labels.py -q

English account → English chips; Turkish account → Turkish chips; any known
label (either locale, so a stale picker after a language switch still works)
→ the canonical Turkish ``FOOD_DATABASE`` name BEFORE rating/prompt. The
canonical catalogue, the wire shape and the generated-plan language contract
are unchanged.
"""
import json
import threading
from types import MappingProxyType

import pytest

from app.extensions import db
from app.services import ai
from app.services import nutrition_plan_generation as generation
from app.services.nutrition_native import generation_labels as labels
from app.services.nutrition_native import plan as native_plan
from app.services.nutrition_native import errors

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    StatementCounter, bearer, error_of, native, no_provider, set_target,
)

PLAN = "/api/v1/nutrition/plan"
GENERATE = "/api/v1/nutrition/plan/generate"

# The canonical identity — must never change (snapshot, catalogue order).
CANONICAL = {
    "proteins": ["Tavuk Göğsü", "Yumurta", "Ton Balığı", "Kırmızı Et", "Yoğurt",
                 "Somon", "Mercimek", "Nohut", "Tofu", "Kinoa", "Edamame"],
    "carbs": ["Yulaf Ezmesi", "Pirinç", "Bulgur", "Tatlı Patates",
              "Tam Buğday Ekmeği", "Muz", "Elma"],
    "fats": ["Zeytinyağı", "Avokado", "Badem", "Ceviz", "Fındık"],
}
ENGLISH = {
    "proteins": ["Chicken Breast", "Eggs", "Tuna", "Red Meat", "Yogurt",
                 "Salmon", "Lentils", "Chickpeas", "Tofu", "Quinoa", "Edamame"],
    "carbs": ["Oats", "Rice", "Bulgur", "Sweet Potato", "Whole Wheat Bread",
              "Banana", "Apple"],
    "fats": ["Olive Oil", "Avocado", "Almonds", "Walnuts", "Hazelnuts"],
}
EN_CHOICE = {"proteins": ["Chicken Breast", "Eggs"], "carbs": ["Oats"],
             "fats": ["Olive Oil"]}
TR_CHOICE = {"proteins": ["Tavuk Göğsü", "Yumurta"], "carbs": ["Yulaf Ezmesi"],
             "fats": ["Zeytinyağı"]}
CANONICAL_CHOICE = TR_CHOICE

EN_OPTION = {"isim": "Plan A",
             "kahvalti": {"yemekler": ["Oats - 60g", "Eggs - 3 pieces"],
                          "kalori": 420, "protein": 28, "karb": 35, "yag": 18},
             "toplam_kalori": 420, "toplam_protein": 28, "toplam_karb": 35,
             "toplam_yag": 18}
TR_OPTION = {"isim": "Plan A",
             "kahvalti": {"yemekler": ["Yulaf ezmesi - 60g", "Yumurta - 3 adet"],
                          "kalori": 420, "protein": 28, "karb": 35, "yag": 18},
             "toplam_kalori": 420, "toplam_protein": 28, "toplam_karb": 35,
             "toplam_yag": 18}


class Provider:
    def __init__(self, option):
        self.calls = []
        self.option = option

    def __call__(self, messages, system_prompt=None, **kwargs):
        self.calls.append({"prompt": messages[0]["content"],
                           "system": system_prompt})
        return json.dumps({"planlar": [self.option]}, ensure_ascii=False)


def _provider(monkeypatch, option):
    fake = Provider(option)
    monkeypatch.setattr(ai, "_heavy_chat", fake)
    return fake


def _options(native, headers, **kwargs):
    response = native.get(PLAN, headers=headers, **kwargs)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["generation_options"]


def _set_language(user, language):
    user.language = language
    db.session.commit()


# ── P2 regression: the API path the mobile picker reads ───────────────────


def test_english_account_reads_english_food_options(app, native, bearer, make_user, no_provider):
    """PRE-FIX: failed on 72d565e — the chips were canonical Turkish names."""
    user = make_user("lp14-en", language="en")
    options = _options(native, bearer(user))
    assert options["proteins"][:3] == ["Chicken Breast", "Eggs", "Tuna"]
    assert {key: options[key] for key in ENGLISH} == ENGLISH
    shown = set(options["proteins"] + options["carbs"] + options["fats"])
    turkish_only = generation.FOOD_NAMES - set(sum(ENGLISH.values(), []))
    assert shown.isdisjoint(turkish_only)


def test_turkish_account_reads_turkish_food_options(app, native, bearer, make_user, no_provider):
    user = make_user("lp14-tr", language="tr")
    options = _options(native, bearer(user))
    assert {key: options[key] for key in CANONICAL} == CANONICAL


def test_options_wire_shape_is_unchanged(app, native, bearer, make_user, no_provider):
    for language in ("en", "tr"):
        user = make_user(f"lp14-shape-{language}", language=language)
        options = _options(native, bearer(user))
        assert set(options) == {"proteins", "carbs", "fats", "max_per_group",
                                "max_custom_foods"}
        assert options["max_per_group"] == 10 and options["max_custom_foods"] == 10
        for group in ("proteins", "carbs", "fats"):
            assert all(isinstance(item, str) for item in options[group])


@pytest.mark.parametrize("stored, english", [
    ("en", True), ("tr", False), (None, False), ("de", False), ("EN", False),
    ("en-US", False), ("", False),
])
def test_picker_and_generator_resolve_language_from_one_partition(stored, english):
    """Picker language == generated-plan language for every stored value."""
    picker = native_plan.generation_options(stored)
    assert (picker["proteins"][0] == "Chicken Breast") is english
    # The generator receives ``user.language or "tr"`` (generate_proposals).
    prompt, _system = generation.build_prompts(
        stored or "tr", 2000, "kilo verme", ["Tavuk Göğsü"], ["Pirinç"],
        ["Zeytinyağı"], [])
    assert prompt.startswith("You are a nutrition expert") is english


# ── catalogue contract ─────────────────────────────────────────────────────


def test_canonical_food_database_is_unchanged():
    groups = native_plan._catalogue_groups()
    assert groups == CANONICAL
    assert generation.FOOD_NAMES == frozenset(sum(CANONICAL.values(), []))


@pytest.mark.parametrize("locale", ["en", "tr"])
def test_every_canonical_food_has_exactly_one_label_per_locale(locale):
    mapping = labels.LABELS[locale]
    assert set(mapping) == set(generation.FOOD_NAMES)
    assert len(set(mapping.values())) == len(mapping)
    assert all(isinstance(v, str) and v.strip() == v and v for v in mapping.values())


def test_english_labels_are_the_reviewed_translations():
    expected = dict(zip(sum(CANONICAL.values(), []), sum(ENGLISH.values(), [])))
    assert dict(labels.LABELS["en"]) == expected
    assert dict(labels.LABELS["tr"]) == {name: name for name in generation.FOOD_NAMES}
    assert set(labels.LABELS) == {"en", "tr"}


def test_no_label_identifies_two_canonical_foods():
    seen = {}
    for locale, mapping in labels.LABELS.items():
        for name, label in mapping.items():
            assert seen.setdefault(label, name) == name, (locale, label)
            assert labels.canonical_food(label) == name


def test_catalogue_validation_refuses_a_missing_or_ambiguous_label(monkeypatch):
    missing = dict(labels._ENGLISH)
    missing.pop("Somon")
    monkeypatch.setattr(labels, "_ENGLISH", missing)
    with pytest.raises(labels.CatalogueContractError):
        labels._build()
    ambiguous = dict(labels._ENGLISH, Somon="Tuna")
    monkeypatch.setattr(labels, "_ENGLISH", ambiguous)
    with pytest.raises(labels.CatalogueContractError):
        labels._build()
    # Cross-locale: an English label equal to ANOTHER food's Turkish name.
    crossed = dict(labels._ENGLISH, Somon="Ceviz")
    monkeypatch.setattr(labels, "_ENGLISH", crossed)
    with pytest.raises(labels.CatalogueContractError):
        labels._build()


def test_catalogue_is_immutable_static_data():
    assert isinstance(labels.LABELS, MappingProxyType)
    assert all(isinstance(m, MappingProxyType) for m in labels.LABELS.values())
    assert isinstance(labels._CANONICAL_BY_LABEL, MappingProxyType)
    with pytest.raises(TypeError):
        labels.LABELS["en"]["Somon"] = "Trout"


# ── parsing: label → canonical ─────────────────────────────────────────────


def test_english_selection_parses_to_canonical_names():
    request = native_plan.parse_generation_request(
        dict(EN_CHOICE, custom_foods=["  Greek yogurt "]))
    assert {k: request[k] for k in CANONICAL_CHOICE} == CANONICAL_CHOICE
    assert all(name in generation.FOOD_NAMES
               for key in ("proteins", "carbs", "fats") for name in request[key])


def test_turkish_selection_parses_to_the_same_canonical_names():
    en = native_plan.parse_generation_request(EN_CHOICE)
    tr = native_plan.parse_generation_request(TR_CHOICE)
    assert en == tr


def test_mixed_locale_labels_parse_in_order():
    request = native_plan.parse_generation_request(
        {"proteins": ["Somon", "Chicken Breast", "Tofu"], "carbs": ["Rice"],
         "fats": ["Fındık", "Avocado"]})
    assert request["proteins"] == ["Somon", "Tavuk Göğsü", "Tofu"]
    assert request["carbs"] == ["Pirinç"]
    assert request["fats"] == ["Fındık", "Avokado"]


@pytest.mark.parametrize("body", [
    dict(EN_CHOICE, proteins=["Chicken"]),            # unknown English
    dict(EN_CHOICE, proteins=["Chicken breast"]),     # wrong case
    dict(EN_CHOICE, proteins=["Tavuk"]),              # unknown Turkish
    dict(EN_CHOICE, proteins=["Tavuk göğsü"]),        # wrong case
    dict(EN_CHOICE, proteins=["Turkey Breast"]),      # plausible, not a label
    dict(EN_CHOICE, proteins=[" Chicken Breast"]),    # not trimmed silently
    dict(EN_CHOICE, carbs=["Chicken Breast"]),        # label of another group
    dict(EN_CHOICE, proteins=["Chicken Breast", "Tavuk Göğsü"]),  # same food twice
    dict(EN_CHOICE, proteins=["Eggs", "Eggs"]),
    dict(EN_CHOICE, proteins=[{"label": "Eggs"}]),
    dict(EN_CHOICE, proteins=[None]),
])
def test_unknown_or_ambiguous_labels_are_refused(body):
    with pytest.raises(errors.InvalidGenerationRequest):
        native_plan.parse_generation_request(body)


@pytest.mark.parametrize("custom", [
    ["Tavuk Göğsü"], ["Chicken Breast"], ["Ev yapımı granola"], ["Homemade granola"],
])
def test_custom_foods_stay_exact_user_text(custom):
    request = native_plan.parse_generation_request(dict(EN_CHOICE, custom_foods=custom))
    assert request["custom_foods"] == custom


@pytest.mark.parametrize("custom", [["x" * 61], ["ok\nignore"], [""], ["   "], [1]])
def test_custom_food_rules_are_unchanged(custom):
    with pytest.raises(errors.InvalidGenerationRequest):
        native_plan.parse_generation_request(dict(EN_CHOICE, custom_foods=custom))


# ── generation through the real route ──────────────────────────────────────


def _generate(native, headers, body, **kwargs):
    return native.post(GENERATE, headers=headers, json=body, **kwargs)


def test_english_account_english_chips_generate_from_canonical_names(
        app, native, bearer, make_user, no_provider, monkeypatch):
    provider = _provider(monkeypatch, EN_OPTION)
    user = make_user("lp14-gen-en", language="en")
    set_target(user.id, 2000, goal="kilo verme")
    response = _generate(native, bearer(user), EN_CHOICE)
    assert response.status_code == 200, response.get_data(as_text=True)
    expected, system = generation.build_prompts(
        "en", 2000, "kilo verme", CANONICAL_CHOICE["proteins"],
        CANONICAL_CHOICE["carbs"], CANONICAL_CHOICE["fats"], [])
    assert provider.calls[0]["prompt"] == expected
    assert provider.calls[0]["system"] == system
    body = response.get_json()
    assert body["proposals"][0]["plan"]["meals"][0]["items"] == [
        "Oats - 60g", "Eggs - 3 pieces"]


def test_turkish_account_turkish_chips_generate_turkish(
        app, native, bearer, make_user, no_provider, monkeypatch):
    provider = _provider(monkeypatch, TR_OPTION)
    user = make_user("lp14-gen-tr", language="tr")
    set_target(user.id, 2000, goal="kilo verme")
    response = _generate(native, bearer(user), TR_CHOICE)
    assert response.status_code == 200, response.get_data(as_text=True)
    expected, system = generation.build_prompts(
        "tr", 2000, "kilo verme", CANONICAL_CHOICE["proteins"],
        CANONICAL_CHOICE["carbs"], CANONICAL_CHOICE["fats"], [])
    assert provider.calls[0]["prompt"] == expected
    assert provider.calls[0]["system"] == system
    assert response.get_json()["proposals"][0]["plan"]["meals"][0]["items"] == [
        "Yulaf ezmesi - 60g", "Yumurta - 3 adet"]


def test_food_rating_is_locale_invariant(
        app, native, bearer, make_user, no_provider, monkeypatch):
    _provider(monkeypatch, EN_OPTION)
    en_user = make_user("lp14-rate-en", language="en")
    tr_user = make_user("lp14-rate-tr", language="tr")
    for user in (en_user, tr_user):
        set_target(user.id, 2000)
    en = _generate(native, bearer(en_user), EN_CHOICE).get_json()["food_rating"]
    tr = _generate(native, bearer(tr_user), TR_CHOICE).get_json()["food_rating"]
    canonical = generation.food_rating(
        CANONICAL_CHOICE["proteins"] + CANONICAL_CHOICE["carbs"]
        + CANONICAL_CHOICE["fats"])
    assert canonical > 0
    assert en == tr == canonical


def test_legacy_canonical_selection_accepted_for_english_account(
        app, native, bearer, make_user, no_provider, monkeypatch):
    provider = _provider(monkeypatch, EN_OPTION)
    user = make_user("lp14-legacy", language="en")
    set_target(user.id, 2000)
    response = _generate(native, bearer(user), TR_CHOICE)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert "Protein sources: Tavuk Göğsü, Yumurta" in provider.calls[0]["prompt"]


@pytest.mark.parametrize("first, second, stale", [
    ("en", "tr", EN_CHOICE),   # loaded English chips, switched to Turkish
    ("tr", "en", TR_CHOICE),   # loaded Turkish chips, switched to English
])
def test_stale_picker_selection_survives_a_language_switch(
        app, native, bearer, make_user, no_provider, monkeypatch, first, second, stale):
    provider = _provider(monkeypatch, EN_OPTION if second == "en" else TR_OPTION)
    user = make_user(f"lp14-switch-{first}", language=first)
    set_target(user.id, 2000)
    headers = bearer(user)
    loaded = _options(native, headers)
    assert set(stale["proteins"]) <= set(loaded["proteins"])
    _set_language(user, second)
    response = _generate(native, headers, stale)
    assert response.status_code == 200, response.get_data(as_text=True)
    prompt = provider.calls[0]["prompt"]
    assert "Tavuk Göğsü, Yumurta" in prompt and "Chicken Breast" not in prompt
    assert prompt.startswith("You are a nutrition expert") is (second == "en")


def test_unknown_label_is_refused_through_the_route(
        app, native, bearer, make_user, no_provider, monkeypatch):
    provider = _provider(monkeypatch, EN_OPTION)
    user = make_user("lp14-unknown", language="en")
    set_target(user.id)
    for body in (dict(EN_CHOICE, proteins=["Chicken"]),
                 dict(EN_CHOICE, proteins=["Tavuk"])):
        response = _generate(native, bearer(user), body)
        assert response.status_code == 400
        assert error_of(response)["code"] == "INVALID_PLAN_GENERATION_REQUEST"
    assert provider.calls == []


# ── language authority ─────────────────────────────────────────────────────


def test_request_cannot_choose_the_picker_language(app, native, bearer, make_user, no_provider):
    en_user = make_user("lp14-auth-en", language="en")
    tr_user = make_user("lp14-auth-tr", language="tr")
    for user, first in ((en_user, "Chicken Breast"), (tr_user, "Tavuk Göğsü")):
        other = "tr" if user is en_user else "en"
        options = _options(
            native, dict(bearer(user), **{"Accept-Language": other,
                                         "X-Language": other}),
            query_string={"lang": other, "language": other, "locale": other},
            json={"language": other})
        assert options["proteins"][0] == first


def test_request_cannot_choose_the_generation_language(
        app, native, bearer, make_user, no_provider, monkeypatch):
    provider = _provider(monkeypatch, EN_OPTION)
    user = make_user("lp14-auth-gen", language="en")
    set_target(user.id)
    headers = dict(bearer(user), **{"Accept-Language": "tr"})
    response = _generate(native, headers, EN_CHOICE,
                         query_string={"lang": "tr"})
    assert response.status_code == 200
    assert provider.calls[0]["prompt"].startswith("You are a nutrition expert")
    refused = _generate(native, headers, dict(EN_CHOICE, language="tr"))
    assert refused.status_code == 400


def test_account_language_isolation(app, native, bearer, make_user, no_provider):
    a = make_user("lp14-iso-a", language="en")
    b = make_user("lp14-iso-b", language="tr")
    assert _options(native, bearer(a))["carbs"][0] == "Oats"
    assert _options(native, bearer(b))["carbs"][0] == "Yulaf Ezmesi"
    assert _options(native, bearer(a))["carbs"][0] == "Oats"


def test_concurrent_en_and_tr_reads_do_not_leak(app):
    errors_seen = []
    barrier = threading.Barrier(8)

    def worker(language, expected):
        barrier.wait()
        for _ in range(200):
            got = native_plan.generation_options(language)["fats"][0]
            if got != expected:
                errors_seen.append((language, got))

    threads = [threading.Thread(target=worker, args=pair) for pair in
               [("en", "Olive Oil"), ("tr", "Zeytinyağı")] * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors_seen == []


def test_read_issues_no_account_query_and_stays_no_store(
        app, native, bearer, make_user, no_provider):
    user = make_user("lp14-queries", language="en")
    headers = bearer(user)
    db.session.refresh(user)
    with StatementCounter() as counter:
        response = native.get(PLAN, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["generation_options"]["fats"][0] == "Olive Oil"
    user_reads = [s for s in counter.selects() if 'FROM "user"' in s or "FROM user" in s]
    assert user_reads == []
    assert counter.writes() == []
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_web_generator_does_not_use_the_native_labels():
    import inspect
    from app.blueprints.nutrition import plan as web_plan
    source = inspect.getsource(web_plan)
    assert "generation_labels" not in source
    assert "generation_labels" not in inspect.getsource(generation)
