"""LP16-A characterization of the two web weight/check-in writers.

These tests were written against ``origin/main`` 088d04d BEFORE the canonical
``app/services/weekly_checkin`` extraction and pass unchanged on both sides of
it. They pin what a browser can observe from ``POST /checkin`` and
``POST /update-weight`` — response bodies, persisted rows, ``User.weight``,
the canonical ``UserSession`` derived targets, ``target_weight``, the AI call
position, the keyed lock, the commit count and the all-or-nothing write — so
the extraction is provably an authority move and not a product change.

Known defects are pinned ON PURPOSE (LP16-A must not fix them):
- keyed ``/checkin`` holds the owner lock across the feedback call;
- ``/update-weight`` overwrites the weight of a same-Istanbul-day FULL
  check-in and never takes the owner lock.

    python -m pytest tests/test_lp16a_web_checkin_characterization.py -q
"""
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.orm import Session

from app.blueprints import tracking
from app.extensions import db
from app.models import DailyQuest, User, UserQuestProgress, UserSession, WeeklyCheckIn
from app.timeutil import app_today, utc_day_bounds


FULL = {"weight": 78, "yogunluk": 4, "fatigue": 2,
        "progressive_overload": "evet", "uyku_kalitesi": 5,
        "beslenme_uyumu": 4, "note": "solid week"}

SAVED_TR = "Check-in kaydedildi."
CONFLICT_TR = "Bu gönderim anahtarı farklı bir check-in için kullanılmış."
INVALID_KEY_TR = "Geçersiz gönderim anahtarı."

# calculate_bmr(78, 180, 30, "male") = 1760; active x1.55; "kilo verme" -400.
BMR_78, TDEE_78, TARGET_78 = 1760.0, 2728.0, 2328.0


@pytest.fixture
def feedback(monkeypatch):
    calls = []

    def _feedback(*args, **kwargs):
        calls.append((args, kwargs))
        return "coach says hi"

    monkeypatch.setattr(tracking, "generate_checkin_feedback", _feedback)
    return calls


def _complete_profile(user, *, session=True):
    user.weight, user.height, user.age = 80.0, 180.0, 30
    user.gender, user.goal, user.current_activity = "male", "kilo verme", "active"
    user.target_weight = 72.0
    if session:
        db.session.add(UserSession(
            user_id=user.id, weight=80.0, height=180.0, age=30, gender="male",
            goal="kilo verme", current_activity="active",
            bmr=1.0, tdee=2.0, target_calories=3.0))
    db.session.commit()


def _fresh_user(user_id):
    db.session.rollback()
    db.session.expire_all()
    return db.session.get(User, user_id)


def _session_row(user_id):
    db.session.expire_all()
    return (UserSession.query.filter_by(user_id=user_id)
            .order_by(UserSession.created_at.desc(), UserSession.id.desc()).first())


def _rows(user_id):
    db.session.expire_all()
    return (WeeklyCheckIn.query.filter_by(user_id=user_id)
            .order_by(WeeklyCheckIn.id.asc()).all())


_VIEWS = ("tracking.checkin", "tracking.update_weight")


@pytest.fixture
def in_view(app, monkeypatch):
    """True only while one of the two writer views runs.

    Request hooks (streak/rollover/observability) commit and lock on their own;
    the characterization is about what the VIEW does, so only statements and
    commits issued inside the view function are attributed to it.
    """
    state = {"inside": False}
    for endpoint in _VIEWS:
        original = app.view_functions[endpoint]

        def _wrapped(*args, _original=original, **kwargs):
            try:
                return _original(*args, **kwargs)
            finally:
                state["inside"] = False

        monkeypatch.setitem(app.view_functions, endpoint, _wrapped)

    # The auth decorator wrapped around each view refreshes the web session
    # (its own commit). Attribution starts at the view body's first step —
    # weight parsing — which both views reach before any persistence work.
    original_parse = tracking._parse_weight

    def _parse(value):
        state["inside"] = True
        return original_parse(value)

    monkeypatch.setattr(tracking, "_parse_weight", _parse)
    return state


class _Commits:
    def __init__(self, state):
        self.state = state
        self.count = 0

    def __call__(self, _session):
        if self.state["inside"]:
            self.count += 1


@pytest.fixture
def commits(in_view):
    counter = _Commits(in_view)
    sa.event.listen(Session, "after_commit", counter)
    try:
        yield counter
    finally:
        sa.event.remove(Session, "after_commit", counter)


LOCK = "LOCK "


@pytest.fixture
def statements(app, in_view):
    """Executed SQL, plus a ``LOCK <entity>`` mark for every ``FOR UPDATE``.

    SQLite's dialect drops ``FOR UPDATE`` from the SQL text, so the row lock is
    observed at the ORM layer (dialect independent) and interleaved with the
    cursor statements in execution order.
    """
    seen = []

    def _record(_conn, _cursor, statement, _params, _context, _many):
        if in_view["inside"]:
            seen.append(" ".join(statement.split()))

    def _orm(state):
        statement = state.statement
        if in_view["inside"] and state.is_select and getattr(statement, "_for_update_arg", None) is not None:
            names = sorted({d.get("entity").__name__
                            for d in statement.column_descriptions
                            if d.get("entity") is not None})
            seen.append(LOCK + ",".join(names))

    engine = db.engine
    sa.event.listen(engine, "before_cursor_execute", _record)
    sa.event.listen(Session, "do_orm_execute", _orm)
    try:
        yield seen
    finally:
        sa.event.remove(engine, "before_cursor_execute", _record)
        sa.event.remove(Session, "do_orm_execute", _orm)


def _seed_checkin_quest():
    db.session.add(DailyQuest(title="Haftalık Check-in", points_reward=20,
                              quest_type="checkin_done", is_active=True))
    db.session.commit()


# ---------------------------------------------------------------------------
# POST /checkin
# ---------------------------------------------------------------------------

def test_unkeyed_full_checkin_response_row_weight_and_session(
        client, auth_user, feedback, commits):
    _complete_profile(auth_user)
    commits.count = 0

    response = client.post("/checkin", json=FULL)

    assert response.status_code == 200
    assert response.get_json() == {"message": SAVED_TR,
                                   "coach_feedback": "coach says hi"}
    # Row commit, then complete_quest_for_user's own commit.
    assert commits.count == 2
    [row] = _rows(auth_user.id)
    assert (row.weight, row.yogunluk, row.fatigue, row.progressive_overload,
            row.uyku_kalitesi, row.beslenme_uyumu, row.note, row.coach_feedback,
            row.idempotency_key, row.request_fingerprint, row.response_snapshot) == (
        78.0, 4, 2, "evet", 5, 4, "solid week", "coach says hi", None, None, None)
    user = _fresh_user(auth_user.id)
    assert user.weight == 78.0
    assert user.target_weight == 72.0
    session = _session_row(auth_user.id)
    assert (session.weight, session.bmr, session.tdee, session.target_calories) == (
        78.0, BMR_78, TDEE_78, TARGET_78)


def test_keyed_full_checkin_commits_once_with_quest_and_snapshot(
        client, auth_user, feedback, commits):
    _complete_profile(auth_user)
    _seed_checkin_quest()
    commits.count = 0

    response = client.post("/checkin", json=FULL,
                           headers={"Idempotency-Key": "lp16a-keyed-0001"})

    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"message", "coach_feedback", "quest_awarded"}
    assert body["message"] == SAVED_TR
    assert body["quest_awarded"]["xp"] == 20
    # Row + weight + session + quest + snapshot are ONE commit.
    assert commits.count == 1
    [row] = _rows(auth_user.id)
    assert row.idempotency_key == "lp16a-keyed-0001"
    assert len(row.request_fingerprint) == 64
    import json
    assert json.loads(row.response_snapshot) == body
    assert UserQuestProgress.query.filter_by(user_id=auth_user.id).count() == 1
    assert _fresh_user(auth_user.id).weight == 78.0


def test_unkeyed_quest_award_is_a_separate_second_commit(
        client, auth_user, feedback, commits):
    _complete_profile(auth_user)
    _seed_checkin_quest()
    commits.count = 0
    body = client.post("/checkin", json=FULL).get_json()
    assert body["quest_awarded"]["xp"] == 20
    assert commits.count == 2


def test_incomplete_profile_updates_weight_but_never_derived_targets(
        client, auth_user, feedback):
    auth_user.weight, auth_user.target_weight = 80.0, 70.0
    db.session.add(UserSession(user_id=auth_user.id, weight=80.0,
                               bmr=11.0, tdee=22.0, target_calories=33.0))
    db.session.commit()

    assert client.post("/checkin", json=FULL).status_code == 200

    user = _fresh_user(auth_user.id)
    assert (user.weight, user.target_weight) == (78.0, 70.0)
    session = _session_row(auth_user.id)
    assert (session.weight, session.bmr, session.tdee, session.target_calories) == (
        78.0, 11.0, 22.0, 33.0)


def test_checkin_without_session_writes_weight_and_creates_no_session(
        client, auth_user, feedback):
    _complete_profile(auth_user, session=False)
    assert client.post("/checkin", json=FULL).status_code == 200
    assert _fresh_user(auth_user.id).weight == 78.0
    assert UserSession.query.filter_by(user_id=auth_user.id).count() == 0


def test_newest_session_is_the_one_recalculated(client, auth_user, feedback):
    _complete_profile(auth_user)
    older = _session_row(auth_user.id)
    newest = UserSession(user_id=auth_user.id, created_at=older.created_at,
                         bmr=5.0, tdee=6.0, target_calories=7.0, weight=80.0)
    db.session.add(newest)
    db.session.commit()
    older_id, newest_id = older.id, newest.id
    assert newest_id > older_id

    client.post("/checkin", json=FULL)

    db.session.expire_all()
    assert db.session.get(UserSession, newest_id).target_calories == TARGET_78
    assert db.session.get(UserSession, older_id).target_calories == 3.0


def test_legacy_parser_fills_missing_metrics_with_three(client, auth_user, feedback):
    response = client.post("/checkin", json={"weight": 79,
                                             "progressive_overload": "belki",
                                             "yogunluk": "yüksek"})
    assert response.status_code == 200
    [row] = _rows(auth_user.id)
    assert (row.yogunluk, row.fatigue, row.uyku_kalitesi, row.beslenme_uyumu,
            row.progressive_overload, row.note) == (3, 3, 3, 3, "kismen", "")


def test_feedback_receives_previous_full_checkin_not_sparse_rows(
        client, auth_user, feedback):
    _complete_profile(auth_user)
    long_ago = datetime.utcnow() - timedelta(days=9)
    db.session.add(WeeklyCheckIn(user_id=auth_user.id, weight=81.0, yogunluk=3,
                                 created_at=long_ago))
    # Sparse /update-weight row, newer: must not become "previous".
    db.session.add(WeeklyCheckIn(user_id=auth_user.id, weight=79.5,
                                 created_at=datetime.utcnow()))
    db.session.commit()

    client.post("/checkin", json=FULL)

    [(args, kwargs)] = feedback
    assert args == ("testuser", 78.0, 81.0, 9, "kilo verme", 4, 2, "evet", 5, 4,
                    "solid week")
    assert kwargs == {"language": "tr"}


def test_feedback_without_previous_or_session_uses_defaults(
        client, auth_user, feedback):
    client.post("/checkin", json={"weight": 79})
    [(args, _kwargs)] = feedback
    assert args[2:5] == (None, None, "genel sağlık")


def test_keyed_replay_returns_snapshot_without_second_feedback_or_write(
        client, auth_user, feedback, commits):
    _complete_profile(auth_user)
    headers = {"Idempotency-Key": "lp16a-replay-0001"}
    first = client.post("/checkin", json=FULL, headers=headers)
    commits.count = 0
    replay = client.post("/checkin", json=FULL, headers=headers)
    assert replay.status_code == 200
    assert replay.get_json() == first.get_json()
    assert len(feedback) == 1
    assert len(_rows(auth_user.id)) == 1
    assert commits.count == 0


def test_keyed_conflict_is_409_without_feedback_or_write(
        client, auth_user, feedback):
    _complete_profile(auth_user)
    headers = {"Idempotency-Key": "lp16a-conflict-0001"}
    assert client.post("/checkin", json=FULL, headers=headers).status_code == 200
    changed = client.post("/checkin", json={**FULL, "fatigue": 5}, headers=headers)
    assert changed.status_code == 409
    assert changed.get_json() == {"error": CONFLICT_TR}
    assert len(feedback) == 1
    [row] = _rows(auth_user.id)
    assert row.fatigue == 2
    assert _fresh_user(auth_user.id).weight == 78.0


def test_invalid_key_and_weight_errors_precede_any_work(
        client, auth_user, feedback, statements):
    statements.clear()
    bad_key = client.post("/checkin", json=FULL,
                          headers={"Idempotency-Key": "bad key"})
    assert bad_key.status_code == 400
    assert bad_key.get_json() == {"error": INVALID_KEY_TR}
    missing = client.post("/checkin", json={"yogunluk": 4})
    assert missing.status_code == 400
    assert not feedback
    assert not [s for s in statements if s.startswith(("INSERT", "UPDATE"))
                and "weekly_check_in" in s.lower()]
    assert _rows(auth_user.id) == []


def test_keyed_lock_is_taken_before_feedback_and_held_until_commit(
        client, auth_user, monkeypatch, statements):
    """Recorded debt: the keyed owner lock spans the feedback call."""
    marks = []

    def _feedback(*args, **kwargs):
        marks.append(len(statements))
        return "fb"

    monkeypatch.setattr(tracking, "generate_checkin_feedback", _feedback)
    statements.clear()
    client.post("/checkin", json=FULL, headers={"Idempotency-Key": "lp16a-lock-0001"})

    lock_at = statements.index(LOCK + "User")
    insert_at = next(i for i, s in enumerate(statements)
                     if s.startswith("INSERT INTO weekly_check_in"))
    [feedback_at] = marks
    assert lock_at < feedback_at <= insert_at


def test_unkeyed_checkin_takes_no_owner_lock(client, auth_user, feedback, statements):
    statements.clear()
    client.post("/checkin", json=FULL)
    assert not [s for s in statements if s.startswith(LOCK)]


class _InsertFails(Exception):
    pass


@pytest.fixture
def failing_checkin_insert():
    def _boom(*_args):
        raise _InsertFails("injected")

    sa.event.listen(WeeklyCheckIn, "before_insert", _boom)
    try:
        yield
    finally:
        sa.event.remove(WeeklyCheckIn, "before_insert", _boom)


def _post_failing(client, path, json, headers=None):
    try:
        response = client.post(path, json=json, headers=headers or {})
    except (_InsertFails, PendingRollbackError):
        # TESTING propagates: the fault (or the poisoned session a later hook
        # trips over) surfaces as the raised exception, never a 2xx.
        status = None
    else:
        status = response.status_code
    # The test shares the request's app context (and so its session).
    db.session.rollback()
    return status


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "lp16a-fail-0001"}])
def test_checkin_persistence_failure_writes_nothing(
        client, auth_user, feedback, failing_checkin_insert, headers):
    _complete_profile(auth_user)
    uid = auth_user.id
    status = _post_failing(client, "/checkin", FULL, headers)
    assert status in (None, 500)
    user = _fresh_user(uid)
    assert user.weight == 80.0
    session = _session_row(uid)
    assert (session.weight, session.bmr, session.tdee, session.target_calories) == (
        80.0, 1.0, 2.0, 3.0)
    assert _rows(uid) == []


def test_keyed_failure_after_staging_writes_nothing(
        client, auth_user, feedback, monkeypatch):
    """A fault between staging and the single commit leaves no half write."""
    _complete_profile(auth_user)

    def _quest_fault(*_args):
        raise _InsertFails("quest staging fault")

    monkeypatch.setattr(tracking, "_claim_quest", _quest_fault)
    uid = auth_user.id
    status = _post_failing(client, "/checkin", FULL,
                           {"Idempotency-Key": "lp16a-fail-0002"})
    assert status in (None, 500)
    assert _fresh_user(uid).weight == 80.0
    assert _session_row(uid).target_calories == 3.0
    assert _rows(uid) == []


# ---------------------------------------------------------------------------
# POST /update-weight
# ---------------------------------------------------------------------------

def test_update_weight_complete_profile_response_and_sparse_row(
        client, auth_user, commits):
    _complete_profile(auth_user)
    commits.count = 0

    response = client.post("/update-weight", json={"weight": 78})

    assert response.status_code == 200
    assert response.get_json() == {"bmr": 1760, "tdee": 2728,
                                   "target_calories": 2328,
                                   "profile_incomplete": False}
    assert commits.count == 2
    [row] = _rows(auth_user.id)
    assert (row.weight, row.yogunluk, row.fatigue, row.progressive_overload,
            row.coach_feedback, row.idempotency_key) == (78.0, None, None, None,
                                                         None, None)
    user = _fresh_user(auth_user.id)
    assert (user.weight, user.target_weight) == (78.0, 72.0)
    assert _session_row(auth_user.id).target_calories == TARGET_78


def test_update_weight_incomplete_profile_reports_stored_targets(client, auth_user):
    auth_user.weight = 80.0
    db.session.add(UserSession(user_id=auth_user.id, weight=80.0,
                               bmr=1500.4, tdee=2000.6, target_calories=1800.5))
    db.session.commit()
    body = client.post("/update-weight", json={"weight": 77}).get_json()
    assert body == {"bmr": 1500, "tdee": 2001, "target_calories": 1800,
                    "profile_incomplete": True}
    session = _session_row(auth_user.id)
    assert (session.weight, session.bmr) == (77.0, 1500.4)


def test_update_weight_without_session_answers_nulls(client, auth_user):
    body = client.post("/update-weight", json={"weight": 77}).get_json()
    assert body == {"bmr": None, "tdee": None, "target_calories": None,
                    "profile_incomplete": True}
    assert _fresh_user(auth_user.id).weight == 77.0


def test_update_weight_overwrites_same_day_full_checkin_known_defect(
        client, auth_user, feedback):
    """Pinned defect (NOT fixed in LP16-A): the full row's weight changes."""
    _complete_profile(auth_user)
    client.post("/checkin", json=FULL)
    response = client.post("/update-weight", json={"weight": 76})
    assert response.status_code == 200
    [row] = _rows(auth_user.id)
    assert (row.weight, row.yogunluk, row.coach_feedback) == (76.0, 4,
                                                              "coach says hi")


def test_update_weight_takes_no_owner_lock_known_defect(
        client, auth_user, statements):
    _complete_profile(auth_user)
    statements.clear()
    client.post("/update-weight", json={"weight": 76})
    assert not [s for s in statements if s.startswith(LOCK)]


def test_update_weight_repeats_update_one_row(client, auth_user):
    _complete_profile(auth_user)
    client.post("/update-weight", json={"weight": 78})
    client.post("/update-weight", json={"weight": 77})
    [row] = _rows(auth_user.id)
    assert row.weight == 77.0


def test_update_weight_same_istanbul_day_row_is_updated(client, auth_user):
    start, _end = utc_day_bounds(app_today())
    db.session.add(WeeklyCheckIn(user_id=auth_user.id, weight=81.0,
                                 created_at=start + timedelta(minutes=1)))
    db.session.commit()
    client.post("/update-weight", json={"weight": 77})
    [row] = _rows(auth_user.id)
    assert row.weight == 77.0


def test_update_weight_previous_istanbul_day_row_is_not_updated(client, auth_user):
    start, _end = utc_day_bounds(app_today())
    db.session.add(WeeklyCheckIn(user_id=auth_user.id, weight=81.0,
                                 created_at=start - timedelta(minutes=1)))
    db.session.commit()
    client.post("/update-weight", json={"weight": 77})
    rows = _rows(auth_user.id)
    assert [r.weight for r in rows] == [81.0, 77.0]


def test_update_weight_never_touches_another_users_same_day_row(
        client, auth_user, make_user):
    other = make_user("lp16aother")
    db.session.add(WeeklyCheckIn(user_id=other.id, weight=90.0))
    db.session.commit()
    client.post("/update-weight", json={"weight": 77})
    assert [r.weight for r in _rows(other.id)] == [90.0]
    assert [r.weight for r in _rows(auth_user.id)] == [77.0]
    assert _fresh_user(other.id).weight is None


def test_update_weight_persistence_failure_writes_nothing(
        client, auth_user, failing_checkin_insert):
    _complete_profile(auth_user)
    uid = auth_user.id
    status = _post_failing(client, "/update-weight", {"weight": 77})
    assert status in (None, 500)
    assert _fresh_user(uid).weight == 80.0
    assert _session_row(uid).target_calories == 3.0
    assert _rows(uid) == []


def test_update_weight_validation_errors_write_nothing(client, auth_user):
    assert client.post("/update-weight", json={}).status_code == 400
    assert client.post("/update-weight", json={"weight": "x"}).status_code == 400
    assert _rows(auth_user.id) == []
