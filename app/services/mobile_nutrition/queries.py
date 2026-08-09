"""The database reads behind the mobile nutrition contract.

Two bounded, user-scoped SELECTs and nothing else: the canonical ledger rows for
one Istanbul day, and the newest onboarding session that owns the calorie
target. No provider call, no per-entry follow-up query, no write.

The rows leave here as frozen value objects rather than ORM instances so that
the projection layer can stay pure — and so a lazy attribute access can never
turn one read into N (docs/MOBILE_NUTRITION.md "Performance and concurrency").
"""
from dataclasses import dataclass
from datetime import datetime

from app.models import MealLog, UserSession


@dataclass(frozen=True)
class LedgerEntry:
    """One canonical `MealLog` row, reduced to what the contract may publish.

    Macros stay `None` when the column is NULL — the whole point of the mobile
    contract is that they survive the boundary as missing. `created_at` is
    carried exactly as stored, i.e. NAIVE UTC; giving it a zone is the
    projection's job, not this layer's.
    """

    entry_id: int
    meal_label: str
    description: str
    source: "str | None"
    energy_kcal: "float | None"
    protein_g: "float | None"
    carbohydrate_g: "float | None"
    fat_g: "float | None"
    created_at: "datetime | None"


def fetch_ledger_entries(user_id, day_key):
    """Return the user's canonical ledger rows for one ISO Istanbul day.

    Ordered the way `/meal-log/today` orders them, with the primary key as a
    tiebreak so two rows written in the same second cannot swap places between
    reads — identities are only stable if the list they arrive in is.
    """
    rows = (MealLog.query
            .filter_by(user_id=user_id, tarih=day_key)
            .order_by(MealLog.created_at.asc(), MealLog.id.asc())
            .all())
    return tuple(
        LedgerEntry(
            entry_id=row.id,
            meal_label=row.ogun,
            description=row.yemekler,
            source=row.source,
            energy_kcal=row.kalori,
            protein_g=row.protein,
            carbohydrate_g=row.karb,
            fat_g=row.yag,
            created_at=row.created_at,
        )
        for row in rows
    )


def fetch_target_energy_kcal(user_id):
    """Return the stored daily calorie target, or None when none is stored.

    Deliberately the same selector `/api/progress/nutrition`, `/meal-log/review`
    and the barcode context already use — the newest `UserSession` row. This
    contract normalises what that value MEANS at the boundary; it does not
    introduce a second place that decides where a target comes from.
    """
    session = (UserSession.query
               .filter_by(user_id=user_id)
               .order_by(UserSession.created_at.desc(), UserSession.id.desc())
               .first())
    return session.target_calories if session else None
