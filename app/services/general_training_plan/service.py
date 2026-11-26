"""Idempotent create-and-persist authority for a non-AI general first plan.

Composition only. Identity: ``contract``. Content: ``template``. Validation:
the canonical save boundary (``validate_plan_for_save``). Persistence and
locking: ``mobile_training_generation.store.commit_general_plan``, the same
ledger, owner lock and absence check as the AI first plan. Reads nothing
about the person beyond the account language that picks day labels.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass

from app.models import TrainingPlan
from app.services.exercise_catalog import ExerciseContext
from app.services.mobile_training_generation import store
from app.services.mobile_training_generation.errors import (
    GenerationPersistenceUnavailable,
    IdempotencyConflict,
    PreviewStale,
)
from app.services.training_generation.output_errors import GenerationOutputError
from app.services.training_generation.service import validate_plan_for_save
from app.services.training_plan_origin import is_general_receipt

from .contract import GeneralPlanRequest
from .errors import GeneralPlanSuperseded, GeneralPlanUnavailable
from .template import build_cardio_program, build_program


@dataclass(frozen=True)
class GeneralPlanResult:
    plan: TrainingPlan
    replayed: bool


def canonical_document(request: GeneralPlanRequest, language) -> str:
    """The persisted plan text: template -> canonical save validation -> JSON."""
    if not isinstance(request, GeneralPlanRequest):
        raise TypeError("request must be GeneralPlanRequest")
    cardio = request.cardio
    context = ExerciseContext(
        equipment_context=request.equipment,
        cardio_type=cardio.cardio_type if cardio is not None else "yok",
        style="genel")
    try:
        if cardio is None:
            program = build_program(request.days, request.equipment, language)
        else:
            program = build_cardio_program(
                request.days, request.equipment, cardio.cardio_type,
                cardio.days, cardio.minutes, cardio.intensity, language,
                sessions=request.sessions)
        document = validate_plan_for_save(
            {"program": program}, context, allow_sessions=request.sessions)
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


@dataclass(frozen=True)
class GeneralPreview:
    document_json: str
    candidate_fingerprint: str


def preview_general_plan(user, request: GeneralPlanRequest) -> GeneralPreview:
    """PD2: the exact document Accept would persist, computed and NOT written.

    Pure: the same ``canonical_document`` the create command persists, so the
    fingerprint is reproducible at Accept. No ledger row, no plan row, no
    provider/consent/allowance/quota seam. Nothing is staged server-side: a
    deterministic candidate needs no draft row to be reproduced exactly.
    """
    document_json = canonical_document(request, user.language)
    return GeneralPreview(
        document_json=document_json,
        candidate_fingerprint=store.candidate_sha(document_json))


def create_general_plan(user, request: GeneralPlanRequest, key: str, *,
                        expected_candidate_fingerprint=None) -> GeneralPlanResult:
    """Create the owner's first plan from the template, or replay/refuse.

    PD2: with ``expected_candidate_fingerprint`` (the preview's If-Match) the
    recomputed document must be byte-identical to what was previewed, else
    ``PreviewStale`` and nothing is written. A same-key replay returns the
    already-created plan without comparing (the key, not the text, is the
    identity once committed).
    """
    if not isinstance(request, GeneralPlanRequest):
        raise TypeError("request must be GeneralPlanRequest")
    user_id = user.id
    language = user.language
    operation = store.find_by_key(user_id, key)
    if operation is not None:
        return _replay(operation, user_id, request)
    document_json = canonical_document(request, language)
    if (expected_candidate_fingerprint is not None
            and not hmac.compare_digest(store.candidate_sha(document_json),
                                        expected_candidate_fingerprint)):
        raise PreviewStale()
    try:
        plan = store.commit_general_plan(
            user_id, key, request.fingerprint, document_json)
    except store.GeneralKeyRaced:
        operation = store.find_by_key(user_id, key)
        if operation is None:
            raise GenerationPersistenceUnavailable() from None
        return _replay(operation, user_id, request)
    return GeneralPlanResult(plan=plan, replayed=False)
