# Challenge blueprint route testleri (Sprint 5 PR3). Hermetik: AutoOriginClient
# (auto-CSRF), in-memory SQLite. auth_user oturum açmış varsayılan kullanıcı.
from app.extensions import db
from app.models import Challenge, UserChallengeProgress


def _seed(app, **kw):
    d = dict(code="weekly_workouts", title="Haftalık Antrenman", description="3 antrenman",
             category="workouts", metric="workout_logged", target_value=3, xp_reward=150,
             challenge_type="global", period_type="weekly", is_active=True)
    d.update(kw)
    c = Challenge(**d)
    db.session.add(c)
    db.session.commit()
    return c


def test_challenges_data_shape(client, auth_user, app):
    _seed(app)
    r = client.get("/challenges/data")
    assert r.status_code == 200
    j = r.get_json()
    assert "weekKey" in j and "periodEndsAt" in j and isinstance(j["challenges"], list)
    ch = j["challenges"][0]
    # global challenge → otomatik katılım (joined=True, Katıl butonu yok).
    assert ch["progress"] == 0 and ch["completed"] is False and ch["joined"] is True


def test_featured_not_joined_shows_joinable(client, auth_user, app):
    _seed(app, code="featured_grind", challenge_type="featured")
    ch = client.get("/challenges/data").get_json()["challenges"][0]
    assert ch["type"] == "featured" and ch["joined"] is False  # Katıl butonu görünür


def test_join_global_400_featured_ok(client, auth_user, app):
    g = _seed(app, code="weekly_workouts", challenge_type="global")
    f = _seed(app, code="featured_grind", challenge_type="featured")
    assert client.post("/challenges/%d/join" % g.id).status_code == 400
    r = client.post("/challenges/%d/join" % f.id)
    assert r.status_code == 200 and r.get_json()["joined"] is True
    assert client.post("/challenges/%d/join" % f.id).status_code == 200  # idempotent


def test_join_unknown_404(client, auth_user):
    assert client.post("/challenges/999999/join").status_code == 404


def test_leaderboard_route(client, auth_user, app):
    c = _seed(app)
    r = client.get("/challenges/%d/leaderboard?scope=global" % c.id)
    assert r.status_code == 200
    assert "entries" in r.get_json() and "me" in r.get_json()


def test_challenges_data_requires_auth(raw_client):
    assert raw_client.get("/challenges/data").status_code in (302, 401)


def test_challenges_page_renders(client, auth_user):
    r = client.get("/challenges")
    assert r.status_code == 200
    assert b"challenges-root" in r.data
