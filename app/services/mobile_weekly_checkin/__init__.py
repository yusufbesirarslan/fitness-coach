"""Native weekly check-in contract (LP16-B).

    POST /api/v1/progress/check-ins   one full check-in, Idempotency-Key required
    GET  /api/v1/progress/check-ins   last 12 full check-ins + current week

The transport (`app/blueprints/mobile_progress.py`) holds HTTP only. This
package owns the native wire contract and composes existing authorities; it
owns no persistence and no Progress rule of its own:

- ``contract``   — the strict native parser (never the legacy web parser),
  the ``native.v1`` fingerprint, the wire <-> stored overload mapping and the
  closed POST projection. Pure: no DB, no Flask, no clock.
- ``submission`` — the provider-free write over the LP16-A primitives
  (``weekly_checkin.claim_submission`` → ``load_context`` →
  ``stage_full_checkin`` → ``commit_full_checkin``). Never constructs a
  ``WeeklyCheckIn`` itself; never calls a provider.
- ``history``    — the bounded read: qualifying rows through
  ``progress_history.fetch_qualifying_checkins`` and the weight delta through
  the Progress History rule (``previous_daily_row`` + ``historical_body``),
  plus the display-only Monday–Sunday Istanbul ``current_week``.

See docs/MOBILE_WEEKLY_CHECKIN.md.
"""
from .contract import (CONTRACT_VERSION, FIELDS, FINGERPRINT_DOMAIN,
                       OVERLOAD_STORED_TO_WIRE, OVERLOAD_WIRE_TO_STORED,
                       IdempotencyConflict, InvalidRequest, InvalidValue,
                       NativeCheckIn, fingerprint, parse_request)
from .history import HISTORY_LIMIT, build_history, current_week
from .submission import submit

__all__ = [
    "CONTRACT_VERSION",
    "FIELDS",
    "FINGERPRINT_DOMAIN",
    "HISTORY_LIMIT",
    "OVERLOAD_STORED_TO_WIRE",
    "OVERLOAD_WIRE_TO_STORED",
    "IdempotencyConflict",
    "InvalidRequest",
    "InvalidValue",
    "NativeCheckIn",
    "build_history",
    "current_week",
    "fingerprint",
    "parse_request",
    "submit",
]
