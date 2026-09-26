"""Bounded same-day counts, from persisted facts through EN/TR presentation."""
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.extensions import db
from app.services.progress_history import build_progress_history, progress_history_payload
from test_progress_history import END_DAY, _add_checkin, _select_count

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("count, older", [(12, False), (12, True), (13, False), (20, True)])
def test_same_day_count_is_truthful_without_an_extra_query(make_user, count, older):
    user = make_user("saturation")
    for minute in range(count):
        _add_checkin(user.id, END_DAY, 80.0,
                     created_at=datetime(2026, 7, 15, 10, minute))
    if older:
        _add_checkin(user.id, END_DAY - timedelta(days=1), 81.0)
    db.session.commit()
    user_id = user.id
    payload = progress_history_payload(build_progress_history(user_id))
    assert len(payload["entries"]) == 12
    assert payload["has_more"] is (count > 12 or older)
    assert payload["incomplete_day"] == (END_DAY.isoformat() if count > 12 else None)
    # One bounded check-in read plus the existing day-granular training read.
    assert _select_count(user_id) == 2

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = ("global.window = {}; require(" + json.dumps(str(ROOT / "static/progress_presentation.js"))
              + "); console.log(JSON.stringify(window.FitXProgressPresentation.buildHistoryView("
              + json.dumps(payload) + ")));")
    result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8",
                            capture_output=True, check=True)
    view = json.loads(result.stdout)
    assert len(view["groups"]) == 1
    group = view["groups"][0]
    assert group["count"] == len(group["entries"]) == 12
    copy = group["updates"]
    for locale, exact, saturated in (("en", "12 check-ins this day", "12+ updates"),
                                      ("tr", "Bu gün 12 check-in", "12+ güncelleme")):
        catalog = json.loads((ROOT / "locales" / (locale + ".json")).read_text(encoding="utf-8"))
        text = catalog[copy["key"]].replace("{n}", str(copy["params"]["n"]))
        assert text == (saturated if count > 12 else exact)


def test_dropped_partial_day_does_not_mark_a_complete_day(make_user):
    user = make_user("trimmedsaturation")
    _add_checkin(user.id, END_DAY, 80.0)
    prior = END_DAY - timedelta(days=1)
    for minute in range(13):
        _add_checkin(user.id, prior, 81.0,
                     created_at=datetime(2026, 7, 14, 10, minute))
    db.session.commit()
    payload = progress_history_payload(build_progress_history(user.id))
    assert len(payload["entries"]) == 1
    assert payload["has_more"] is True
    assert payload["incomplete_day"] is None
