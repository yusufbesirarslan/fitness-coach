"""Shared declarations for the DOB eligibility screen (FDO-AGE18).

``born(age)`` is the date of birth of someone whose ``age``-th birthday is
today on the app calendar (Europe/Istanbul): the youngest person of that age.
``legacy_minor_record`` writes the signed 13-17 record an account could hold
from before the adult-only decision; the screen can no longer issue one.
"""
from datetime import date, datetime, timedelta, timezone

from app.services import account_eligibility as policy
from app.timeutil import app_today


def born(age, days=0, today=None):
    today = today or app_today()
    try:
        birthday = date(today.year - age, today.month, today.day)
    except ValueError:  # 29 February in a common birth year
        birthday = date(today.year - age, 2, 28)
    return (birthday + timedelta(days=days)).isoformat()


def declaration(age, country="TR", **kwargs):
    return {"birth_date": born(age, **kwargs), "country": country}


def legacy_minor_record(user, age=17, country="GB"):
    record = {"version": policy.VERSION, "age_band": "13_17", "country": country,
              "source": "self_declared",
              "checked_at": datetime.now(timezone.utc).isoformat()}
    user.age = age
    metadata = dict(user.user_metadata or {})
    metadata[policy.METADATA_KEY] = policy._signer(account=True).dumps(
        {"subject": user.cognito_sub, "record": record})
    user.user_metadata = metadata
    return user
