"""Test helper: an account in the state LP-03's canonical rule calls onboarded.

`account_profile.onboarding_state` requires BOTH persisted facts onboarding
writes — `User.profile_complete` and the canonical `UserSession` — so setting
the flag alone no longer models an onboarded account (it models the legacy
false-complete row the rule deliberately refuses). Tests that need an
onboarded user call this instead of assigning the flag. It does not commit.
"""
from app.extensions import db
from app.models import UserSession


def mark_onboarded(user):
    user.profile_complete = True
    db.session.flush()
    if UserSession.query.filter_by(user_id=user.id).first() is None:
        db.session.add(UserSession(user_id=user.id, name=user.username))
    return user
