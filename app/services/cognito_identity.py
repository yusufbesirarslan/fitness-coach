"""Canonical binding rules for verified Cognito identities and local users.

Provider identifiers are case-insensitive. The production pool is
`UsernameConfiguration.CaseSensitive = false` with `AliasAttributes = [email]`
(verified against AWS, docs/MOBILE_PASSWORD_RECOVERY.md §11): `Alice`, `alice`
and `ALICE` sign in, reset and receive codes as ONE provider user, and so does
that user's e-mail in any casing. `local_users_for_identifier` is the one rule
that maps such an identifier back to local rows; password recovery and the
mobile credential fence both go through it, so the account a reset revokes and
the account a racing login is fenced against cannot differ by casing.
"""

from app.extensions import db
from app.models import User


class AmbiguousLocalIdentity(Exception):
    """More than one local account answers to one provider identifier.

    Local `username`/`email` uniqueness is case-SENSITIVE, so rows that differ
    only by case are storable (a legacy row without a provider account, or a
    profile rename). The provider knows at most one of them; which one cannot
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


def reconcilable_local_user(username, verified_email, verified_sub):
    """Return an existing safe binding candidate and a stable denial reason."""
    email = (verified_email or "").strip().lower()
    email_user = User.query.filter(db.func.lower(User.email) == email).first()
    username_user = User.query.filter_by(username=username).first()
    if (username_user is not None
            and (username_user.email or "").strip().lower() != email):
        return None, "username_email_mismatch"
    existing = email_user or username_user
    if (existing is not None and existing.cognito_sub
            and existing.cognito_sub != verified_sub):
        return None, "subject_mismatch"
    return existing, None
