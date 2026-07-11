import json

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.services.training_generation.classifier_service import classify_user
from app.services.training_generation.feature_extractor import build_features, parse_preferences
from app.services.training_generation.program_generator import build_program_context
from app.services.training_generation.prompt_builder import build_system_prompt, build_training_prompt
from app.services.training_generation.response_validator import PlanValidationError, validate_generated_plan


_COMPACT_JSON_RETRY_SUFFIX = "Yanıtı kısa tut ve yalnızca eksiksiz JSON döndür."


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


def _extract_json(raw: str) -> dict:
    cleaned = (raw or "").replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start != -1 and end > start:
        cleaned = cleaned[start:end]
    return json.loads(cleaned)


def _request_and_validate_plan(
        chat_fn, prompt, system_prompt, preferences, max_tokens):
    raw = chat_fn(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=0.35,
    )
    return validate_generated_plan(
        _extract_json(raw), preferences, injuries=preferences.injuries)


def generate_training_plan_payload(user, last_session, request_data, chat_fn, language="tr", logger=None):
    persist_posted_injuries(user, request_data.get("injuries"), logger=logger)
    stored = (getattr(user, "user_metadata", None) or {}).get("injuries") or ""
    preferences = parse_preferences(request_data, stored_injuries=stored)
    features = build_features(user, last_session, preferences)
    classification = classify_user(features)
    context = build_program_context(features, preferences, classification)
    prompt = build_training_prompt(features, preferences, classification, context, language=language)
    system_prompt = build_system_prompt(language)
    try:
        plan, injury_warnings = _request_and_validate_plan(
            chat_fn, prompt, system_prompt, preferences, max_tokens=4000)
    except (json.JSONDecodeError, PlanValidationError) as exc:
        if logger:
            logger.warning(
                "[TRAINING] invalid model response; retrying once (%s)",
                type(exc).__name__,
            )
        retry_prompt = f"{prompt}\n\n{_COMPACT_JSON_RETRY_SUFFIX}"
        plan, injury_warnings = _request_and_validate_plan(
            chat_fn, retry_prompt, system_prompt, preferences, max_tokens=7000)
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
