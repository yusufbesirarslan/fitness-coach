"""Native hydration contract over the ONE ``WaterLog`` authority.

The persisted truth is an ABSOLUTE count of glasses per (owner, Istanbul day),
so the native write is an absolute desired state under ``If-Match`` — never a
"+1 glass" command, which a lost response would turn into a double count.

Revision = owner-bound digest over (owner, server day, count). It changes with
the day, so a screen read yesterday can never overwrite today, and with every
count change, so two devices that read one state cannot both succeed with
different answers. A missing row and a confirmed 0 have the same revision: both
mean "0 glasses today" and a write over either is the same decision.
"""
from app.services import hydration
from app.timeutil import APP_TZ, app_today

from . import errors, tokens


CONTRACT_VERSION = 1

AVAILABLE = "available"
EMPTY = "empty"
INVALID = "invalid"


def hydration_revision(secret, user_id, day_iso, count):
    return tokens.digest_token(
        secret, tokens.HYDRATION_REVISION,
        tokens.integer(user_id), tokens.text(day_iso), tokens.number(count))


def _valid_count(count):
    return (isinstance(count, int) and not isinstance(count, bool)
            and 0 <= count)


def _payload(secret, user_id, day_iso, count):
    if _valid_count(count):
        state = AVAILABLE if count else EMPTY
        amount = count
    else:
        state, amount = INVALID, None
    return {"contract_version": CONTRACT_VERSION, "hydration": {
        "day": day_iso,
        "timezone": APP_TZ.key,
        "state": state,
        "amount": amount,
        "unit": hydration.UNIT,
        "max_amount": hydration.MAX_GLASSES,
        "revision": hydration_revision(secret, user_id, day_iso, count),
    }}


def read_today(user_id, secret, day=None):
    """The owner's server-day hydration. Raises on storage failure (→ 503)."""
    day_iso = (day or app_today()).isoformat()
    _exists, count = hydration.read_today(user_id, day_iso)
    return _payload(secret, user_id, day_iso, count)


def parse_command(data):
    """Closed body ``{"amount": <int 0..MAX>}`` — nothing else is accepted."""
    if not isinstance(data, dict) or set(data) != {"amount"}:
        raise errors.InvalidHydration
    amount = data["amount"]
    if (isinstance(amount, bool) or not isinstance(amount, int)
            or not 0 <= amount <= hydration.MAX_GLASSES):
        raise errors.InvalidHydration
    return amount


def set_today(user_id, secret, revision, amount, day=None):
    """Absolute desired state under the precondition; returns the committed view."""
    day_iso = (day or app_today()).isoformat()

    def check(current):
        if not tokens.matches(
                hydration_revision(secret, user_id, day_iso, current), revision):
            raise errors.StaleHydration

    committed = hydration.set_today_count(user_id, day_iso, amount, check)
    hydration.award_water_logged(user_id, day_iso, committed)
    return _payload(secret, user_id, day_iso, committed)
