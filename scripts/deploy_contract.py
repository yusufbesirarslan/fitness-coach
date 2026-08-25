"""Canonical time bounds shared by the deploy controller and host helper."""

from __future__ import annotations

from types import MappingProxyType


SSM_HEARTBEAT_MAX_AGE_SECONDS: int = 360
SSM_EXECUTION_TIMEOUT_SECONDS: int = 1800
HOST_PHASE_SECONDS = MappingProxyType({
    "root_bootstrap": 10,
    "lock_acquisition": 60,
    "authority_and_stale_proof": 80,
    "clock_setup": 10,
    "git_fetch_checkout": 70,
    "candidate_build_start": 620,
    "candidate_revision_health": 160,
    "diagnostics": 30,
    "rollback_build_start": 440,
    "rollback_revision_health": 80,
    "cleanup": 20,
})
HOST_WORST_CASE_SECONDS: int = sum(HOST_PHASE_SECONDS.values())
SSM_EXECUTION_MARGIN_SECONDS: int = (
    SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS
)
CONTROLLER_REQUIRED_SECONDS: int = 300 + 2100 + 30 + 60

if HOST_WORST_CASE_SECONDS != 1580 or SSM_EXECUTION_MARGIN_SECONDS < 220:
    raise RuntimeError("invalid host timeout contract")
if CONTROLLER_REQUIRED_SECONDS >= 46 * 60:
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
