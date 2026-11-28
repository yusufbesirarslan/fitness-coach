"""PD6 authorization and one selected provider route."""
import json

from flask import current_app

from app.services import ai, ai_consent, ai_input_budget, ai_launch, ai_spend_guard, health_consent

from . import allowance, contract

FLAG = "FITX_WEEKLY_CHECKIN_AI_REVIEW_ENABLED"
FEATURE = "coach_weekly_review"
PURPOSE = "coach.weekly_checkin_review"


def enabled():
    return current_app.config.get(FLAG) is True


def _require_policy():
    binding = ai_consent.BINDINGS.get(FEATURE)
    if (not enabled() or binding is None or binding[0] != PURPOSE
            or ai_input_budget.normalize_feature(FEATURE) != FEATURE
            or allowance.APPROVED_CAMPAIGN is None):
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    ai_launch.require_runtime()


def capture_intent(owner):
    """Snapshot operation authority before the canonical writer takes its lock."""
    if not enabled():
        return None
    intent = {"health_generation": health_consent.current_generation(), "authority": None}
    try:
        _require_policy()
        ai_launch.admit_endpoint(owner, FEATURE)
        intent["authority"] = ai_consent.begin_operation(
            owner, FEATURE, *ai.heavy_primary_route()).to_context()
    except Exception as error:
        current_app.logger.info("weekly_review event=authority_not_captured type=%s",
                                type(error).__name__)
    return intent


def authorize(owner, context):
    _require_policy()
    authority = ai_consent.OperationAuthority.from_context(owner, context["authority"])
    if authority.feature != FEATURE:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    ai_launch.admit_endpoint(owner, FEATURE)
    ai_consent.authorize_operation(authority)
    return authority


def review(owner, language, evidence, authority):
    _require_policy()
    message = json.dumps({
        "language": language, "evidence": evidence,
        "suggested_actions": contract.suggested_actions(evidence),
    }, ensure_ascii=False)
    with ai_spend_guard.subject_scope(owner), ai_consent.operation_scope(authority):
        completion = ai._heavy_complete(
            [{"role": "user", "content": message}],
            system_prompt=contract.INSTRUCTIONS, max_tokens=contract.MAX_OUTPUT_TOKENS,
            temperature=0.0, feature="coach_weekly_review",
        )
    if completion.truncated or completion.provider not in {"bedrock", "openai"}:
        raise contract.InvalidReview()
    return contract.validate(completion.text, language, evidence)
