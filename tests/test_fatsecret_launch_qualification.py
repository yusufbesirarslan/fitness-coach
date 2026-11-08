"""Launch-boundary probes, not a compliance PASS.

All inputs are synthetic. Strict xfails identify missing restrictions requiring
the founder decision in docs/compliance/fatsecret-boundary-01.md. An xfail is a
release blocker; unrelated errors must fail normally. No runtime gate is added.
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog, UserSession
from app.services import ai_coach, ai_consent, coach_privacy, context_builder
from app.services import foodcache, menu_analysis
from app.services.mobile_log_food import service as logging_service
from app.services.mobile_log_food.commands import ProviderBackedLogFoodCommand

from test_fatsecret_terms_boundary import env_absent, native  # noqa: F401
from app.timeutil import day_key

SENTINEL = 731
SOURCES = ["search", "barcode", "manual", None]
GAP = pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "OPEN FATSECRET LAUNCH BLOCKER: provider/unknown lineage reaches restricted "
    "consumers; material fallback and retention disposition need founder choice"))


def _row(user, source):
    row = MealLog(user_id=user.id, ogun="Lunch", yemekler="Synthetic historical food",
                  kalori=SENTINEL, protein=25, karb=60, yag=15, tarih=day_key(),
                  source=source, created_at=datetime.utcnow() - timedelta(days=2))
    db.session.add(row)
    db.session.commit()
    # ORM default replaces explicit None on INSERT; represent a genuine legacy
    # NULL in a second update. A discovery label is never a licence attestation.
    if source is None:
        row.source = None
        db.session.commit()
    return row


@GAP
@pytest.mark.parametrize("source", SOURCES)
def test_provider_or_unproven_history_excluded_at_coach_tool_boundary(app, make_user, source):
    user = make_user("fs-qual-tool")
    _row(user, source)
    raw = ai_coach._tool_query_fitx_metrics(user.id, "calories", "today")
    projected = coach_privacy.tool_result("query_fitx_metrics", raw)
    assert str(SENTINEL) not in json.dumps(projected)


@GAP
@pytest.mark.parametrize("source", SOURCES)
def test_provider_or_unproven_history_excluded_before_coach_context(app, make_user, source):
    user = make_user("fs-qual-context")
    _row(user, source)
    result = context_builder.fetch_coach_context(
        user.id, question="nutrition", language="en", handoff="nutrition-day")
    assert str(SENTINEL) not in result


@pytest.fixture
def menu_probe(app, make_user, monkeypatch):
    user = make_user("fs-qual-qr")
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    db.session.commit()
    monkeypatch.setattr(foodcache, "_macro_cache", {"per_100g": {}, "per_serving": {}})
    monkeypatch.setattr(foodcache, "_get_redis", lambda: None)
    monkeypatch.setattr(menu_analysis, "redis_client", None)
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **k: {"Main": ["Independent fixture dish"]})
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", lambda names, *a, **k: {
        n: {"calories": 330., "protein": 30., "carbs": 25., "fat": 12.} for n in names})

    def forbidden(*a, **k):
        raise RuntimeError("No live provider call is allowed in this test")

    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", forbidden)
    observed = []
    original_score = menu_analysis._menu_score

    def score(macros, remaining):
        observed.append(dict(remaining))
        return original_score(macros, remaining)

    monkeypatch.setattr(menu_analysis, "_menu_score", score)

    def analyze():
        authority = ai_consent.OperationAuthority(user.id, "menu_extract", 5, "menu_extract")
        with app.test_request_context(), ai_consent.operation_scope(authority):
            return menu_analysis._analyze_menu_text(user.id, "Independent fixture restaurant menu")

    yield user, observed, analyze


@GAP
@pytest.mark.parametrize("source", SOURCES)
def test_unproven_aggregate_never_enters_qr_scorer(menu_probe, source):
    user, observed, analyze = menu_probe
    _row(user, source)
    analyze()
    # The boundary is BEFORE scoring: suppressing a final displayed number or
    # supplying a partial/zero budget is insufficient.
    assert observed == []


@GAP
def test_unknown_current_redis_envelope_never_enters_qr_scorer(menu_probe, monkeypatch):
    _, _, analyze = menu_probe
    key = foodcache._macro_redis_key("per_serving", "Independent fixture dish")
    # This is not the already isolated v1 namespace. A raw v2 value still has
    # no trustworthy producer/input lineage and must not acquire it on read.
    store = {key: json.dumps({"calories": SENTINEL, "protein": 25., "carbs": 60., "fat": 15.})}
    redis = SimpleNamespace(mget=lambda keys: [store.get(k) for k in keys],
        pipeline=lambda transaction=False: SimpleNamespace(setex=lambda *a: None, execute=lambda: []))
    monkeypatch.setattr(foodcache, "_get_redis", lambda: redis)
    original_score = menu_analysis._menu_score
    scored_calories = []

    def score(macros, remaining):
        scored_calories.append(macros["calories"])
        return original_score(macros, remaining)

    monkeypatch.setattr(menu_analysis, "_menu_score", score)
    analyze()
    assert SENTINEL not in scored_calories


@GAP
def test_unbounded_provider_snapshot_is_not_replayed_after_24h(app, make_user, monkeypatch):
    user = make_user("fs-qual-retention")
    snapshot = SimpleNamespace(description="Synthetic provider snapshot", nutrition=SimpleNamespace(
        energy_kcal=Decimal(SENTINEL), protein_g=Decimal(25),
        carbohydrate_g=Decimal(60), fat_g=Decimal(15)))
    monkeypatch.setattr(logging_service, "resolve_provider_food", lambda *a: snapshot)
    command = ProviderBackedLogFoodCommand(
        "fatsecret", "fixture-food", "fixture-serving", Decimal(1), "ogle", "search")
    row, created = logging_service.log_food(user.id, "fs-qual-retained-0001", command)
    assert created
    row.created_at = datetime.utcnow() - timedelta(hours=25)
    db.session.commit()

    def unavailable(*a):
        raise RuntimeError("Provider unavailable; a stale snapshot is not a fallback")

    monkeypatch.setattr(logging_service, "resolve_provider_food", unavailable)
    replay, created = logging_service.log_food(user.id, "fs-qual-retained-0001", command)
    # Current replay serves the indefinite nutrient snapshot without a refresh
    # or verified retention exception. Keep the existing record: never purge.
    assert not created
    assert replay.kalori != SENTINEL


def test_manual_logging_and_history_work_with_provider_unavailable(env_absent, native, raw_client):
    user, headers = native
    response = raw_client.post("/api/v1/nutrition/logs",
        headers={**headers, "Idempotency-Key": "fs-qual-manual-0001"}, json={
            "kind": "manual", "description": "Independent home meal", "slot": "ogle",
            "nutrition": {"energy_kcal": 400, "protein_g": 20,
                          "carbohydrate_g": 40, "fat_g": 10}})
    assert response.status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    assert row.source == "manual" and row.kalori == 400
    # Read through the canonical non-AI history interface, not the ORM alone.
    from app.services.mobile_nutrition.queries import fetch_ledger_entries
    entries = fetch_ledger_entries(user.id, day_key())
    assert len(entries) == 1 and entries[0].description == "Independent home meal"
    assert entries[0].energy_kcal == 400


def test_independent_estimate_menu_without_diary_still_works(menu_probe):
    _, observed, analyze = menu_probe
    result = analyze()
    assert observed and result["coach_picks"]
    assert result["coach_picks"][0]["macro_source"] == "llm"
    assert result["coach_picks"][0]["macros"]["calories"] == 330
