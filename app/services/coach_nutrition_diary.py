"""Closed Coach projection of owner-scoped nutrition diary records."""
from sqlalchemy import and_, case

from app.extensions import db
from app.models import MealLog, MealLogRetainedTotal, User
from app.services import coach_privacy
from app.services.mobile_nutrition.serialization import slot_token

_MAX_DIARY_ENTRIES = 12
_MACROS = (
    ("calories", MealLog.kalori),
    ("protein_g", MealLog.protein),
    ("carbs_g", MealLog.karb),
    ("fat_g", MealLog.yag),
)


def nutrition_diary(user_id, day):
    """A day total requires a complete set of exact, first-party logged values.

    Positive evidence comes from the explicit user-entered LogFood command after server
    copy checks. Withhold raw diary text, unmarked legacy rows, estimates,
    and every provider-backed amount, including retained day aggregates.
    """
    day_key = day.isoformat()
    qualified = MealLog.user_entered_amounts()
    retained = (db.session.query(MealLogRetainedTotal.user_id)
                .filter_by(user_id=user_id, tarih=day_key).exists())
    # Read the retained-presence bit and the bounded ledger in one statement
    # so a retention fold cannot race the completeness decision.
    rows = (db.session.query(
        MealLog.id, retained, case((qualified, MealLog.ogun)),
        *(case((qualified, column)) for _, column in _MACROS))
        .select_from(User)
        .outerjoin(MealLog, and_(MealLog.user_id == User.id, MealLog.tarih == day_key))
        .filter(User.id == user_id)
        .order_by(MealLog.id.desc())
        .limit(_MAX_DIARY_ENTRIES + 1).all())
    result = {"day": day_key, "status": "unavailable", "entries": []}
    if not rows:
        return result
    retained_present = rows[0][1]
    if rows[0][0] is None and not retained_present:
        return {**result, "status": "empty"}

    for _, _, slot, *amounts in rows[:_MAX_DIARY_ENTRIES]:
        if slot is None:
            continue
        values = {key: value for (key, _), value in zip(_MACROS, amounts)
                  if coach_privacy.number(value) is not None and value >= 0}
        if values:
            result["entries"].append({"slot": slot_token(slot), "logged_nutrition": values})

    entries = result["entries"]
    if (not retained_present and len(rows) == len(entries) <= _MAX_DIARY_ENTRIES
            and all(len(entry["logged_nutrition"]) == len(_MACROS) for entry in entries)):
        result["status"] = "available"
        result["logged_totals"] = {
            key: round(sum(entry["logged_nutrition"][key] for entry in entries), 2)
            for key, _ in _MACROS
        }
    return result
