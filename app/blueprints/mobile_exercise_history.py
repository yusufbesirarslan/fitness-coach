"""TD-01 PR2 authenticated read-only transport; independent default-dark gate."""
from functools import wraps

from flask import abort, current_app, g, jsonify
from sqlalchemy.exc import SQLAlchemyError

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import runtime_metrics
from app.services.exercise_catalog import (
    CatalogConfigurationError, ExerciseIdentityInvalid, ExerciseUnknown,
)
from app.services.exercise_performance_history import build_exercise_history
from app.services.mobile_exercise_history import CONTRACT_VERSION, history_json

_EVENTS = frozenset({
    'available', 'available_with_gaps', 'no_history', 'undetermined',
    'checkpoint_missing', 'checkpoint_invalid', 'session_invalid',
    'completed_at_missing', 'scan_limit_reached', 'occurrence_limit_reached',
    'invalid_request', 'exercise_not_found', 'read_failed', 'invariant_failed', 'catalog_unavailable',
})


def _record_event(event):
    if event not in _EVENTS:
        return
    try:
        runtime_metrics.increment('ExercisePerformanceHistory', dimensions={'Event': event})
    except Exception:
        pass


def _ready():
    return bool(current_app.config.get('FITX_EXERCISE_PERFORMANCE_HISTORY_ENABLED', False)
                and current_app.config.get('FITX_WORKOUT_SESSIONS_ENABLED', False))


def _rollout_gated(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        # Before view auth; shared blueprint hooks/limiter/OPTIONS/405 still apply.
        if not _ready():
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.get('/training/exercise-history-capabilities')
@_rollout_gated
@require_mobile_auth
def exercise_history_capabilities():
    return jsonify({'contract_version': CONTRACT_VERSION,
                    'exercise_performance_history_enabled': True,
                    'history_contract_version': CONTRACT_VERSION})


def _unavailable(event):
    try:
        db.session.rollback()
    except Exception:
        pass
    _record_event(event)
    current_app.logger.error('mobile_exercise_history event=%s request_id=%s',
                             event, current_request_id())
    return mobile_error('TRAINING_HISTORY_TEMPORARILY_UNAVAILABLE',
                        'Exercise history is temporarily unavailable.', 503, True, retry_after=15)


@bp.get('/training/exercises/<exercise_id>/performance-history')
@_rollout_gated
@require_mobile_auth
def read_exercise_history(exercise_id):
    owner_id = g.mobile_user.id
    try:
        history = build_exercise_history(owner_id, exercise_id)
        encoded = history_json(history)
    except ExerciseUnknown:
        _record_event('exercise_not_found')
        return mobile_error('TRAINING_HISTORY_EXERCISE_NOT_FOUND', 'Exercise was not found.', 404, False)
    except ExerciseIdentityInvalid:
        _record_event('invalid_request')
        return mobile_error('TRAINING_HISTORY_INVALID_REQUEST', 'Invalid exercise history request.', 400, False)
    except CatalogConfigurationError:
        return _unavailable('catalog_unavailable')
    except SQLAlchemyError:
        return _unavailable('read_failed')
    except Exception:
        # PR1 exceptions propagate unchanged; transport refusal never becomes empty history.
        return _unavailable('invariant_failed')
    _record_event(history.state)
    reasons = {marker.reason for marker in history.coverage.unavailable}
    for reason in sorted(reasons):
        _record_event(reason)
    for active, event in ((history.coverage.excluded_missing_completed_at, 'completed_at_missing'),
                          (history.coverage.scan_limit_reached, 'scan_limit_reached'),
                          (history.coverage.occurrence_limit_reached, 'occurrence_limit_reached')):
        if active:
            _record_event(event)
    if reasons & {'checkpoint_invalid', 'session_invalid'}:
        current_app.logger.warning('mobile_exercise_history event=source_defect request_id=%s',
                                   current_request_id())
    return current_app.response_class(encoded, mimetype='application/json')
