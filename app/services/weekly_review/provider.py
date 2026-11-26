"""Weekly review admission over the existing D1, Coach and allowance doors."""
import json

from flask import current_app

from app.services import ai, ai_consent, ai_input_budget, ai_launch, ai_spend_guard, health_consent
from app.services.ai_allowances import execution

from . import contract

FLAG = "FITX_WEEKLY_CHECKIN_AI_REVIEW_ENABLED"
FEATURE = "coach_weekly_review"
PURPOSE = "coach.weekly_checkin_review"
# Source-owned activation dependency. There is no approved billing family for
# this operation. A rollout flag alone cannot select one or authorize AI.
APPROVED_ALLOWANCE_FAMILY = None


def enabled():
    return current_app.config.get(FLAG) is True


def _require_policy():
    binding = ai_consent.BINDINGS.get(FEATURE)
    family = APPROVED_ALLOWANCE_FAMILY
    if (not enabled() or binding is None or binding[0] != PURPOSE
            or ai_input_budget.normalize_feature(FEATURE) != FEATURE
            or family is None or FEATURE not in execution.PHYSICAL_FEATURES.get(family, ())):
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    ai_launch.require_runtime()
    return family


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
        # A denied or unavailable authority cannot fail the canonical check-in.
        current_app.logger.info("weekly_review event=authority_not_captured type=%s",
                                type(error).__name__)
    return intent


def authorize(owner, context):
    family = _require_policy()
    authority = ai_consent.OperationAuthority.from_context(owner, context["authority"])
    if authority.feature != FEATURE:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    ai_launch.admit_endpoint(owner, FEATURE)
    ai_consent.authorize_operation(authority)
    return authority, family


def review(owner, check_in_id, language, evidence, authority, family, publish):
    """One logical admission and one provider operation through the shared router."""
    # Runtime requires Redis and allowance enforcement. The router invokes the
    # standard admission on every attempt, including broker-managed retries.
    if _require_policy() != family:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    command = {"check_in_id": check_in_id, "version": contract.VERSION}
    key_digest = execution.fingerprint(owner, "weekly-review-operation-key-v1", command)
    with ai_spend_guard.subject_scope(owner), ai_consent.operation_scope(authority):
        with execution.logical_action(
            owner, family, command, key=f"weekly-review-{key_digest[:48]}",
        ) as action:
            if action is None:
                raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
            message = json.dumps({
                "language": language, "evidence": evidence,
                "suggested_actions": contract.suggested_actions(evidence),
            }, ensure_ascii=False)
            try:
                completion = ai._heavy_complete(
                    [{"role": "user", "content": message}],
                    system_prompt=contract.INSTRUCTIONS, max_tokens=contract.MAX_OUTPUT_TOKENS,
                    temperature=0.0, feature=FEATURE,
                )
                if completion.truncated or completion.provider not in {"bedrock", "openai"}:
                    raise contract.InvalidReview()
                result = contract.validate(completion.text, language, evidence)
            except ai.ModelRefusal:
                execution.failure("provider_error", result_known=True)
                raise
            except contract.InvalidReview:
                execution.failure("invalid_output", result_known=True)
                raise
            publish(authority, result)
            action.success()
