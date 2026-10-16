"""PA06 provider projections. Unknown fields and opaque prose fail closed.

These are transfer boundaries, not consent decisions. Current user-authored
messages remain user input; stored enrichment must pass an operation projection.
"""
import json
import math

GOALS = ('kilo verme', 'kas kazanma', 'lose_weight', 'build_muscle')
LEVELS = ('beginner', 'intermediate', 'advanced')
EQUIPMENT = ('dumbbell', 'barbell', 'bench', 'cable', 'machine', 'bodyweight',
             'resistance_band', 'kettlebell', 'pull_up_bar')
RESTRICTIONS = ('vegan', 'vegetarian', 'gluten_free', 'lactose_free', 'halal')


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


def tool_result(name, raw):
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
