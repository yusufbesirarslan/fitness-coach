"""Give synthetic D1 test owners the canonical screened cohort a grant needs.

Since the screened-grant closure (2026-11-14) a NEW affirmative D1 grant
requires a valid subject-bound signed eligibility record
(account_eligibility.d1_grant_cohort). Suites that are about something else
(fencing, publication, allowance accounting, launch integration) grant D1 for
their synthetic owners; this attaches a REAL adult TR record to them through
``account_eligibility.attach`` (subscription_support.adult_account, the
existing purchase-fixture helper), instead of bypassing the gate. The gate
itself is qualified in tests/test_ai_consent_cohort_grant{,_pg}.py.
"""
from app.extensions import db
from app.models import User
from subscription_support import adult_account


def screen_adult_tr(*owner_ids):
    """Attach the adult TR record to each owner id and commit (app context)."""
    for owner in owner_ids:
        adult_account(db.session.get(User, owner))
    db.session.commit()
    db.session.remove()
