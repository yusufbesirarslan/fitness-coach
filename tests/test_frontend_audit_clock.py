import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "docs" / "frontend-readiness" / "sprint-0" / "scenario-clocks.json"


def test_audit_clock_overrides_all_canonical_time_helpers_and_restores():
    from app.timeutil import APP_TZ, audit_clock, app_now, app_today, day_key, utc_day_bounds

    real_before = app_now()
    fixed = datetime.fromisoformat("2026-07-20T09:30:00+03:00")
    with audit_clock(fixed):
        assert app_now() == fixed
        assert app_today().isoformat() == "2026-07-20"
        assert day_key() == "2026-07-20"
        start, end = utc_day_bounds()
        assert start.isoformat() == "2026-07-19T21:00:00"
        assert end.isoformat() == "2026-07-20T21:00:00"
    restored = app_now()
    assert restored.tzinfo == APP_TZ
    assert restored != fixed
    assert restored >= real_before


def test_audit_clock_is_nested_and_timezone_normalized():
    from app.timeutil import audit_clock, app_now

    outer = datetime.fromisoformat("2026-07-20T06:30:00+00:00")
    inner = datetime.fromisoformat("2026-07-21T12:00:00+03:00")
    with audit_clock(outer):
        assert app_now().isoformat() == "2026-07-20T09:30:00+03:00"
        with audit_clock(inner):
            assert app_now() == inner
        assert app_now().isoformat() == "2026-07-20T09:30:00+03:00"


def test_every_scenario_declares_deterministic_date_expectations():
    document = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.0.0"
    required = {
        "fixed_current_datetime", "timezone", "expected_local_date",
        "workout_day_state", "history_boundaries", "wearable_last_sync",
        "date_sensitive_expected_output",
    }
    assert {
        "anonymous", "new-empty", "partial", "active-rest-day",
        "active-workout", "completed-workout", "progress-history",
        "coach-history", "wearable-connected", "wearable-disconnected",
        "social-empty",
    } <= {scenario["id"] for scenario in document["scenarios"]}
    for scenario in document["scenarios"]:
        assert required <= set(scenario)
        assert scenario["timezone"] == "Europe/Istanbul"
        assert datetime.fromisoformat(scenario["fixed_current_datetime"]).tzinfo is not None
