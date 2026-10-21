"""Bounded live qualification: Sonnet 4.5 baseline vs Sonnet 5.5 / Haiku 5.5.

Operator tool for an AUTHORIZED NON-PRODUCTION account only. Synthetic
fixtures, no user data, no database, no consent records. It never runs by
accident:

    python -m scripts.ai_routing55_qualification              # dry run: plan + worst-case $
    python -m scripts.ai_routing55_qualification --live \\
        --confirm-synthetic-staging --profile eu              # real calls

`--live` refuses unless AWS_PROFILE is axisai-staging, the Region is
eu-central-1, the planned invocations fit --max-invocations and the
worst-case dollar bound fits --max-usd. Each call is one physical attempt
(SDK retries off). Every 5.5 payload is shaped by app.services.ai_model_policy
exactly as production admission shapes it, so the run exercises the shipped
request surface, not a hand-written one.

Output: one JSON line per attempt (fixture, model, latency, provider-reported
usage, stop reason, check result, estimated cost) and a summary. Model text is
not printed; only the fixture's own pass/fail check reads it.
"""
import argparse
import json
import os
import sys
import time

SONNET45 = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
PROFILES = {
    "eu": ("eu.anthropic.claude-sonnet-5-5", "eu.anthropic.claude-haiku-5-5"),
    "global": ("global.anthropic.claude-sonnet-5-5", "global.anthropic.claude-haiku-5-5"),
}


def _json_ok(text, required):
    start, end = text.find("{"), text.rfind("}")
    try:
        value = json.loads(text[start:end + 1])
    except Exception:
        return False
    return isinstance(value, dict) and all(k in value for k in required)


# (name, feature, class, max_tokens, system, user, check)
FIXTURES = [
    ("coach_single_turn", "coach", "heavy", 700,
     "You are a concise fitness coach. Answer in Turkish in at most 4 sentences.",
     "Haftada 3 gün antrenman yapıyorum, bacak gününde squat ağırlığımı nasıl artırırım?",
     lambda t: 40 <= len(t) <= 1500),
    ("training_plan_json", "training_plan", "heavy", 2000,
     "Return ONLY a JSON object with keys plan_name, days (list of {day, exercises}).",
     "Design a 3-day beginner full-body plan with 4 exercises per day, sets and reps.",
     lambda t: _json_ok(t, ("plan_name", "days"))),
    ("nutrition_plan_json", "nutrition_plan", "heavy", 2000,
     "Return ONLY a JSON object with keys daily_calories, meals (list).",
     "2200 kcal, high protein, Turkish breakfast, no pork. Three meals and one snack.",
     lambda t: _json_ok(t, ("daily_calories", "meals"))),
    ("nutrition_translate", "nutrition", "light", 24,
     "Translate the Turkish food name to a short English food search term. Output only the term.",
     "mercimek çorbası",
     lambda t: "lentil" in t.lower()),
    ("nutrition_macros_json", "nutrition", "light", 400,
     "Return ONLY JSON {\"items\": [{\"name\", \"kcal\", \"protein_g\"}]} per 100 g.",
     "tavuk göğsü, pirinç pilavı, yoğurt",
     lambda t: _json_ok(t, ("items",))),
    ("summary", "summary", "light", 500,
     "Summarize the conversation in 3 short bullet points.",
     "User: I want to lose 5 kg. Coach: Aim for a 400 kcal deficit. "
     "User: I train 3 days. Coach: Keep strength work, add walking.",
     lambda t: 20 <= len(t) <= 1200),
    ("refusal_probe_benign", "coach", "heavy", 300,
     "You are a fitness coach.",
     "Is creatine safe for a healthy adult? Answer briefly.",
     lambda t: len(t) > 10),
]


def _plan(sonnet55, haiku55):
    runs = []
    for fixture in FIXTURES:
        candidate = sonnet55 if fixture[2] == "heavy" else haiku55
        runs.append((fixture, SONNET45))
        runs.append((fixture, candidate))
    return runs


def _worst_case_usd(runs):
    from app.services import ai_input_budget, ai_usage
    total = 0.0
    for (name, feature, _cls, max_tokens, system, user, _check), model in runs:
        bound, _ = ai_input_budget.input_upper_bound(
            {"model": model, "system": system,
             "messages": [{"role": "user", "content": user}]})
        norm, profile = ai_usage._model_identity("bedrock", model)
        cost = ai_usage.estimated_cost_usd(
            "bedrock", norm, {"input_tokens": bound, "output_tokens": max_tokens}, profile)
        total += cost if cost is not None else 1.0  # unpriced = refuse cheaply
    return round(total, 4)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-synthetic-staging", action="store_true")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="eu")
    parser.add_argument("--max-invocations", type=int, default=len(FIXTURES) * 2)
    parser.add_argument("--max-usd", type=float, default=0.50)
    args = parser.parse_args(argv)

    sonnet55, haiku55 = PROFILES[args.profile]
    os.environ.update(AI_MODEL_ROUTING="claude55", BEDROCK_ENABLED="1",
                      BEDROCK_SONNET55_MODEL=sonnet55, BEDROCK_HAIKU55_MODEL=haiku55)
    from app.services import ai_model_policy, ai_usage
    ai_model_policy.validate_routing_config()

    runs = _plan(sonnet55, haiku55)
    worst = _worst_case_usd(runs)
    print(json.dumps({"planned_invocations": len(runs), "worst_case_usd": worst,
                      "profile": args.profile, "live": args.live}))
    if not args.live:
        return 0
    refusals = []
    if not args.confirm_synthetic_staging:
        refusals.append("--confirm-synthetic-staging is required")
    if os.environ.get("AWS_PROFILE") != "axisai-staging":
        refusals.append("AWS_PROFILE must be axisai-staging")
    if (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")) != "eu-central-1":
        refusals.append("AWS_REGION must be eu-central-1")
    if len(runs) > args.max_invocations:
        refusals.append("planned invocations exceed --max-invocations")
    if worst > args.max_usd:
        refusals.append("worst-case cost exceeds --max-usd")
    if refusals:
        print(json.dumps({"refused": refusals}))
        return 2

    from anthropic import AnthropicBedrock
    client = AnthropicBedrock(aws_region="eu-central-1", timeout=60.0, max_retries=0)
    results = []
    for (name, feature, _cls, max_tokens, system, user, check), model in runs:
        payload = {"model": model, "max_tokens": max_tokens, "system": system,
                   "temperature": 0.3, "messages": [{"role": "user", "content": user}]}
        if model != SONNET45:
            payload, _ = ai_model_policy.prepare(feature, "bedrock", payload)
        started = time.monotonic()
        row = {"fixture": name, "model": ai_usage.normalize_model("bedrock", model)}
        try:
            resp = client.messages.create(**payload)
            text = "".join(getattr(b, "text", "") for b in resp.content
                           if getattr(b, "type", None) == "text")
            usage = ai_usage.usage_from_response("bedrock", resp)
            norm, profile = ai_usage._model_identity("bedrock", model)
            row.update(ok=bool(check(text)), stop_reason=resp.stop_reason,
                       usage=usage, est_usd=ai_usage.estimated_cost_usd(
                           "bedrock", norm, usage, profile))
        except Exception as exc:  # recorded, never retried
            row.update(ok=False, error=type(exc).__name__)
        row["latency_ms"] = round((time.monotonic() - started) * 1000)
        results.append(row)
        print(json.dumps(row))
    summary = {}
    for row in results:
        s = summary.setdefault(row["model"], {"n": 0, "ok": 0, "usd": 0.0, "ms": 0})
        s["n"] += 1
        s["ok"] += int(row["ok"])
        s["usd"] = round(s["usd"] + (row.get("est_usd") or 0.0), 6)
        s["ms"] += row["latency_ms"]
    print(json.dumps({"summary": summary}))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
