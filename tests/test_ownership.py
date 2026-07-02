"""IDOR/ownership regression tests.

CLAUDE.md kuralı: "Sorgular daima current_user.id'ye scope'lanır; ID ile
yüklenen kayıtlarda sahiplik kontrolü zorunlu." Bu testler, ID ile kayıt
yükleyen her endpoint'in başka kullanıcının kaydına 403/404 döndürdüğünü ve
kaydı DEĞİŞTİRMEDİĞİNİ sabitler — bir refactor'da sahiplik kontrolü düşerse
burada patlar.

    python -m pytest tests/test_ownership.py -v
"""
import pytest

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, Friendship, Message, Supplement


@pytest.fixture
def owner(make_user):
    return make_user("owner")


@pytest.fixture
def attacker_client(client, make_user, login):
    """Kurban kayıtlarının sahibi OLMAYAN, oturum açmış kullanıcı."""
    make_user("mallory")
    login("mallory")
    return client


def _make_supplement(owner):
    supp = Supplement(user_id=owner.id, product_name="Whey", brand="ON")
    db.session.add(supp)
    db.session.commit()
    return supp


def _make_custom_meal(owner, with_item=True):
    meal = CustomMeal(user_id=owner.id, meal_name="kahvalti", date_key="2026-06-12")
    if with_item:
        meal.items.append(CustomMealItem(food_name="yulaf", grams=100, calories=380))
    db.session.add(meal)
    db.session.commit()
    return meal


# ---------------------------------------------------------------------------
# Supplement — edit/delete yalnızca sahibine açık.
# ---------------------------------------------------------------------------

def test_supplement_edit_foreign_record_forbidden(owner, attacker_client):
    supp = _make_supplement(owner)
    response = attacker_client.post(f"/supplement/edit/{supp.id}",
                                    json={"product_name": "HACKED"})
    assert response.status_code == 404
    db.session.expire_all()
    assert db.session.get(Supplement, supp.id).product_name == "Whey"


def test_supplement_delete_foreign_record_forbidden(owner, attacker_client):
    supp = _make_supplement(owner)
    response = attacker_client.post(f"/supplement/delete/{supp.id}")
    assert response.status_code == 404
    db.session.expire_all()
    assert db.session.get(Supplement, supp.id) is not None


def test_supplement_owner_can_edit_own_record(client, owner, login):
    # Pozitif kontrol: 403'lerin sahiplikten geldiğini doğrular.
    supp = _make_supplement(owner)
    login("owner")
    response = client.post(f"/supplement/edit/{supp.id}", json={"product_name": "Casein"})
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(Supplement, supp.id).product_name == "Casein"


def test_supplement_unknown_id_404(attacker_client):
    assert attacker_client.post("/supplement/edit/999999", json={}).status_code == 404


# ---------------------------------------------------------------------------
# Arkadaşlık istekleri — yalnızca alıcı kabul/ret edebilir.
# ---------------------------------------------------------------------------

def test_friend_request_accept_by_third_party_forbidden(owner, make_user, attacker_client):
    other = make_user("recipient")
    fr = Friendship(sender_id=owner.id, receiver_id=other.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    assert attacker_client.post(f"/friend/accept/{fr.id}").status_code == 404
    assert attacker_client.post(f"/friend/reject/{fr.id}").status_code == 404
    db.session.expire_all()
    assert db.session.get(Friendship, fr.id).status == "pending"


# ---------------------------------------------------------------------------
# Sohbet — arkadaş olmayan kullanıcının mesajları okunamaz/yazılamaz.
# ---------------------------------------------------------------------------

def test_chat_with_non_friend_forbidden(owner, attacker_client):
    assert attacker_client.get("/chat/owner/messages").status_code == 403
    response = attacker_client.post("/chat/owner/send", json={"body": "selam"})
    assert response.status_code == 403
    assert Message.query.count() == 0


def test_suggestion_respond_by_non_recipient_forbidden(owner, make_user, attacker_client):
    other = make_user("recipient")
    msg = Message(sender_id=owner.id, receiver_id=other.id,
                  body="öneri", message_type="suggestion_meal")
    db.session.add(msg)
    db.session.commit()

    response = attacker_client.post(f"/suggest/respond/{msg.id}", json={"accept": True})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Beslenme günlüğü — başka kullanıcının öğünü/besini görünmez (404) ve değişmez.
# ---------------------------------------------------------------------------

def test_diary_add_item_to_foreign_meal_not_found(owner, attacker_client):
    meal = _make_custom_meal(owner, with_item=False)
    response = attacker_client.post(f"/api/diary/meal/{meal.id}/item",
                                    json={"food_name": "zehir", "grams": 100})
    assert response.status_code == 404
    db.session.expire_all()
    assert db.session.get(CustomMeal, meal.id).items == []


def test_diary_update_foreign_item_not_found(owner, attacker_client):
    meal = _make_custom_meal(owner)
    item_id = meal.items[0].id
    response = attacker_client.patch(f"/api/diary/item/{item_id}", json={"grams": 1})
    assert response.status_code == 404
    db.session.expire_all()
    assert db.session.get(CustomMealItem, item_id).grams == 100


def test_diary_delete_foreign_item_not_found(owner, attacker_client):
    meal = _make_custom_meal(owner)
    item_id = meal.items[0].id
    assert attacker_client.delete(f"/api/diary/item/{item_id}").status_code == 404
    db.session.expire_all()
    assert db.session.get(CustomMealItem, item_id) is not None


def test_diary_log_foreign_meal_not_found(owner, attacker_client):
    meal = _make_custom_meal(owner)
    assert attacker_client.post(f"/api/diary/meal/{meal.id}/log").status_code == 404
    db.session.expire_all()
    assert db.session.get(CustomMeal, meal.id).is_logged is False


# ---------------------------------------------------------------------------
# AI Koç in-process araçları — user_id ASLA LLM'den gelmez; istek bağlamında
# current_user ile eşleşmeyen user_id reddedilir (S1, savunma derinliği).
# ---------------------------------------------------------------------------

def test_coach_principal_rejects_foreign_user_id(app, owner, make_user):
    from flask_login import login_user
    from app.services.ai_coach import _assert_principal

    mallory = make_user("mallory_coach")
    with app.test_request_context():
        login_user(owner)
        _assert_principal(owner.id)                  # kendi id'si → sorun yok
        with pytest.raises(PermissionError):
            _assert_principal(mallory.id)            # başka kullanıcı → reddedilir


def test_coach_principal_noop_without_request_context(owner):
    # İstek bağlamı yoksa (CLI / in-process / test) kontrol atlanır — mevcut
    # davranışı bozmasın diye.
    from app.services.ai_coach import _assert_principal
    _assert_principal(owner.id)
    _assert_principal(owner.id + 999)
