from dataclasses import dataclass

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.services.exercise_catalog import ExerciseContext
from app.services.training_generation.capability import require_supported
from app.services.training_generation.classifier_service import classify_user
from app.services.training_generation.exercise_context_token import (
    ExerciseContextInvalid,
    verify_exercise_context,
)
from app.services.training_generation.exercise_resolution import (
    canonicalize_plan_exercises,
)
from app.services.training_generation.extractor import extract_plan_object
from app.services.training_generation.feature_extractor import build_features, parse_preferences
from app.services.training_generation.models import (
    ClassificationResult,
    TrainingPreferences,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseAmbiguousError,
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseIncompatibleError,
    GenerationExerciseUnresolvedError,
    GenerationOutputError,
    GenerationUnavailableError,
    ParseFailedError,
    PlanValidationError,
    SaveContextInvalidError,
    SaveExerciseInvalidError,
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
    annotate_injuries,
    coerce_plan_document,
    validate_generated_plan,
    validate_plan_structure,
)
from app.services.training_generation.semantic_validator import validate_plan_semantics


_REPAIR_JSON_SUFFIX = (
    "REPAIR: previous output was not one valid canonical JSON object. "
    "Return ONLY the complete JSON object. No markdown, no code fences, "
    "no commentary."
)

# The schema repair turn. Appended to the ORIGINAL prompt, so the accepted
# request (days, equipment, duration, focus, cardio, injuries) is carried over
# verbatim and this turn can only restate the canonical shape — it never
# invents, relaxes or re-asks a preference.
_REPAIR_SCHEMA_SUFFIX = (
    "REPAIR: previous output was valid JSON but not the canonical weekly plan "
    "shape. Keep every requested preference above exactly as stated and fix "
    "ONLY the structure: exactly 7 days with the canonical Turkish weekday "
    'names; "tip" one of antrenman/dinlenme/kardiyo; a tip="dinlenme" day '
    'MUST have "egzersizler": []; every exercise object exactly isim, set, '
    "tekrar, dinlenme, not; set/sure_dk/tahmini_kalori integers; no extra "
    "keys anywhere. Return ONLY the complete JSON object."
)


@dataclass(frozen=True)
class GeneratedTrainingPlanCandidate:
    """A fully validated, catalog-owned plan ready for canonical persistence."""

    document: dict
    overall_score: float
    exercise_context: ExerciseContext
    injury_warnings: list[dict]
    classification: ClassificationResult
    risk_flags: list[str]
    constraints_applied: list[str]


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
    structured, _ = validate_generated_plan(
        parsed, preferences, injuries=preferences.injuries)
    return structured


def resolve_save_exercise_context(token, secret_key, user_id):
    """Turn the carried token back into the accepted context, or refuse the save.

    The one translation point between the transport-integrity layer (which
    knows nothing about the wire) and the save boundary. The domain exception
    carries no client text and neither does the typed error raised here.
    """
    try:
        return verify_exercise_context(token, secret_key, user_id)
    except ExerciseContextInvalid as exc:
        raise SaveContextInvalidError(
            "exercise context token could not be verified") from exc


def validate_plan_for_save(plan, exercise_context):
    """Canonical save-time re-validation. Runs before any delete/insert.

    Accepts either shape a client can honestly hold: the provider-style
    name-only program, or the ID/name pairs canonicalization produced at
    generation time. Both are re-validated from scratch — structure, then
    semantics, then catalog identity and equipment compatibility against the
    VERIFIED ``exercise_context``, never anything the payload claims.

    Returns the canonical document to persist: ``program``, the client's
    ``haftalik_ozet`` when (and only when) it supplied one, and a
    server-created ``exercise_context`` block. Scores are preserved, never
    fabricated — a save is not a planning decision.
    """
    try:
        document = coerce_plan_document(plan)
        supplied_ozet = "haftalik_ozet" in document
        structured = validate_plan_structure(
            document, require_ozet=False, allow_exercise_id=True)
        validate_plan_semantics(structured, None)
    except (SchemaInvalidError, SemanticInvalidError, PlanValidationError) as exc:
        raise SaveInvalidError(str(exc)) from exc

    try:
        canonical = canonicalize_plan_exercises(structured, exercise_context)
    except (
        GenerationExerciseAmbiguousError,
        GenerationExerciseIdentityInvalidError,
        GenerationExerciseIncompatibleError,
        GenerationExerciseUnresolvedError,
    ) as exc:
        # Collapsed on purpose: at the save boundary the client must not be
        # able to tell "no such exercise" from "retired" from "not allowed by
        # your equipment" — that difference is a catalog oracle.
        raise SaveExerciseInvalidError(
            "plan references an exercise the catalog will not authorize") from exc

    saved = {"program": canonical["program"]}
    if supplied_ozet:
        saved["haftalik_ozet"] = canonical["haftalik_ozet"]
    saved["exercise_context"] = {
        "equipment_context": exercise_context.equipment_context,
        "cardio_type": exercise_context.cardio_type,
        "style": exercise_context.style,
        "catalog_version": exercise_context.catalog_version,
    }
    return saved


def generate_training_plan_candidate(
    user,
    last_session,
    preferences: TrainingPreferences,
    chat_fn,
    language="tr",
    logger=None,
) -> GeneratedTrainingPlanCandidate:
    """Run the canonical generator and return one persistence-ready candidate.

    The caller supplies already parsed canonical preferences. This boundary is
    deliberately free of request parsing and preference persistence, so a
    native generate-and-persist command has no intermediate domain write.
    """
    if not isinstance(preferences, TrainingPreferences):
        raise TypeError("preferences must be TrainingPreferences")
    require_supported(preferences)
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
            plan = _parse_and_validate(text, truncated, preferences)
        except (SchemaInvalidError, SemanticInvalidError):
            # Provider said it stopped at the token cap. A closed-but-short
            # object is still truncation, not a semantic command miss.
            if truncated:
                raise TruncatedError(
                    "provider truncated a closed JSON object")
            raise
    # One bounded repair turn, for provider FORMATTING failures only: a
    # malformed/truncated response, or a well-formed one that missed the
    # canonical shape. All three are the provider getting the format wrong on a
    # request the server already accepted, and a basic supported plan must not
    # become unusable because of that. The turn is spent from the SAME
    # ``MAX_PROVIDER_COMPLETIONS`` budget (so there is one retry, never a loop),
    # re-runs the full canonical validation via ``_parse_and_validate``, and
    # re-raises the typed error unchanged if it still does not validate —
    # nothing unvalidated can reach the caller, let alone the save path.
    # SemanticInvalidError is deliberately NOT repaired: that is a candidate
    # contradicting the accepted command, which is a different answer, not a
    # formatting slip.
    except (ParseFailedError, TruncatedError, SchemaInvalidError) as exc:
        if isinstance(exc, TruncatedError):
            category = "truncated"
        elif isinstance(exc, SchemaInvalidError):
            category = "schema_invalid"
        else:
            category = "parse_failed"
        _log(logger, category, repair_eligible=1, calls=len(budget.calls))
        _log(logger, "repair_attempted", reason=type(exc).__name__, calls=len(budget.calls))
        repair_tokens = REPAIR_MAX_TOKENS if isinstance(exc, TruncatedError) else PRIMARY_MAX_TOKENS
        repair_suffix = (
            _REPAIR_SCHEMA_SUFFIX if isinstance(exc, SchemaInvalidError)
            else _REPAIR_JSON_SUFFIX
        )
        try:
            text, truncated = budget.complete(
                prompt=f"{prompt}\n\n{repair_suffix}",
                system_prompt=system_prompt,
                max_tokens=repair_tokens,
                temperature=0.35,
            )
            plan = _parse_and_validate(text, truncated, preferences)
        except (ParseFailedError, TruncatedError, SchemaInvalidError, SemanticInvalidError):
            _log(logger, "repair_failed", calls=len(budget.calls))
            raise
    except SemanticInvalidError:
        _log(logger, "semantic_invalid", calls=len(budget.calls), repair_eligible=0)
        raise

    # Sprint 11 PR4 Task 3 / Sprint 12 PR2B: canonicalize exercise identity
    # exactly once, on the final accepted candidate, strictly OUTSIDE the
    # try/except above. Never move this inside the repair except clauses —
    # the repair path re-enters _parse_and_validate, so an exercise-authority
    # failure canonicalized there would be misclassified as a
    # parse/truncation-repairable outcome.
    # Injury annotation is warn-only and must run AFTER identity is
    # catalog-owned; a raw provider spelling is not warning authority.
    plan = canonicalize_plan_exercises(plan, exercise_context)
    injury_warnings = annotate_injuries(plan, preferences.injuries)

    ozet = plan.get("haftalik_ozet", {})
    yogunluk = ozet.get("yogunluk_skoru") or 7
    denge = ozet.get("denge_skoru") or 7
    uygunluk = ozet.get("uygunluk_skoru") or 7
    overall = round((yogunluk + denge + uygunluk) / 3, 1)
    persistence_document = {
        "program": plan["program"],
        "haftalik_ozet": ozet,
        "exercise_context": {
            "equipment_context": exercise_context.equipment_context,
            "cardio_type": exercise_context.cardio_type,
            "style": exercise_context.style,
            "catalog_version": exercise_context.catalog_version,
        },
    }
    return GeneratedTrainingPlanCandidate(
        document=persistence_document,
        overall_score=overall,
        exercise_context=exercise_context,
        injury_warnings=injury_warnings,
        classification=classification,
        risk_flags=classification.risk_flags,
        constraints_applied=classification.constraints_applied,
    )


def generate_training_plan_payload(
    user, last_session, request_data, chat_fn, language="tr", logger=None,
    *, context_token_factory=None,
):
    """Preserve the legacy browser candidate payload over the shared generator.

    ``context_token_factory`` (``Callable[[ExerciseContext], str]``) is how the
    accepted equipment context reaches the later browser save call. Native
    generation consumes the typed candidate directly and never round-trips it.
    """
    stored = (getattr(user, "user_metadata", None) or {}).get("injuries") or ""
    preferences = parse_preferences(request_data, stored_injuries=stored)
    require_supported(preferences)
    persist_posted_injuries(user, request_data.get("injuries"), logger=logger)
    candidate = generate_training_plan_candidate(
        user,
        last_session,
        preferences,
        chat_fn,
        language=language,
        logger=logger,
    )
    if candidate.overall_score >= 8:
        score_label = "İyi"
    elif candidate.overall_score >= 6:
        score_label = "Orta"
    else:
        score_label = "Kötü"
    payload = {
        "program": candidate.document["program"],
        "haftalik_ozet": candidate.document["haftalik_ozet"],
        "overall_score": candidate.overall_score,
        "score_label": score_label,
        "injury_warnings": candidate.injury_warnings,
        "classification": {
            "level": candidate.classification.level,
            "confidence": candidate.classification.confidence,
            "score": candidate.classification.score,
        },
        "risk_flags": candidate.risk_flags,
        "constraints_applied": candidate.constraints_applied,
    }
    if context_token_factory is not None:
        # The context itself is never echoed in the clear: the opaque token is
        # the only carrier, so a client cannot read what it is asserting, let
        # alone edit it.
        payload["exercise_context_token"] = context_token_factory(
            candidate.exercise_context)
    return payload
