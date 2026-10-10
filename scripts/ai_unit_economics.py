#!/usr/bin/env python3
"""Read-only, credential-free AI usage aggregation. Never outputs account IDs/content."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import statistics
import uuid

FEATURES = ("coach", "training_plan", "nutrition_plan", "nutrition", "menu_extract",
            "menu_ocr", "vision", "summary", "health_probe", "other")
ATTEMPTS = ("success", "provider_error", "timeout", "client_disconnect")
TOKENS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
ADJUSTMENTS = ("explicit_non_application", "billing_timing")


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("UTC interval endpoints must be ISO timestamps")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("explicit UTC (+00:00 or Z) is required")
    return parsed.timestamp()


def _utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid numeric quantity")
    try:
        number = Decimal(str(value))
    except Exception:
        raise ValueError("invalid numeric quantity") from None
    if not number.is_finite() or number < 0:
        raise ValueError("quantities must be finite and nonnegative")
    return number


def _qty(value):
    number = _number(value)
    if number is not None and number != number.to_integral_value():
        raise ValueError("token quantities must be integers")
    return None if number is None else int(number)


def _money(value):
    number = _number(value)
    return None if number is None else float(number)


def _structural_id(value, job=False):
    if not isinstance(value, str):
        return None
    if job:
        try:
            return value if str(uuid.UUID(value)) == value.lower() else None
        except ValueError:
            return None
    return value if len(value) == 16 and all(c in "0123456789abcdef" for c in value) else None


def _action(event):
    rid = _structural_id(event.get("request_id"))
    if rid:
        return "request:" + rid
    jid = _structural_id(event.get("job_id"), job=True)
    return "job:" + jid if jid else None


def _window(events, start, end):
    if start >= end:
        raise ValueError("interval must have start < end")
    selected, seen = [], set()
    for event in events:
        if event.get("event") != "ai_usage":
            continue
        ts = _number(event.get("ts"))
        if ts is None:
            raise ValueError("usage event timestamp is required")
        if not start <= float(ts) < end:
            continue
        if event.get("provider") not in ("bedrock", "openai", "other"):
            raise ValueError("unknown provider family")
        if event.get("feature") not in FEATURES:
            raise ValueError("unknown feature taxonomy")
        aid = event.get("admission_id")
        if aid and event.get("outcome") in ATTEMPTS:
            key = (aid, event.get("attempt"))
            if key in seen:
                raise ValueError("duplicate physical attempt; fix export overlap")
            seen.add(key)
        selected.append(event)
    return selected


def _reported(events, token):
    if any(e.get("usage_source") != "provider" or e.get(token) is None for e in events):
        return None
    return sum(_qty(e[token]) for e in events)


def _known(events, token):
    return sum(_qty(e.get(token)) or 0 for e in events if e.get("usage_source") == "provider")


def _complete_economics(event):
    return (event.get("estimated_cost_usd") is not None and event.get("usage_source") == "provider"
            and all(event.get(t) is not None for t in TOKENS))


def _cost(events):
    return float(sum((_number(e.get("estimated_cost_usd")) or Decimal(0)) for e in events))


def _percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def summarize(events, start, end):
    selected = _window(events, start, end)
    paid = [e for e in selected if e.get("outcome") in ATTEMPTS]
    by_feature = {}
    for feature in FEATURES:
        rows = [e for e in paid if e["feature"] == feature]
        if not rows:
            continue
        actions = {_action(e) for e in rows}
        actions.discard(None)
        complete_actions = all(_action(e) is not None for e in rows)
        costs_known = all(_complete_economics(e) for e in rows)
        total = _cost(rows)
        by_feature[feature] = {
            "attempts": len(rows), "logical_actions": len(actions) if complete_actions else None,
            "known_logical_actions": len(actions), "uncorrelated_attempts": sum(_action(e) is None for e in rows),
            "provider_tokens": {t: _reported(rows, t) for t in TOKENS},
            "known_provider_tokens": {t: _known(rows, t) for t in TOKENS},
            "uncertain_token_attempts": {t: sum(e.get("usage_source") != "provider" or e.get(t) is None
                                                  for e in rows) for t in TOKENS},
            "estimated_input_tokens": sum(_qty(e.get("input_tokens")) or 0 for e in rows
                                           if e.get("usage_source") == "estimated"),
            "estimated_gross_usd": total, "unpriced_attempts": sum(e.get("estimated_cost_usd") is None for e in rows),
            "unknown_output_attempts": sum(e.get("output_tokens") is None for e in rows),
            "retry_attempts": sum((_qty(e.get("attempt")) or 0) > 1 for e in rows),
            "fallback_attempts": sum(e.get("fallback") is True for e in rows),
            "fallback_unknown_attempts": sum("fallback" not in e for e in rows),
            "failure_attempts": sum(e["outcome"] != "success" for e in rows),
            "estimated_cost_per_known_action": (total / len(actions)
                if complete_actions and actions and costs_known and feature != "health_probe" else None),
        }
    users = {}
    for e in paid:
        subject = e.get("subject_id")
        if isinstance(subject, int) and not isinstance(subject, bool) and subject > 0:
            users.setdefault(subject, []).append(e)
    user_costs = [_cost(rows) for rows in users.values() if all(_complete_economics(e) for e in rows)]
    invalid_subjects = sum(e.get("subject_id") is not None and not (isinstance(e.get("subject_id"), int)
        and not isinstance(e.get("subject_id"), bool) and e["subject_id"] > 0) for e in paid)
    expected_null = sum(e.get("subject_id") is None and e["feature"] == "health_probe" for e in paid)
    nulls = sum(e.get("subject_id") is None for e in paid)
    return {
        "interval": {"start": _utc(start), "end": _utc(end), "end_exclusive": True},
        "attempts": len(paid), "by_feature": by_feature,
        "outcomes": {outcome: sum(e.get("outcome") == outcome for e in paid) for outcome in ATTEMPTS},
        "rejections": {outcome: sum(e.get("outcome") == outcome for e in selected)
                       for outcome in ("guard_rejected", "input_budget_rejected")},
        "attribution": {"subject_attributed_attempts": sum(len(r) for r in users.values()),
                        "null_subject_attempts": nulls, "invalid_subject_attempts": invalid_subjects, "expected_null_subject": expected_null,
                        "unexpected_null_subject": nulls - expected_null,
                        "other_feature_attempts": sum(e["feature"] == "other" for e in paid)},
        "subjects": {"active_ai_users": len(users),
                     "users_with_complete_economics": len(user_costs),
                     "users_with_incomplete_economics": len(users) - len(user_costs),
                     "median_estimated_usd": statistics.median(user_costs) if user_costs else None,
                     "p95_estimated_usd": _percentile(user_costs, .95),
                     "unpriced_attempts": sum(e.get("estimated_cost_usd") is None for r in users.values() for e in r)},
        "estimated_gross_usd": _cost(paid),
        "economics_basis": "estimated gross list price; partial where usage or prices are missing",
    }


def reconcile(events, aws):
    start, end = parse_time(aws["start"]), parse_time(aws["end"])
    rows = [e for e in _window(events, start, end)
            if e["provider"] == "bedrock" and e.get("outcome") in ATTEMPTS]
    tolerance = _qty(aws.get("token_tolerance"))
    if tolerance is None or not aws.get("tolerance_reason"):
        raise ValueError("explicit token tolerance and justification are required")
    adjustments = aws.get("adjustments", [])
    for adjustment in adjustments:
        if (adjustment.get("category") not in ADJUSTMENTS or adjustment.get("status") not in
                ("CONFIRMED", "SUPPORTED", "ESTIMATED", "UNPROVEN") or not adjustment.get("evidence")):
            raise ValueError("adjustments need a bounded category, status and evidence")
    summary = summarize(rows, start, end)
    billed = _number(aws.get("billed_gross_usd"))
    if billed is None:
        raise ValueError("AWS gross billed dollars are required")
    result = {"interval": summary["interval"], "application_attempts": sum(e["feature"] != "health_probe" for e in rows),
              "health_probe_attempts": sum(e["feature"] == "health_probe" for e in rows),
              "aws_invocations": _qty(aws.get("invocations")),
              "token_tolerance": tolerance, "within_token_tolerance": True,
              "adjustments": [{"category": a["category"], "status": a["status"],
                               **{t: _qty(a.get(t)) for t in TOKENS}} for a in adjustments]}
    for token, label in zip(TOKENS, ("input", "output", "cache_write", "cache_read")):
        aws_count = _qty(aws.get(token))
        app_count = _known(rows, token)
        uncertain = sum(e.get("usage_source") != "provider" or e.get(token) is None for e in rows)
        adjustment_count = sum(_qty(a.get(token)) or 0 for a in adjustments if a["status"] == "CONFIRMED")
        delta = None if aws_count is None else aws_count - app_count
        residual = None if delta is None else delta - adjustment_count
        result.update({"aws_" + token: aws_count, "app_" + token: app_count,
                       label + "_delta": delta,
                       label + "_delta_pct": (None if not aws_count else 100 * delta / aws_count),
                       "adjustment_" + token: adjustment_count,
                       "unattributed_" + token: residual,
                       "uncertain_" + label + "_attempts": uncertain})
        # Missing AWS cache observations do not become zero-cache evidence.
        if residual is None or abs(residual) > tolerance or uncertain:
            result["within_token_tolerance"] = False
        if aws_count is not None and residual is not None and abs(residual) > tolerance:
            result["within_token_tolerance"] = False
    estimated = Decimal(str(summary["estimated_gross_usd"]))
    attributed = Decimal(0)
    classes = aws.get("billing_classes", [])
    # Optional per-class AWS token component charges establish billed attribution.
    # Without this mapping the entire bill remains financially UNATTRIBUTED.
    identities = set()
    component_charges = Decimal(0)
    component_quantities = {t: 0 for t in TOKENS}
    for cls in classes:
        identity = (cls.get("model"), cls.get("billing_profile"))
        if (identity in identities or identity[0] not in ("claude-sonnet-4-5", "claude-haiku-4-5")
                or identity[1] not in ("global", "geographic", "direct")):
            raise ValueError("billing classes require distinct bounded model/profile identities")
        identities.add(identity)
        for token in TOKENS:
            quantity = _qty(cls.get(token))
            charge = _number(cls.get(token + "_gross_usd"))
            if charge and not quantity:
                raise ValueError("billed component charges require positive token quantities")
            component_quantities[token] += quantity or 0
            component_charges += charge or Decimal(0)
    if component_charges > billed:
        raise ValueError("billing component charges exceed aggregate gross bill")
    billed_quantities = aws.get("billed_token_quantities", {})
    for token in TOKENS:
        aggregate = _qty(billed_quantities.get(token, aws.get(token)))
        if aggregate is not None and component_quantities[token] > aggregate:
            raise ValueError("billing class quantities exceed aggregate observations")
    for cls in classes:
        matching = [e for e in rows if e.get("model") == cls.get("model") and
                    e.get("billing_profile", aws.get("legacy_billing_profile")) == cls.get("billing_profile")]
        for token in TOKENS:
            quantity = _qty(cls.get(token))
            charge = _number(cls.get(token + "_gross_usd"))
            known = _known(matching, token)
            if quantity is not None and known > quantity:
                raise ValueError("matched application tokens exceed billed class component")
            if quantity and charge is not None:
                attributed += Decimal(known) * charge / quantity
    result.update(aws_billed_gross_usd=float(billed), app_estimated_list_usd=float(estimated),
                  usd_delta=float(billed - estimated), usd_delta_pct=float((billed - estimated) / billed * 100) if billed else None,
                  attributed_usd=float(attributed) if classes else None,
                  unattributed_usd=float(max(Decimal(0), billed - attributed)),
                  overattributed_usd=float(max(Decimal(0), attributed - billed)),
                  billed_attribution_basis="AWS token-component charges by explicit model/profile class" if classes else "NOT AVAILABLE",
                  application_summary=summary)
    return result


def load_usage(path):
    events = []
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            if line.startswith("[AI-USAGE] "):
                line = line[len("[AI-USAGE] "):]
            value = json.loads(line)
            if "log" in value:
                log = value["log"]
                if not log.startswith("[AI-USAGE] "):
                    continue
                value = json.loads(log[len("[AI-USAGE] "):])
            if value.get("event") == "ai_usage":
                events.append(value)
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("usage")
    summary.add_argument("--start", required=True)
    summary.add_argument("--end", required=True)
    reconciliation = commands.add_parser("reconcile")
    reconciliation.add_argument("--usage", required=True)
    reconciliation.add_argument("--aws", required=True)
    args = parser.parse_args()
    try:
        events = load_usage(args.usage)
        result = (summarize(events, parse_time(args.start), parse_time(args.end)) if args.mode == "summarize"
                  else reconcile(events, json.loads(Path(args.aws).read_text(encoding="utf-8"))))
    except (ValueError, KeyError, TypeError, OSError):
        parser.exit(2, "Invalid input; check sanitized usage, quantities, and explicit UTC interval.\n")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
