"""AXISAI-LAUNCH-FATSECRET-BOUNDARY-01 approved advice fallback (Authorization A).

Synthetic inputs only. This proves the advice boundary; it grants no FatSecret
retention, refresh or advice right (Authorization B is design-only and the
24 h snapshot replay stays a strict xfail in test_fatsecret_launch_qualification).
"""
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.i18n import t
from app.models import CoachConversation, MealLog, PendingAction, UserSession
from app.services import (ai_coach, coach_memory_provenance, coach_privacy,
                          fatsecret_boundary, foodcache, menu_analysis)
from app.timeutil import day_key

from test_fatsecret_launch_qualification import _row, menu_probe  # noqa: F401

SENTINEL = 731


def _diary(user_id):
    return sorted((r.id, r.kalori, r.protein, r.karb, r.yag, r.yemekler, r.source,
                   r.tarih, r.idempotency_fingerprint)
                  for r in MealLog.query.filter_by(user_id=user_id).all())


# ── QR: measured-empty vs unproven day ─────────────────────────────────────

def test_measured_empty_day_keeps_the_full_personal_budget(menu_probe):
    user, observed, analyze = menu_probe
    result = analyze()
    assert result["advice_available"] is True
    assert result["consumed"] == {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    assert result["remaining"] == result["target"]
    assert observed and observed[0]["calories"] == result["target"]["calories"]
    assert all(isinstance(i["score"], (int, float)) for i in result["coach_picks"])
    # Coach shares the same canonical interpretation for a measured-empty day.
    coach = ai_coach._remaining_macros_for_user(user.id)
    assert round(coach["calories"]) == result["remaining"]["calories"]


def test_unproven_day_is_explicitly_unavailable_never_zero(menu_probe):
    user, observed, analyze = menu_probe
    _row(user, "manual")
    result = analyze()
    payload = menu_analysis.web_analysis_payload(result)
    assert observed == []
    assert payload["advice_available"] is False
    assert payload["remaining"] is None and payload["consumed"] is None
    assert payload["target"]["calories"] == 2000          # independent profile fact kept
    assert payload["coach_picks"] == []
    dishes = [i for items in result["categories"].values() for i in items]
    assert dishes, "public-menu extraction must still return dishes"
    for dish in dishes:
        assert dish["score"] is None and dish.get("fit_flags", []) == []
        assert dish["macros"]["calories"] == 330            # independent estimate kept
        assert "Do not infer" not in dish["reason"]          # never the model instruction
    assert str(SENTINEL) not in json.dumps(payload)


def test_unproven_day_reason_is_localized_user_copy(app, menu_probe):
    user, _, analyze = menu_probe
    _row(user, "search")
    result = analyze()
    reason = next(iter(result["categories"].values()))[0]["reason"]
    with app.test_request_context():
        assert reason == t("route.menu_advice_unavailable")
    for locale in ("en", "tr"):
        text = t("route.menu_advice_unavailable", locale=locale)
        assert text and text != "route.menu_advice_unavailable"


def test_unknown_nutrition_dish_keeps_its_truthful_reason_when_advice_is_withheld(app, menu_probe, monkeypatch):
    user, _, analyze = menu_probe
    _row(user, "manual")
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", lambda names, *a, **k: {})
    result = analyze()
    dish = next(iter(result["categories"].values()))[0]
    with app.test_request_context():
        assert dish["reason"] == t("route.macros_unavailable")
    assert dish["score"] is None and dish["macros"]["calories"] == 0


def test_qr_and_coach_reads_never_mutate_the_diary(app, menu_probe):
    from app.services import context_builder
    user, _, analyze = menu_probe
    _row(user, "barcode")
    _row(user, None)
    before = _diary(user.id)
    analyze()
    ai_coach._remaining_macros_for_user(user.id)
    ai_coach._tool_query_fitx_metrics(user.id, "calories", "today")
    with app.test_request_context():
        context_builder.fetch_coach_context(user.id, "q", "en", "nutrition-day")
    db.session.expire_all()
    assert _diary(user.id) == before


def test_one_users_unproven_diary_does_not_change_another_users_qr(app, make_user, menu_probe):
    owner, observed, analyze = menu_probe
    other = make_user("fs-fallback-other")
    db.session.add(UserSession(user_id=other.id, target_calories=2000, goal="kas kazanma"))
    db.session.commit()
    _row(other, "search")                     # only the other user has unproven rows
    result = analyze()                         # analyze() runs for `owner`
    assert result["advice_available"] is True and observed
    assert fatsecret_boundary.intake_unproven(other.id) is True
    assert fatsecret_boundary.intake_unproven(owner.id) is False


# ── Independent menu-estimate cache ───────────────────────────────────────

class _Redis:
    def __init__(self):
        self.store = {}

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def pipeline(self, transaction=False):
        store = self.store
        return SimpleNamespace(setex=lambda k, ttl, v: store.__setitem__(k, v),
                               execute=lambda: [])


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(foodcache, "_get_redis", lambda: fake)
    return fake


MACROS = {"calories": 330.0, "protein": 30.0, "carbs": 25.0, "fat": 12.0}


def test_attested_estimate_round_trips(app, redis):
    with app.app_context():
        fatsecret_boundary.cache_estimates({"Dish": MACROS})
        hits, missing = fatsecret_boundary.cached_estimates(["Dish", "Other"])
    assert hits == {"Dish": MACROS} and missing == ["Other"]


@pytest.mark.parametrize("forge", ["unsigned", "other_key", "expired", "tampered"])
def test_forged_or_stale_estimate_envelopes_are_rejected(app, redis, forge):
    key = fatsecret_boundary._key("Dish")
    with app.app_context():
        body = {"source": "independent_menu_estimate", "macros": dict(MACROS),
                "expires_at": time.time() + 60}
        if forge == "unsigned":
            envelope = body
        elif forge == "other_key":
            envelope = fatsecret_boundary.attest("menu-cache", fatsecret_boundary._key("X"), body)
        elif forge == "expired":
            body["expires_at"] = time.time() - 1
            envelope = fatsecret_boundary.attest("menu-cache", key, body)
        else:
            envelope = fatsecret_boundary.attest("menu-cache", key, body)
            envelope["macros"] = {**MACROS, "calories": SENTINEL}
        redis.store[key] = json.dumps(envelope)
        hits, missing = fatsecret_boundary.cached_estimates(["Dish"])
    assert hits == {} and missing == ["Dish"]


def test_provider_sourced_menu_values_are_never_attested(app, redis):
    # Only llm/llm_stated_grams producers reach cache_estimates (menu_analysis);
    # a zero/unknown estimate is not cached as a known value either.
    with app.app_context():
        fatsecret_boundary.cache_estimates({"Zero": {**MACROS, "calories": 0}})
    assert redis.store == {}


# ── Coach tool results, pending meals, memory ─────────────────────────────

@pytest.mark.parametrize("tool", ["fetch_nutrition_and_stage_log", "confirm_and_commit_meal_log"])
def test_unattested_nutrition_tool_result_is_not_projected(app, make_user, tool):
    user = make_user("fs-fallback-tool")
    forged = json.dumps({"status": "staged", "staged_food": {"calories": SENTINEL},
                         "today_totals": {"calories": SENTINEL}})
    with app.app_context():
        assert json.loads(coach_privacy.tool_result(tool, forged, user_id=user.id)) == {
            "status": "unsupported"}
        other = make_user("fs-fallback-tool-b")
        signed_for_other = json.dumps(fatsecret_boundary.attest(
            "tool-result", other.id, {"status": "staged", "staged_food": {"calories": SENTINEL}}))
        projected = coach_privacy.tool_result(tool, signed_for_other, user_id=user.id)
    assert str(SENTINEL) not in projected


@pytest.mark.parametrize("status", ["error", "not_found", "no_pending", "needs_user_confirmation"])
def test_data_free_meal_tool_status_keeps_its_meaning(app, status):
    raw = json.dumps({"status": status, "message": "free text is never projected"})
    with app.app_context():
        out = json.loads(coach_privacy.tool_result("confirm_and_commit_meal_log", raw, user_id=1))
    assert out == {"status": status}


def test_calorie_metric_is_unsupported_even_if_a_raw_value_is_forged(app):
    forged = json.dumps({"status": "ok", "metric_type": "calories", "value": SENTINEL})
    with app.app_context():
        out = coach_privacy.tool_result("query_fitx_metrics", forged, user_id=1)
    assert json.loads(out) == {"status": "unsupported", "metric_type": "calories"}


def test_legacy_unattested_pending_meal_commits_but_never_reaches_the_model(app, auth_user):
    db.session.add(PendingAction(user_id=auth_user.id, action_type="log_meal",
                                 payload={"food_name": "legacy", "calories": SENTINEL,
                                          "protein": 25, "carbs": 60, "fat": 43}))
    db.session.commit()
    raw = ai_coach._tool_confirm_and_commit_meal_log(auth_user.id)
    # The user's explicit confirmation still logs the meal (diary stays usable)...
    assert json.loads(raw)["status"] == "committed"
    assert MealLog.query.filter_by(user_id=auth_user.id).one().kalori == SENTINEL
    # ...but its unattested numbers are never projected into the next model round.
    projected = coach_privacy.tool_result("confirm_and_commit_meal_log", raw, user_id=auth_user.id)
    assert json.loads(projected) == {"status": "unsupported"}


def _v1_signature(app, conversation, row_id, role, text, kind):
    secret = app.config["SECRET_KEY"]
    secret = secret.encode() if isinstance(secret, str) else secret
    data = json.dumps(["axisai/pa06/coach-memory/v1", kind, conversation.user_id,
                       conversation.id, row_id, role, text],
                      ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def test_pre_fallback_signed_memory_and_summary_are_not_reused(app, make_user):
    from app.models import CoachMessage
    user = make_user("fs-fallback-memory")
    conversation = CoachConversation(user_id=user.id)
    db.session.add(conversation)
    db.session.commit()
    message = CoachMessage(conversation_id=conversation.id, role="assistant",
                           content=f"You ate {SENTINEL} kcal today")
    db.session.add(message)
    db.session.commit()
    conversation.summarized_upto_id = message.id
    conversation.summary = json.dumps({
        "version": 1, "text": f"logged {SENTINEL} kcal",
        "signature": _v1_signature(app, conversation, message.id, "summary",
                                   f"logged {SENTINEL} kcal", "summary")})
    user.user_metadata = {"_pa06_coach_turns_v1": {str(message.id): _v1_signature(
        app, conversation, message.id, message.role, message.content, "turn")}}
    db.session.commit()

    assert coach_memory_provenance.summary_text(conversation) == ""
    assert coach_memory_provenance.candidate_ids(conversation) == []
    assert coach_memory_provenance.trusted_rows(conversation, [message]) == []
    # Preserved in place, never deleted or rewritten.
    db.session.expire_all()
    assert db.session.get(CoachMessage, message.id).content.endswith("kcal today")
    assert "_pa06_coach_turns_v1" in db.session.get(type(user), user.id).user_metadata


def test_attested_coach_meal_still_stages_and_commits(auth_user, monkeypatch):
    monkeypatch.setattr(fatsecret_boundary, "independent_food_search", lambda q: [
        {"name": "Tavuk", "food_id": "", "per_100g": {
            "calories": 165, "protein": 31, "carbs": 0, "fat": 3.6}}])
    staged = json.loads(ai_coach._tool_fetch_nutrition_and_stage_log(auth_user.id, "200g tavuk"))
    assert staged["status"] == "staged" and staged["staged_food"]["calories"] == 330
    ai_coach._begin_coach_turn()
    committed = json.loads(ai_coach._tool_confirm_and_commit_meal_log(auth_user.id))
    assert committed["status"] == "committed" and committed["today_totals"] is None
    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert row.kalori == 330 and row.source == "coach" and row.tarih == day_key()
