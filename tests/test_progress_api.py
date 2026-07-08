from app.extensions import db
from app.models import MealLog, WorkoutLog, WORKOUT_COMPLETION_MARKER
from app.timeutil import app_today


def _login(make_user, login, name="prog"):
    u = make_user(name, profile_complete=True)
    login(name)
    return u


def test_nutrition_trend_groups_by_day(app, client, make_user, login):
    u = _login(make_user, login, "nutru")
    today = app_today().isoformat()
    db.session.add(MealLog(user_id=u.id, ogun="Kahvaltı", yemekler="x",
                           kalori=500, protein=30, karb=50, yag=10, tarih=today))
    db.session.add(MealLog(user_id=u.id, ogun="Öğle", yemekler="y",
                           kalori=700, protein=40, karb=60, yag=20, tarih=today))
    db.session.commit()
    r = client.get("/api/progress/nutrition?range=week")
    assert r.status_code == 200
    d = r.get_json()
    assert len(d["days"]) == 7
    last = d["days"][-1]
    assert last["date"] == today and last["kcal"] == 1200 and last["p"] == 70
    assert d["avg"]["kcal"] == 1200   # one logged day


def test_nutrition_scoped_to_user(app, client, make_user, login):
    other = make_user("nutother", profile_complete=True)
    db.session.add(MealLog(user_id=other.id, ogun="Kahvaltı", yemekler="z",
                           kalori=999, protein=1, karb=1, yag=1,
                           tarih=app_today().isoformat()))
    db.session.commit()
    _login(make_user, login, "nutme")
    d = client.get("/api/progress/nutrition?range=week").get_json()
    assert all(day["kcal"] == 0 for day in d["days"])   # other user's meal not visible


def test_workout_trend_marker_excluded_from_volume(app, client, make_user, login):
    u = _login(make_user, login, "wktrend")
    db.session.add(WorkoutLog(user_id=u.id, exercise_name="Squat",
                              sets=3, reps=10, weight_kg=100, volume=3000))
    db.session.add(WorkoutLog(user_id=u.id, exercise_name=WORKOUT_COMPLETION_MARKER,
                              sets=1, reps=1, weight_kg=0, volume=0))
    db.session.commit()
    d = client.get("/api/progress/workout?range=week").get_json()
    assert d["totals"]["volume"] == 3000          # marker excluded
    assert d["totals"]["sessions"] == 1           # today counts once
    assert d["days"][-1]["sessions"] == 1 and d["days"][-1]["volume"] == 3000


def test_heatmap_levels_from_activity(app, client, make_user, login):
    u = _login(make_user, login, "hmuser")
    today = app_today().isoformat()
    db.session.add(MealLog(user_id=u.id, ogun="Kahvaltı", yemekler="x",
                           kalori=100, protein=1, karb=1, yag=1, tarih=today))
    db.session.add(WorkoutLog(user_id=u.id, exercise_name="Squat",
                              sets=1, reps=1, weight_kg=1, volume=1))
    db.session.commit()
    d = client.get("/api/progress/heatmap?weeks=4").get_json()
    assert d["weeks"] == 4 and len(d["cells"]) == 28
    today_cell = [c for c in d["cells"] if c["date"] == today][0]
    assert today_cell["level"] == 2          # meal + workout = score 2
    assert d["cells"][0]["level"] == 0       # an empty earlier day


def test_heatmap_multi_exercise_workout_counts_once(app, client, make_user, login):
    # A real workout writes one WorkoutLog per exercise. The heatmap gradient is
    # "did each of meal/activity/workout/check-in happen" (0-4), so a single
    # session's many rows must bump the day only once — not once per exercise
    # (which would collapse every workout day to level 4). Regression guard.
    u = _login(make_user, login, "hmmulti")
    today = app_today().isoformat()
    for name in ("Squat", "Bench", "Deadlift", "Row", "Curl"):
        db.session.add(WorkoutLog(user_id=u.id, exercise_name=name,
                                  sets=3, reps=10, weight_kg=50, volume=1500))
    db.session.commit()
    d = client.get("/api/progress/heatmap?weeks=4").get_json()
    today_cell = [c for c in d["cells"] if c["date"] == today][0]
    assert today_cell["level"] == 1          # one workout signal, not 5 / not maxed


def test_heatmap_weeks_clamped(app, client, make_user, login):
    _login(make_user, login, "hmclamp")
    d = client.get("/api/progress/heatmap?weeks=999").get_json()
    assert d["weeks"] == 53 and len(d["cells"]) == 53 * 7


def test_achievements_shape(app, client, make_user, login):
    u = _login(make_user, login, "achuser")
    u.rank_points = 1200
    u.streak_count = 9
    # update_streak (app/hooks.py before_request) resets streak_count to 1 on the
    # first authenticated request for a user whose last_login isn't already "today"
    # (fresh test users have last_login=None). Pin last_login=today so this test
    # observes our manually-set streak value instead of the unrelated streak hook.
    u.last_login = app_today()
    db.session.commit()
    d = client.get("/api/progress/achievements").get_json()
    assert d["rank_points"] == 1200 and d["streak"] == 9
    assert isinstance(d["level"], int) and isinstance(d["title"], str)
    assert any(m["key"] == "streak7" and m["hit"] for m in d["milestones"])
    assert any(m["key"] == "streak30" and not m["hit"] for m in d["milestones"])


def test_insights_always_nonempty(app, client, make_user, login):
    _login(make_user, login, "insuser")
    d = client.get("/api/progress/insights").get_json()
    assert isinstance(d["insights"], list) and len(d["insights"]) >= 1
    first = d["insights"][0]
    assert set(("icon", "title", "body", "tone")).issubset(first)
    assert first["tone"] in ("success", "warning", "info")
