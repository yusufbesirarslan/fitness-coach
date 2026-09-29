"""Canonical binding rules for verified Cognito identities and local users.

Provider identifiers are case-insensitive. The production pool is
`UsernameConfiguration.CaseSensitive = false` with `AliasAttributes = [email]`
(verified against AWS, docs/MOBILE_PASSWORD_RECOVERY.md §11): `Alice`, `alice`
and `ALICE` sign in, reset and receive codes as ONE provider user, and so does
that user's e-mail in any casing. `local_users_for_identifier` is the one rule
that maps such an identifier back to local rows; password recovery and the
mobile credential fence both go through it, so the account a reset revokes and
the account a racing login is fenced against cannot differ by casing.

`User.username` IS the provider username. Registration creates both from one
value and login reconciliation copies the verified `cognito:username` claim
(`provider_username`); nothing may change it afterwards, because the provider
username cannot change (`/edit-profile` refuses, and
`tests/test_identity_immutability.py` gates every writer). That is what makes
a username or an e-mail — through its owner's username — name the same
account locally and at the provider. The profile's display name is
`User.full_name`.
"""

from app.extensions import db
from app.models import User


class AmbiguousLocalIdentity(Exception):
    """More than one local account answers to one provider identifier.

    Local `username`/`email` uniqueness is case-SENSITIVE, so rows that differ
    only by case are storable (legacy data: a row without a provider account,
    or a profile rename from before usernames became immutable; no new write
    can create them). The provider knows at most one of them; which one cannot
    be told from the identifier, so callers must fail closed, never pick.
    """


def local_users_for_identifier(identifier, *columns):
    """Every local row the provider identifier `identifier` can name.

    An identifier containing "@" is an e-mail (`validate_username` forbids "@",
    and the recovery transports already split on it); anything else is a
    username. Exactly one column is compared, for equality, with
    `lower()` on BOTH sides so the database applies one folding to stored and
    submitted text alike. No prefix, pattern or cross-column match exists.
    `columns` narrows what is loaded (default: the `User` entity).
    """
    identifier = identifier.strip() if isinstance(identifier, str) else ""
    if not identifier:
        return []
    column = User.email if "@" in identifier else User.username
    return db.session.query(*(columns or (User,))).filter(
        db.func.lower(column) == db.func.lower(identifier)).all()


def resolve_local_user(identifier, *columns):
    """The one local row `identifier` names, None when there is none.

    Raises `AmbiguousLocalIdentity` when case-folding matches more than one
    row: there is no safe "first match".
    """
    rows = local_users_for_identifier(identifier, *columns)
    if len(rows) > 1:
        raise AmbiguousLocalIdentity()
    return rows[0] if rows else None


def provider_username(verified_claims):
    """The provider's own username for a VERIFIED ID token, or None.

    This, not the identifier the user typed, is what a new local row's
    `username` must be: a login may be submitted as the e-mail alias or in any
    casing. A value containing "@" is refused too — `local_users_for_identifier`
    reads such an identifier as an e-mail, so it could never name the row.
    """
    value = verified_claims.get("cognito:username")
    value = value.strip() if isinstance(value, str) else ""
    return value if value and "@" not in value else None


def diverges_from_provider(user, verified_claims):
    """True when a verified login proves `user.username` is not the provider
    username — a row renamed before usernames became immutable. Recovery by
    that username cannot find this row; the operator repair is in
    docs/MOBILE_PASSWORD_RECOVERY.md §11."""
    canonical = provider_username(verified_claims)
    return (canonical is not None
            and canonical.lower() != (user.username or "").lower())


def reconcilable_local_user(username, verified_email, verified_sub):
    """Return an existing safe binding candidate and a stable denial reason.

    Both lookups use the provider's case folding (`local_users_for_identifier`):
    an exact-case match would miss a local `alice` for provider `Alice` and
    create a case twin beside it. More than one row for either identifier is
    `identity_ambiguous` — there is no safe first match.
    """
    email = (verified_email or "").strip().lower()
    email_rows = local_users_for_identifier(email)
    username_rows = local_users_for_identifier(username)
    if len(email_rows) > 1 or len(username_rows) > 1:
        return None, "identity_ambiguous"
    email_user = email_rows[0] if email_rows else None
    username_user = username_rows[0] if username_rows else None
    if (username_user is not None
            and (username_user.email or "").strip().lower() != email):
        return None, "username_email_mismatch"
    existing = email_user or username_user
    if (existing is not None and existing.cognito_sub
            and existing.cognito_sub != verified_sub):
        return None, "subject_mismatch"
    return existing, None
