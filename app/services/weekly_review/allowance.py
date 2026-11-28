"""PD6-only logical allowance. Check-in workers own transaction boundaries."""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import WeeklyReviewAdmission
from app.timeutil import app_date_of

FAMILY = "weekly_checkin_evaluation"
LIMIT = 5
WINDOW_DAYS = 30
RESERVED = "reserved"
CHARGED = "charged"
RESTORED = "restored"


@dataclass(frozen=True)
class Campaign:
    """Source-approved grant periods, anchored to an Istanbul calendar date."""

    starts_on: date
    windows: int


# The approved cap does not establish a production grant or its dates.
APPROVED_CAMPAIGN = None


def window_for(now):
    campaign = APPROVED_CAMPAIGN
    if campaign is None:
        return None
    elapsed = (app_date_of(now) - campaign.starts_on).days
    if not 0 <= elapsed < WINDOW_DAYS * campaign.windows:
        return None
    return campaign.starts_on + timedelta(days=elapsed // WINDOW_DAYS * WINDOW_DAYS)


def remaining(owner, window):
    used = db.session.execute(select(func.count()).select_from(WeeklyReviewAdmission)
        .filter_by(user_id=owner, window_start=window)
        .where(WeeklyReviewAdmission.state.in_((RESERVED, CHARGED)))).scalar_one()
    return max(0, LIMIT - used)


def reserve(owner, week, window):
    """The caller holds the owner fence through the dispatch marker commit."""
    if db.session.get(WeeklyReviewAdmission, (owner, week)) is not None:
        return None, "weekly_slot_used"
    if window is None:
        return None, "allowance_unavailable"
    if remaining(owner, window) == 0:
        return None, "allowance_exhausted"
    admission = WeeklyReviewAdmission(
        user_id=owner, week_start=week, window_start=window, state=RESERVED)
    db.session.add(admission)
    return admission, None


def admission_for(checkin):
    if checkin.review_admission_week is None:
        return None
    return db.session.get(
        WeeklyReviewAdmission, (checkin.user_id, checkin.review_admission_week),
        populate_existing=True,
    )


def restore_erased_checkins(owner):
    """Release pending units while retaining minimal account billing evidence."""
    return WeeklyReviewAdmission.query.filter_by(user_id=owner, state=RESERVED).update(
        {"state": RESTORED}, synchronize_session=False)
