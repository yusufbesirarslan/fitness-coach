"""FatSecret 24h server retention (AXISAI-FATSECRET-RETENTION-MVP-PHASE1-01).

Written clarification, FatSecret Platform API Support, 2026-11-09: raw per-item
nutrient values at most 24h on the server; food IDs and aggregated meal/daily
totals indefinitely. Provider names/serving text are treated as 24h content.

Every test is deterministic: time is passed explicitly to the expiry pass, the
provider is a stub, Redis is an in-memory fake. No network.

    python -m pytest tests/test_fatsecret_retention_phase1.py -q
"""
import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (BarcodeFoodCache, CustomMeal, CustomMealItem, MealLog,
                        MealLogRetainedTotal)
from app.services import (fatsecret_boundary, foodcache, mobile_auth,
                          mobile_food_discovery, provider_retention)
from app.services.nutrition_native import history
from app.timeutil import app_today, day_key

ROOT = Path(__file__).resolve().parents[1]
LOG = "/api/v1/nutrition/logs"
HISTORY = "/api/v1/nutrition/history"
DIARY = "/api/v1/nutrition/diary/today"
DAY_VIEW = "/api/v1/nutrition/day-view"
H = timedelta(hours=1)

FOOD = {
    "provider": "fatsecret", "food_id": "food-1", "name": "Provider Oats",
    "brand": "", "servings": [{
        "serving_id": "serving-1", "description": "1 cup",
        "metric_mass": {"amount": 50.0, "unit": "g"},
        "nutrition": {"energy_kcal": 120.0, "protein_g": 4.0,
                      "carbohydrate_g": 20.0, "fat_g": 3.0},
        "nutrition_per_100g": {"energy_kcal": 240.0, "protein_g": 8.0,
                               "carbohydrate_g": 40.0, "fat_g": 6.0},
    }],
}


# ── fixtures ────────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self):
        self.data, self.ttl, self.sets = {}, {}, []

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        self.sets.append((key, ex, nx))
        if nx and key in self.data:
            return None
        self.data[key], self.ttl[key] = value, ex
        return True

    def scan_iter(self, match=None, count=None):
        prefix = match.rstrip("*")
        return iter([k for k in list(self.data) if k.startswith(prefix)])

    def unlink(self, *keys):
        removed = sum(1 for k in keys if self.data.pop(k, None) is not None)
        return removed


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(foodcache, "_get_redis", lambda: fake)
    return fake


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def servings(food_id):
        calls.append(food_id)
        return dict(FOOD, food_id=food_id)

    monkeypatch.setattr(mobile_food_discovery, "servings", servings)
    return calls


@pytest.fixture
def provider_down(monkeypatch):
    calls = []

    def servings(food_id):
        calls.append(food_id)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(mobile_food_discovery, "servings", servings)
    return calls


@pytest.fixture
def device(monkeypatch):
    """``device(user, name)`` → Bearer headers of one of the user's devices."""
    credentials = {}

    def authenticate(raw):
        user = credentials.get(raw)
        if user is None:
            raise mobile_auth.MobileAuthFailure("AUTH_SESSION_EXPIRED", 401, False, "unknown")
        return mobile_auth.MobilePrincipal(user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def headers(user, name="phone", key=None):
        credential = f"opaque-{user.id}-{name}"
        credentials[credential] = user
        result = {"Authorization": f"Bearer {credential}"}
        if key:
            result["Idempotency-Key"] = key
        return result
    return headers


@pytest.fixture
def native(app):
    from flask.testing import FlaskClient
    return FlaskClient(app, app.response_class)


def provider_log(native, headers, quantity=1, slot="kahvalti"):
    return native.post(LOG, headers=headers, json={
        "kind": "provider_backed", "provider": "fatsecret", "food_id": "food-1",
        "serving_id": "serving-1", "quantity": quantity, "slot": slot,
        "discovery_source": "search"})


def manual_log(native, headers, description="Ev yemeği", kcal=420.0, slot="ogle"):
    return native.post(LOG, headers=headers, json={
        "kind": "manual", "description": description, "slot": slot,
        "nutrition": {"energy_kcal": kcal, "protein_g": 25, "carbohydrate_g": 40, "fat_g": 14}})


def age(row, hours, day=None):
    """Move one stored row back in time (acquisition + creation together)."""
    when = datetime.utcnow() - timedelta(hours=hours)
    row.created_at = when
    if row.provider_acquired_at is not None:
        row.provider_acquired_at = when
    if day is not None:
        row.tarih = day
    db.session.commit()


def history_day(native, headers, day):
    body = native.get(HISTORY, headers=headers, query_string={"limit": "14"}).get_json()
    return next(d for d in body["days"] if d["date"] == day)


def stored_day_total(user_id, day):
    rows = MealLog.query.filter_by(user_id=user_id, tarih=day).all()
    retained = provider_retention.retained_for_day(user_id, day)
    return round(sum(r.kalori or 0 for r in rows) + retained["energy_kcal"], 2)


def manual_row(user_id, day, kcal=500.0, source="manual", text="Typed by the user"):
    row = MealLog(user_id=user_id, ogun="Öğle", yemekler=text, kalori=kcal, protein=20.0,
                  karb=60.0, yag=10.0, tarih=day, source=source,
                  created_at=datetime.utcnow() - timedelta(days=40))
    db.session.add(row)
    db.session.commit()
    return row


# ── provenance on write; the hold scope ─────────────────────────────────────

def test_covered_provider_log_is_open_and_stamped_with_acquisition_time(
        app, native, device, make_user, provider):
    user = make_user("ret-write")
    before = datetime.utcnow()
    response = provider_log(native, device(user, key="k-write-0001"), quantity=1.5)
    assert response.status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    assert row.source == "search" and row.kalori == 180.0
    assert row.provider_items == [{"provider": "fatsecret", "food_id": "food-1",
                                   "serving_id": "serving-1", "quantity": "1.5"}]
    assert before <= row.provider_acquired_at <= datetime.utcnow()
    assert row.provider_expired_at is None


def test_uncovered_paths_stay_held_by_default(client, auth_user, monkeypatch):
    from app.services import barcode as barcode_svc
    monkeypatch.setattr(barcode_svc, "get_barcode_product",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no lookup")))
    held = client.post("/api/food/barcode/add", json={
        "barcode": "5000159407236", "meal": "Ara Öğün", "serving_id": "s", "serving_quantity": 1})
    assert held.status_code == 503
    assert held.get_json()["error"] == "provider_logging_unavailable"
    assert fatsecret_boundary.uncovered_provider_persistence_allowed() is False


# ── 24h expiry: before/after, totals, counts ────────────────────────────────

def _yesterday():
    return (app_today() - timedelta(days=1)).isoformat()


def test_before_deadline_nothing_moves_and_values_are_published(
        app, native, device, make_user, provider):
    user = make_user("ret-fresh")
    headers = device(user, key="k-fresh-0001")
    assert provider_log(native, headers).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 21, day=_yesterday())
    result = provider_retention.expire_due()
    assert result.folded_entries == 0
    db.session.refresh(row)
    assert row.kalori == 120.0 and row.provider_expired_at is None
    meal = history_day(native, device(user), _yesterday())["meals"][0]
    assert meal["nutrition"]["energy_kcal"] == 120.0
    assert meal["description"] == "Provider Oats (1x 1 cup)"


def test_after_deadline_values_move_into_the_day_total_without_new_entries(
        app, native, device, make_user, provider, redis):
    user = make_user("ret-fold")
    day = _yesterday()
    assert provider_log(native, device(user, key="k-fold-0001")).status_code == 201
    assert provider_log(native, device(user, key="k-fold-0002"), quantity=2,
                        slot="aksam").status_code == 201
    for row in MealLog.query.filter_by(user_id=user.id).all():
        age(row, 23, day=day)
    typed = manual_row(user.id, day, kcal=333.0)

    headers = device(user)
    before = history_day(native, headers, day)
    stored_before = stored_day_total(user.id, day)
    counts_before = MealLog.query.filter_by(user_id=user.id).count()

    result = provider_retention.expire_due()
    assert result.folded_entries == 2 and result.overdue == 0

    after = history_day(native, headers, day)
    assert after["totals"] == before["totals"]                  # day total conserved
    assert stored_day_total(user.id, day) == stored_before
    assert MealLog.query.filter_by(user_id=user.id).count() == counts_before   # no phantom row
    assert len(after["meals"]) == len(before["meals"])        # no inflated meal count
    folded = [m for m in after["meals"] if m["source"] == "search"]
    assert all(m["nutrition"] == {"energy_kcal": None, "protein_g": None,
                                  "carbohydrate_g": None, "fat_g": None} for m in folded)
    assert {m["slot"] for m in folded} == {"kahvalti", "aksam"}   # identity/slot kept
    db.session.refresh(typed)
    assert (typed.kalori, typed.yemekler, typed.provider_acquired_at) == (
        333.0, "Typed by the user", None)                    # manual untouched
    retained = MealLogRetainedTotal.query.filter_by(user_id=user.id, tarih=day).one()
    assert (retained.energy_kcal, retained.item_count) == (360.0, 2)
    # Provider name/serving text left storage; IDs and quantity stayed.
    for row in MealLog.query.filter_by(user_id=user.id, source="search").all():
        assert "Provider Oats" not in row.yemekler and "cup" not in row.yemekler
        assert row.provider_items[0]["food_id"] == "food-1"


def test_expiry_is_idempotent_and_never_double_counts(app, native, device, make_user, provider):
    user = make_user("ret-idem")
    assert provider_log(native, device(user, key="k-idem-0001")).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 23, day=_yesterday())
    first = provider_retention.expire_due()
    second = provider_retention.expire_due()
    assert (first.folded_entries, second.folded_entries) == (1, 0)
    retained = MealLogRetainedTotal.query.filter_by(user_id=user.id).one()
    assert (retained.energy_kcal, retained.item_count) == (120.0, 1)


@pytest.mark.parametrize("days_back", [1, 7, 30])
def test_history_at_25h_7d_30d_keeps_entries_and_totals(
        app, native, device, make_user, provider, redis, days_back):
    user = make_user(f"ret-hist-{days_back}")
    day = (app_today() - timedelta(days=days_back)).isoformat()
    assert provider_log(native, device(user, key=f"k-hist-{days_back:04d}")).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 24 * days_back + 1, day=day)
    manual_row(user.id, day, kcal=200.0)
    provider_retention.expire_due()
    found = history_day(native, device(user), day)
    assert found["totals"]["energy_kcal"] == 320.0
    assert len(found["meals"]) == 2
    folded = next(m for m in found["meals"] if m["source"] == "search")
    assert folded["nutrition"]["energy_kcal"] is None
    assert folded["description"] == "Provider Oats (1x 1 cup)"   # live name by stored ID


def test_cross_device_history_is_server_state(app, native, device, make_user, provider, redis):
    user = make_user("ret-devices")
    assert provider_log(native, device(user, "phone", key="k-dev-0001")).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 30, day=_yesterday())
    provider_retention.expire_due()
    phone = history_day(native, device(user, "phone"), _yesterday())
    new_phone = history_day(native, device(user, "replacement"), _yesterday())
    assert phone == new_phone
    assert phone["totals"]["energy_kcal"] == 120.0


def test_today_edge_fold_conserves_day_view_diary_and_web_totals(
        app, native, device, make_user, provider, client, login):
    user = make_user("ret-edge")
    headers = device(user, key="k-edge-0001")
    assert provider_log(native, headers).status_code == 201
    manual_row(user.id, day_key(), kcal=100.0)
    row = MealLog.query.filter_by(user_id=user.id, source="search").one()
    age(row, 23)  # logged just after midnight: due before its own day ends
    view_before = native.get(DAY_VIEW, headers=device(user)).get_json()
    diary_before = native.get(DIARY, headers=device(user)).get_json()
    provider_retention.expire_due()
    view_after = native.get(DAY_VIEW, headers=device(user)).get_json()
    diary_after = native.get(DIARY, headers=device(user)).get_json()
    assert view_after["intake"] == view_before["intake"]       # totals + meal_count
    assert diary_after["totals"] == diary_before["totals"]
    assert len(diary_after["meals"]) == len(diary_before["meals"])
    # Coach/QR provenance boundary unchanged: rows exist, advice stays withheld.
    assert fatsecret_boundary.intake_unproven(user.id) is True


def test_due_values_are_withheld_at_read_time_even_before_the_pass(
        app, native, device, make_user, provider, redis):
    """Fail closed: a missed tick never publishes values past the cutoff."""
    user = make_user("ret-failclosed")
    assert provider_log(native, device(user, key="k-fc-0001")).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 22.5, day=_yesterday())          # past 22h, no pass has run
    day = history_day(native, device(user), _yesterday())
    assert day["meals"][0]["nutrition"]["energy_kcal"] is None
    assert day["totals"]["energy_kcal"] == 120.0              # still conserved
    db.session.refresh(row)
    assert row.kalori == 120.0                                # nothing mutated by a read


def test_schedule_bounds_and_zero_traffic_enqueue(app, make_user, provider, monkeypatch):
    import worker
    from app.jobs import tasks
    assert provider_retention.EXPIRE_AFTER == timedelta(hours=22)
    assert worker.RETENTION_INTERVAL_SECONDS == 300
    # worst case = cutoff + one interval + the 240 s job timeout, well under 24h
    assert (provider_retention.EXPIRE_AFTER
            + timedelta(seconds=worker.RETENTION_INTERVAL_SECONDS + 240)) < timedelta(hours=23)

    enqueued = []
    queue = SimpleNamespace(enqueue_call=lambda func, **kw: enqueued.append((func, kw)))
    monkeypatch.setattr("app.jobs.get_queue", lambda: queue)
    last = worker._retention_tick(-300.0, 0.0)
    assert worker._retention_tick(last, 100.0) == last        # inside the interval
    assert worker._retention_tick(last, 300.0) == 300.0
    assert [f for f, _ in enqueued] == [tasks.expire_provider_nutrition] * 2

    # The enqueued job alone expires a due row; no HTTP request is involved.
    user = make_user("ret-zero-traffic")
    db.session.add(MealLog(
        user_id=user.id, ogun="Öğle", yemekler="Provider Oats (1x 1 cup)", kalori=120.0,
        protein=4.0, karb=20.0, yag=3.0, tarih=_yesterday(), source="search",
        created_at=datetime.utcnow() - 72 * H, provider_acquired_at=datetime.utcnow() - 72 * H,
        provider_items=[provider_retention.provider_component("food-1", "serving-1", 1)]))
    db.session.commit()
    assert tasks.expire_provider_nutrition()["folded"] == 1


def test_queue_unavailable_never_runs_the_pass_inline_in_the_worker_parent(monkeypatch):
    import worker
    monkeypatch.setattr("app.jobs.get_queue", lambda: None)
    monkeypatch.setattr("app.jobs.tasks.expire_provider_nutrition",
                        lambda: (_ for _ in ()).throw(AssertionError("ran inline")))
    assert worker._retention_tick(-300.0, 0.0) == 0.0


# ── live names: cache cannot extend the lifetime; outage ────────────────────

def test_name_cache_is_set_once_and_a_hit_never_refreshes_ttl(
        app, native, device, make_user, provider, redis):
    user = make_user("ret-cache")
    assert provider_log(native, device(user, key="k-cache-0001")).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 30, day=_yesterday())
    provider_retention.expire_due()
    provider.clear()
    history_day(native, device(user), _yesterday())
    history_day(native, device(user), _yesterday())
    assert provider == ["food-1"]                              # one live request
    key = provider_retention.NAME_CACHE_PREFIX + "food-1"
    assert [s for s in redis.sets if s[0] == key] == [
        (key, int(timedelta(hours=22).total_seconds()), True)]  # NX, fixed TTL, once
    cached = json.loads(redis.data[key])
    assert set(cached) == {"name", "servings"}                 # names only, no nutrients


def test_provider_outage_renders_a_neutral_label_and_keeps_totals(
        app, native, device, make_user, provider, redis, monkeypatch):
    user = make_user("ret-outage")
    assert provider_log(native, device(user, key="k-out-0001")).status_code == 201
    assert provider_log(native, device(user, key="k-out-0002"), slot="aksam").status_code == 201
    for row in MealLog.query.filter_by(user_id=user.id).all():
        age(row, 30, day=_yesterday())
    provider_retention.expire_due()
    calls = []
    monkeypatch.setattr(mobile_food_discovery, "servings",
                        lambda food_id: calls.append(food_id) or (_ for _ in ()).throw(RuntimeError()))
    day = history_day(native, device(user), _yesterday())
    assert {m["description"] for m in day["meals"]} == {"FatSecret (1x)"}
    assert day["totals"]["energy_kcal"] == 240.0
    assert len(calls) == 1                                      # stop asking after a failure


# ── delete / replay / Repeat ────────────────────────────────────────────────

def test_deleting_a_summarized_entry_is_a_defined_409(app, native, device, make_user, provider):
    user = make_user("ret-delete")
    headers = device(user, key="k-del-0001")
    created = provider_log(native, headers).get_json()["meal"]
    row = MealLog.query.filter_by(user_id=user.id).one()
    age(row, 23)
    provider_retention.expire_due()
    meal = native.get(DIARY, headers=device(user)).get_json()["meals"][0]
    response = native.delete(f"{LOG}/{created['id']}",
                             headers={**device(user), "If-Match": f'"{meal["revision"]}"'})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "DIARY_ENTRY_SUMMARIZED"
    assert MealLog.query.filter_by(user_id=user.id).count() == 1


def test_replay_after_expiry_returns_the_same_entry_without_values(
        app, native, device, make_user, provider):
    user = make_user("ret-replay")
    headers = device(user, key="k-replay-0001")
    first = provider_log(native, headers).get_json()["meal"]
    age(MealLog.query.filter_by(user_id=user.id).one(), 23)
    provider_retention.expire_due()
    replay = provider_log(native, headers)
    assert replay.status_code == 200
    assert replay.get_json()["meal"]["id"] == first["id"]
    assert replay.get_json()["meal"]["nutrition"]["energy_kcal"] is None
    assert MealLog.query.filter_by(user_id=user.id).count() == 1


def test_repeat_of_a_fresh_provider_entry_keeps_the_original_deadline(
        app, native, device, make_user, provider):
    user = make_user("ret-repeat-fresh")
    meal = provider_log(native, device(user, key="k-rep-0001")).get_json()["meal"]
    original = MealLog.query.filter_by(user_id=user.id).one()
    acquired = original.provider_acquired_at
    copy = native.post(LOG, headers=device(user, key="k-rep-0002"), json={
        "kind": "manual", "description": meal["description"], "slot": "ogle",
        "nutrition": {k: meal["nutrition"][k] for k in
                      ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")}})
    assert copy.status_code == 201
    row = MealLog.query.filter(MealLog.user_id == user.id, MealLog.id != original.id).one()
    assert row.source == "manual" and row.provider_acquired_at == acquired
    assert row.provider_items == original.provider_items


def test_legacy_provider_rows_never_publish_values_and_a_copy_cannot_escape(
        app, native, device, make_user, provider):
    user = make_user("ret-repeat-legacy")
    day = (app_today() - timedelta(days=50)).isoformat()
    legacy = manual_row(user.id, day, kcal=410.0, source="search", text="Legacy Granola (1x bar)")
    typed = manual_row(user.id, day, kcal=410.0, source="manual", text="Uncertain manual row")
    found = history_day(native, device(user), day)
    by_source = {m["source"]: m for m in found["meals"]}
    assert by_source["search"]["nutrition"]["energy_kcal"] is None   # iOS cannot Repeat it
    assert by_source["search"]["description"] == "FatSecret"         # no ID: placeholder (D2)
    assert by_source["manual"]["nutrition"]["energy_kcal"] == 410.0  # D4: untouched
    assert found["totals"]["energy_kcal"] == 820.0                   # totals conserved

    # Server backstop: an exact copy (an older client) is due immediately.
    copy = native.post(LOG, headers=device(user, key="k-leg-0001"), json={
        "kind": "manual", "description": "Legacy Granola (1x bar)", "slot": "ogle",
        "nutrition": {"energy_kcal": 410.0, "protein_g": 20.0, "carbohydrate_g": 60.0, "fat_g": 10.0}})
    assert copy.status_code == 201
    assert copy.get_json()["meal"]["nutrition"]["energy_kcal"] is None
    new = MealLog.query.filter_by(user_id=user.id, tarih=day_key()).one()
    assert new.provider_items == [provider_retention.LEGACY_COMPONENT]
    assert provider_retention.expire_due().folded_entries == 1
    db.session.refresh(new)
    assert new.kalori is None
    assert stored_day_total(user.id, day_key()) == 410.0
    for row in (legacy, typed):                                       # never mutated
        db.session.refresh(row)
        assert row.kalori == 410.0 and row.provider_expired_at is None


def test_a_genuinely_typed_entry_is_never_classified_as_provider(app, native, device, make_user, provider):
    user = make_user("ret-typed")
    provider_log(native, device(user, key="k-typed-0001"))
    assert manual_log(native, device(user, key="k-typed-0002"), kcal=121.0).status_code == 201
    row = MealLog.query.filter_by(user_id=user.id, source="manual").one()
    assert row.provider_acquired_at is None and row.provider_items is None


# ── saved meals (web diary builder) ─────────────────────────────────────────

def _saved_meal(user_id, *, acquired_hours_ago, day=None):
    meal = CustomMeal(user_id=user_id, meal_name="Öğle", date_key=day or app_today().isoformat())
    db.session.add(meal)
    db.session.flush()
    item = CustomMealItem(
        custom_meal_id=meal.id, food_name="Provider Oats", grams=75.0, calories=180.0,
        protein=6.0, carbs=30.0, fat=4.5, fatsecret_food_id="food-1", serving_id="serving-1",
        serving_description="1 cup", serving_quantity=1.5, per_100g_calories=240.0,
        per_100g_protein=8.0, per_100g_carbs=40.0, per_100g_fat=6.0,
        provider_acquired_at=datetime.utcnow() - timedelta(hours=acquired_hours_ago))
    typed = CustomMealItem(custom_meal_id=meal.id, food_name="Ev salatası", grams=100.0,
                           calories=90.0, protein=2.0, carbs=8.0, fat=6.0)
    db.session.add_all([item, typed])
    db.session.commit()
    return meal, item, typed


def test_saved_meal_scrub_keeps_quantity_grams_and_ids(app, make_user):
    user = make_user("ret-saved")
    _, item, typed = _saved_meal(user.id, acquired_hours_ago=23)
    assert provider_retention.expire_due().scrubbed_items == 1
    db.session.refresh(item)
    assert (item.fatsecret_food_id, item.serving_id, item.serving_quantity, item.grams) == (
        "food-1", "serving-1", 1.5, 75.0)
    assert (item.calories, item.per_100g_calories, item.food_name, item.serving_description) == (
        0, None, "FatSecret", None)
    db.session.refresh(typed)
    assert (typed.food_name, typed.calories) == ("Ev salatası", 90.0)


def test_saved_meal_reuse_resolves_live_after_scrub(app, client, auth_user, provider):
    meal, item, _ = _saved_meal(auth_user.id, acquired_hours_ago=23)
    provider_retention.expire_due()
    response = client.post(f"/api/diary/meal/{meal.id}/log")
    assert response.status_code == 200
    logged = MealLog.query.filter_by(user_id=auth_user.id, source="diary").one()
    assert logged.kalori == round(180.0 + 90.0, 1)                   # 1.5 x 120 live + typed
    assert logged.provider_acquired_at is not None
    assert [c.get("food_id") or c.get("text") for c in logged.provider_items] == [
        "food-1", "Ev salatası (100g)"]
    db.session.refresh(item)
    assert item.provider_expired_at is None and item.food_name == "Provider Oats"


def test_saved_meal_reuse_fails_closed_when_the_provider_is_down(app, client, auth_user, provider_down):
    meal, _, _ = _saved_meal(auth_user.id, acquired_hours_ago=23)
    provider_retention.expire_due()
    response = client.post(f"/api/diary/meal/{meal.id}/log")
    assert response.status_code == 503
    assert response.get_json() == {"error": "food_provider_unavailable", "retryable": True}
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    assert not db.session.get(CustomMeal, meal.id).is_logged


def _add_provider_item(meal_id, food_id="food-1"):
    db.session.add(CustomMealItem(
        custom_meal_id=meal_id, food_name="Provider Oats", grams=50.0, calories=120.0,
        protein=4.0, carbs=20.0, fat=3.0, fatsecret_food_id=food_id, serving_id="serving-1",
        serving_description="1 cup", serving_quantity=1.0,
        provider_acquired_at=datetime.utcnow()))
    db.session.commit()


def test_two_provider_foods_are_a_meal_summary_and_keep_their_total(app, client, auth_user, provider):
    meal, _, _ = _saved_meal(auth_user.id, acquired_hours_ago=1)
    _add_provider_item(meal.id)
    assert client.post(f"/api/diary/meal/{meal.id}/log").status_code == 200
    logged = MealLog.query.filter_by(user_id=auth_user.id, source="diary").one()
    logged.provider_acquired_at = datetime.utcnow() - 23 * H
    db.session.commit()
    result = provider_retention.expire_due()
    assert (result.folded_entries, result.relabelled_summaries) == (0, 1)
    db.session.refresh(logged)
    assert logged.kalori == 390.0                                    # summary kept
    assert logged.yemekler == "FatSecret (1.5x), Ev salatası (100g), FatSecret (1x)"


def test_one_provider_food_plus_typed_items_is_per_item_and_folds(app, client, auth_user, provider):
    """Its total minus the typed items' stored values would reveal the
    provider item's own value, so it is not a meal summary."""
    meal, _, _ = _saved_meal(auth_user.id, acquired_hours_ago=1)
    assert client.post(f"/api/diary/meal/{meal.id}/log").status_code == 200
    logged = MealLog.query.filter_by(user_id=auth_user.id, source="diary").one()
    logged.provider_acquired_at = datetime.utcnow() - 23 * H
    logged.tarih = _yesterday()
    db.session.commit()
    before = stored_day_total(auth_user.id, _yesterday())
    assert provider_retention.expire_due().folded_entries == 1
    db.session.refresh(logged)
    assert logged.kalori is None
    assert logged.yemekler == "FatSecret (1.5x), Ev salatası (100g)"  # typed text kept
    assert stored_day_total(auth_user.id, _yesterday()) == before == 270.0


def test_repeat_of_a_summary_from_history_carries_no_provider_name(
        app, native, device, make_user, provider, redis):
    """Review P1: a published summary keeps its total (Repeat stays available),
    so its description must hold no provider text a copy could store."""
    user = make_user("ret-repeat-summary")
    day = _yesterday()
    components = [provider_retention.provider_component("food-1", "serving-1", 1),
                  provider_retention.provider_component("food-2", "serving-1", 2)]
    db.session.add(MealLog(
        user_id=user.id, ogun="Öğle", yemekler="Provider Oats (1x 1 cup), Provider Oats (2x 1 cup)",
        kalori=360.0, protein=12.0, karb=60.0, yag=9.0, tarih=day, source="diary",
        created_at=datetime.utcnow() - 30 * H, provider_acquired_at=datetime.utcnow() - 30 * H,
        provider_items=components))
    db.session.commit()
    for when in ("before_pass", "after_pass"):
        if when == "after_pass":
            provider_retention.expire_due()
        meal = history_day(native, device(user), day)["meals"][0]
        assert meal["nutrition"]["energy_kcal"] == 360.0             # Repeat available
        assert "Provider Oats" not in meal["description"]
    copy = native.post(LOG, headers=device(user, key="k-sum-0001"), json={
        "kind": "manual", "description": meal["description"], "slot": "ogle",
        "nutrition": {k: meal["nutrition"][k] for k in
                      ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")}})
    assert copy.status_code == 201
    stored = MealLog.query.filter_by(user_id=user.id, tarih=day_key()).one()
    assert "Provider Oats" not in stored.yemekler and "cup" not in stored.yemekler


# ── web surfaces ────────────────────────────────────────────────────────────

def test_web_history_and_today_conserve_totals(app, client, auth_user, redis, provider):
    day = _yesterday()
    db.session.add(MealLog(
        user_id=auth_user.id, ogun="Öğle", yemekler="Provider Oats (1x 1 cup)", kalori=120.0,
        protein=4.0, karb=20.0, yag=3.0, tarih=day, source="search",
        created_at=datetime.utcnow() - 30 * H, provider_acquired_at=datetime.utcnow() - 30 * H,
        provider_items=[provider_retention.provider_component("food-1", "serving-1", 1)]))
    db.session.commit()
    before = client.get("/meal-log/history").get_json()
    provider_retention.expire_due()
    after = client.get("/meal-log/history").get_json()
    assert [d["totals"] for d in after] == [d["totals"] for d in before]
    assert after[0]["meals"][0]["kalori"] is None


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_web_meal_review_follows_the_advice_boundary(app, client, auth_user, monkeypatch):
    """Production keeps this route closed by D1 consent; when it opens, the
    #497 boundary still withholds advice on unproven intake (founder D3)."""
    from app.blueprints.nutrition import meallog
    monkeypatch.setattr(meallog, "_openai_chat",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    manual_row(auth_user.id, day_key(), kcal=640.0)
    response = client.post("/meal-log/review")
    assert response.status_code == 200
    body = response.get_json()
    assert body["advice_available"] is False and body["total_calories"] == 640
    assert body["review"]


# ── legacy dry run (D5) and the Redis v1 procedure (D6) ─────────────────────

def _seed_db4_shape(make_user):
    owner = make_user("ret-db4")
    other = make_user("ret-db4-other")
    for n, day in enumerate(("2026-09-12", "2026-09-20", "2026-09-27")):
        manual_row(owner.id, day, kcal=300.0 + n, source="search", text=f"Provider {n}")
        manual_row(owner.id, day, kcal=50.0, source="ai_plan", text="Plan meal")
    for n in range(54):
        manual_row(other.id, "2026-07-01", kcal=10.0, source="manual", text=f"typed {n}")
    for n in range(9):
        manual_row(owner.id, "2026-08-01", kcal=600.0, source="diary", text=f"meal {n}")
    for m in range(13):
        meal = CustomMeal(user_id=owner.id, meal_name=f"M{m}", date_key="2026-08-01")
        db.session.add(meal)
        db.session.flush()
        for _ in range(2):
            db.session.add(CustomMealItem(custom_meal_id=meal.id, food_name="P", grams=50.0,
                                          calories=100.0, fatsecret_food_id=f"f{m}"))
        db.session.add(CustomMealItem(custom_meal_id=meal.id, food_name="typed", grams=10.0,
                                      calories=5.0, fatsecret_food_id=""))
    db.session.commit()


def _snapshot():
    return (sorted((r.id, r.kalori, r.yemekler, r.provider_acquired_at) for r in MealLog.query.all()),
            sorted((i.id, i.calories, i.food_name) for i in CustomMealItem.query.all()),
            MealLogRetainedTotal.query.count())


def test_legacy_dry_run_matches_db4_exactly_and_never_mutates(app, make_user):
    _seed_db4_shape(make_user)
    before = _snapshot()
    report = provider_retention.legacy_dry_run()
    assert _snapshot() == before
    assert report["meal_log_provider_rows"] == 3
    assert report["custom_meal_item_provider_items"] == 26     # '' IDs excluded
    assert report["custom_meal_item_provider_meals"] == 13
    assert report["meal_log_user_day_buckets"] == 3
    assert report["meal_log_rows_placeholder_only"] == 3
    assert report["meal_log_macro_sums_to_move"]["energy_kcal"] == 903.0
    assert report["day_totals_conserved"] is True
    assert report["day_totals_before"] == report["day_totals_after"]
    assert report["untouched_meal_log_by_source"] == {"manual": 54, "diary": 9, "ai_plan": 3}


def test_legacy_dry_run_cli_matches_or_holds(app, make_user):
    _seed_db4_shape(make_user)
    runner = app.test_cli_runner()
    ok = runner.invoke(args=["fatsecret-retention-legacy-dry-run",
                             "--expect-meal-log", "3", "--expect-items", "26"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["RESULT"] == "MATCH"
    held = runner.invoke(args=["fatsecret-retention-legacy-dry-run",
                               "--expect-meal-log", "4", "--expect-items", "26"])
    assert held.exit_code == 2
    assert json.loads(held.output)["RESULT"] == "HOLD"
    assert "--apply" not in runner.invoke(args=["fatsecret-retention-legacy-dry-run", "--help"]).output


def test_redis_v1_procedure_counts_then_deletes_only_the_exact_v1_scope(app, redis):
    for n in range(3):
        redis.data[f"food:macros:v1:per_100g:{n}"] = "{}"
    redis.data["food:macros:v2:per_100g:x"] = "{}"
    redis.data["food:independent-menu:v1:per_serving:y"] = "{}"
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["fatsecret-retention-redis-v1"])
    assert json.loads(dry.output)["keys"] == 3 and len(redis.data) == 5
    wrong = runner.invoke(args=["fatsecret-retention-redis-v1", "--apply", "--expect", "2"])
    assert wrong.exit_code == 2 and len(redis.data) == 5
    done = runner.invoke(args=["fatsecret-retention-redis-v1", "--apply", "--expect", "3"])
    assert json.loads(done.output)["deleted"] == 3
    assert sorted(redis.data) == ["food:independent-menu:v1:per_serving:y",
                                  "food:macros:v2:per_100g:x"]


# ── structural: no FatSecret value reaches the shared macro caches ──────────

def _callers_of(path, name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    found = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == name):
                    found.add(fn.name)
    return found


def test_shared_macro_cache_writers_are_only_the_builtin_table_and_the_llm():
    """``food:macros:v2`` and L1 never hold FatSecret values.

    The only two writers are the built-in static table and the independent LLM
    estimate; FatSecret search caches nothing but a digest → food_id map.
    """
    writers = {}
    for path in ROOT.joinpath("app").rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        callers = _callers_of(rel, "_cache_macros")
        if callers:
            writers[rel] = callers
    assert writers == {"app/services/fatsecret.py": {"_food_search_static"},
                       "app/services/ai_nutrition.py": {"_food_search_llm"}}


def test_food_id_map_holds_no_provider_text():
    foodcache._food_id_cache.clear()
    foodcache._cache_food_id("Provider Oats", "food-1")
    assert list(foodcache._food_id_cache) == [foodcache._food_id_key("provider oats")]
    assert "Provider Oats".lower() not in foodcache._food_id_cache
    assert foodcache._get_cached_food_id("PROVIDER OATS") == "food-1"
    foodcache._food_id_cache.clear()


def test_account_erasure_covers_the_retained_total(app, make_user):
    from app.cli import _user_child_models
    assert MealLogRetainedTotal in tuple(_user_child_models())
    assert BarcodeFoodCache.__tablename__ == "barcode_food_cache"
