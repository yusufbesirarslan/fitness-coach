"""Frozen value object for the adaptive-planning layer (Sprint 6 PR3).

A plain data holder (no logic, no DB, no Flask) describing the safest next weekly
training adjustment. Not to be confused with the ``TrainingPlan`` model or
``training_generation/`` — those produce LLM-authored workout *content*; this is
the deterministic *adjustment recommendation* layered on top of the progression
engine. Style mirrors ``app/services/training_progression/models.py``.
"""
from dataclasses import dataclass, field

from app.services.training_progression import ProgressionReport


@dataclass(frozen=True)
class AdaptivePlan:
    """Normalized, deterministic weekly plan recommendation for a user.

    The canonical planning output. Every field is a safe, explicit value — when a
    decision cannot be computed reliably the neutral default is used rather than a
    speculative guess (``"hold"`` / ``False`` / ``"insufficient_data"``).

    ``week_focus`` is one of ``"overload" | "steady" | "maintenance" | "deload" |
    "build_consistency" | "insufficient_data"``; ``volume_action`` is
    ``"increase" | "hold" | "decrease"``; ``intensity_action`` is
    ``"progress" | "hold" | "deload"``. ``volume_delta_pct`` is a signed fraction
    (``0.0`` whenever ``volume_action == "hold"``). ``reason_codes`` are ordered,
    locale-neutral machine codes — position 0 is always the primary cause, and the
    neutral default carries ``"insufficient_history"`` so even a default-constructed
    plan explains itself (UI copy is a later PR's concern). The full
    ``ProgressionReport`` is embedded so consumers get the underlying trends and
    weekly series without a second history read.
    """
    weeks: int
    has_data: bool = False
    week_focus: str = "insufficient_data"
    volume_action: str = "hold"
    intensity_action: str = "hold"
    volume_delta_pct: float = 0.0
    overload_ready: bool = False
    maintenance_recommended: bool = False
    reason_codes: tuple[str, ...] = ("insufficient_history",)
    progression: ProgressionReport = field(
        default_factory=lambda: ProgressionReport(weeks=0)
    )
