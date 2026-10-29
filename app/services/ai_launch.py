"""Narrow native launch admission over existing D1 and allowance authorities.

No environment switch publishes a contract. Absent exact source versions,
texts, finite policy or operational guards, the legacy closure remains closed.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import current_app
from sqlalchemy import select

from app.services import ai_consent as consent, account_eligibility
from app.services.ai_consent_disclosures import localized_texts
from app.services.ai_allowances import execution, launch_policy


NATIVE_ENDPOINTS = {
    "mobile_api.send_coach_message": "coach",
    "mobile_api.create_training_plan": "training_plan",
    "mobile_api.nutrition_plan_generate_native": "nutrition_plan",
    "mobile_api.create_pump_check": "vision",
    "mobile_api.nutrition_menu_analyze": "menu_extract",
}


def require_cohort(user):
    # The existing canonical subject-bound signed record is the authority.
    # Editable profile/client claims, a purchase or a D1 grant cannot promote it.
    if not account_eligibility.purchase_eligible(user, datetime.now(timezone.utc)):
        raise consent.ConsentDenied("AI_SERVICE_ELIGIBILITY_REQUIRED")


def require_runtime():
    from app.services import ai_spend_guard
    policy = launch_policy.installed_policy()
    if (policy is None or current_app.config.get("AI_ALLOWANCE_POLICY") != policy
            or not execution.enabled() or not ai_spend_guard.ENABLED
            or ai_spend_guard._get_redis() is None):
        raise consent.ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE")
    execution.require_ready()


def require_owner_within(conn, owner):
    """Fresh canonical evidence in D1's existing account transaction/lock."""
    _, _, users = consent._tables()
    row = conn.execute(select(users.c.cognito_sub, users.c.age, users.c.user_metadata)
                       .where(users.c.id == owner)).mappings().one_or_none()
    if row is None:
        raise consent.ConsentDenied()
    require_cohort(SimpleNamespace(**row))


def require_selected(state, feature):
    from app.services.ai_consent_disclosures import LAUNCH_VERSIONS
    purpose, categories = consent.BINDINGS[feature]
    version = LAUNCH_VERSIONS.get(purpose)
    policy = consent.POLICIES.get(version)
    if (policy is None or policy.purpose != purpose or policy.categories != categories
            or localized_texts(policy) is None
            or (feature == "menu_extract"
                and policy.external_scopes != ("public_https_menu_source",))
            or (feature != "menu_extract" and policy.external_scopes)):
        raise consent.ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE")
    grants = state.get("grants")
    record = grants.get(purpose) if isinstance(grants, dict) else None
    if not consent._record_valid(purpose, record) or record["version"] != version:
        raise consent.ConsentDenied()


def admit_endpoint(owner, feature):
    require_runtime()
    with consent._transaction(owner) as conn:
        require_owner_within(conn, owner)
        require_selected(consent._state(conn, owner), feature)


def check_operation_within(conn, state, authority):
    """Re-check an opened launch operation before dispatch and publication.

    The source policy being absent leaves historical closed integrations and
    isolated layer qualification untouched. Public opening always requires it.
    """
    if launch_policy.installed_policy() is not None:
        require_runtime()
        require_owner_within(conn, authority.owner)
        require_selected(state, authority.feature)


def check_allocation(user):
    if launch_policy.installed_policy() is not None:
        require_cohort(user)
