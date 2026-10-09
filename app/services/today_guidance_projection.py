"""Pure bounded LP17 projection over already resolved canonical payloads."""
import json

from app.today_guidance import decide_today_guidance

CONTRACT_VERSION = 1
MAX_PAYLOAD_BYTES = 16384


def project_guidance(training, nutrition, checkin):
    """No reads or new domain decisions; all absence carries source state."""
    decision = decide_today_guidance(
        read_ok=True, primary_state=training["status"], action=training["action"])
    result = {
        "contract_version": CONTRACT_VERSION,
        "day": training["date"],
        "timezone": "Europe/Istanbul",
        "training": {
            "state": "available",
            "status": training["status"],
            "action": training["action"],
            "workout": training["workout"],
            "daily_context": training["daily_context"],
            "guidance": {"state": decision.state, "kind": decision.primary_kind},
        },
        "nutrition": {
            "state": "unavailable" if nutrition is None else "available",
            "facts": None if nutrition is None else {
                key: nutrition[key] for key in ("target", "intake", "plan", "next_action")},
        },
        "hydration": ({"state": "unavailable", "amount": None, "unit": "glass"}
                      if nutrition is None else nutrition["hydration"]),
        "checkin": {
            "state": ("unavailable" if checkin is None else
                      "available" if checkin["check_ins"] else "empty"),
            "current_week": None if checkin is None else checkin["current_week"],
            "latest": (checkin["check_ins"][0]
                       if checkin is not None and checkin["check_ins"] else None),
        },
        "recovery": {"state": "unsupported", "facts": None},
        "action_priority": {"state": "not_established", "primary": None},
        "freshness": {"cacheable": False, "revision": None,
                      "consistency": "independent_source_snapshots"},
    }
    # Canonical bounds limit text/items; this final ceiling also protects against
    # future upstream expansion. Refuse, never truncate semantic facts.
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("guidance payload exceeds bound")
    return result
