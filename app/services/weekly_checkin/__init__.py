"""Canonical WeeklyCheckIn persistence authority (LP16-A).

The one owner of full weekly check-in writes, the shared body-weight /
derived-session primitive, and the legacy ``/update-weight`` write. Web
``POST /checkin`` and ``POST /update-weight`` delegate here; the native
check-in API (LP16-B) will compose the same primitives provider-free.

Layering: ``models`` (framework-free values) ← ``queries`` (owner-scoped
reads) ← ``service`` (staging + the explicit transaction owners). No Flask,
no request globals, no AI/provider, no Progress/Coach/plan concern.
"""
from .models import (BodyWeightUpdate, CheckInContext, FullCheckIn,
                     SubmissionOutcome, SubmissionResult)
from .queries import FULL_CHECKIN
from .service import (apply_body_weight, claim_submission, commit_full_checkin,
                      load_context, record_legacy_weight_update,
                      reload_locked_owner, stage_full_checkin)

__all__ = [
    "BodyWeightUpdate",
    "CheckInContext",
    "FULL_CHECKIN",
    "FullCheckIn",
    "SubmissionOutcome",
    "SubmissionResult",
    "apply_body_weight",
    "claim_submission",
    "commit_full_checkin",
    "load_context",
    "record_legacy_weight_update",
    "reload_locked_owner",
    "stage_full_checkin",
]
