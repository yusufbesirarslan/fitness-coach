"""The mobile nutrition wire projection.

Pure: stdlib plus `app/timeutil` (the canonical day/zone authority, itself pure).
No ORM, no Flask, no query, no clock of its own — the day and the key arrive as
arguments, which is what lets the whole contract be exercised without a database.

Every ambiguity the legacy web payloads carry is resolved here, once:

  * the day is a full ISO calendar date with the IANA zone that produced it,
    instead of a `DD.MM` label that cannot be placed in a year;
  * timestamps carry an explicit offset instead of being naive UTC read as
    whatever the reader assumes;
  * a NULL macro stays `null` instead of arriving as a measured zero;
  * an unset calorie target is an absent goal instead of `0`;
  * the Turkish meal column becomes a stable slot token, and a label nobody
    recognises becomes `unknown` rather than being forced into a bucket.

Totals keep the server's existing arithmetic on purpose — see `day_totals`.
"""
from app.timeutil import APP_TZ, to_app_tz


SLOT_UNKNOWN = "unknown"
SOURCE_UNKNOWN = "unknown"

# `MealLog.ogun` stores a Turkish DISPLAY label, and only some writers use one of
# these four: the AI coach writes "AI Koç" and a shared meal suggestion writes a
# sentence containing the sender's name. So the mapping must be exact and must
# have a fallback; guessing a slot from free text would be inventing data.
#
# The wire keys are not a new vocabulary invented for mobile — they are the keys
# `POST /api/quick-add-meal` already accepts for the same four slots.
SLOT_BY_MEAL_LABEL = {
    "Kahvaltı": "kahvalti",
    "Öğle": "ogle",
    "Akşam": "aksam",
    "Ara Öğün": "ara_ogun",
}

# Every value application code writes to `MealLog.source`. A row written before
# the column existed carries NULL, and NULL is not evidence of a manual entry —
# it becomes `unknown` rather than being folded into a source it may not have
# had. The web surface reads NULL as "manual"; that is a display default, and
# copying it here would be the same fabrication this contract exists to stop.
KNOWN_SOURCES = frozenset({
    "manual", "diary", "ai_plan", "barcode", "search", "coach",
})


def slot_token(meal_label):
    """Map the stored Turkish meal label onto a stable slot token."""
    if not isinstance(meal_label, str):
        return SLOT_UNKNOWN
    return SLOT_BY_MEAL_LABEL.get(meal_label.strip(), SLOT_UNKNOWN)


def source_token(source):
    """Map the stored source onto the published vocabulary."""
    return source if source in KNOWN_SOURCES else SOURCE_UNKNOWN


def _amount(value):
    """Publish a stored macro as a number, and a missing one as missing."""
    return None if value is None else float(value)


def _logged_at(created_at):
    """Serialise a naive-UTC `created_at` as an offset-aware instant.

    `to_app_tz` is the repository's single rule for this column (it documents
    that the value is naive UTC), so the conversion is the canonical one rather
    than a local reinterpretation. The offset shown is the same zone that
    resolved the diary day, which keeps a response internally coherent: an entry
    dated inside `day` reads as belonging to `day`.
    """
    local = to_app_tz(created_at)
    return local.isoformat() if local is not None else None


def nutrient_facts(energy_kcal, protein_g, carbohydrate_g, fat_g):
    return {
        "energy_kcal": _amount(energy_kcal),
        "protein_g": _amount(protein_g),
        "carbohydrate_g": _amount(carbohydrate_g),
        "fat_g": _amount(fat_g),
    }


def logged_meal(entry, entry_id_for):
    return {
        "id": entry_id_for(entry.entry_id),
        "slot": slot_token(entry.meal_label),
        "description": entry.description,
        "source": source_token(entry.source),
        "logged_at": _logged_at(entry.created_at),
        "nutrition": nutrient_facts(
            entry.energy_kcal, entry.protein_g,
            entry.carbohydrate_g, entry.fat_g),
    }


def day_totals(entries):
    """Sum the day the way the server already sums it: NULL counts as zero.

    This is the existing behaviour of `/meal-log/today` and it is published
    unchanged and on purpose. The alternative — a null total as soon as one
    entry is incomplete — would erase a day's worth of real measurements
    because one row is missing its fat figure.

    The consequence is stated rather than hidden: a client that re-adds the
    published entry nutrition can legitimately disagree with these totals when
    some entry carries a null, so the contract says the server's totals are
    authoritative and the client does not recompute them
    (docs/MOBILE_NUTRITION.md "Totals").

    A day with no entries totals zero, which is a measurement, not a gap.
    """
    return {
        "energy_kcal": round(sum(e.energy_kcal or 0 for e in entries), 2),
        "protein_g": round(sum(e.protein_g or 0 for e in entries), 2),
        "carbohydrate_g": round(sum(e.carbohydrate_g or 0 for e in entries), 2),
        "fat_g": round(sum(e.fat_g or 0 for e in entries), 2),
    }


def nutrition_goal(target_energy_kcal):
    """Publish a configured calorie target, or `null` when none is configured.

    A stored NULL means the user never completed the calculation that sets one.
    A stored non-positive number is not a goal either: zero kilocalories a day
    is not a target anyone configured, so it is normalised here rather than sent
    on for the client to guess about. Zero as "unset" stops at this boundary —
    the persisted column keeps whatever it holds.
    """
    if target_energy_kcal is None:
        return None
    value = float(target_energy_kcal)
    if value <= 0:
        return None
    return {"target_energy_kcal": value}


def diary_day_payload(day, entries, target_energy_kcal, entry_id_for):
    """Project one canonical ledger day onto the mobile contract."""
    return {
        "day": {"date": day.isoformat(), "timezone": APP_TZ.key},
        "meals": [logged_meal(entry, entry_id_for) for entry in entries],
        "totals": day_totals(entries),
        "goal": nutrition_goal(target_energy_kcal),
    }
