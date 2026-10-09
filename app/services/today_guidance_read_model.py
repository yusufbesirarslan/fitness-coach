"""LP17 internal read model. No route, cache, writes, or global action ranking.

Call only at a read boundary with an authenticated principal's scalar id and
a clean session: canonical snapshot helpers release the scoped session.
Sources are independent snapshots, not an atomic cross-domain observation.
"""
from app.extensions import db
from app.services import mobile_today, nutrition_day_view
from app.services.mobile_weekly_checkin import history
from app.services.workout_state.snapshot import coherent_read_snapshot
from app.timeutil import app_today

from app.services.today_guidance_projection import project_guidance


class GuidanceUnavailable(RuntimeError):
    """Strict training failure, date incoherence, or unpublishable projection."""


def _secondary(read):
    try:
        with coherent_read_snapshot():
            return read()
    except Exception:
        # Never publish exception text, fabricated emptiness, or reassuring facts.
        return None


def build_today_guidance(user_id):
    """Read-only, owner-scoped, server-day foundation; not an HTTP contract."""
    if db.session.new or db.session.dirty or db.session.deleted:
        raise GuidanceUnavailable("requires a clean read boundary")
    try:
        training = mobile_today.build_today(user_id)["today"]
        nutrition = _secondary(lambda: nutrition_day_view.nutrition_day_view_payload(
            nutrition_day_view.build_nutrition_day_view(user_id)))
        checkin = _secondary(lambda: history.build_history(user_id))
        day = app_today().isoformat()
        # Existing readers capture their own canonical day. Never combine days
        # when Istanbul midnight falls between the independent source snapshots.
        if training["date"] != day or (nutrition is not None and nutrition["day"] != day):
            raise GuidanceUnavailable("server day changed; reread")
        if checkin is not None:
            week = checkin["current_week"]
            if not week["start_day"] <= day <= week["end_day"]:
                raise GuidanceUnavailable("server week changed; reread")
        return project_guidance(training, nutrition, checkin)
    except GuidanceUnavailable:
        raise
    except Exception as error:
        raise GuidanceUnavailable("canonical guidance read failed") from error
