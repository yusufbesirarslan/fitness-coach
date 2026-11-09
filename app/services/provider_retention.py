"""FatSecret server retention: the 24-hour per-item boundary (Phase 1).

Source of the rule: FatSecret Platform API Support, written clarification of
2026-11-09 (recorded as ``fatsecret_boundary.PROVIDER_PERSISTENCE_PERMISSION_REF``):

* raw nutritional values of an individual food item may stay on AxisAI servers
  for at most 24 hours;
* food IDs (and recipe IDs) may be stored indefinitely;
* aggregated / calculated totals, such as daily calorie totals and meal
  summaries, may be stored indefinitely.

Not inferred from it: indefinite storage of provider food names or serving
descriptions. They are treated as provider content with the same 24h limit.

What this module does
---------------------
Every server writer that persists provider values stamps the row with
``provider_items`` (references in display order) and ``provider_acquired_at``
(the moment the provider answered). Once a row is ``EXPIRE_AFTER`` old, one
pass, in one transaction per batch:

* a **single-item** entry (one component: one food at one quantity) is
  per-item data. Its four macros move into ``MealLogRetainedTotal`` for its
  owner and Istanbul day, and become NULL on the row. The row itself, its slot,
  time, quantity and IDs stay, so the diary keeps the entry and the day total
  stays exactly what it was;
* a **multi-item** entry (a diary meal of two or more foods) is already a meal
  summary. Its macros stay;
* in both cases provider names and serving text leave ``yemekler``. A neutral
  label replaces them; readers of past days re-request names live by ID.

Nothing restarts the 24h clock: the deadline is computed from
``provider_acquired_at`` only, cache hits never re-store, and a later live
request is a new acquisition that is displayed, never persisted.

Legacy rows (NULL provenance) are never mutated here. ``legacy_dry_run`` is a
read-only simulation for a separate founder-approved disposition; readers
already withhold legacy ``search``/``barcode`` per-item values (``is_due``).
"""
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog, MealLogRetainedTotal

_log = logging.getLogger(__name__)

PROVIDER = "fatsecret"
RETENTION = timedelta(hours=24)
# Values are removed at 22h. The worker enqueues the pass every five minutes
# (worker.RETENTION_INTERVAL_SECONDS) and a pass is killed after 240 s, so the
# routine worst case is about 22h10m. The 2h margin absorbs queue latency and a
# worker restart; readers additionally treat a row past 22h as expired even if
# no pass has run yet (fail closed at read time, see ``is_due``).
EXPIRY_MARGIN = timedelta(hours=2)
EXPIRE_AFTER = RETENTION - EXPIRY_MARGIN
BATCH_SIZE = 200
MAX_BATCHES = 50

PLACEHOLDER = "FatSecret"
NAME_CACHE_PREFIX = "fatsecret:display:v1:"
MAX_LIVE_LOOKUPS = 8

_MACROS = (("kalori", "energy_kcal"), ("protein", "protein_g"),
           ("karb", "carbohydrate_g"), ("yag", "fat_g"))


def utcnow():
    return datetime.utcnow()


# ── provenance written by server writers ────────────────────────────────────

def _quantity_text(quantity):
    try:
        text = format(quantity.normalize(), "f")
    except AttributeError:
        value = float(quantity)
        text = format(value, "g") if math.isfinite(value) else "1"
    return text


def provider_component(food_id, serving_id, quantity):
    return {"provider": PROVIDER, "food_id": str(food_id),
            "serving_id": str(serving_id), "quantity": _quantity_text(quantity)}


def text_component(label):
    return {"text": str(label)}


def stamp(entry, components, acquired_at):
    """Mark a row as holding provider values acquired at ``acquired_at``."""
    entry.provider_items = list(components)
    entry.provider_acquired_at = acquired_at
    entry.provider_expired_at = None


def _components(row):
    items = row.provider_items
    if isinstance(items, str):  # defensive: a JSON column read back as text
        try:
            items = json.loads(items)
        except ValueError:
            items = None
    if not isinstance(items, list) or not items:
        return None
    return items


def _is_provider(component):
    return (isinstance(component, dict) and component.get("provider") == PROVIDER
            and component.get("food_id"))


def is_single_item(row):
    """One component = one food at one quantity = per-item values.

    Unreadable provenance fails closed: it is treated as per-item.
    """
    items = _components(row)
    return items is None or len(items) == 1


def neutral_label(components):
    """``yemekler`` after expiry: user/independent text kept, provider text gone."""
    if not components:
        return PLACEHOLDER
    parts = []
    for component in components:
        if _is_provider(component):
            parts.append(f"{PLACEHOLDER} ({component.get('quantity') or '1'}x)")
        elif isinstance(component, dict) and isinstance(component.get("text"), str):
            parts.append(component["text"])
        else:
            parts.append(PLACEHOLDER)
    return ", ".join(parts)


def is_summarized(row):
    """True for an expired single-item row: its value lives in the day total."""
    return row.provider_expired_at is not None and is_single_item(row)


LEGACY_PROVIDER_SOURCES = ("search", "barcode")


def is_legacy_provider(row):
    """A provider-backed row written before provenance existed (no deadline).

    Only ``search``/``barcode``: the provider LogFood writer is their sole
    writer. ``manual``/``diary`` rows of uncertain origin are never classified
    from their label (founder disposition D4).
    """
    return (getattr(row, "provider_acquired_at", None) is None
            and getattr(row, "source", None) in LEGACY_PROVIDER_SOURCES)


def is_due(row, now=None):
    """Must this row's provider per-item values be treated as gone right now?

    True once expired, once past ``EXPIRE_AFTER`` even if no pass has run yet,
    and for legacy provider rows (already far past 24h; their stored values
    await a separate founder disposition and are never displayed).
    """
    if getattr(row, "provider_expired_at", None) is not None:
        return True
    acquired = getattr(row, "provider_acquired_at", None)
    if acquired is None:
        return is_legacy_provider(row)
    return acquired <= (now or utcnow()) - EXPIRE_AFTER


def withholds(row, now=None):
    """Does a reader publish this row's per-item macros as null?"""
    return is_due(row, now) and (is_legacy_provider(row) or is_single_item(row))


def display(row, names=None, now=None):
    """(description, macros-or-None) a reader may publish for one row.

    Macros are None for a due single-item row: its value is part of the day
    total, which readers compute from storage, so totals are unchanged while
    the per-item number is never shown (and therefore never re-copied by the
    iOS Repeat, which needs all four). Meal summaries keep their macros.
    """
    values = tuple(getattr(row, column) for column, _ in _MACROS)
    if not is_due(row, now):
        return row.yemekler, values
    # Without a LiveNames (identity/revision paths) the label is deterministic:
    # it must not depend on whether a name happens to be cached.
    description = (names.description(row) if names is not None
                   else stable_label(row))
    if withholds(row, now):
        return description, None
    return description, values


def stable_label(row):
    """The provider-free label of a due row, without any lookup."""
    if is_legacy_provider(row):
        return PLACEHOLDER
    components = _components(row)
    return neutral_label(components) if components else PLACEHOLDER


def published(row, names=None, now=None):
    """A read-only view of ``row`` with due provider values withheld.

    Attribute names follow ``MealLog`` (``yemekler``/``kalori``/...), so a reader
    swaps ``row`` for ``published(row)`` and changes nothing else.
    """
    from types import SimpleNamespace
    description, values = display(row, names, now)
    kalori, protein, karb, yag = values if values is not None else (None,) * 4
    return SimpleNamespace(yemekler=description, kalori=kalori, protein=protein,
                           karb=karb, yag=yag)


# ── the expiry pass ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExpiryResult:
    folded_entries: int
    relabelled_summaries: int
    scrubbed_items: int
    overdue: int


def _upsert_totals(totals, now):
    """Add folded values to the per-day aggregate, in a fixed key order.

    The fixed order means two concurrent passes take the aggregate rows' locks
    in the same order, so they can wait for each other but never deadlock.
    """
    if not totals:
        return
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = MealLogRetainedTotal.__table__
    for (user_id, day), values in sorted(totals.items()):
        statement = insert(table).values(
            user_id=user_id, tarih=day, updated_at=now, **values)
        excluded = statement.excluded
        db.session.execute(statement.on_conflict_do_update(
            index_elements=[table.c.user_id, table.c.tarih],
            set_={
                "energy_kcal": table.c.energy_kcal + excluded.energy_kcal,
                "protein_g": table.c.protein_g + excluded.protein_g,
                "carbohydrate_g": table.c.carbohydrate_g + excluded.carbohydrate_g,
                "fat_g": table.c.fat_g + excluded.fat_g,
                "item_count": table.c.item_count + excluded.item_count,
                "updated_at": excluded.updated_at,
            }))


def _expire_meal_log_batch(now):
    cutoff = now - EXPIRE_AFTER
    rows = (MealLog.query
            .filter(MealLog.provider_acquired_at.isnot(None),
                    MealLog.provider_expired_at.is_(None),
                    MealLog.provider_acquired_at <= cutoff)
            .order_by(MealLog.id)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
            .all())
    totals = {}
    folded = relabelled = 0
    for row in rows:
        components = _components(row)
        if is_single_item(row):
            key = (row.user_id, row.tarih)
            bucket = totals.setdefault(key, {
                "energy_kcal": 0.0, "protein_g": 0.0, "carbohydrate_g": 0.0,
                "fat_g": 0.0, "item_count": 0})
            for column, field in _MACROS:
                bucket[field] += float(getattr(row, column) or 0)
                setattr(row, column, None)
            bucket["item_count"] += 1
            folded += 1
        else:
            relabelled += 1
        row.yemekler = neutral_label(components)
        row.provider_expired_at = now
    _upsert_totals(totals, now)
    return len(rows), folded, relabelled


def _expire_item_batch(now):
    cutoff = now - EXPIRE_AFTER
    items = (CustomMealItem.query
             .filter(CustomMealItem.provider_acquired_at.isnot(None),
                     CustomMealItem.provider_expired_at.is_(None),
                     CustomMealItem.provider_acquired_at <= cutoff)
             .order_by(CustomMealItem.id)
             .limit(BATCH_SIZE)
             .with_for_update(skip_locked=True)
             .all())
    for item in items:
        # Staging copy only: logging a saved meal re-requests every provider
        # item live (diary.diary_log_meal), so nothing here is ever re-used.
        # Quantity, grams and IDs are the user's selection and stay; only the
        # provider's nutrient values and its name/serving text are removed.
        item.food_name = PLACEHOLDER
        item.serving_description = None
        item.calories = item.protein = item.carbs = item.fat = 0
        item.per_100g_calories = item.per_100g_protein = None
        item.per_100g_carbs = item.per_100g_fat = None
        item.provider_expired_at = now
    return len(items)


def overdue_count(now=None):
    """Provider rows past the full 24h with values still present (should be 0)."""
    now = now or utcnow()
    cutoff = now - RETENTION
    meals = (db.session.query(func.count(MealLog.id))
             .filter(MealLog.provider_acquired_at.isnot(None),
                     MealLog.provider_expired_at.is_(None),
                     MealLog.provider_acquired_at <= cutoff).scalar())
    items = (db.session.query(func.count(CustomMealItem.id))
             .filter(CustomMealItem.provider_acquired_at.isnot(None),
                     CustomMealItem.provider_expired_at.is_(None),
                     CustomMealItem.provider_acquired_at <= cutoff).scalar())
    return int(meals or 0) + int(items or 0)


def expire_due(now=None):
    """Remove every provider per-item value that has reached its deadline.

    Idempotent: an expired row is excluded by ``provider_expired_at``, set in
    the same transaction as the aggregate it fed. Safe to run concurrently:
    candidate rows are claimed ``FOR UPDATE SKIP LOCKED``.
    """
    now = now or utcnow()
    folded = relabelled = scrubbed = 0
    try:
        for _ in range(MAX_BATCHES):
            seen, f, r = _expire_meal_log_batch(now)
            db.session.commit()
            folded += f
            relabelled += r
            if seen < BATCH_SIZE:
                break
        for _ in range(MAX_BATCHES):
            seen = _expire_item_batch(now)
            db.session.commit()
            scrubbed += seen
            if seen < BATCH_SIZE:
                break
        overdue = overdue_count(now)
        db.session.commit()  # close the read; nothing is pending
    except Exception:
        db.session.rollback()
        raise
    if overdue:
        _log.warning("[RETENTION] event=provider_values_overdue count=%d", overdue)
    if folded or relabelled or scrubbed:
        _log.info("[RETENTION] folded=%d relabelled=%d scrubbed=%d",
                  folded, relabelled, scrubbed)
    return ExpiryResult(folded, relabelled, scrubbed, overdue)


# ── readers ─────────────────────────────────────────────────────────────────

ZERO = {"energy_kcal": 0.0, "protein_g": 0.0, "carbohydrate_g": 0.0, "fat_g": 0.0}


RETAINED_FIELDS = ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")


def retained_scalar(field, user_id, day):
    """SQL expression: the retained aggregate for one owner/day, 0 when none.

    ``day`` may be a literal day key or ``MealLog.tarih`` (correlated), so a
    reader adds it inside its existing aggregate SELECT and its statement count
    does not change.
    """
    column = getattr(MealLogRetainedTotal, field)
    return func.coalesce(
        select(column)
        .where(MealLogRetainedTotal.user_id == user_id,
               MealLogRetainedTotal.tarih == day)
        .scalar_subquery(), 0)


def withheld_sum(rows):
    """Stored values of rows whose per-item numbers a reader withholds."""
    total = dict(ZERO)
    for row in rows:
        for column, field in _MACROS:
            total[field] += float(getattr(row, column) or 0)
    return total


def add_totals(*parts):
    total = dict(ZERO)
    for part in parts:
        for field in RETAINED_FIELDS:
            total[field] += float((part or {}).get(field, 0) or 0)
    return total


def with_retained(user_id, day, kalori, protein, karb, yag):
    """Add one day's retained aggregate to a reader's in-Python sums."""
    retained = retained_for_day(user_id, day)
    return (kalori + retained["energy_kcal"], protein + retained["protein_g"],
            karb + retained["carbohydrate_g"], yag + retained["fat_g"])


def retained_totals(user_id, day_keys):
    """{day: {energy_kcal, protein_g, carbohydrate_g, fat_g}} for those days."""
    day_keys = list(day_keys)
    if not day_keys:
        return {}
    rows = (MealLogRetainedTotal.query
            .filter(MealLogRetainedTotal.user_id == user_id,
                    MealLogRetainedTotal.tarih.in_(day_keys))
            .all())
    return {row.tarih: {"energy_kcal": float(row.energy_kcal or 0),
                        "protein_g": float(row.protein_g or 0),
                        "carbohydrate_g": float(row.carbohydrate_g or 0),
                        "fat_g": float(row.fat_g or 0)} for row in rows}


def retained_for_day(user_id, day_key):
    return retained_totals(user_id, [day_key]).get(day_key, dict(ZERO))


# ── live display of provider names after expiry ─────────────────────────────

def _redis():
    from app.services import foodcache
    return foodcache._get_redis()


def _cached_food(food_id):
    redis = _redis()
    if redis is None:
        return None
    try:
        raw = redis.get(NAME_CACHE_PREFIX + food_id)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _remember_food(food_id, value):
    # NX: a hit never re-stores, so a cached name expires EXPIRE_AFTER after
    # the provider answered, whatever happens in between.
    redis = _redis()
    if redis is None:
        return
    try:
        redis.set(NAME_CACHE_PREFIX + food_id, json.dumps(value),
                  ex=int(EXPIRE_AFTER.total_seconds()), nx=True)
    except Exception:
        pass


def _fetch_food(food_id):
    from app.services import mobile_food_discovery
    food = mobile_food_discovery.servings(food_id)
    if not food:
        return None
    return {"name": str(food.get("name") or PLACEHOLDER),
            "servings": {str(s.get("serving_id")): str(s.get("description") or "")
                         for s in food.get("servings", []) if s.get("serving_id")}}


class LiveNames:
    """Per-request, bounded, fail-soft provider name refresh."""

    def __init__(self, max_lookups=MAX_LIVE_LOOKUPS):
        self._foods = {}
        self._lookups = max_lookups
        self._failed = False

    def _food(self, food_id):
        if food_id in self._foods:
            return self._foods[food_id]
        food = _cached_food(food_id)
        if food is None and not self._failed and self._lookups > 0:
            self._lookups -= 1
            try:
                food = _fetch_food(food_id)
            except Exception:
                food = None
                self._failed = True  # provider down: stop asking this request
            if food is not None:
                _remember_food(food_id, food)
        self._foods[food_id] = food
        return food

    def _render(self, component):
        if not _is_provider(component):
            if isinstance(component, dict) and isinstance(component.get("text"), str):
                return component["text"]
            return PLACEHOLDER
        quantity = component.get("quantity") or "1"
        food = self._food(str(component["food_id"]))
        if not isinstance(food, dict) or not food.get("name"):
            return f"{PLACEHOLDER} ({quantity}x)"
        serving = (food.get("servings") or {}).get(str(component.get("serving_id")))
        if serving:
            return f"{food['name']} ({quantity}x {serving})"
        return f"{food['name']} ({quantity}x)"

    def description(self, row):
        """The display description of one row; stored text unless due."""
        if not is_due(row):
            return row.yemekler
        if is_legacy_provider(row):
            return PLACEHOLDER  # no stored ID: nothing to re-request (D2)
        components = _components(row)
        if not components:
            return PLACEHOLDER
        if row.provider_expired_at is None or any(_is_provider(c) for c in components):
            return ", ".join(self._render(c) for c in components)
        return row.yemekler


# ── Repeat-copy provenance ──────────────────────────────────────────────────

def _same(value, expected):
    return value is not None and abs(float(value) - float(expected)) < 1e-9


LEGACY_COMPONENT = {"provider": PROVIDER, "legacy": True}


def copied_provenance(user_id, description, nutrition):
    """Provenance an exact copy of a provider row must keep, or None.

    The shipped iOS Repeat sends a past entry's description and four macros as
    a ``manual`` log. Copying a provider row that way would put per-item
    provider values into a row with no deadline. An exact copy (same owner,
    same description, same four macros) of a provider row that still holds its
    values therefore inherits that row's components and ORIGINAL acquisition
    time, so it expires at the original deadline. A copy of a legacy provider
    row (no provenance, already past 24h) is due immediately. Readers never
    publish due values, so the shipped Repeat cannot see them in the first
    place; this is the server-side backstop. Every other manual entry is the
    user's own and is never touched.
    """
    candidates = (MealLog.query
                  .filter(MealLog.user_id == user_id,
                          MealLog.provider_expired_at.is_(None),
                          MealLog.yemekler == description,
                          (MealLog.provider_acquired_at.isnot(None))
                          | MealLog.source.in_(LEGACY_PROVIDER_SOURCES))
                  .order_by(MealLog.id.asc())
                  .all())
    values = (nutrition.energy_kcal, nutrition.protein_g,
              nutrition.carbohydrate_g, nutrition.fat_g)
    for row in candidates:
        if all(_same(getattr(row, column), value)
               for (column, _), value in zip(_MACROS, values)):
            if row.provider_acquired_at is None:
                return [dict(LEGACY_COMPONENT)], row.created_at or datetime(1970, 1, 1)
            return list(row.provider_items or [dict(LEGACY_COMPONENT)]), row.provider_acquired_at
    return None


# ── legacy dry run (read-only) ──────────────────────────────────────────────

LEGACY_V1_CACHE_PATTERN = "food:macros:v1:*"


def _legacy_meal_filter():
    return (MealLog.source.in_(LEGACY_PROVIDER_SOURCES),
            MealLog.provider_acquired_at.is_(None))


def _legacy_item_filter():
    return (CustomMealItem.fatsecret_food_id.isnot(None),
            CustomMealItem.fatsecret_food_id != "",
            CustomMealItem.provider_acquired_at.is_(None))


def _sums(query_rows):
    total = dict(ZERO)
    for row in query_rows:
        for column, field in _MACROS:
            total[field] += float(getattr(row, column) or 0)
    return total


def _rounded(values):
    return {k: round(v, 2) for k, v in values.items()}


def legacy_dry_run():
    """Read-only simulation of the founder-gated legacy disposition (D5).

    Scope is exactly the DB4 predicates: ``meal_log`` rows with source
    ``search``/``barcode`` and no provenance, and ``custom_meal_item`` rows with
    a non-empty FatSecret ID and no provenance. Reports aggregates only (no
    user id, name, value per row or key). Mutates nothing; there is no apply.
    ``manual``/``diary`` rows and ID-less items are counted as untouched (D4).
    """
    meal_rows = MealLog.query.filter(*_legacy_meal_filter()).all()
    buckets = sorted({(row.user_id, row.tarih) for row in meal_rows})
    moved = _sums(meal_rows)
    before = dict(ZERO)
    after = dict(ZERO)
    moved_ids = {row.id for row in meal_rows}
    for user_id, day in buckets:
        day_rows = MealLog.query.filter_by(user_id=user_id, tarih=day).all()
        retained = retained_for_day(user_id, day)
        kept = [r for r in day_rows if r.id not in moved_ids]
        folded = [r for r in day_rows if r.id in moved_ids]
        before = add_totals(before, _sums(day_rows), retained)
        after = add_totals(after, _sums(kept), retained, _sums(folded))
    item_count = (db.session.query(func.count(CustomMealItem.id))
                  .filter(*_legacy_item_filter()).scalar())
    item_meals = (db.session.query(func.count(func.distinct(CustomMealItem.custom_meal_id)))
                  .filter(*_legacy_item_filter()).scalar())
    untouched_sources = dict(
        db.session.query(MealLog.source, func.count(MealLog.id))
        .filter(MealLog.provider_acquired_at.is_(None),
                ~MealLog.source.in_(LEGACY_PROVIDER_SOURCES) | MealLog.source.is_(None))
        .group_by(MealLog.source).all())
    idless_items = (db.session.query(func.count(CustomMealItem.id))
                    .filter((CustomMealItem.fatsecret_food_id.is_(None))
                            | (CustomMealItem.fatsecret_food_id == "")).scalar())
    result = {
        "meal_log_provider_rows": len(meal_rows),
        "meal_log_user_day_buckets": len(buckets),
        "meal_log_rows_placeholder_only": len(meal_rows),  # no stored food_id (D2)
        "meal_log_macro_sums_to_move": _rounded(moved),
        "day_totals_before": _rounded(before),
        "day_totals_after": _rounded(after),
        "day_totals_conserved": _rounded(before) == _rounded(after),
        "custom_meal_item_provider_items": int(item_count or 0),
        "custom_meal_item_provider_meals": int(item_meals or 0),
        "custom_meal_item_live_name_lookup": int(item_count or 0),  # IDs stored
        "untouched_meal_log_by_source": {str(k): int(v) for k, v in untouched_sources.items()},
        "untouched_custom_meal_items_without_id": int(idless_items or 0),
    }
    db.session.rollback()
    return result


def legacy_v1_cache_keys():
    """Count old ``food:macros:v1:*`` keys (FatSecret-era, written by e7bfa139)."""
    redis = _redis()
    if redis is None:
        return None
    return sum(1 for _ in redis.scan_iter(match=LEGACY_V1_CACHE_PATTERN, count=500))


def unlink_legacy_v1_cache(expected):
    """Delete ``food:macros:v1:*`` only if the scanned count equals ``expected``.

    Scoped to that one pattern: ``food:macros:v2:*`` never held FatSecret values
    (its only writers are the built-in table and the LLM estimate) and the
    attested ``food:independent-menu:v1:*`` cache is not provider data.
    Production use is separately founder-gated (docs/compliance runbook).
    """
    redis = _redis()
    if redis is None:
        raise RuntimeError("redis unavailable")
    keys = list(redis.scan_iter(match=LEGACY_V1_CACHE_PATTERN, count=500))
    if len(keys) != expected:
        return {"result": "HOLD", "scanned": len(keys), "expected": expected, "deleted": 0}
    deleted = 0
    for start in range(0, len(keys), 500):
        deleted += int(redis.unlink(*keys[start:start + 500]) or 0)
    return {"result": "DELETED", "scanned": len(keys), "expected": expected, "deleted": deleted}
