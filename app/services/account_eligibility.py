"""PRIV-LAUNCH: declaration screen and immutable account cohort, NOT consent.

The registration ticket proves only that this server evaluated a declaration.
It is not age assurance, guardianship, a health legal basis or AI permission.
The reserved signed metadata record cannot be edited through profile/memory
fields and never promotes a teen because their editable profile age changed.
No DOB, identity document, address, IP or guardian assertion is retained here.
"""
from datetime import datetime, timezone

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

VERSION = "age-screen-v1"
METADATA_KEY = "_account_eligibility_v1"
COUNTRIES = frozenset({"TR", "US", "GB", "CA", "AU"})
REGISTRATION_TTL_SECONDS = 600  # Credential lifetime, not a data-retention rule.
# No age-of-record purchase expiry: nothing renews an existing record (review is
# one-time), so one would strand renewing subscribers. Profile age never renews it.
PURCHASE_COUNTRIES = frozenset({"TR"})


class EligibilityDenied(Exception):
    def __init__(self, code="AUTH_ELIGIBILITY_REQUIRED", status=422):
        super().__init__(code)
        self.code, self.status = code, status


def enabled():
    return current_app.config.get("ACCOUNT_ELIGIBILITY_ENABLED") is True


def qualification_open():
    """RC2 StoreKit qualification window. Remove with the D1 launch commit.

    Needs a named list on a complete Production deployment with its Sandbox
    fallback. It restricts no account, gates no feature and opens no AI.
    """
    config = current_app.config
    return (bool(config.get("APPLE_PURCHASE_QUALIFICATION_SUBJECTS"))
            and config.get("APPLE_SUBSCRIPTIONS_ENABLED") is True
            and config.get("APPLE_SUBSCRIPTION_ENVIRONMENT") == "Production"
            and config.get("APPLE_SANDBOX_FALLBACK_ENABLED") is True)


def _listed(user, key):
    return (qualification_open() and bool(user.cognito_sub)
            and user.cognito_sub.lower() in current_app.config.get(key, frozenset()))


def qualification_subject(user):
    """May pass the closed purchase gate. A subject in both lists is a control."""
    return (_listed(user, "APPLE_PURCHASE_QUALIFICATION_SUBJECTS")
            and not _listed(user, "APPLE_QUALIFICATION_CONTROL_SUBJECTS"))


def prerelease_401_window_open():
    """RC2 pre-release Production lock. Remove with the D1 launch commit.

    Needs the flag, an unexpired deadline, a control list and the complete
    qualification window. A malformed control list parses as empty, and a subject
    on it must not become a purchase subject here, so an empty one closes it too.
    """
    config = current_app.config
    deadline = config.get("APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT")
    return (config.get("APPLE_PRERELEASE_401_FALLBACK_ENABLED") is True
            and isinstance(deadline, datetime) and deadline.tzinfo is not None
            and datetime.now(timezone.utc) < deadline
            and bool(config.get("APPLE_QUALIFICATION_CONTROL_SUBJECTS"))
            and qualification_open())


def served_to(user):
    """Whether this account is asked for a declaration. Enforcement stays on enabled().

    A control account holds a real record but never purchases, so the qualification
    can prove the purchase gate itself refuses it, not the eligibility check.
    """
    return (enabled() or qualification_subject(user)
            or _listed(user, "APPLE_QUALIFICATION_CONTROL_SUBJECTS"))


def _signer(*, account=False):
    kind = URLSafeSerializer if account else URLSafeTimedSerializer
    return kind(current_app.config["SECRET_KEY"],
                salt="axisai-account-cohort-v1" if account else "axisai-age-screen-v1")


def evaluate(data):
    if (not isinstance(data, dict) or set(data) != {"age", "country"}
            or type(data["age"]) is not int or not 1 <= data["age"] <= 2147483647
            or not isinstance(data["country"], str) or len(data["country"]) != 2):
        raise EligibilityDenied("AUTH_ELIGIBILITY_INVALID_REQUEST", 400)
    age, country = data["age"], data["country"]
    status, reason = "screen_passed", None
    if age < 13:
        status, reason = "ineligible", "UNDER_MINIMUM_AGE"
    elif country not in COUNTRIES:
        status, reason = "ineligible", "MARKET_NOT_SUPPORTED"
    elif ((country == "TR" and age < 18)
          or (country == "CA" and age < 14)
          or (country == "AU" and age < 15)):
        # Concrete unresolved social-scope/guardian/capacity qualification.
        # No client checkbox, invented guardian proof or blanket 18+ rule.
        status, reason = "qualification_required", "MARKET_QUALIFICATION_REQUIRED"
    band = None if age < 13 else "13_17" if age < 18 else "18_plus"
    return {"status": status, "age_band": band, "country": country,
            "reason": reason, "capabilities": _capabilities(status, band)}


def _capabilities(status, band):
    adult = status == "screen_passed" and band == "18_plus"
    # Social stays absent for the entire private v1 surface: merely denying
    # teen senders would leave adult discovery/messages to minors possible.
    return {"body_photos": adult, "automated_weight_targets": adult,
            "social": False}


def preview(data):
    # The native route is pre-auth, so the window opens it for every caller. The
    # ticket is inert unless the flag is on: registration ignores it and only a
    # named account can present it to review_existing.
    window_only = not enabled()
    if window_only and not (qualification_open()
                            and request.endpoint == "mobile_api.eligibility_preview"):
        raise EligibilityDenied("AUTH_ELIGIBILITY_UNAVAILABLE", 503)
    result = evaluate(data)
    # With the flag off nothing would record a minor's declaration or restrict the
    # account, so the window must not open sign-up for one.
    if window_only and result["status"] == "screen_passed" and result["age_band"] != "18_plus":
        raise EligibilityDenied("AUTH_ELIGIBILITY_UNAVAILABLE", 503)
    result["registration_token"] = (_signer().dumps({
        "version": VERSION, "age": data["age"], "country": data["country"]})
        if result["status"] == "screen_passed" else None)
    return {"eligibility": result}


def registration_record(token):
    return _ticket_record(token) if enabled() else None


def _ticket_record(token):
    try:
        if not isinstance(token, str) or not token or len(token) > 1024:
            raise ValueError()
        payload = _signer().loads(token, max_age=REGISTRATION_TTL_SECONDS)
        if not isinstance(payload, dict) or set(payload) != {"version", "age", "country"}:
            raise ValueError()
        if payload["version"] != VERSION:
            raise ValueError()
        result = evaluate({"age": payload["age"], "country": payload["country"]})
        if result["status"] != "screen_passed":
            raise ValueError()
    except (BadSignature, ValueError, TypeError, EligibilityDenied):
        raise EligibilityDenied() from None
    return {"version": VERSION, "age_band": result["age_band"], "_age": payload["age"],
            "country": result["country"], "source": "self_declared",
            "checked_at": datetime.now(timezone.utc).isoformat()}


def attach(user, record):
    """Caller owns the account transaction. No independent commit or grant."""
    if record is None:
        return
    if not user.cognito_sub:
        raise EligibilityDenied()
    record = dict(record)
    # Keep the already-collected age in its canonical existing column. This
    # also retains a conservative minority signal if another metadata writer
    # loses the reserved record; profile age is never affirmative authority.
    user.age = record.pop("_age")
    metadata = dict(user.user_metadata or {})
    metadata[METADATA_KEY] = _signer(account=True).dumps({
        "subject": user.cognito_sub, "record": record})
    user.user_metadata = metadata


def _record(user):
    try:
        raw = user.user_metadata.get(METADATA_KEY)
        if not isinstance(raw, str) or len(raw) > 2048:
            return None
        envelope = _signer(account=True).loads(raw)
        if set(envelope) != {"subject", "record"} or envelope["subject"] != user.cognito_sub:
            return None
        record = envelope["record"]
        if (not isinstance(record, dict)
                or set(record) != {"version", "age_band", "country", "source", "checked_at"}
                or record["version"] != VERSION or record["source"] != "self_declared"
                or record["country"] not in COUNTRIES
                or record["age_band"] not in {"13_17", "18_plus"}
                or not isinstance(record["checked_at"], str)):
            return None
        if record["age_band"] == "13_17" and record["country"] == "TR":
            return None
        datetime.fromisoformat(record["checked_at"])
        # A contradictory legacy/profile signal can only restrict access.
        if type(user.age) is int and (user.age < 13 or
                (user.age < 18 and record["age_band"] == "18_plus")):
            return None
        return record
    except (AttributeError, BadSignature, ValueError, TypeError, KeyError):
        return None


def projection(user):
    record = _record(user)
    status = "screen_passed" if record else "review_required"
    band = record["age_band"] if record else None
    return {"status": status, "age_band": band,
            "country": record["country"] if record else None,
            "source": record["source"] if record else None,
            "capabilities": _capabilities(status, band)}


def purchase_eligible(user, now):
    """Validate the canonical subject-bound signature, independently of rollout.

    The canonical schema derives screen_passed from a valid record; it has no
    client-writable status. Unknown/review schemas fail closed in _record.
    ``now`` is an aware UTC instant; a declaration dated after it is not one yet.
    """
    record = _record(user)
    if (not user.cognito_sub or not record or record["age_band"] != "18_plus"
            or record["country"] not in PURCHASE_COUNTRIES):
        return False
    checked = datetime.fromisoformat(record["checked_at"])
    return checked.tzinfo is not None and checked <= now


def require_capability(user, capability):
    if enabled() and not projection(user)["capabilities"].get(capability, False):
        raise EligibilityDenied("ELIGIBILITY_FEATURE_UNAVAILABLE", 403)


def review_existing(user, token):
    """One-time legacy declaration; no self-service promotion/country switch."""
    if not served_to(user):
        raise EligibilityDenied("AUTH_ELIGIBILITY_UNAVAILABLE", 503)
    record = _ticket_record(token)
    from app.extensions import db
    from app.models import User
    owner = db.session.execute(db.select(User).where(User.id == user.id)
        .with_for_update().execution_options(populate_existing=True)).scalar_one()
    existing = _record(owner)
    if existing:
        if any(existing[k] != record[k] for k in ("age_band", "country")):
            raise EligibilityDenied("ELIGIBILITY_REVIEW_REQUIRED", 409)
        return projection(owner)
    if isinstance(owner.user_metadata, dict) and METADATA_KEY in owner.user_metadata:
        raise EligibilityDenied("ELIGIBILITY_REVIEW_REQUIRED", 409)
    # An existing minority signal cannot be erased with an adult declaration.
    if type(owner.age) is int and (owner.age < 13 or
            (owner.age < 18 and record["age_band"] == "18_plus")):
        raise EligibilityDenied("ELIGIBILITY_REVIEW_REQUIRED", 409)
    attach(owner, record)
    db.session.commit()
    return projection(owner)


def enforce_request(user):
    """Run after authentication and before view/body/quota/product work.

    This is the v1 negative feature boundary, not first-party health consent.
    Neutral teen onboarding and the actual permission/AI lanes remain required.
    """
    if not enabled():
        return
    endpoint = request.endpoint or ""
    if endpoint.startswith("social."):
        require_capability(user, "social")
    if endpoint in {
        "mobile_api.create_pump_check", "mobile_api.create_pump_check_comparison",
        "mobile_api.list_pump_checks", "mobile_api.get_pump_check",
        "mobile_api.get_pump_check_comparison", "tracking.progress_physique_read",
        "training.complete_workout",
    }:
        require_capability(user, "body_photos")
    if endpoint == "mobile_api.complete_workout_session":
        # Admit only the existing affirmative photo-free representation. Do
        # not inspect multipart, even if it advertises no/empty photo field.
        if request.mimetype != "application/json" and (
                request.mimetype or request.content_length != 0
                or request.headers.get("Transfer-Encoding")):
            require_capability(user, "body_photos")
        elif request.mimetype == "application/json":
            # Existing classifier includes every supported photo field and
            # malformed/ambiguous representation. No second spelling list.
            from app.blueprints.mobile_workout_sessions import _photo_evidence_submitted
            if _photo_evidence_submitted():
                require_capability(user, "body_photos")
    # Every current profile goal is weight loss/gain; a numeric minimum change
    # would still run adult calculations. Refuse before reading health input
    # until an independently qualified neutral teen profile is composed.
    if request.method in {"POST", "PUT", "PATCH"} and endpoint in {
        "profile.setup", "profile.edit_profile", "tracking.checkin",
        "tracking.update_weight", "mobile_api.put_account_profile",
        "mobile_api.create_check_in", "coach.chat",
    }:
        require_capability(user, "automated_weight_targets")
