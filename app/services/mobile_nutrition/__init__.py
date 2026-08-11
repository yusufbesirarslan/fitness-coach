"""Mobile-facing projection of the canonical nutrition ledger.

This package is an ADAPTER over the existing nutrition authority, not a second
one. The backend already has three related surfaces — the `MealLog` ledger, the
`CustomMeal`/`CustomMealItem` diary builder, and progress aggregation — and both
route docstrings state that the ledger totals and the builder totals must never
be added together, because committing a builder meal writes it into the ledger
as well. So the mobile contract reads exactly one of them: the ledger, which the
repository already calls the single canonical record of what was eaten
(CLAUDE.md, "Veritabanı"). Nothing here sums two collections, and nothing here
decides what "today" or "the target" means — `app/timeutil` and the newest
`UserSession` row keep those jobs.

Layering follows the read-model convention used by `app/services/workout_state`:

    queries.py        touches the database and returns frozen value objects
    serialization.py  pure projection — no ORM, no Flask, no clock
    identity.py       pure opaque-identity codec — no ORM, no Flask
    __init__.py       the orchestrator, and the only part that needs an app

Contract, null semantics and source-of-truth matrix: docs/MOBILE_NUTRITION.md
"""
from app.timeutil import app_today

from .identity import diary_entry_id, matches_diary_entry_id
from .queries import fetch_ledger_entries, fetch_target_energy_kcal
from .revision import (
    DiaryEntryRevisionState,
    diary_entry_revision,
    matches_diary_entry_revision,
)
from .serialization import diary_day_payload


__all__ = [
    "build_diary_day",
    "diary_entry_id",
    "diary_entry_revision",
    "DiaryEntryRevisionState",
    "matches_diary_entry_id",
    "matches_diary_entry_revision",
]


def build_diary_day(user_id, secret, day=None):
    """Build the canonical mobile diary day for one authenticated user.

    `day` exists for tests that need a fixed date; production passes nothing and
    gets the server's Istanbul day, because the day boundary is the server's to
    own and no client input reaches it.
    """
    resolved_day = day or app_today()
    entries = fetch_ledger_entries(user_id, resolved_day.isoformat())
    target = fetch_target_energy_kcal(user_id)
    return diary_day_payload(
        resolved_day, entries, target,
        lambda entry_id: diary_entry_id(secret, user_id, entry_id))
