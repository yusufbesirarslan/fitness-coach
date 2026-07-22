"""Adaptive Training Engine — weekly-program consumer layer (Sprint 6 PR5).

Translates the canonical ``AdaptivePlan`` (Sprint 6 PR3) into a structured weekly
**program recommendation** future UI/runtime consumers can present: which kind of week
to run, whether volume/intensity move, the absolute weekly-volume target implied by the
plan's own delta, and locale-neutral explanation keys for coach copy.

**An application/translation layer, not a planning engine.** ``AdaptivePlan`` remains
the sole planning authority; this layer is read-only over it and never independently
decides whether the user is progressing, plateauing, deload-due, overload-ready, or
should hold volume/intensity. Anything the plan does not model
(``models.UNSUPPORTED_CAPABILITIES``) is reported as unsupported rather than guessed.

Second consumer of the planner, after the PR4 coach-prompt contract
(``app/services/adaptive_plan_context.py``, which owns the only ``AdaptivePlan`` →
JSON serialization). This layer emits a frozen value object and no serialized form, so
the two consumers cannot drift into competing contracts.

Layering (mirrors the siblings):
- ``models``   — frozen value object (``WeeklyProgramRecommendation``).
- ``analysis`` — pure deterministic translation rules (fixture-free).
- ``payload``  — pure JSON-safe projection of the value object for HTTP consumers.
- ``__init__`` — public API + ``build_weekly_program`` orchestrator (the one impure
  boundary, only because it reads the plan through ``build_adaptive_plan``).

Dependency is strictly one-way: ``weekly_program`` → ``training_planning`` →
``training_progression`` → ``training_history``. No history is read here, and no
lower layer imports this one.

See ``docs/WEEKLY_PROGRAM.md``. The one runtime surface is the read-only
``GET /api/training/weekly-program`` (Sprint 6 PR5 part 2), a thin route over
``build_weekly_program`` + ``weekly_program_payload``. Still no schema, no migration,
no coach-prompt, no flag and no UI change — those remain later PRs' work.
"""
from datetime import date

from app.services.training_planning import build_adaptive_plan

from .analysis import (
    derive_explanation_keys,
    derive_weekly_program,
    select_volume_baseline,
    target_volume_for,
)
from .models import UNSUPPORTED_CAPABILITIES, WeeklyProgramRecommendation
from .payload import weekly_program_payload

__all__ = [
    "WeeklyProgramRecommendation",
    "UNSUPPORTED_CAPABILITIES",
    "select_volume_baseline",
    "target_volume_for",
    "derive_explanation_keys",
    "derive_weekly_program",
    "weekly_program_payload",
    "build_weekly_program",
]


def build_weekly_program(
    user_id: int, weeks: int = 4, *, end_day: date | None = None
) -> WeeklyProgramRecommendation:
    """Deterministic weekly-program recommendation for ``user_id``.

    Reads the plan once (which reads progression once, which reads history once),
    then translates it purely. ``end_day`` defaults to today (Istanbul); pass an
    explicit day for hermetic tests. Empty history (or ``weeks <= 0``) yields the
    fully neutral recommendation — hold everything, no volume numbers, no flags.
    """
    return derive_weekly_program(build_adaptive_plan(user_id, weeks, end_day=end_day))
