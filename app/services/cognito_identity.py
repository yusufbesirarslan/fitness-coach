"""Canonical binding rules for verified Cognito identities and local users."""

from app.extensions import db
from app.models import User


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
