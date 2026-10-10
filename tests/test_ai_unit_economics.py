"""Financial authority stays separate from estimated list economics."""
import importlib.util
from pathlib import Path

import pytest


def tool():
    path = Path(__file__).resolve().parents[1] / "scripts/ai_unit_economics.py"
    spec = importlib.util.spec_from_file_location("economics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(**kwargs):
    return {"event": "ai_usage", "ts": 100, "provider": "bedrock",
            "model": "claude-sonnet-4-5", "billing_profile": "global",
            "feature": "coach", "request_id": "0123456789abcdef", "subject_id": 7,
            "outcome": "success", "attempt": 1, "usage_source": "provider",
            "input_tokens": 100, "output_tokens": 20, "cache_write_tokens": 0,
            "cache_read_tokens": 0, "estimated_cost_usd": 0.0006, **kwargs}


def test_retry_is_not_a_second_action_and_subject_ids_never_leave_tool():
    result = tool().summarize([event(), event(attempt=2)], 0, 200)
    assert result["by_feature"]["coach"]["attempts"] == 2
    assert result["by_feature"]["coach"]["logical_actions"] == 1
    assert result["by_feature"]["coach"]["retry_attempts"] == 1
    assert result["subjects"]["active_ai_users"] == 1
    assert result["subjects"]["median_estimated_usd"] == pytest.approx(0.0012)
    assert "subject_id" not in str(result)
    assert "request_id" not in str(result)


def test_unknown_output_and_action_are_not_zero_or_an_invented_denominator():
    result = tool().summarize([event(outcome="timeout", request_id=None,
                                   usage_source="estimated", output_tokens=None)], 0, 200)
    row = result["by_feature"]["coach"]
    assert row["logical_actions"] is None
    assert row["provider_tokens"]["output_tokens"] is None
    assert row["unknown_output_attempts"] == 1
    assert row["estimated_input_tokens"] == 100


def test_explicit_utc_interval_excludes_end_and_rejections():
    result = tool().summarize([event(ts=199), event(ts=200),
                               event(ts=150, outcome="guard_rejected")], 0, 200)
    assert result["attempts"] == 1
    assert result["rejections"]["guard_rejected"] == 1


def test_reconcile_tokens_first_and_never_calls_list_estimates_billed_attribution():
    aws = {"start": "1970-01-01T00:00:00Z", "end": "1970-01-01T00:03:20Z",
           "input_tokens": 120, "output_tokens": 30, "cache_write_tokens": 0,
           "cache_read_tokens": 0, "billed_gross_usd": "0.001",
           "token_tolerance": 0, "tolerance_reason": "integer quantities for complete UTC interval"}
    result = tool().reconcile([event()], aws)
    assert result["input_delta"] == 20
    assert result["output_delta"] == 10
    assert result["app_estimated_list_usd"] == pytest.approx(0.0006)
    assert result["usd_delta"] == pytest.approx(0.0004)
    assert result["attributed_usd"] is None
    assert result["unattributed_usd"] == pytest.approx(0.001)
    assert result["within_token_tolerance"] is False


def test_confirmed_adjustment_is_visible_but_unproven_adjustment_cannot_hide_residual():
    aws = {"start": "1970-01-01T00:00:00Z", "end": "1970-01-01T00:03:20Z",
           "input_tokens": 120, "output_tokens": 30, "billed_gross_usd": "0.001",
           "token_tolerance": 0, "tolerance_reason": "integer tokens",
           "adjustments": [{"category": "explicit_non_application", "status": "CONFIRMED",
                            "evidence": "sanitized operator export", "input_tokens": 5,
                            "output_tokens": 4},
                           {"category": "billing_timing", "status": "UNPROVEN",
                            "evidence": "hypothesis", "input_tokens": 15, "output_tokens": 6}]}
    result = tool().reconcile([event()], aws)
    assert result["unattributed_input_tokens"] == 15
    assert result["unattributed_output_tokens"] == 6
    assert len(result["adjustments"]) == 2


def test_bad_intervals_and_duplicate_attempts_are_refused():
    mod = tool()
    with pytest.raises(ValueError):
        mod.summarize([], 200, 0)
    with pytest.raises(ValueError):
        mod.parse_time("2026-10-01T00:00:00")
    with pytest.raises(ValueError):
        mod.summarize([event(admission_id="a" * 32), event(admission_id="a" * 32)], 0, 200)


def aws_input(**kwargs):
    return {"start": "1970-01-01T00:00:00Z", "end": "1970-01-01T00:03:20Z",
            "input_tokens": 100, "output_tokens": 20, "cache_write_tokens": 0,
            "cache_read_tokens": 0, "billed_gross_usd": "0.002",
            "token_tolerance": 0, "tolerance_reason": "integer observations", **kwargs}


def test_duplicate_billing_class_cannot_hide_unattributed_dollars():
    cls = {"model": "claude-sonnet-4-5", "billing_profile": "global",
           "input_tokens": 200, "input_tokens_gross_usd": "0.002"}
    with pytest.raises(ValueError):
        tool().reconcile([event()], aws_input(input_tokens=200, billing_classes=[cls, cls]))


def test_unpriced_users_are_not_zero_cost_users():
    result = tool().summarize([event(estimated_cost_usd=None)], 0, 200)
    assert result["subjects"]["active_ai_users"] == 1
    assert result["subjects"]["users_with_incomplete_economics"] == 1
    assert result["subjects"]["median_estimated_usd"] is None
    assert result["subjects"]["p95_estimated_usd"] is None


def test_partial_output_cannot_qualify_action_or_user_cost():
    rows = [event(), event(outcome="timeout", attempt=2, usage_source="estimated",
                           output_tokens=None, estimated_cost_usd=0.0003)]
    result = tool().summarize(rows, 0, 200)
    coach = result["by_feature"]["coach"]
    assert coach["logical_actions"] == 1
    assert coach["estimated_cost_per_known_action"] is None
    assert coach["provider_tokens"]["output_tokens"] is None
    assert coach["known_provider_tokens"]["output_tokens"] == 20
    assert coach["uncertain_token_attempts"]["output_tokens"] == 1
    assert result["subjects"]["median_estimated_usd"] is None


def test_unknown_cache_cannot_pass_all_token_qualification():
    assert tool().reconcile([event()], aws_input(cache_read_tokens=None))["within_token_tolerance"] is False
    assert tool().reconcile([event(cache_write_tokens=None)], aws_input())["within_token_tolerance"] is False


def test_invalid_subject_remains_explicitly_accounted():
    result = tool().summarize([event(subject_id="7")], 0, 200)
    assert result["attribution"]["invalid_subject_attempts"] == 1
    assert result["attribution"]["subject_attributed_attempts"] == 0


def test_billing_component_charges_cannot_exceed_aggregate_bill():
    cls = {"model": "claude-sonnet-4-5", "billing_profile": "global",
           "input_tokens": 100, "input_tokens_gross_usd": "0.003"}
    with pytest.raises(ValueError):
        tool().reconcile([event()], aws_input(billing_classes=[cls]))


def test_app_tokens_cannot_extrapolate_beyond_a_billed_component():
    cls = {"model": "claude-sonnet-4-5", "billing_profile": "global",
           "input_tokens": 50, "input_tokens_gross_usd": "0.001"}
    with pytest.raises(ValueError):
        tool().reconcile([event()], aws_input(input_tokens=200, billing_classes=[cls]))


def test_collapsed_unknown_identity_cannot_assign_billed_dollars():
    cls = {"model": "other", "billing_profile": "unknown",
           "input_tokens": 100, "input_tokens_gross_usd": "0.001"}
    with pytest.raises(ValueError):
        tool().reconcile([event(model="other", billing_profile="unknown")], aws_input(billing_classes=[cls]))
