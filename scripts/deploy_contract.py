"""Canonical time bounds shared by the deploy controller and host helper.

R6-03A replaced the single-container recreate transaction with an exact-SHA
blue/green transaction (scripts/r6_deploy_transaction.py). The host budget is
therefore no longer one straight sum: after the release-forward phase the
transaction takes exactly ONE of two tails --

* failure tail: bounded diagnostics, then the rollback (route back, previous
  web re-verified, exact previous worker restored, candidate removed);
* success tail: bounded drain of the old backend, its retirement, and
  housekeeping that can never fail the committed release.

so the worst case is ``pre + forward + max(failure tail, success tail) +
cleanup``. Every figure below is a ceiling enforced with a monotonic deadline
and a bounded command timeout; nothing here waits unboundedly.
"""

from __future__ import annotations

from types import MappingProxyType


SSM_HEARTBEAT_MAX_AGE_SECONDS: int = 360
# Clock skew the send boundary tolerates before calling a heartbeat
# impossible. Its sibling above is canonical; this was a bare literal.
SSM_HEARTBEAT_FUTURE_SKEW_SECONDS: int = 60
SSM_EXECUTION_TIMEOUT_SECONDS: int = 2200
HOST_PHASE_SECONDS = MappingProxyType({
    "root_bootstrap": 10,
    "lock_acquisition": 60,
    "authority_and_stale_proof": 80,
    "clock_setup": 10,
    "git_preparation": 70,
    "release_forward": 1200,
    "diagnostics": 30,
    "release_rollback": 500,
    "retirement": 400,
    "cleanup": 20,
})
HOST_PRE_TRANSACTION_PHASES = (
    "root_bootstrap", "lock_acquisition", "authority_and_stale_proof",
    "clock_setup",
)
HOST_FAILURE_TAIL_SECONDS: int = (
    HOST_PHASE_SECONDS["diagnostics"] + HOST_PHASE_SECONDS["release_rollback"]
)
HOST_SUCCESS_TAIL_SECONDS: int = HOST_PHASE_SECONDS["retirement"]
HOST_WORST_CASE_SECONDS: int = (
    sum(HOST_PHASE_SECONDS[name] for name in HOST_PRE_TRANSACTION_PHASES)
    + HOST_PHASE_SECONDS["git_preparation"]
    + HOST_PHASE_SECONDS["release_forward"]
    + max(HOST_FAILURE_TAIL_SECONDS, HOST_SUCCESS_TAIL_SECONDS)
    + HOST_PHASE_SECONDS["cleanup"]
)
SSM_EXECUTION_MARGIN_SECONDS: int = (
    SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS
)
# Pre-send reserve + polling horizon + final invocation read + authority
# cleanup. The polling horizon keeps the 240-second recovery margin over the
# AWS expiry (60-second delivery + execution timeout).
POLL_HORIZON_SECONDS: int = 2500
CONTROLLER_REQUIRED_SECONDS: int = 300 + POLL_HORIZON_SECONDS + 30 + 60
CONTROLLER_STEP_MINUTES: int = 50

# ── R6-03A step ceilings inside the phases above ────────────────────────────
# Each step is bounded by min(its own ceiling, the phase cutoff). Step ceilings
# may add up to more than their phase: the phase cutoff, not the sum, is the
# binding bound, and a step that finds the phase exhausted fails closed.
RELEASE_FORWARD_STEP_SECONDS = MappingProxyType({
    "baseline": 60,            # route status, control-plane parity, revisions
    "migration_gate": 30,      # static delta classification, before any DB I/O
    "image_build": 600,        # git-archive context -> axisai-web:<sha>
    "image_proof": 30,         # docker run --network none cat BUILD_REVISION
    "release_prepare": 150,    # exactly once, before any candidate boots
    "candidate_start": 180,    # web_slot_runtime.py start (admission inside)
    "candidate_verify": 240,   # web_slot_runtime.py verify
    "pre_switch_verify": 90,   # immediate re-verify + route re-read
    "route_switch": 150,       # helper: nginx -t x2 + reload (+ restore path)
    "post_switch": 150,        # route, candidate, public health, mobile ingress
    "worker_update": 150,      # worker only, exact image, health + revision
    "checkout": 30,            # git reset --hard DEPLOY_SHA + HEAD proof
})
RELEASE_ROLLBACK_STEP_SECONDS = MappingProxyType({
    "route_restore": 150,
    "previous_verify": 90,
    "worker_restore": 150,
    "candidate_removal": 100,
})
# Old-backend drain ceiling. Derivation (docs/DEPLOYMENT.md "Old backend
# drain"): the longest normal request is an AI Coach turn, bounded by
# AI_COACH_TURN_TIMEOUT_SECONDS = 90 s, whose last provider call may overshoot
# it by at most one BEDROCK_CALL_TIMEOUT_SECONDS = 60 s -> 150 s. nginx's 300 s
# proxy_read_timeout is an IDLE bound, not a request bound, so a progressing
# stream could outlive any wait; it is cut at this ceiling, then given the
# slot's 45 s stop grace (gunicorn graceful_timeout 30 s) before SIGKILL.
OLD_BACKEND_DRAIN_MAX_SECONDS: int = 150
OLD_BACKEND_DRAIN_POLL_SECONDS: int = 2
RETIREMENT_STEP_SECONDS = MappingProxyType({
    "drain": OLD_BACKEND_DRAIN_MAX_SECONDS + 10,
    "backend_retirement": 100,   # docker stop --time 45 + rm / slot remove
    "housekeeping": 140,         # image cleanup + bounded BuildKit prune
})

if SSM_EXECUTION_MARGIN_SECONDS < 220:
    raise RuntimeError("invalid host timeout contract")
if sum(RELEASE_ROLLBACK_STEP_SECONDS.values()) > HOST_PHASE_SECONDS["release_rollback"]:
    raise RuntimeError("rollback steps exceed the rollback phase")
if sum(RETIREMENT_STEP_SECONDS.values()) > HOST_PHASE_SECONDS["retirement"]:
    raise RuntimeError("retirement steps exceed the retirement phase")
if any(seconds > HOST_PHASE_SECONDS["release_forward"]
       for seconds in RELEASE_FORWARD_STEP_SECONDS.values()):
    raise RuntimeError("a forward step exceeds the forward phase")
if POLL_HORIZON_SECONDS - (60 + SSM_EXECUTION_TIMEOUT_SECONDS) < 240:
    raise RuntimeError("polling horizon lost its recovery margin")
if CONTROLLER_REQUIRED_SECONDS >= CONTROLLER_STEP_MINUTES * 60:
    raise RuntimeError("invalid controller timeout contract")


def host_timeout_environment() -> dict[str, str]:
    """Return the complete fixed timeout contract for the host helper."""
    return {
        "SSM_EXECUTION_TIMEOUT_SECONDS": str(SSM_EXECUTION_TIMEOUT_SECONDS),
        "HOST_WORST_CASE_SECONDS": str(HOST_WORST_CASE_SECONDS),
        "SSM_EXECUTION_MARGIN_SECONDS": str(SSM_EXECUTION_MARGIN_SECONDS),
        **{
            f"HOST_{name.upper()}_SECONDS": str(seconds)
            for name, seconds in HOST_PHASE_SECONDS.items()
        },
    }
