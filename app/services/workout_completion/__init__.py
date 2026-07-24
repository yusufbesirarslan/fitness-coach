"""Canonical workout-completion mutation boundary (Sprint 7 PR2).

The single owner of *confirmed workout completion* writes. Both production
completion writers — ``POST /workout/complete`` and the AI-coach gym-photo tool —
delegate here; evidence-only writers (AI-coach / MCP / manual exercise logging)
stay separate and never call this module.

Public contract:
  * :func:`complete_workout` — the atomic, idempotent, replay-safe mutation.
  * :class:`CompleteWorkoutCommand` / :class:`CompletionResult` /
    :class:`CompletionOutcome` — the typed input/output.
  * :func:`already_completed_today` — read-only completion preflight for entry
    paths to skip expensive provider work on obvious replays.

Layering mirrors ``workout_state``: ``queries`` (impure reads) + ``service``
(the mutation) + ``models`` (framework-free value objects).
"""
from .models import CompleteWorkoutCommand, CompletionOutcome, CompletionResult
from .queries import already_completed_today
from .service import complete_workout

__all__ = [
    "complete_workout",
    "already_completed_today",
    "CompleteWorkoutCommand",
    "CompletionResult",
    "CompletionOutcome",
]
