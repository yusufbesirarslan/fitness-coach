"""Bounded native MealLog history — a secondary READ over the one ledger.

Pages are whole Istanbul days (``MealLog.tarih``), newest day first: a day is
never split across pages, so every published day total is complete (the same
reason ``/meal-log/history`` bounds by days, not rows). Entries use the exact
diary projection (``mobile_nutrition.serialization.logged_meal``) and the
diary's ordering, so today's entries here are byte-identical to
``/api/v1/nutrition/diary/today``'s.

Two bounded SELECTs per page: the next ``limit + 1`` day keys, then those days'
rows. The cursor is an owner-bound, integrity-protected "before <day>" value; a
cursor from another account, a forged one or a malformed one is refused.
"""
import re
from datetime import date

from app.models import MealLog
from app.services import provider_retention
from app.services.mobile_nutrition.identity import diary_entry_id
from app.services.mobile_nutrition.queries import LedgerEntry
from app.services.mobile_nutrition.revision import (
    diary_entry_revision,
    revision_state_from_entry,
)
from app.services.mobile_nutrition.serialization import day_totals, logged_meal
from app.timeutil import APP_TZ

from . import errors, tokens


CONTRACT_VERSION = 1
DEFAULT_DAYS = 7
MAX_DAYS = 14
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_limit(raw):
    if raw is None:
        return DEFAULT_DAYS
    if not isinstance(raw, str) or not raw.isdigit() or len(raw) > 3:
        raise errors.InvalidHistoryLimit
    value = int(raw)
    if not 1 <= value <= MAX_DAYS:
        raise errors.InvalidHistoryLimit
    return value


def encode_cursor(secret, user_id, before_day):
    return tokens.sign_payload(
        secret, tokens.HISTORY_CURSOR, user_id, {"before": before_day})


def decode_cursor(secret, user_id, cursor):
    """The exclusive upper day bound carried by a valid cursor of THIS owner."""
    if cursor is None:
        return None
    try:
        payload = tokens.verify_payload(
            secret, tokens.HISTORY_CURSOR, user_id, cursor, max_length=256)
    except tokens.InvalidSignedToken:
        raise errors.InvalidHistoryCursor from None
    before = payload.get("before") if set(payload) == {"before"} else None
    if not isinstance(before, str) or not _ISO_DAY.fullmatch(before):
        raise errors.InvalidHistoryCursor
    try:
        date.fromisoformat(before)
    except ValueError:
        raise errors.InvalidHistoryCursor from None
    return before


def _entry(row, names=None):
    # A due FatSecret entry publishes no per-item macros (its value is in the
    # day total) and its provider name is re-requested live by stored ID, never
    # stored again; fail-soft to a neutral label. History is read-only.
    shown = provider_retention.published(row, names)
    return LedgerEntry(
        user_id=row.user_id, entry_id=row.id, meal_label=row.ogun,
        description=shown.yemekler, source=row.source, energy_kcal=shown.kalori,
        protein_g=shown.protein, carbohydrate_g=shown.karb, fat_g=shown.yag,
        diary_date=row.tarih, idempotency_key=row.idempotency_key,
        idempotency_fingerprint=row.idempotency_fingerprint,
        photo_key=row.photo_key, created_at=row.created_at)


def read_page(user_id, secret, cursor=None, limit=DEFAULT_DAYS):
    """One bounded page. Raises on storage failure (→ 503)."""
    from app.extensions import db

    before = decode_cursor(secret, user_id, cursor)
    # The day-key SELECT also carries each day's retained aggregate of expired
    # FatSecret per-item values (correlated on the grouped day), so the page
    # stays two bounded SELECTs and every day total is conserved.
    retained_columns = [provider_retention.retained_scalar(f, user_id, MealLog.tarih)
                        for f in provider_retention.RETAINED_FIELDS]
    day_query = (db.session.query(MealLog.tarih, *retained_columns)
                 .filter(MealLog.user_id == user_id))
    if before is not None:
        day_query = day_query.filter(MealLog.tarih < before)
    day_rows = (day_query.group_by(MealLog.tarih)
                .order_by(MealLog.tarih.desc())
                .limit(limit + 1).all())
    retained = {row[0]: dict(zip(provider_retention.RETAINED_FIELDS,
                                 (float(v or 0) for v in row[1:])))
                for row in day_rows}
    day_keys = [row[0] for row in day_rows]
    has_more = len(day_keys) > limit
    day_keys = day_keys[:limit]
    rows = []
    if day_keys:
        rows = (MealLog.query
                .filter(MealLog.user_id == user_id, MealLog.tarih.in_(day_keys))
                .order_by(MealLog.tarih.desc(), MealLog.created_at.asc(),
                          MealLog.id.asc())
                .all())
    grouped = {key: [] for key in day_keys}
    withheld_rows = {key: [] for key in day_keys}
    names = provider_retention.LiveNames()
    for row in rows:
        entry = _entry(row, names)
        grouped[row.tarih].append(entry)
        if provider_retention.withholds(row):
            withheld_rows[row.tarih].append(row)

    def entry_id_for(entry_id):
        return diary_entry_id(secret, user_id, entry_id)

    def revision_for(entry):
        return diary_entry_revision(secret, revision_state_from_entry(entry))

    days = [{
        "date": key,
        "meals": [logged_meal(entry, entry_id_for, revision_for)
                  for entry in grouped[key]],
        "totals": day_totals(grouped[key], provider_retention.add_totals(
            retained.get(key), provider_retention.withheld_sum(withheld_rows[key]))),
    } for key in day_keys]
    return {
        "contract_version": CONTRACT_VERSION,
        "timezone": APP_TZ.key,
        "state": "available" if days else "empty",
        "days": days,
        "next_cursor": (encode_cursor(secret, user_id, day_keys[-1])
                        if has_more else None),
    }
