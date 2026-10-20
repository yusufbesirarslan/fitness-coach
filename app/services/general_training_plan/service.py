"""Idempotent create-and-persist authority for a non-AI general first plan.

Composition only. Identity: ``contract``. Content: ``template``. Validation:
the canonical save boundary (``validate_plan_for_save``). Persistence and
locking: ``mobile_training_generation.store.commit_general_plan``, the same
ledger, owner lock and absence check as the AI first plan. Reads nothing
about the person beyond the account language that picks day labels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.models import TRAINING_PLAN_GENERATION_SUCCEEDED, TrainingPlan
from app.services.exercise_catalog import ExerciseContext
from app.services.mobile_training_generation import store
from app.services.mobile_training_generation.errors import (
    GenerationPersistenceUnavailable,
    IdempotencyConflict,
)
from app.services.training_generation.output_errors import GenerationOutputError
from app.services.training_generation.service import validate_plan_for_save

from .contract import GeneralPlanRequest
from .errors import GeneralPlanSuperseded, GeneralPlanUnavailable
from .template import build_program


@dataclass(frozen=True)
class GeneralPlanResult:
    plan: TrainingPlan
    replayed: bool


def is_general_receipt(operation) -> bool:
    """The only ledger state a general create writes; no AI path reaches it."""
    return (operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED
            and operation.provider_dispatch_possible is False
            and operation.allowance_metered is None
            and operation.quota_reserved is False)


def canonical_document(request: GeneralPlanRequest, language) -> str:
    """The persisted plan text: template -> canonical save validation -> JSON."""
    if not isinstance(request, GeneralPlanRequest):
        raise TypeError("request must be GeneralPlanRequest")
    context = ExerciseContext(
        equipment_context=request.equipment, cardio_type="yok", style="genel")
    try:
        document = validate_plan_for_save(
            {"program": build_program(request.days, request.equipment, language)},
            context)
    except (GenerationOutputError, KeyError, ValueError) as error:
        raise GeneralPlanUnavailable() from error
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)


def _replay(operation, user_id, request):
    if operation.request_fingerprint != request.fingerprint:
        raise IdempotencyConflict()
    if not is_general_receipt(operation):
        # Same fingerprint domain but not a general receipt: impossible unless
        # the ledger was edited by hand. Never replay it as a general plan.
        raise GenerationPersistenceUnavailable()
    plan = TrainingPlan.query.filter_by(
        id=operation.training_plan_id, user_id=user_id).one_or_none()
    if plan is None or plan.lineage_id != operation.plan_lineage_id:
        raise GeneralPlanSuperseded()
    return GeneralPlanResult(plan=plan, replayed=True)


def create_general_plan(user, request: GeneralPlanRequest, key: str) -> GeneralPlanResult:
    """Create the owner's first plan from the template, or replay/refuse."""
    if not isinstance(request, GeneralPlanRequest):
        raise TypeError("request must be GeneralPlanRequest")
    user_id = user.id
    language = user.language
    operation = store.find_by_key(user_id, key)
    if operation is not None:
        return _replay(operation, user_id, request)
    document_json = canonical_document(request, language)
    try:
        plan = store.commit_general_plan(
            user_id, key, request.fingerprint, document_json)
    except store.GeneralKeyRaced:
        operation = store.find_by_key(user_id, key)
        if operation is None:
            raise GenerationPersistenceUnavailable() from None
        return _replay(operation, user_id, request)
    return GeneralPlanResult(plan=plan, replayed=False)
