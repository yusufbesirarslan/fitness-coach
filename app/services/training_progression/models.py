"""Frozen value objects for the progression-analysis layer (Sprint 6 PR2).

Plain data holders (no logic, no DB, no Flask) that sit on top of the
training-history foundation's own value objects. The analysis layer computes over
them; the orchestrator composes them into a single normalized report future PRs
(adaptive coaching, program updates) consume. Style mirrors
``app/services/training_history/models.py``.
"""
from dataclasses import dataclass, field
from datetime import date

from app.services.training_history import WeeklyVolume


@dataclass(frozen=True)
class WeeklyStrength:
    """Per-week peak estimated strength for one 7-day window.

    ``best_estimated_1rm`` is the maximum Epley estimate across the window's real
    exercise entries (markers excluded); ``0.0`` when the window has no loaded
    work (rest week, or volume-only / bodyweight logging with no positive load).
    """
    week_start: date
    best_estimated_1rm: float = 0.0
    entry_count: int = 0


@dataclass(frozen=True)
class ProgressionReport:
    """Normalized, deterministic progression signals for a user's recent training.

    The canonical progression output. Every field is a safe, explicit value — when
    a concept cannot be computed reliably the neutral default is used rather than a
    speculative guess (``"flat"`` / ``False`` / ``"insufficient_data"``). Trends are
    ``"up" | "flat" | "down"``; ``load_consistency`` is
    ``"consistent" | "inconsistent" | "insufficient_data"``; ``next_signal`` is one
    of ``"progressing" | "plateau" | "deload" | "build_consistency" |
    "keep_pushing" | "insufficient_data"`` (single documented precedence).
    """
    weeks: int
    has_data: bool = False
    volume_trend: str = "flat"
    strength_trend: str = "flat"
    is_progressing: bool = False
    is_plateau: bool = False
    deload_due: bool = False
    load_consistency: str = "insufficient_data"
    next_signal: str = "insufficient_data"
    weekly_volume: list[WeeklyVolume] = field(default_factory=list)
    weekly_strength: list[WeeklyStrength] = field(default_factory=list)
