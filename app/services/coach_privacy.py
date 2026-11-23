"""PA06 provider projections. Unknown fields and opaque prose fail closed.

These are transfer boundaries, not consent decisions. Current user-authored
messages remain user input; stored enrichment must pass an operation projection.
"""
import json
import math
import re

from app.services.exercise_catalog import HYPHENS

GOALS = ('kilo verme', 'kas kazanma', 'lose_weight', 'build_muscle')
LEVELS = ('beginner', 'intermediate', 'advanced')
EQUIPMENT = ('dumbbell', 'barbell', 'bench', 'cable', 'machine', 'bodyweight',
             'resistance_band', 'kettlebell', 'pull_up_bar')
RESTRICTIONS = ('vegan', 'vegetarian', 'gluten_free', 'lactose_free', 'halal')


def assert_principal(user_id):
    """Savunma derinliği: in-process koç/MCP araçları yalnızca kimliği doğrulanmış
    kullanıcı için çalışmalı. user_id ASLA LLM'den gelmez; yine de bir istek
    bağlamında çağrıldıysak current_user ile eşleştiğini doğrula (gelecekteki bir
    yanlış kullanım çapraz-kullanıcı okuma/yazmaya dönüşmesin)."""
    from flask import g, has_request_context
    from flask_login import current_user
    if has_request_context():
        principal = getattr(g, "mobile_user", None)
        if principal is None and getattr(current_user, "is_authenticated", False):
            principal = current_user
        if principal is not None and principal.id != user_id:
            raise PermissionError("user_id does not match the authenticated owner")


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def numeric_fields(source, keys):
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if number(source.get(key)) is not None}


def token(value, choices):
    return value if isinstance(value, str) and value in choices else None


def memory_value(key, value):
    if key == 'fitness_goals':
        return token(value, GOALS)
    if key in ('equipment_available', 'dietary_restrictions'):
        allowed = EQUIPMENT if key == 'equipment_available' else RESTRICTIONS
        values = value if isinstance(value, list) else [value]
        return [v for v in values[:20] if token(v, allowed)]
    # Legacy free-form injuries may contain someone else's identity. Preserve
    # only deterministic movement safety, never the original text/header.
    if key == 'injuries' and isinstance(value, str) and value.strip():
        from app.services.injury_constraints import _match_conditions
        conditions = _match_conditions(value[:2000])
        return {'banned_movements': sorted({name for c in conditions for name in c['banned']}),
                'constraints_require_confirmation': True}
    return None


def operation(question, handoff=None):
    if handoff == 'nutrition-day':
        return 'nutrition'
    if handoff == 'progress-insight':
        return 'training'
    q = (question or '').casefold()
    # Conservative routing selects context, never authorization or a write.
    for kind, words in (
        ('nutrition', ('nutrition', 'calorie', 'kalori', 'beslen', 'meal', 'yiyeyim', 'protein')),
        ('recovery', ('recovery', 'sleep', 'fatigue', 'toparlan', 'uyku', 'yorgun')),
        ('training', ('training', 'workout', 'exercise', 'antrenman', 'egzersiz', 'plan', 'progress', 'ilerle', 'squat', 'deadlift', 'bench', 'curl', 'çömel', 'kosu', 'koşu')),
        ('body', ('weight', 'kilo', 'bmr', 'tdee')),
    ):
        if any(word in q for word in words):
            return kind
    return 'chat'


# Relevance rules for richer D1 context. Keep ambiguous "plan" and "weight"
# out of the training/body rules unless the message names the domain.
_CONTEXT_RULES = {
    "training": r"\b(?:training|workouts?|exercis\w*|sets?|reps?|squats?|deadlifts?|bench|"
                r"lift(?:ed|ing)?|work(?:ed|ing)? out|antrenman\w*|egzersiz\w*|çömel\w*|koşu\w*)\b",
    "nutrition": r"\b(?:nutrition|calories?|meals?|macros?|food|intake|(?:did|have) i (?:eat|eaten)|diet\w*|"
                 r"protein|kalori\w*|beslen\w*|öğün\w*|yemek\w*|yiyeyim|yedi(?:m|ğim|klerim|klerimi)|tükettim)\b",
    "recovery": r"\b(?:recovery|sleep|fatigue|toparlan\w*|uyku\w*|yorgun\w*)\b",
    "body": r"\b(?:body weight|weight (?:loss|gain|progress|change)|my weight|body measurements?|bmr|tdee|"
            r"vücut ağırlığ\w*|kilo\s+(?:ver\w*|al\w*)|kilom\w*)\b",
    "goals": r"\b(?:my (?:fitness )?goals?|hedefim\w*|hedeflerim\w*)\b",
    "progress": r"\b(?:progress\w*|performance|plateau\w*|strength trend\w*|"
                r"ilerle\w*|performans\w*|plato\w*)\b",
}


def context_operations(question, handoff=None):
    """Collect named domains for a turn. An allowlisted handoff adds its domain."""
    question = (question or "").casefold()
    selected = {kind for kind, pattern in _CONTEXT_RULES.items() if re.search(pattern, question)}
    domain = {"nutrition-day": "nutrition", "progress-insight": "progress"}.get(handoff)
    if domain:
        selected.add(domain)
    if domain != "progress" and "body" in selected and "progress" in selected and "training" not in selected:
        selected.remove("progress")
    return selected or {"chat"}


def exercise_name(value):
    from app.services.exercise_catalog import resolve_exercise, ExerciseResolutionError
    if not isinstance(value, str):
        return None
    try:
        return resolve_exercise(name=value).canonical_name
    except ExerciseResolutionError:
        return None


_STATUSES = ('ok', 'error', 'staged', 'committed', 'cancelled', 'no_pending',
             'not_found', 'needs_user_confirmation', 'already_done', 'unsupported',
             'applied', 'no_op', 'replayed', 'confirmation_required', 'needs_user_input',
             'blocked')
_MACROS = ('calories', 'protein', 'carbs', 'fat')
_WORKOUT = ('sets', 'reps', 'weight_kg', 'volume')


_WEEKDAYS = ('Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar')
_DAY_KINDS = ('antrenman', 'dinlenme', 'kardiyo')
_PLAN_READ_ERRORS = ('PLAN_UNREADABLE', 'CAPABILITY_DISABLED')
_MAX_PLAN_EXERCISES = 20
_PLAN_REPS = re.compile(r'([0-9]{1,3})(?: *- *([0-9]{1,3}))?(?: +(sn|dk|sec|min|s))?')
_PLAN_REPS_DASHES = str.maketrans({dash: '-' for dash in HYPHENS})


def _plan_reps(value):
    """A rep prescription rebuilt from a closed grammar, else None.

    Digits, an optional range and an optional time unit ("8-12", "5",
    "30 sn", "45 sec"). Anything else is stored prose and fails closed.
    """
    if not isinstance(value, str) or len(value) > 16:
        return None
    match = _PLAN_REPS.fullmatch(value.strip(' ').translate(_PLAN_REPS_DASHES))
    if match is None:
        return None
    start, end, unit = match.groups()
    lower = int(start)
    upper = int(end) if end is not None else lower
    if not 1 <= lower <= upper <= 500:
        return None
    count = str(lower) if end is None else f'{lower}-{upper}'
    return f'{count} {unit}' if unit is not None else count


def _curated_plan_identity(exercise_id):
    """Catalog V3 fields re-derived from the server taxonomy, never forwarded."""
    from app.services.exercise_catalog import CatalogConfigurationError
    from app.services.exercise_catalog_v3 import describe_exercise
    try:
        descriptor = describe_exercise(exercise_id)
    except CatalogConfigurationError:
        return {}
    if descriptor is None:
        return {}
    return {key: descriptor[key] for key in (
        'exercise_id', 'source', 'family_id', 'target_region', 'equipment')}


def _private_plan_labels(days, user_id, max_exercises):
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from app.extensions import db
    from app.services.private_exercises import is_private_id, owner_definitions

    ids = set()
    for day in days[:7]:
        session = day.get('cardio_session') if isinstance(day, dict) else None
        lists = [day.get('exercises') if isinstance(day, dict) else None,
                 session.get('exercises') if isinstance(session, dict) else None]
        for raw in lists:
            if not isinstance(raw, list):
                continue
            for exercise in raw[:max_exercises]:
                identity = exercise.get('private_exercise_id') if isinstance(exercise, dict) else None
                if is_private_id(identity):
                    ids.add(identity)
    if not ids or user_id is None:
        return {}
    try:
        # This tool reprojects just before provider transfer. Release the read
        # connection immediately without touching the Coach's ambient session.
        with Session(db.engine) as session:
            return owner_definitions(user_id, ids, session=session)
    except SQLAlchemyError:
        return {}


def _training_plan_projection(source, user_id):
    """Rebuild the plan read from tokens and owner-authorized definitions."""
    from app.services.training_generation.plan_schema import NATIVE_MAX_EXERCISES_PER_DAY
    out = {}
    if token(source.get('error'), _PLAN_READ_ERRORS):
        out['error'] = source['error']
    plan = source.get('plan')
    days = plan.get('days') if isinstance(plan, dict) else None
    if source.get('status') != 'ok' or not isinstance(days, list):
        return out
    private_labels = _private_plan_labels(days, user_id, NATIVE_MAX_EXERCISES_PER_DAY)
    projected = []
    for day in days[:7]:
        if not isinstance(day, dict) or not token(day.get('day'), _WEEKDAYS):
            continue
        exercises = _plan_exercises(day.get('exercises'), private_labels)
        entry = {'day': day['day'], 'kind': token(day.get('kind'), _DAY_KINDS),
                 'exercises': exercises}
        if day.get('truncated') is True:
            entry['truncated'] = True
        session = day.get('cardio_session')
        if isinstance(session, dict):
            # G4: the strength day's separate cardio session, re-projected
            # through the same closed exercise shape.
            entry['cardio_session'] = {
                'exercises': _plan_exercises(session.get('exercises'), private_labels)}
            if session.get('truncated') is True:
                entry['cardio_session']['truncated'] = True
        projected.append(entry)
    out['plan'] = {'days': projected}
    return out


def _plan_exercises(raw, private_labels):
    """One plan exercise list rebuilt from tokens and authorized labels."""
    from app.services.exercise_catalog_v3 import LEGACY_REGION_FOR
    from app.services.private_exercises import is_private_id
    from app.services.training_generation.plan_schema import NATIVE_MAX_EXERCISES_PER_DAY
    exercises = []
    raw = raw if isinstance(raw, list) else []
    limit = _MAX_PLAN_EXERCISES
    if private_labels and any(
        is_private_id(item.get('private_exercise_id')) and item['private_exercise_id'] in private_labels
        for item in raw[:NATIVE_MAX_EXERCISES_PER_DAY] if isinstance(item, dict)
    ):
        limit = NATIVE_MAX_EXERCISES_PER_DAY
    for position, exercise in enumerate(raw[:limit]):
        if not isinstance(exercise, dict):
            continue
        sets = exercise.get('sets')
        label = exercise.get('name')
        projected_exercise = {
            'name': None,
            'sets': sets if type(sets) is int and 1 <= sets <= 100 else None,
            'reps': _plan_reps(exercise.get('reps')),
        }
        if 'private_exercise_id' in exercise:
            identity = exercise['private_exercise_id']
            definition = private_labels.get(identity) if is_private_id(identity) else None
            if definition is not None:
                projected_exercise.update(
                    name=definition.canonical_name,
                    movement=definition.movement,
                    equipment=sorted(definition.equipment),
                    primary_region=definition.primary_region,
                    target_region=token(definition.target_region, LEGACY_REGION_FOR),
                    position=position,
                    user_defined=True,
                )
        else:
            if isinstance(label, str) and len(label) <= 120 and exercise_name(label):
                projected_exercise['name'] = label
            stored_position = exercise.get('position')
            if type(stored_position) is int and 0 <= stored_position < NATIVE_MAX_EXERCISES_PER_DAY:
                projected_exercise['position'] = stored_position
            projected_exercise.update(_curated_plan_identity(exercise.get('exercise_id')))
        exercises.append(projected_exercise)
    return exercises


# ── Adaptive Coach C2: saved Nutrition Plan tools ──────────────────────────
# Item text is the owner's own saved food text (nutrition.plan data the turn
# was authorized to read); it is bounded/control-free and labelled as data by
# the tool description. Everything else is a token or a finite number.

def _nutrition_tool_names():
    from app.services.coach_plan_tools import nutrition
    return nutrition.TOOL_NAMES


def _nutrition_text(value):
    from app.services.nutrition_plan_schema import ITEM_MAX
    if (isinstance(value, str) and value.strip() and len(value) <= ITEM_MAX
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return value
    return None


def _nutrition_meal(source):
    from app.services.nutrition_plan_schema import ITEMS_MAX, MEAL_MACRO_KEYS
    if not isinstance(source, dict):
        return None
    items = source.get('items')
    out = {'items': [x for x in items[:ITEMS_MAX] if _nutrition_text(x)]
           if isinstance(items, list) else []}
    out.update(numeric_fields(source, MEAL_MACRO_KEYS))
    if type(source.get('estimated')) is bool:
        out['estimated'] = source['estimated']
    return out


def _nutrition_projection(source, status):
    from app.services.coach_plan_tools import nutrition
    from app.services.nutrition_plan_schema import MEAL_KEYS, TOTAL_KEYS
    out = {'domain': nutrition.DOMAIN}
    error = token(source.get('error'), tuple(nutrition.ERRORS))
    if error:
        out['error'] = error
        out['message'] = nutrition.ERRORS[error]
    if status == nutrition.STATUS_CONFIRMATION_REQUIRED:
        out['message'] = nutrition.CONFIRMATION_INSTRUCTION
    if token(source.get('operation'), ('undo', 'confirm', 'cancel')):
        out['operation'] = source['operation']
    meals = source.get('meals')
    if isinstance(meals, list):
        out['meals'] = []
        for meal in meals[:len(MEAL_KEYS)]:
            projected = _nutrition_meal(meal)
            if projected is not None and token(meal.get('meal'), MEAL_KEYS):
                out['meals'].append(dict(projected, meal=meal['meal']))
    if isinstance(source.get('totals'), dict):
        out['totals'] = numeric_fields(source['totals'], TOTAL_KEYS)
    change = source.get('change')
    if isinstance(change, dict):
        out['change'] = {}
        if token(change.get('kind'), ('replace', 'add', 'remove')):
            out['change']['kind'] = change['kind']
        if token(change.get('meal'), MEAL_KEYS):
            out['change']['meal'] = change['meal']
        for key in ('item', 'replacement'):
            if _nutrition_text(change.get(key)):
                out['change'][key] = change[key]
    for key in ('before', 'after'):
        projected = _nutrition_meal(source.get(key))
        if projected is not None:
            out[key] = projected
    day = source.get('day_totals')
    if isinstance(day, dict):
        out['day_totals'] = {key: numeric_fields(day[key], TOTAL_KEYS)
                             for key in ('before', 'after') if isinstance(day.get(key), dict)}
    if isinstance(source.get('estimate'), dict):
        out['estimate'] = {'source': 'independent_whole_meal_estimate',
                           'whole_meal_reestimated': True,
                           'unchanged_foods_may_change': True}
    return out


def tool_result(name, raw, *, user_id=None):
    """Project immediately before insertion into *any* provider's next round.

    Internal confirmation logic still receives the original domain result.
    No generic recursion, stringified JSON passthrough or wildcard fields.
    """
    try:
        source = json.loads(raw)
    except (TypeError, ValueError):
        source = {}
    if not isinstance(source, dict):
        source = {}
    out = {'status': token(source.get('status'), _STATUSES) or 'error'}
    if name == 'query_fitx_metrics' and source.get('metric_type') == 'calories':
        return json.dumps({'status': 'unsupported', 'metric_type': 'calories'})
    if name in ('fetch_nutrition_and_stage_log', 'confirm_and_commit_meal_log'):
        from app.services import fatsecret_boundary
        if not fatsecret_boundary.verified('tool-result', user_id, source):
            # Unattested nutrition never reaches the model: status only, no
            # values. A legacy commit still reports 'committed' (no false
            # failure that invites a duplicate log); refusals keep their meaning.
            return json.dumps(out, ensure_ascii=False, allow_nan=False)

    if name == 'query_fitx_metrics':
        for key, choices in (
            ('metric_type', ('calories', 'water_ml', 'volume_lifted', 'weight_kg', 'sleep_hours')),
            ('date_range', ('today', 'yesterday', 'this_week', 'last_week', 'last_7_days', 'last_30_days', 'current_month')),
            ('unit', ('kcal', 'ml', 'kg')),
        ):
            if token(source.get(key), choices):
                out[key] = source[key]
        out.update(numeric_fields(source, ('value', 'change', 'entries')))
    elif name == 'manage_user_memory':
        # The tool itself projects key/value, so nested legacy memory cannot
        # acquire trust merely by being wrapped in a successful result.
        key = token(source.get('key'), ('injuries', 'fitness_goals', 'equipment_available', 'dietary_restrictions'))
        if key:
            out['key'] = key
            value = source.get('value')
            if key == 'injuries' and isinstance(value, dict):
                # Reconstruct from the closed condition catalog, not input strings.
                from app.services.injury_constraints import _CONDITIONS
                allowed = {n for c in _CONDITIONS for n in c['banned']}
                moves = value.get('banned_movements')
                out['value'] = {'banned_movements': [n for n in moves[:100] if token(n, allowed)]
                                if isinstance(moves, list) else [],
                                'constraints_require_confirmation': True}
            else:
                out['value'] = memory_value(key, value)
    elif name in ('fetch_nutrition_and_stage_log', 'confirm_and_commit_meal_log'):
        for key in ('staged_food', 'logged', 'today_totals'):
            if isinstance(source.get(key), dict):
                out[key] = numeric_fields(source[key], _MACROS + ('entry_count',))
        evaluation = source.get('evaluation')
        if isinstance(evaluation, dict):
            out['evaluation'] = numeric_fields(evaluation, ('compatibility_score',))
    elif name in ('stage_workout_log', 'confirm_and_commit_workout_log'):
        for key in ('staged_workout', 'logged', 'today_totals'):
            if isinstance(source.get(key), dict):
                out[key] = numeric_fields(source[key], _WORKOUT + ('total_volume', 'entry_count'))
                canonical = exercise_name(source[key].get('exercise_name'))
                if canonical:
                    out[key]['exercise_name'] = canonical
    elif name == 'cancel_pending_log':
        out.update(numeric_fields(source, ('removed',)))
    elif name == 'get_current_training_plan':
        out.update(_training_plan_projection(source, user_id))
    elif name in _nutrition_tool_names():
        out.update(_nutrition_projection(source, out['status']))
    elif name == 'analyze_gym_photo':
        out.update(numeric_fields(source, ('form_rating', 'xp_awarded')))
        for key in ('is_gym', 'awarded'):
            if type(source.get(key)) is bool:
                out[key] = source[key]
    else:
        from app.services.coach_plan_tools import PLAN_WRITE_TOOL_NAMES
        if name in PLAN_WRITE_TOOL_NAMES:
            out.update(numeric_fields(source, ('plan_version',)))
            from app.services.coach_plan_tools.results import _ERROR_MESSAGES
            if token(source.get('error'), _ERROR_MESSAGES):
                out['error'] = source['error']
            change = source.get('change')
            if isinstance(change, dict):
                out['change'] = numeric_fields(change, ('sets', 'reps', 'match_sets', 'match_reps'))
                for key in ('day', 'target_day'):
                    if token(change.get(key), ('Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar')):
                        out['change'][key] = change[key]
                for key in ('reps', 'match_reps'):
                    value = change.get(key)
                    if isinstance(value, str):
                        parts = value.split('-')
                        if 1 <= len(parts) <= 2 and all(
                                part.isascii() and part.isdigit() and len(part) <= 3
                                and 1 <= int(part) <= 500 for part in parts):
                            out['change'][key] = value
                for key in ('exercise', 'replacement'):
                    canonical = exercise_name(change.get(key))
                    if canonical:
                        out['change'][key] = canonical
    return json.dumps(out, ensure_ascii=False, allow_nan=False)
