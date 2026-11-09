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

    user_id: int
    entry_id: int
    meal_label: str
    description: str
    source: "str | None"
    energy_kcal: "float | None"
    protein_g: "float | None"
    carbohydrate_g: "float | None"
    fat_g: "float | None"
    diary_date: str
    idempotency_key: "str | None"
    idempotency_fingerprint: "str | None"
    photo_key: "str | None"
    created_at: "datetime | None"


def fetch_ledger_entries(user_id, day_key):
    """Return the user's canonical ledger rows for one ISO Istanbul day.

    Ordered the way `/meal-log/today` orders them, with the primary key as a
    tiebreak so two rows written in the same second cannot swap places between
    reads — identities are only stable if the list they arrive in is.
    """
    return fetch_published_ledger(user_id, day_key)[0]


def fetch_published_ledger(user_id, day_key):
    """``(entries, withheld)`` for one day.

    Entries publish no per-item FatSecret values once those are due
    (app/services/provider_retention.py). ``withheld`` is the sum of the values
    so withheld that are still stored (not yet moved into the retained daily
    aggregate), so the caller's day total stays exactly what storage holds.
    """
    rows = (MealLog.query
            .filter_by(user_id=user_id, tarih=day_key)
            .order_by(MealLog.created_at.asc(), MealLog.id.asc())
            .all())
    from app.services import provider_retention
    shown = {row.id: provider_retention.published(row) for row in rows}
    withheld = provider_retention.withheld_sum(
        (row for row in rows if provider_retention.withholds(row)))
    return tuple(
        LedgerEntry(
            user_id=row.user_id,
            entry_id=row.id,
            meal_label=row.ogun,
            description=shown[row.id].yemekler,
            source=row.source,
            energy_kcal=shown[row.id].kalori,
            protein_g=shown[row.id].protein,
            carbohydrate_g=shown[row.id].karb,
            fat_g=shown[row.id].yag,
            diary_date=row.tarih,
            idempotency_key=row.idempotency_key,
            idempotency_fingerprint=row.idempotency_fingerprint,
            photo_key=row.photo_key,
            created_at=row.created_at,
        )
        for row in rows
    ), withheld


def fetch_target_energy_kcal(user_id):
    """Return the stored daily calorie target, or None when none is stored.

    Deliberately the same selector `/meal-log/today`, `/meal-log/review`
    and the barcode context already use — the newest `UserSession` row. This
    contract normalises what that value MEANS at the boundary; it does not
    introduce a second place that decides where a target comes from.
    """
    session = (UserSession.query
               .filter_by(user_id=user_id)
               .order_by(UserSession.created_at.desc(), UserSession.id.desc())
               .first())
    return session.target_calories if session else None
