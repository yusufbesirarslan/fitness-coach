"""Best-effort, fixed-cardinality workout-session lifecycle telemetry."""
from app.services import runtime_metrics


METRIC_NAME = "WorkoutSessionLifecycle"
EVENTS = frozenset({
    "started",
    "resumed",
    "checkpointed",
    "abandoned",
    "completed",
    "revision_conflict",
})


def record_lifecycle_event(event: str) -> None:
    """Buffer one known event; observability can never affect execution."""
    if event not in EVENTS:
        return
    try:
        runtime_metrics.increment(METRIC_NAME, dimensions={"Event": event})
    except Exception:  # noqa: BLE001 - metrics are never execution authority
        pass
