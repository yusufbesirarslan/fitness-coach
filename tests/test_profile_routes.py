"""Route tests for the profile blueprint (app/blueprints/profile.py).

/setup (ilk profil + ilk UserSession) ve /edit-profile (kullanıcı adı/foto/
hedef güncelleme) akışları. AI yok; hesaplamalar deterministik.

    python -m pytest tests/test_profile_routes.py -v
"""
from app.extensions import db
from app.models import User, UserSession

SETUP_PAYLOAD = {
    "weight": 80, "height": 180, "age": 30, "gender": "male",
    "goal": "kilo verme", "fitness_level": "beginner",
    "current_activity": "active", "target_weight": "75",
}


def _fresh_user(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


# ---------------------------------------------------------------------------
# /setup
# ---------------------------------------------------------------------------

def test_setup_page_renders(client, auth_user):
    assert client.get("/setup").status_code == 200


def test_setup_saves_profile_and_creates_session(client, auth_user):
    response = client.post("/setup", json=SETUP_PAYLOAD)
    assert response.status_code == 200
    body = response.get_json()
    assert body["bmr"] == 1780                       # Mifflin erkek 80/180/30
    assert body["tdee"] == round(1780 * 1.55)
    assert body["target_calories"] == round(1780 * 1.55 - 400)

    user = _fresh_user(auth_user.id)
    assert user.profile_complete is True
    assert user.goal_type == "loss"
    assert user.target_weight == 75.0

    session = UserSession.query.filter_by(user_id=auth_user.id).one()
    assert session.bmr == 1780
    assert "yürüyüş" in session.training_plan


def test_setup_missing_field_rejected(client, auth_user):
    payload = dict(SETUP_PAYLOAD)
    del payload["goal"]
    response = client.post("/setup", json=payload)
    assert response.status_code == 400
    assert "goal" in response.get_json()["error"]


def test_setup_non_numeric_values_rejected(client, auth_user):
    response = client.post("/setup", json={**SETUP_PAYLOAD, "age": "otuz"})
    assert response.status_code == 400


def test_setup_invalid_target_weight_ignored(client, auth_user):
    response = client.post("/setup", json={**SETUP_PAYLOAD, "target_weight": "bilmem"})
    assert response.status_code == 200
    assert _fresh_user(auth_user.id).target_weight is None


def test_setup_gain_goal_sets_goal_type(client, auth_user):
    client.post("/setup", json={**SETUP_PAYLOAD, "goal": "kas kazanma"})
    assert _fresh_user(auth_user.id).goal_type == "gain"


# ---------------------------------------------------------------------------
# /edit-profile
# ---------------------------------------------------------------------------

def test_edit_profile_page_renders(client, auth_user):
    assert client.get("/edit-profile").status_code == 200


def test_edit_profile_updates_fields(client, auth_user):
    response = client.post("/edit-profile", json={
        "username": "yeniad", "full_name": "Yusuf B", "goal": "kas kazanma",
        "target_weight": 85,
    })
    assert response.status_code == 200
    user = _fresh_user(auth_user.id)
    assert user.username == "yeniad"
    assert user.full_name == "Yusuf B"
    assert user.goal == "kas kazanma"
    assert user.goal_type == "gain"
    assert user.target_weight == 85.0


def test_edit_profile_keeping_own_username_ok(client, auth_user):
    response = client.post("/edit-profile", json={"username": "testuser"})
    assert response.status_code == 200


def test_edit_profile_taken_username_rejected(client, auth_user, make_user):
    make_user("mevcut")
    response = client.post("/edit-profile", json={"username": "mevcut"})
    assert response.status_code == 400
    assert "alınmış" in response.get_json()["error"]


def test_edit_profile_invalid_username_rejected(client, auth_user):
    assert client.post("/edit-profile", json={"username": "a b"}).status_code == 400


def test_edit_profile_long_full_name_rejected(client, auth_user):
    response = client.post("/edit-profile",
                           json={"username": "testuser", "full_name": "x" * 151})
    assert response.status_code == 400


def test_edit_profile_control_character_in_full_name_rejected(client, auth_user):
    response = client.post("/edit-profile", json={
        "username": "testuser", "full_name": "Yusuf\u0000Admin",
    })
    assert response.status_code == 400
    assert _fresh_user(auth_user.id).full_name != "Yusuf\u0000Admin"


def test_edit_profile_legitimate_name_punctuation_allowed(client, auth_user):
    full_name = "O'Connor & Sons <TR>"
    response = client.post("/edit-profile", json={
        "username": "testuser", "full_name": full_name,
    })
    assert response.status_code == 200
    assert _fresh_user(auth_user.id).full_name == full_name


def test_edit_profile_invalid_goal_rejected(client, auth_user):
    response = client.post("/edit-profile",
                           json={"username": "testuser", "goal": "dunya fethi"})
    assert response.status_code == 400


def test_edit_profile_bad_picture_rejected_good_one_stored(client, auth_user):
    bad = client.post("/edit-profile", json={
        "username": "testuser", "profile_picture": "https://evil.example/x.png"})
    assert bad.status_code == 400

    from tests.test_validators import _image_data_url
    pic = _image_data_url("PNG")
    good = client.post("/edit-profile", json={"username": "testuser", "profile_picture": pic})
    assert good.status_code == 200
    assert _fresh_user(auth_user.id).profile_picture == pic


def test_edit_profile_empty_target_weight_clears(client, auth_user):
    auth_user.target_weight = 75.0
    db.session.commit()
    client.post("/edit-profile", json={"username": "testuser", "target_weight": ""})
    assert _fresh_user(auth_user.id).target_weight is None
