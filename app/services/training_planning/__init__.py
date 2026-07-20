"""Adaptive Training Engine — adaptive-planning layer (Sprint 6 PR3).

Turns the progression engine's canonical signals (Sprint 6 PR2) into a
deterministic weekly training-plan recommendation AxisAI can consume for adaptive
coaching: what kind of week comes next, whether volume/intensity should move, and
whether the user is ready for overload or due a maintenance/deload week. Pure
composition on top of ``training_progression`` — it never re-derives windowing,
marker exclusion, or signal precedence; dependency is strictly one-way
(planning → progression → history).

NOT the plan generator: ``training_generation/`` + the ``TrainingPlan`` model
produce LLM-authored workout content at ``POST /training-plan``. This layer emits
a deterministic *adjustment recommendation* object for future coach/UI wiring.

Layering (mirrors the siblings):
- ``models``   — frozen value object (``AdaptivePlan``).
- ``analysis`` — pure deterministic decision rules (fixture-free).
- ``__init__`` — public API + ``build_adaptive_plan`` orchestrator (the one impure
  boundary, only because it reads signals through ``build_progression_report``).

See ``docs/TRAINING_PLANNING.md``. This layer adds no schema, no route, and no
coach-prompt change — later PRs wire it into runtime.
"""
from datetime import date

from app.services.training_progression import build_progression_report

from .analysis import (
    derive_adaptive_plan,
    derive_intensity_action,
    derive_reason_codes,
    derive_volume_action,
    derive_week_focus,
    volume_delta_for,
)
from .models import AdaptivePlan

__all__ = [
    "AdaptivePlan",
    "derive_week_focus",
    "derive_volume_action",
    "derive_intensity_action",
    "volume_delta_for",
    "derive_reason_codes",
    "derive_adaptive_plan",
    "build_adaptive_plan",
]


def build_adaptive_plan(
    user_id: int, weeks: int = 4, *, end_day: date | None = None
) -> AdaptivePlan:
    """Deterministic weekly plan recommendation for ``user_id``'s recent training.

    Reads progression signals once (which reads history once through the
    foundation), then derives the plan purely. ``end_day`` defaults to today
    (Istanbul); pass an explicit day for hermetic tests. Empty history (or
    ``weeks <= 0``) yields the fully neutral plan — hold everything, no flags.
    """
    report = build_progression_report(user_id, weeks, end_day=end_day)
    return derive_adaptive_plan(report)
