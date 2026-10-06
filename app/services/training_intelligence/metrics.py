"""Best-effort, fixed-cardinality Training Insight telemetry.

One metric, one ``Event`` dimension from a closed set. Never a user, session,
exercise, evidence value or diagnostic payload as a label.
"""
from app.services import runtime_metrics

METRIC_NAME = "TrainingInsight"
EVENTS = frozenset({
    "insight_generated",
    "insight_insufficient",
    "insight_not_comparable",
    "insight_unavailable",
    "history_row_excluded",
})


def record_insight_event(event: str) -> None:
    if event not in EVENTS:
        return
    try:
        runtime_metrics.increment(METRIC_NAME, dimensions={"Event": event})
    except Exception:  # noqa: BLE001 - metrics are never projection authority
        pass
