"""Who may be represented on a cross-user ranking: the global XP leaderboard
(Redis cache + PostgreSQL fallback), challenge boards and weekly winners.

Minors are never published there. Public ranking of a TR 13-17 account is not
covered by the counsel qualification (G4) or by any F01/D1 disclosure, and
Law 5651 Ek-4(7) social-network classification is unresolved, so every account
carrying a minority age signal is excluded, not only TR records. `User.age` is
the conservative signal account_eligibility.attach writes from the declaration
and the TR minor profile keeps within 13-17. A NULL age (legacy adult rows)
keeps the pre-existing behaviour. A minor still sees their own XP, unranked.
"""
from app.models import User

ADULT_AGE = 18


def rankable_clause():
    """SQL predicate: rows that may appear on a public ranking."""
    return (User.age.is_(None)) | (User.age >= ADULT_AGE)


def rankable(age):
    return not (type(age) is int and age < ADULT_AGE)
