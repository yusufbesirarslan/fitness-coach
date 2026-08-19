"""Canonical Axis Insights read model (Progress Redesign PR3).

PR2 made Progress truthful: one server-owned authority answering *how am I
doing*. PR3 makes it useful without making it speculative, by answering three
further questions in exactly three slots:

    WHAT'S WORKING   what is genuinely going well
    WATCH THIS       what deserves attention
    NEXT MOVE        the safest useful next action

It is a **read model** — nothing is persisted, cached, scored or generated. It
owns exactly one decision: *which canonical signal earns which slot*. Every fact
it publishes was already decided by an existing authority, and there is no LLM,
prompt, provider or model fallback anywhere in the package (see
``docs/PROGRESS_INSIGHTS.md`` §"Why no LLM in V1" — the intelligence here is the
deterministic interpretation graph, and that is what makes it auditable, cheap,
reproducible, and incapable of overriding a canonical training decision).

Layering (mirrors ``progress_summary``):
- ``models``   — frozen value objects + every bounded code constant.
- ``analysis`` — pure slot selection (no DB/Flask/clock, fixture-free).
- ``payload``  — the JSON projection of the contract.
- ``__init__`` — public API + the ``build_progress_insights`` orchestrator.

There is deliberately no ``queries`` module: every fact this layer needs is
already owned upstream, so a read of its own would necessarily be a fact nobody
else owns — which is the definition of the second authority PR3 must not create.

Dependency direction is one-way and must stay that way::

    progress_insights.js → GET /api/progress/axis-insights → progress_insights
                                                              ↙          ↘
                                              progress_summary      training_planning
                                                              ↘          ↙
                                                        training_progression
                                                                  ↓
                                                          training_history

Nothing under ``progress_summary`` / ``training_*`` imports this package.
"""
from datetime import date

from app.services.progress_summary import (
    SUMMARY_WEEKS,
    build_window,
    summarize_consistency,
    summarize_performance,
)
from app.services.training_planning import derive_adaptive_plan
from app.services.training_progression import build_progression_report
from app.timeutil import app_today

from .analysis import (
    NEXT_MOVE_BY_WEEK_FOCUS,
    NON_ATTENTION_REASON_CODES,
    WATCH_BY_REASON_CODE,
    WORKING_BY_PERFORMANCE_STATE,
    select_next_move,
    select_watch,
    select_working,
)
from .models import (
    CONTRACT_VERSION,
    DOMAIN_TRAINING,
    DOMAINS,
    NEXT_BUILD_BASELINE,
    NEXT_DELOAD,
    NEXT_MAINTAIN_AND_CONSOLIDATE,
    NEXT_MAINTAIN_CURRENT_TRAINING,
    NEXT_MOVE_CODES,
    NEXT_PRIORITIZE_CONSISTENCY,
    NEXT_PROGRESS_TRAINING,
    SLOT_AVAILABLE,
    SLOT_EMPTY,
    SLOT_INSUFFICIENT_DATA,
    SLOT_STATUSES,
    WATCH_BUILD_CONSISTENCY,
    WATCH_CODES,
    WATCH_DELOAD_DUE,
    WATCH_PLATEAU_DETECTED,
    WATCH_STRENGTH_TREND_DOWN,
    WATCH_VOLUME_TREND_DOWN,
    WORKING_CODES,
    WORKING_TRAINING_CONSISTENT,
    WORKING_TRAINING_PROGRESSING,
    WORKING_TRAINING_STEADY,
    InsightSlot,
    NextMoveAction,
    ProgressInsights,
    UnknownCanonicalVocabulary,
)
from .payload import progress_insights_payload

__all__ = [
    "InsightSlot",
    "NextMoveAction",
    "ProgressInsights",
    "UnknownCanonicalVocabulary",
    "CONTRACT_VERSION",
    "INSIGHTS_WEEKS",
    "SLOT_STATUSES",
    "SLOT_AVAILABLE",
    "SLOT_EMPTY",
    "SLOT_INSUFFICIENT_DATA",
    "DOMAINS",
    "DOMAIN_TRAINING",
    "WORKING_CODES",
    "WORKING_TRAINING_PROGRESSING",
    "WORKING_TRAINING_STEADY",
    "WORKING_TRAINING_CONSISTENT",
    "WATCH_CODES",
    "WATCH_BUILD_CONSISTENCY",
    "WATCH_DELOAD_DUE",
    "WATCH_PLATEAU_DETECTED",
    "WATCH_VOLUME_TREND_DOWN",
    "WATCH_STRENGTH_TREND_DOWN",
    "NEXT_MOVE_CODES",
    "NEXT_BUILD_BASELINE",
    "NEXT_PRIORITIZE_CONSISTENCY",
    "NEXT_DELOAD",
    "NEXT_MAINTAIN_AND_CONSOLIDATE",
    "NEXT_PROGRESS_TRAINING",
    "NEXT_MAINTAIN_CURRENT_TRAINING",
    "WORKING_BY_PERFORMANCE_STATE",
    "WATCH_BY_REASON_CODE",
    "NON_ATTENTION_REASON_CODES",
    "NEXT_MOVE_BY_WEEK_FOCUS",
    "select_working",
    "select_watch",
    "select_next_move",
    "progress_insights_payload",
    "build_progress_insights",
]

# The analysis window. Not a second constant: it *is* the Progress summary's
# window, imported rather than re-declared, so the two surfaces cannot drift
# into analysing different periods and publishing contradictory cards.
INSIGHTS_WEEKS = SUMMARY_WEEKS


def build_progress_insights(
    user_id: int, *, end_day: date | None = None
) -> ProgressInsights:
    """The canonical Axis Insights for ``user_id`` over the fixed 4-week window.

    Read-only and deterministic: for the same persisted data and the same
    ``end_day`` the result is equal. ``end_day`` defaults to the application's
    single day authority (``app.timeutil.app_today``, fixed Europe/Istanbul);
    tests pass an explicit day to stay hermetic. It is a *service* argument, not
    a request parameter — the route pins it, exactly as PR2 does, because a
    client that can re-window the analysis until the advice improves owns the
    advice.

    **One history read, four canonical derivations.** ``ProgressionReport`` is
    the shared root of both authorities this layer consumes: ``progress_summary``
    derives PERFORMANCE and CONSISTENCY from it with pure public functions, and
    ``training_planning`` derives ``AdaptivePlan`` from it with another. Reading
    it once here and feeding all of them is therefore not a shortcut around
    either authority — it is the same computation those packages would perform,
    invoked through their own exported entry points. Calling
    ``build_progress_summary`` and ``build_adaptive_plan`` instead would issue a
    second identical history read for no additional correctness, and avoiding
    that needed no new seam: both pure functions were already public.

    The consequence worth naming: because both branches descend from *one*
    report computed on *one* resolved day, WHAT'S WORKING and NEXT MOVE cannot
    be evaluated over different windows even if the request straddles Istanbul
    midnight. That coherence is structural here, not a convention someone has to
    remember.

    Raises ``UnknownCanonicalVocabulary`` if an upstream contract yields a value
    this layer has no mapping for — fail closed rather than invent advice.
    """
    end_day = end_day or app_today()

    report = build_progression_report(user_id, weeks=INSIGHTS_WEEKS, end_day=end_day)

    # Canonical projections, each computed by the package that owns it.
    performance = summarize_performance(report)
    consistency = summarize_consistency(report)
    plan = derive_adaptive_plan(report)

    return ProgressInsights(
        window=build_window(end_day, INSIGHTS_WEEKS),
        working=select_working(performance, consistency),
        watch=select_watch(plan, consistency),
        next_move=select_next_move(plan),
    )
