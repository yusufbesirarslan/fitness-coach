"""Durable generation admission, bounded lease recovery and atomic publication.

Provider calls hold no database transaction. Recovery can duplicate uncertain
provider work; each logical operation reserves quota once, across both attempts.
"""
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import text
from app.extensions import db
from app.models import User, TrainingPlan, TrainingPlanReplacementProposal as Proposal
from app.models import TrainingPlanReplacementGenerationOperation as Operation
from app.services import premium, mobile_training
from app.services.today_facts import get_active_plan
from app.services.plan_owner_lock import lock_plan_owner
from app.services.plan_mutation.fingerprint import snapshot_fingerprint
from app.services.mobile_training_generation.service import _required_session
from app.services.mobile_training_generation.contract import NativePlanRequest, parse_idempotency_key
from app.services.mobile_training_generation.errors import (
    GenerationInProgress, IdempotencyConflict, GenerationPersistenceUnavailable,
    GenerationQuotaExceeded, StoredGenerationFailure)
from app.services.training_generation.feature_extractor import build_features
from app.services.training_generation.models import UserTrainingFeatures, PerformanceHistory
from app.services.training_generation.service import generate_training_plan_candidate
from app.services.training_generation.output_errors import GenerationOutputError
from app.services.training_generation.response_validator import validate_plan_structure, WEEKDAYS
from .service import BaseBinding, _digest, stage_proposal_in_transaction

LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 2
GENERATION_LOCK_NAMESPACE = 0x41584919


@dataclass(frozen=True)
class GenerationResult:
    proposal_token: str
    review: dict
    expires_at: datetime
    replayed: bool


def review_candidate(document, score):
    """Closed bounded candidate projection without executable workout identity."""
    shaped = validate_plan_structure({k: document[k] for k in ('program', 'haftalik_ozet')}, allow_exercise_id=True)
    days = []
    by_day = {d['gun']: d for d in shaped['program']}
    for weekday in WEEKDAYS:
        day = by_day[weekday]
        if len(day['egzersizler']) > mobile_training.MAX_EXERCISES_PER_DAY:
            raise mobile_training.PlanUnprojectable('candidate exceeds review bound')
        days.append(dict(weekday=weekday, kind=mobile_training._KIND[day['tip']],
                         focus=day['odak'], duration_minutes=day['sure_dk'],
                         estimated_calories=day['tahmini_kalori'],
                         exercises=[mobile_training._project_exercise(e) for e in day['egzersizler']]))
    result = {'score': mobile_training._score(score), 'days': days}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 524288:
        raise mobile_training.PlanUnprojectable('candidate exceeds response bound')
    return result


def _lock_owner_generation(uid):
    if db.session.get_bind().dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(:namespace, :owner)'),
                           {'namespace': GENERATION_LOCK_NAMESPACE, 'owner': uid})


def _finish(row, status, code=None, http=None):
    row.status = status
    row.error_code, row.error_http_status = code, http
    row.lease_token = row.lease_expires_at = row.generation_context = None
    row.updated_at = row.completed_at = datetime.utcnow()


def _result(row, replayed):
    return GenerationResult(row.proposal_public_id, json.loads(row.review_data),
                            row.proposal_expires_at, replayed)


def claim(user, request, key):
    """Commit identity and quota before any provider call; refresh on every lock."""
    uid = user.id
    parse_idempotency_key(key)
    digest = _digest('axisai/replacement-generation-key/v1', key)
    try:
        _lock_owner_generation(uid)
        row = Operation.query.filter_by(user_id=uid, key_digest=digest).populate_existing().with_for_update().one_or_none()
        now = datetime.utcnow()
        if row is not None:
            if row.intent_fingerprint != request.fingerprint:
                raise IdempotencyConflict()
            if row.status == 'SUCCEEDED':
                result = _result(row, True)
                db.session.rollback()
                return result
            if row.status == 'FAILED':
                raise StoredGenerationFailure(row.error_code, row.error_http_status)
            if row.lease_expires_at > now:
                raise GenerationInProgress()
            if row.attempt_count >= MAX_ATTEMPTS:
                _finish(row, 'FAILED', 'TRAINING_PLAN_GENERATION_RECOVERY_EXHAUSTED', 409)
                db.session.commit()
                raise StoredGenerationFailure('TRAINING_PLAN_GENERATION_RECOVERY_EXHAUSTED', 409)
            row.attempt_count += 1
        else:
            active = Operation.query.filter_by(user_id=uid, status='IN_PROGRESS').populate_existing().with_for_update().first()
            if active is not None:
                if active.lease_expires_at > now:
                    raise GenerationInProgress()
                # An explicitly new key retires uncertain abandoned work. Keep
                # its reservation spent: refunding while a provider may return
                # would make recovered work bypass the weekly allowance.
                _finish(active, 'FAILED', 'TRAINING_PLAN_GENERATION_SUPERSEDED', 409)
                db.session.flush()
            last_session = _required_session(user)
            language = user.language or 'tr'
            features = asdict(build_features(user, last_session, request.preferences))
            context = json.dumps({'features': features, 'language': language}, ensure_ascii=False, allow_nan=False)
            if len(context) > 32768:
                raise GenerationPersistenceUnavailable()
            plan = get_active_plan(uid)
            if plan is None:
                raise StoredGenerationFailure('TRAINING_PLAN_CURRENT_REQUIRED', 409)
            row = Operation(user_id=uid, key_digest=digest, intent_fingerprint=request.fingerprint,
                            base_lineage_id=plan.lineage_id, base_mutation_version=plan.mutation_version,
                            base_snapshot_digest=snapshot_fingerprint(plan.plan_data),
                            generation_context=context, status='IN_PROGRESS', attempt_count=1)
            # No plan lock is held while quota takes User UPDATE.
            if current_app.config.get('AI_PLAN_QUOTA_ENABLED', True):
                week = premium.reserve_ai_quota_in_transaction(uid, 'training', premium.FREE_WEEKLY_AI_PLANS)
                owner = db.session.get(User, uid)
                if owner is None:
                    raise GenerationPersistenceUnavailable()
                if not owner.is_premium and week is None:
                    raise GenerationQuotaExceeded()
                row.quota_reserved, row.quota_week = week is not None, week
            db.session.add(row)
        row.lease_token = secrets.token_urlsafe(32)
        row.lease_expires_at = now + LEASE
        row.updated_at = now
        db.session.flush()
        admitted = (row.id, row.lease_token, row.generation_context)
        db.session.commit()
        return admitted
    except Exception:
        db.session.rollback()
        raise


def _owned_attempt(operation_id, token):
    row = Operation.query.filter_by(id=operation_id).populate_existing().with_for_update().one_or_none()
    if row is None or row.status != 'IN_PROGRESS' or row.lease_token != token or row.lease_expires_at <= datetime.utcnow():
        raise GenerationInProgress()
    return row


def fail(operation_id, token, code, http, *, refund=True):
    try:
        row = _owned_attempt(operation_id, token)
        if refund and row.quota_reserved:
            premium.refund_ai_quota_in_transaction(row.user_id, 'training', row.quota_week)
            row.quota_reserved = False
        _finish(row, 'FAILED', code, http)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return StoredGenerationFailure(code, http)


def publish(operation_id, token, document, score):
    # Validation/projection and serialization run BEFORE any publication locks.
    review = review_candidate(document, score)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode('utf-8')) > 262144:
        raise mobile_training.PlanUnprojectable('candidate exceeds storage bound')
    try:
        row = _owned_attempt(operation_id, token)
        lock_plan_owner(row.user_id)
        plan = (TrainingPlan.query.filter_by(user_id=row.user_id).order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc())
                .populate_existing().with_for_update().first())
        binding = BaseBinding(row.base_lineage_id, row.base_mutation_version, row.base_snapshot_digest)
        if plan is None or (plan.lineage_id, plan.mutation_version, snapshot_fingerprint(plan.plan_data)) != (binding.lineage_id, binding.mutation_version, binding.snapshot_digest):
            _finish(row, 'FAILED', 'TRAINING_PLAN_STALE_CURRENT_PLAN', 409)
            db.session.commit()
            raise StoredGenerationFailure('TRAINING_PLAN_STALE_CURRENT_PLAN', 409)
        token = stage_proposal_in_transaction(row.user_id, binding, plan_data=raw, score=score)
        proposal = Proposal.query.filter_by(public_id=token).one()
        row.proposal_public_id = token
        row.proposal_expires_at = proposal.expires_at
        row.review_data = json.dumps(review, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        _finish(row, 'SUCCEEDED')
        result = GenerationResult(proposal.public_id, review, proposal.expires_at, False)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def generate_proposal(user, request, key, *, chat_fn, provider_guard):
    if not isinstance(request, NativePlanRequest):
        raise TypeError('request must be NativePlanRequest')
    admitted = claim(user, request, key)
    if isinstance(admitted, GenerationResult):
        return admitted
    operation_id, token, context = admitted
    frozen = json.loads(context)
    fields = frozen['features']
    fields['performance_history'] = PerformanceHistory(**fields['performance_history'])
    features = UserTrainingFeatures(**fields)
    # No implicit expired ORM load may start a transaction before provider I/O.
    db.session.rollback()
    try:
        with provider_guard():
            candidate = generate_training_plan_candidate(None, None, request.preferences, chat_fn,
                        language=frozen['language'], frozen_features=features)
    except GenerationOutputError as error:
        code = error.public_code
        http = 503 if code == 'TRAINING_PLAN_GENERATION_UNAVAILABLE' else 422
        raise fail(operation_id, token, code, http) from None
    except Exception as error:
        from flask_limiter.errors import RateLimitExceeded
        from app.services.ai_gate import BlockingConcurrencyLimit
        if isinstance(error, RateLimitExceeded):
            failure = fail(operation_id, token, 'TRAINING_PLAN_RATE_LIMITED', 429)
            failure.retry_after = error.limit.limit.get_expiry()
        elif isinstance(error, BlockingConcurrencyLimit):
            failure = fail(operation_id, token, 'TRAINING_PLAN_GENERATION_BUSY', 503)
            failure.retry_after = 15
        else:
            failure = fail(operation_id, token, 'TRAINING_PLAN_GENERATION_UNAVAILABLE', 503)
        raise failure from None
    try:
        return publish(operation_id, token, candidate.document, candidate.overall_score)
    except (mobile_training.PlanUnprojectable, GenerationOutputError, ValueError):
        raise fail(operation_id, token, 'TRAINING_PLAN_UNPROJECTABLE', 422) from None
