"""Durable training-plan confirmation proposals (Adaptive Coaching S1 PR4).

Persistence only. This package does not write ``TrainingPlan`` or
``PlanMutationRecord`` — confirmation execution still goes through
``PlanMutationService`` via ``coach_plan_tools``. It does not import the AI
Coach or any provider.
"""
from .service import (
    STATUS_APPLIED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_STALE,
    STATUS_SUPERSEDED,
    PendingConfirmationConflict,
    cancel_pending,
    create_or_reuse_pending,
    get_pending,
    lock_proposal,
    mark_applied,
    mark_stale,
)

__all__ = [
    "STATUS_APPLIED",
    "STATUS_CANCELLED",
    "STATUS_PENDING",
    "STATUS_STALE",
    "STATUS_SUPERSEDED",
    "PendingConfirmationConflict",
    "cancel_pending",
    "create_or_reuse_pending",
    "get_pending",
    "lock_proposal",
    "mark_applied",
    "mark_stale",
]
