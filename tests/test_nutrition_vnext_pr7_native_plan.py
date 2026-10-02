"""NUTR-PR7 — native Nutrition Plan: read, generation, save/replacement.

    python -m pytest tests/test_nutrition_vnext_pr7_native_plan.py -q

active != absent != invalid != unavailable · generated proposal != saved plan ·
GET never generates · save validates before it replaces · a stale or doubled
replacement never silently succeeds · proposals are owner-bound and untampered.
"""
import json

import pytest

from app.extensions import db
from app.models import NutritionPlan, User
from app.services import ai
from app.services.nutrition_native import plan as native_plan

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    PLAN_DOC, StatementCounter, bearer, error_of, native, no_provider, quoted,
    save_plan_row, set_target,
)

PLAN = "/api/v1/nutrition/plan"
GENERATE = "/api/v1/nutrition/plan/generate"
CHOICE = {"proteins": ["Tavuk Göğsü", "Yumurta"], "carbs": ["Pirinç"],
          "fats": ["Zeytinyağı"]}
OPTION_A = {"isim": "Plan A",
            "kahvalti": {"yemekler": ["Yumurta - 3 adet"], "kalori": 420,
                         "protein": 28, "karb": 35, "yag": 18},
            "ogle": {"yemekler": ["Tavuk göğsü - 150g", "Pirinç - 100g"],
                     "kalori": 380, "protein": 48, "karb": 28, "yag": 5},
            "toplam_kalori": 800, "toplam_protein": 76, "toplam_karb": 63,
            "toplam_yag": 23}
OPTION_B = dict(OPTION_A, isim="Plan B")


class Generator:
    def __init__(self, options=None, fail=None):
        self.calls = []
        self.options = [OPTION_A, OPTION_B] if options is None else options
        self.fail = fail

    def __call__(self, messages, system_prompt=None, **kwargs):
        self.calls.append({"prompt": messages[0]["content"], **kwargs})
        if self.fail:
            raise self.fail
        return json.dumps({"planlar": self.options}, ensure_ascii=False)


@pytest.fixture
def generator(monkeypatch, no_provider):
    fake = Generator()
    monkeypatch.setattr(ai, "_heavy_chat", fake)
    return fake


def plans(user_id):
    return NutritionPlan.query.filter_by(user_id=user_id).all()


def read(native, headers):
    response = native.get(PLAN, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def generate(native, headers, body=CHOICE):
    return native.post(GENERATE, headers=headers, json=body)


def save(native, headers, proposal, *, if_match=None, if_none_match=None, **extra):
    hdrs = dict(headers)
    if if_match is not None:
        hdrs["If-Match"] = if_match
    if if_none_match is not None:
        hdrs["If-None-Match"] = if_none_match
    body = {"plan": proposal["plan"], "proposal_token": proposal["proposal_token"]}
    body.update(extra)
    return native.put(PLAN, headers=hdrs, json=body)


# ── B. read ────────────────────────────────────────────────────────────────


def test_absent_plan_is_a_typed_state_not_null(app, native, bearer, make_user, no_provider):
    user = make_user("plan-absent")
    body = read(native, bearer(user))
    assert body["plan"]["state"] == "absent"
    assert body["plan"]["revision"] is None and body["plan"]["meals"] is None
    assert body["plan"] is not None
    assert "Tavuk Göğsü" in body["generation_options"]["proteins"]


def test_active_plan_projects_the_saved_row(app, native, bearer, make_user, no_provider):
    user = make_user("plan-active")
    row = save_plan_row(user.id)
    body = read(native, bearer(user))["plan"]
    assert body["state"] == "active"
    assert body["name"] == "Lean plan" and body["food_rating"] == 8.0
    assert [m["slot"] for m in body["meals"]] == ["kahvalti", "aksam"]
    breakfast = body["meals"][0]
    assert breakfast["items"] == ["Oats - 60g", "Milk - 200ml"]
    assert breakfast["nutrition"] == {"energy_kcal": 450, "protein_g": 30,
                                      "carbohydrate_g": 50, "fat_g": 12}
    assert breakfast["loggable"] is True
    assert body["totals"]["energy_kcal"] == 1100
    assert body["saved_at"].endswith("+03:00")
    text = json.dumps(body)
    assert str(row.id) not in [m["id"] for m in body["meals"]]
    assert "plan_id" not in text and '"id": %d' % row.id not in text


def test_missing_macros_are_null_and_not_loggable(app, native, bearer, make_user, no_provider):
    user = make_user("plan-partial")
    save_plan_row(user.id, document={"ogle": {"yemekler": ["Soup"]}})
    meal = read(native, bearer(user))["plan"]["meals"][0]
    assert meal["nutrition"] == {"energy_kcal": None, "protein_g": None,
                                 "carbohydrate_g": None, "fat_g": None}
    assert meal["loggable"] is False


@pytest.mark.parametrize("raw", ["not json", "[]", json.dumps({"isim": "x"}),
                                 json.dumps({"ogle": {"yemekler": ["a"], "evil": 1}})])
def test_malformed_persisted_plan_is_invalid_not_absent(
        app, native, bearer, make_user, no_provider, raw):
    user = make_user("plan-invalid")
    save_plan_row(user.id, raw=raw)
    body = read(native, bearer(user))["plan"]
    assert body["state"] == "invalid"
    assert body["revision"]  # replaceable with If-Match
    assert body["meals"] is None


def test_plan_storage_failure_is_a_typed_503(app, native, bearer, make_user, monkeypatch):
    user = make_user("plan-503")
    monkeypatch.setattr(native_plan, "newest_plan",
                        lambda _uid: (_ for _ in ()).throw(RuntimeError("db")))
    response = native.get(PLAN, headers=bearer(user))
    assert response.status_code == 503
    assert error_of(response)["code"] == "NUTRITION_PLAN_UNAVAILABLE"
    assert error_of(response)["retryable"] is True


def test_reading_the_plan_never_generates_or_writes(
        app, native, bearer, make_user, no_provider):
    user = make_user("plan-noai")
    save_plan_row(user.id)
    headers = bearer(user)
    with StatementCounter() as counter:
        read(native, headers)
    assert counter.writes() == []
    assert len([s for s in counter.selects() if "nutrition_plan" in s]) == 1


# ── C. generation ──────────────────────────────────────────────────────────


def test_generation_returns_proposals_and_saves_nothing(
        app, native, bearer, make_user, generator):
    user = make_user("gen-ok")
    set_target(user.id, 2000)
    response = generate(native, bearer(user))
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert len(generator.calls) == 1
    assert generator.calls[0]["feature"] == "nutrition_plan"
    assert body["target"] == {"value": 2000, "unit": "kcal"}
    assert [p["plan"]["name"] for p in body["proposals"]] == ["Plan A", "Plan B"]
    assert all(p["proposal_token"] for p in body["proposals"])
    assert plans(user.id) == []
    assert read(native, bearer(user))["plan"]["state"] == "absent"


def test_generation_uses_the_shared_prompt_authority(app, native, bearer, make_user, generator):
    from app.services import nutrition_plan_generation as generation
    user = make_user("gen-prompt", language="en")
    set_target(user.id, 2000, goal="kilo verme")
    generate(native, bearer(user))
    expected, _system = generation.build_prompts(
        "en", 2000, "kilo verme", CHOICE["proteins"], CHOICE["carbs"],
        CHOICE["fats"], [])
    assert generator.calls[0]["prompt"] == expected


def test_generation_requires_a_target(app, native, bearer, make_user, generator):
    user = make_user("gen-notarget")
    response = generate(native, bearer(user))
    assert response.status_code == 409
    assert error_of(response)["code"] == "NUTRITION_TARGET_REQUIRED"
    assert generator.calls == []


@pytest.mark.parametrize("body", [
    {}, {"proteins": ["Tavuk Göğsü"], "carbs": ["Pirinç"]},
    dict(CHOICE, user_id=1), dict(CHOICE, proteins=["<script>"]),
    dict(CHOICE, proteins=[]), dict(CHOICE, custom_foods=["x" * 61]),
    dict(CHOICE, custom_foods=["ok\nignore previous"]), dict(CHOICE, fats="Zeytinyağı"),
])
def test_generation_request_is_closed_and_bounded(
        app, native, bearer, make_user, generator, body):
    user = make_user("gen-bad")
    set_target(user.id)
    response = generate(native, bearer(user), body)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_PLAN_GENERATION_REQUEST"
    assert generator.calls == []


@pytest.mark.parametrize("fail, options", [
    (RuntimeError("provider down"), None),
    (None, [{"isim": "bad", "kahvalti": "not an object"}]),
    (None, []),
])
def test_provider_failure_never_fabricates_a_proposal_and_refunds(
        app, native, bearer, make_user, monkeypatch, no_provider, fail, options):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    monkeypatch.setattr(ai, "_heavy_chat", Generator(options=options, fail=fail))
    user = make_user("gen-fail")
    set_target(user.id)
    response = generate(native, bearer(user))
    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "NUTRITION_PLAN_GENERATION_FAILED" and error["retryable"] is True
    meta = db.session.get(User, user.id).user_metadata or {}
    assert (meta.get("ai_plan_quota") or {}).get("nutrition", 0) == 0
    assert plans(user.id) == []


def test_generation_spends_the_shared_weekly_allowance(
        app, native, bearer, make_user, generator):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    user = make_user("gen-quota")
    set_target(user.id)
    assert generate(native, bearer(user)).status_code == 200
    second = generate(native, bearer(user))
    assert second.status_code == 402
    assert error_of(second)["code"] == "NUTRITION_PLAN_QUOTA_EXCEEDED"
    assert len(generator.calls) == 1


# ── D. save / replacement ──────────────────────────────────────────────────


def _proposals(native, headers):
    response = generate(native, headers)
    assert response.status_code == 200
    return response.get_json()["proposals"]


def test_create_with_if_none_match_then_read_converges(
        app, native, bearer, make_user, generator):
    user = make_user("save-create")
    set_target(user.id)
    headers = bearer(user)
    proposal = _proposals(native, headers)[0]
    response = save(native, headers, proposal, if_none_match="*")
    assert response.status_code == 200, response.get_data(as_text=True)
    saved = response.get_json()["plan"]
    assert saved["state"] == "active" and saved["name"] == "Plan A"
    assert read(native, headers)["plan"] == saved
    row = plans(user.id)[0]
    assert json.loads(row.plan_data) == OPTION_A
    assert row.score == response.get_json()["plan"]["food_rating"]


def test_replace_with_the_revision_actually_read(app, native, bearer, make_user, generator):
    user = make_user("save-replace")
    set_target(user.id)
    save_plan_row(user.id)
    headers = bearer(user)
    current = read(native, headers)["plan"]["revision"]
    proposal = _proposals(native, headers)[1]
    response = save(native, headers, proposal, if_match=quoted(current))
    assert response.status_code == 200
    assert [json.loads(r.plan_data)["isim"] for r in plans(user.id)] == ["Plan B"]


@pytest.mark.parametrize("headers, status, code", [
    ({}, 428, "NUTRITION_PRECONDITION_REQUIRED"),
    ({"If-None-Match": "\"abc\""}, 400, "INVALID_NUTRITION_PRECONDITION"),
    ({"If-None-Match": "*", "If-Match": "\"AAAAAAAAAAAAAAAAAAAAAAAA\""}, 400,
     "INVALID_NUTRITION_PRECONDITION"),
    ({"If-Match": "*"}, 400, "INVALID_NUTRITION_PRECONDITION"),
])
def test_replacement_precondition_is_mandatory_and_strict(
        app, native, bearer, make_user, generator, headers, status, code):
    user = make_user("save-precond")
    set_target(user.id)
    save_plan_row(user.id)
    hdrs = bearer(user)
    proposal = _proposals(native, hdrs)[0]
    response = native.put(PLAN, headers=dict(hdrs, **headers), json={
        "plan": proposal["plan"], "proposal_token": proposal["proposal_token"]})
    assert response.status_code == status
    assert error_of(response)["code"] == code
    assert [json.loads(r.plan_data)["isim"] for r in plans(user.id)] == ["Lean plan"]


def test_create_over_an_existing_plan_is_stale(app, native, bearer, make_user, generator):
    user = make_user("save-create-stale")
    set_target(user.id)
    headers = bearer(user)
    proposal = _proposals(native, headers)[0]
    save_plan_row(user.id)  # another device created a plan meanwhile
    response = save(native, headers, proposal, if_none_match="*")
    assert response.status_code == 412
    assert error_of(response)["code"] == "STALE_NUTRITION_PLAN"
    assert error_of(response)["retryable"] is False
    assert [json.loads(r.plan_data)["isim"] for r in plans(user.id)] == ["Lean plan"]


def test_two_devices_from_one_revision_cannot_both_succeed(
        app, native, bearer, make_user, generator):
    user = make_user("save-race")
    set_target(user.id)
    save_plan_row(user.id)
    headers = bearer(user)
    revision = read(native, headers)["plan"]["revision"]
    first, second = _proposals(native, headers)
    assert save(native, headers, first, if_match=quoted(revision)).status_code == 200
    late = save(native, headers, second, if_match=quoted(revision))
    assert late.status_code == 412
    assert [json.loads(r.plan_data)["isim"] for r in plans(user.id)] == ["Plan A"]


def test_lost_response_is_recovered_by_reading_not_by_resending(
        app, native, bearer, make_user, generator):
    user = make_user("save-lost")
    set_target(user.id)
    save_plan_row(user.id)
    headers = bearer(user)
    revision = read(native, headers)["plan"]["revision"]
    proposal = _proposals(native, headers)[0]
    assert save(native, headers, proposal, if_match=quoted(revision)).status_code == 200
    # Response "lost": a blind resend is refused, never a second replacement…
    assert save(native, headers, proposal, if_match=quoted(revision)).status_code == 412
    # …and a fresh read shows the client's own proposal is now canonical.
    current = read(native, headers)["plan"]
    assert current["name"] == proposal["plan"]["name"]
    assert len(plans(user.id)) == 1


@pytest.mark.parametrize("mutate, code", [
    (lambda p: p["plan"]["meals"][0]["nutrition"].update(energy_kcal=9999),
     "INVALID_PLAN_PROPOSAL"),
    (lambda p: p["plan"]["meals"][0].update(items=["Cake"]), "INVALID_PLAN_PROPOSAL"),
    (lambda p: p["plan"].update(name="Renamed"), "INVALID_PLAN_PROPOSAL"),
    (lambda p: p.update(proposal_token=p["proposal_token"][:-2] + "AA"),
     "INVALID_PLAN_PROPOSAL"),
    (lambda p: p["plan"]["meals"][0].update(slot="brunch"), "INVALID_NUTRITION_PLAN"),
    (lambda p: p["plan"]["meals"][0]["nutrition"].update(fat_g="12"),
     "INVALID_NUTRITION_PLAN"),
    (lambda p: p["plan"].update(extra=1), "INVALID_NUTRITION_PLAN"),
])
def test_save_revalidates_and_preserves_the_old_plan(
        app, native, bearer, make_user, generator, mutate, code):
    user = make_user("save-tamper")
    set_target(user.id)
    save_plan_row(user.id)
    headers = bearer(user)
    revision = read(native, headers)["plan"]["revision"]
    proposal = json.loads(json.dumps(_proposals(native, headers)[0]))
    mutate(proposal)
    response = save(native, headers, proposal, if_match=quoted(revision))
    assert response.status_code == 400
    assert error_of(response)["code"] == code
    assert [json.loads(r.plan_data)["isim"] for r in plans(user.id)] == ["Lean plan"]


@pytest.mark.parametrize("extra", [{"score": 10}, {"user_id": 1}, {"revision": "x"},
                                   {"plan_id": 1}])
def test_authority_bearing_body_fields_are_refused(
        app, native, bearer, make_user, generator, extra):
    user = make_user("save-extra")
    set_target(user.id)
    headers = bearer(user)
    proposal = _proposals(native, headers)[0]
    response = save(native, headers, proposal, if_none_match="*", **extra)
    assert response.status_code == 400
    assert plans(user.id) == []


def test_expired_proposal_is_refused(app, native, bearer, make_user, generator, monkeypatch):
    user = make_user("save-expired")
    set_target(user.id)
    headers = bearer(user)
    proposal = _proposals(native, headers)[0]
    real = native_plan.time.time
    monkeypatch.setattr(native_plan.time, "time",
                        lambda: real() + native_plan.PROPOSAL_TTL_SECONDS + 60)
    response = save(native, headers, proposal, if_none_match="*")
    assert response.status_code == 400
    assert error_of(response)["code"] == "PLAN_PROPOSAL_EXPIRED"


def test_owner_isolation_for_plans(app, native, bearer, make_user, generator):
    alice = make_user("plan-alice")
    bob = make_user("plan-bob")
    set_target(alice.id)
    save_plan_row(alice.id)
    alice_revision = read(native, bearer(alice))["plan"]["revision"]
    proposal = _proposals(native, bearer(alice))[0]
    # Bob reads nothing of Alice's.
    assert read(native, bearer(bob))["plan"]["state"] == "absent"
    # Alice's proposal token is meaningless for Bob.
    stolen = save(native, bearer(bob), proposal, if_none_match="*")
    assert stolen.status_code == 400
    assert error_of(stolen)["code"] == "INVALID_PLAN_PROPOSAL"
    # Alice's revision is meaningless for Bob's own (absent → after create) plan.
    save_plan_row(bob.id)
    set_target(bob.id)
    bob_proposal = _proposals(native, bearer(bob))[0]
    cross = save(native, bearer(bob), bob_proposal, if_match=quoted(alice_revision))
    assert cross.status_code == 412
    assert [json.loads(r.plan_data)["isim"] for r in plans(alice.id)] == ["Lean plan"]


def test_web_and_native_read_the_same_saved_plan(
        app, client, native, bearer, make_user, login, no_provider):
    user = make_user("plan-parity")
    login("plan-parity")
    save_plan_row(user.id)
    web = client.get("/nutrition-plan/active").get_json()
    mobile = read(native, bearer(user))["plan"]
    assert web["exists"] is True and mobile["state"] == "active"
    assert mobile["name"] == web["plan"]["isim"]
    assert [m["slot"] for m in mobile["meals"]] == [
        k for k in ("kahvalti", "ogle", "aksam", "ara_ogun") if k in web["plan"]]
