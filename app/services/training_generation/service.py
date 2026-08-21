from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.services.exercise_catalog import ExerciseContext
from app.services.training_generation.capability import require_supported
from app.services.training_generation.classifier_service import classify_user
from app.services.training_generation.exercise_resolution import (
    canonicalize_plan_exercises,
)
from app.services.training_generation.extractor import extract_plan_object
from app.services.training_generation.feature_extractor import build_features, parse_preferences
from app.services.training_generation.output_errors import (
    GenerationOutputError,
    GenerationUnavailableError,
    ParseFailedError,
    PlanValidationError,
    SaveInvalidError,
    SchemaInvalidError,
    SemanticInvalidError,
    TruncatedError,
)
from app.services.training_generation.plan_schema import (
    MAX_PROVIDER_COMPLETIONS,
    PRIMARY_MAX_TOKENS,
    REPAIR_MAX_TOKENS,
)
from app.services.training_generation.program_generator import build_program_context
from app.services.training_generation.prompt_builder import (
    build_system_prompt,
    build_training_prompt,
    canonical_exercise_vocabulary,
)
from app.services.training_generation.response_validator import (
    validate_generated_plan,
    validate_plan_structure,
)
from app.services.training_generation.semantic_validator import validate_plan_semantics


_REPAIR_JSON_SUFFIX = (
    "REPAIR: previous output was not one valid canonical JSON object. "
    "Return ONLY the complete JSON object. No markdown, no code fences, "
    "no commentary."
)


def persist_posted_injuries(user, posted_injuries, logger=None):
    if not isinstance(posted_injuries, str) or not posted_injuries.strip():
        return
    try:
        meta = dict(user.user_metadata or {})
        clean = posted_injuries.strip()[:2000]
        if meta.get("injuries") != clean:
            meta["injuries"] = clean
            user.user_metadata = meta
            flag_modified(user, "user_metadata")
            db.session.commit()
    except Exception:
        db.session.rollback()
        if logger:
            logger.warning("[TRAINING] injury persistence failed", exc_info=True)


def _log(logger, event, **fields):
    log_info = getattr(logger, "info", None) if logger else None
    if not callable(log_info):
        return
    parts = " ".join(f"{key}={value}" for key, value in fields.items())
    log_info("[TRAINING] %s %s", event, parts)


def _normalize_completion(raw):
    if raw is None:
        return "", False, None
    text = getattr(raw, "text", None)
    if isinstance(text, str) or text is not None:
        return (
            "" if text is None else str(text),
            bool(getattr(raw, "truncated", False)),
            getattr(raw, "finish_reason", None),
        )
    return str(raw), False, None


class _CompletionBudget:
    """Hard cap on generation-layer provider completions. Max 2."""

    def __init__(self, chat_fn, max_calls=MAX_PROVIDER_COMPLETIONS):
        self.chat_fn = chat_fn
        self.max_calls = max_calls
        self.calls = []

    def complete(self, *, prompt, system_prompt, max_tokens, temperature):
        if len(self.calls) >= self.max_calls:
            raise GenerationUnavailableError("provider completion budget exhausted")
        kwargs = dict(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            raw = self.chat_fn(**kwargs)
        except GenerationOutputError:
            raise
        except Exception as exc:
            raise GenerationUnavailableError("provider unavailable") from exc
        text, truncated, finish_reason = _normalize_completion(raw)
        record = {
            "max_tokens": max_tokens,
            "truncated": truncated,
            "finish_reason": finish_reason,
            "messages": kwargs["messages"],
        }
        self.calls.append(record)
        return text, truncated


def _parse_and_validate(text, truncated, preferences):
    parsed = extract_plan_object(text, truncated=truncated)
    return validate_generated_plan(
        parsed, preferences, injuries=preferences.injuries)


def validate_plan_for_save(plan, preferences=None):
    """Canonical save-time re-validation. Runs before any delete/insert."""
    try:
        structured = validate_plan_structure(plan, require_ozet=False)
        validate_plan_semantics(structured, preferences)
    except (SchemaInvalidError, SemanticInvalidError, PlanValidationError) as exc:
        raise SaveInvalidError(str(exc)) from exc
    return structured


def generate_training_plan_payload(user, last_session, request_data, chat_fn, language="tr", logger=None):
    stored = (getattr(user, "user_metadata", None) or {}).get("injuries") or ""
    preferences = parse_preferences(request_data, stored_injuries=stored)
    require_supported(preferences)
    persist_posted_injuries(user, request_data.get("injuries"), logger=logger)
    _log(
        logger, "generation_started",
        style=preferences.antrenman_tarzi,
        days=preferences.gun_sayisi,
        duration=preferences.sure,
        equipment=preferences.ekipman,
        focus=preferences.odak_hedef,
        cardio_type=preferences.kardiyo_tipi,
        cardio_days=preferences.kardiyo_gun,
        provider_invoked=1,
    )
    features = build_features(user, last_session, preferences)
    classification = classify_user(features)
    context = build_program_context(features, preferences, classification)
    exercise_context = ExerciseContext(
        equipment_context=preferences.ekipman,
        cardio_type=preferences.kardiyo_tipi,
        style=preferences.antrenman_tarzi,
    )
    exercise_vocabulary = canonical_exercise_vocabulary(exercise_context)
    prompt = build_training_prompt(
        features, preferences, classification, context, language=language,
        exercise_vocabulary=exercise_vocabulary)
    system_prompt = build_system_prompt(language)
    budget = _CompletionBudget(chat_fn)

    try:
        text, truncated = budget.complete(
            prompt=prompt, system_prompt=system_prompt,
            max_tokens=PRIMARY_MAX_TOKENS, temperature=0.35)
        try:
            plan, injury_warnings = _parse_and_validate(text, truncated, preferences)
        except (SchemaInvalidError, SemanticInvalidError):
            # Provider said it stopped at the token cap. A closed-but-short
            # object is still truncation, not a semantic command miss.
            if truncated:
                raise TruncatedError(
                    "provider truncated a closed JSON object")
            raise
    except (ParseFailedError, TruncatedError) as exc:
        category = "truncated" if isinstance(exc, TruncatedError) else "parse_failed"
        _log(logger, category, repair_eligible=1, calls=len(budget.calls))
        _log(logger, "repair_attempted", reason=type(exc).__name__, calls=len(budget.calls))
        repair_tokens = REPAIR_MAX_TOKENS if isinstance(exc, TruncatedError) else PRIMARY_MAX_TOKENS
        try:
            text, truncated = budget.complete(
                prompt=f"{prompt}\n\n{_REPAIR_JSON_SUFFIX}",
                system_prompt=system_prompt,
                max_tokens=repair_tokens,
                temperature=0.35,
            )
            plan, injury_warnings = _parse_and_validate(text, truncated, preferences)
        except (ParseFailedError, TruncatedError, SchemaInvalidError, SemanticInvalidError):
            _log(logger, "repair_failed", calls=len(budget.calls))
            raise
    except SchemaInvalidError:
        _log(logger, "schema_invalid", calls=len(budget.calls), repair_eligible=0)
        raise
    except SemanticInvalidError:
        _log(logger, "semantic_invalid", calls=len(budget.calls), repair_eligible=0)
        raise

    # Sprint 11 PR4 Task 3: canonicalize exercise identity exactly once, on the
    # final accepted candidate, strictly OUTSIDE the try/except above. Never
    # move this inside the repair except clauses — the repair path re-enters
    # _parse_and_validate, so an exercise-authority failure canonicalized
    # there would be misclassified as a parse/truncation-repairable outcome.
    plan = canonicalize_plan_exercises(plan, exercise_context)

    ozet = plan.get("haftalik_ozet", {})
    yogunluk = ozet.get("yogunluk_skoru") or 7
    denge = ozet.get("denge_skoru") or 7
    uygunluk = ozet.get("uygunluk_skoru") or 7
    overall = round((yogunluk + denge + uygunluk) / 3, 1)
    if overall >= 8:
        score_label = "İyi"
    elif overall >= 6:
        score_label = "Orta"
    else:
        score_label = "Kötü"
    return {
        "program": plan["program"],
        "haftalik_ozet": ozet,
        "overall_score": overall,
        "score_label": score_label,
        "injury_warnings": injury_warnings,
        "classification": {
            "level": classification.level,
            "confidence": classification.confidence,
            "score": classification.score,
        },
        "risk_flags": classification.risk_flags,
        "constraints_applied": classification.constraints_applied,
    }
