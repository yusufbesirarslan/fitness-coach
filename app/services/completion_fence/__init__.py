"""PRIV-K6 completion-authority fence: application contract (rollback gate, Layer A).

The fence itself lives in PostgreSQL (migration ``d7e3a9c1f5b8``) and holds for
every writer, whatever image wrote it. This package is the application's view
of it, and nothing more:

* :func:`refusal_of` classifies a fence refusal (custom SQLSTATE class ``AX``)
  into a closed, typed reason with a retry decision, so no caller ever parses
  an error message and a permanent incompatibility never becomes a retry loop;
* :func:`floor_status` is the read-only probe release tooling uses: floor
  raised/absent, fence verified/absent/invalid and whether any claim exists
  (one EXISTS, no row content, no customer data);
* :func:`raise_floor` is the ONLY floor writer: verified fence, READ COMMITTED,
  bounded ``lock_timeout``, exclusive floor lock, idempotent insert. Lowering is
  not offered anywhere.

The fence verifier is loaded from the reviewed migration file itself, so the
probe checks the exact text that was reviewed, never a second copy of it.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy import text

CAPABILITY = "workout_completion_claims_v1"
FENCE_REVISION = "d7e3a9c1f5b8"
FLOOR_LOCK_KEY = -3391128469959823719
RAISE_LOCK_TIMEOUT_MS = 5000
STATUS_TIMEOUT_MS = 10000

_MIGRATION = (Path(__file__).resolve().parents[3] / "migrations" / "versions"
              / f"{FENCE_REVISION}_completion_authority_fence.py")
_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class FenceRefusal:
    sqlstate: str
    reason: str
    retryable: bool


# The closed SQLSTATE vocabulary raised by the fence functions. Retryable means
# the SAME request can succeed later without anyone changing code or data:
# activation pending (the floor is about to be raised) or a lost race whose
# retry resolves to ALREADY_COMPLETED. Everything else is permanent for that
# writer and must never be advertised as retryable.
REFUSALS = {
    "AXF01": FenceRefusal("AXF01", "activation_pending", True),
    "AXF02": FenceRefusal("AXF02", "writer_below_floor", False),
    "AXF03": FenceRefusal("AXF03", "floor_transition_refused", False),
    "AXF04": FenceRefusal("AXF04", "historical_conflict", True),
    "AXF05": FenceRefusal("AXF05", "claim_authority_violation", False),
    "AXF06": FenceRefusal("AXF06", "isolation_unsupported", False),
}


def refusal_of(exc) -> Optional[FenceRefusal]:
    """The typed fence refusal carried by ``exc`` (a SQLAlchemy DBAPIError or a
    raw driver error), or None if it is anything else."""
    for candidate in (getattr(exc, "orig", None), exc):
        code = getattr(candidate, "pgcode", None)
        if isinstance(code, str) and code in REFUSALS:
            return REFUSALS[code]
    return None


class FloorError(RuntimeError):
    """The floor could not be read or raised; ``reason`` is a fixed token."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@lru_cache(maxsize=1)
def _migration():
    spec = importlib.util.spec_from_file_location("_completion_fence_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_fence(connection) -> None:
    """Raise RuntimeError unless the installed fence is exactly the reviewed one."""
    _migration().verify_fence(connection)


def _fence_state(connection):
    migration = _migration()
    functions, triggers = migration._fence_catalog(connection)
    if not functions and not triggers:
        return "absent"
    try:
        migration.verify_fence(connection)
    except RuntimeError:
        return "invalid"
    return "verified"


def floor_status(connection, timeout_ms: int = STATUS_TIMEOUT_MS) -> dict:
    """Read-only probe. Runs in its own READ ONLY transaction on ``connection``.

    Bounded: a lock wait or a slow catalog read raises (a timeout is UNKNOWN,
    which callers treat as raised), it never reports ``absent``."""
    if connection.dialect.name != "postgresql":
        raise FloorError("unsupported_dialect", connection.dialect.name)
    if not isinstance(timeout_ms, int) or not 0 < timeout_ms <= 60000:
        raise FloorError("invalid_timeout")
    with connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'"))
        connection.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))
        fence = _fence_state(connection)
        floor_table = connection.execute(text(
            "SELECT pg_catalog.to_regclass('public.completion_capability_floor')")).scalar()
        claim_table = connection.execute(text(
            "SELECT pg_catalog.to_regclass('public.workout_completion_day')")).scalar()
        raised_by = None
        if floor_table is not None:
            raised_by = connection.execute(text(
                "SELECT raised_by_revision FROM public.completion_capability_floor "
                "WHERE capability = :c"), {"c": CAPABILITY}).scalar()
        claims_present = bool(claim_table is not None and connection.execute(text(
            "SELECT EXISTS (SELECT 1 FROM public.workout_completion_day)")).scalar())
    return {
        "capability": CAPABILITY,
        "fence": fence,
        "floor": "raised" if raised_by else "absent",
        "raised_by_revision": raised_by,
        "claims_present": claims_present,
    }


def raise_floor(connection, revision: str, lock_timeout_ms: int = RAISE_LOCK_TIMEOUT_MS) -> dict:
    """Raise the floor once; idempotent. The only floor writer in the codebase.

    Order inside ONE transaction: verified fence -> READ COMMITTED -> bounded
    lock wait -> EXCLUSIVE floor lock (waits for every in-flight completion
    commit that already took it SHARED) -> insert. The database refuses the
    insert without that lock (AXF03), so skipping a step cannot raise a floor
    that some in-flight historical commit could still slip past."""
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise FloorError("invalid_revision")
    if connection.dialect.name != "postgresql":
        raise FloorError("unsupported_dialect", connection.dialect.name)
    if not isinstance(lock_timeout_ms, int) or not 0 < lock_timeout_ms <= 60000:
        raise FloorError("invalid_lock_timeout")
    with connection.begin():
        if connection.execute(text(
                "SELECT pg_catalog.current_setting('transaction_isolation')")
                ).scalar() != "read committed":
            raise FloorError("isolation_unsupported")
        try:
            verify_fence(connection)
        except RuntimeError as exc:
            raise FloorError("fence_not_verified", str(exc)) from exc
        connection.execute(text(f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'"))
        connection.execute(text("SELECT pg_catalog.pg_advisory_xact_lock(:k)"),
                           {"k": FLOOR_LOCK_KEY})
        existing = connection.execute(text(
            "SELECT raised_by_revision FROM public.completion_capability_floor "
            "WHERE capability = :c"), {"c": CAPABILITY}).scalar()
        if existing:
            return {"result": "already_raised", "raised_by_revision": existing}
        connection.execute(text(
            "INSERT INTO public.completion_capability_floor "
            "(capability, raised_at, raised_by_revision) "
            "VALUES (:c, pg_catalog.clock_timestamp(), :r)"),
            {"c": CAPABILITY, "r": revision})
    return {"result": "raised", "raised_by_revision": revision}
