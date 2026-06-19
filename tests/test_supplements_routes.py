"""Route tests for the supplement blueprint (app/blueprints/supplements.py).

Sahiplik testleri edit/delete'i kapsıyor; burada ekleme akışı sabitlenir:
doğrulama, rating/kategori temizliği, ilk-ekleme XP'si ve quest entegrasyonu.

    python -m pytest tests/test_supplements_routes.py -v
"""
from app.extensions import db
from app.models import DailyQuest, Supplement, User


def _add(client, **overrides):
    payload = {"product_name": "Whey", "brand": "ON", **overrides}
    return client.post("/supplement/add", json=payload)


def test_add_requires_name_and_brand(client, auth_user):
    assert _add(client, product_name="  ").status_code == 400
    assert _add(client, brand="").status_code == 400


def test_add_sanitizes_category_status_and_ratings(client, auth_user):
    response = _add(client, category="Sihirli İksir", status="Kayıp",
                    rating_effect=7, rating_taste="çok iyi", rating_price=4,
                    price_paid="900.50", review_text="  fena değil  ")
    assert response.status_code == 200

    supp = db.session.get(Supplement, response.get_json()["id"])
    assert supp.category == "Other"          # bilinmeyen kategori → Other
    assert supp.status == "Active"           # bilinmeyen durum → Active
    assert supp.rating_effect is None        # 1-5 dışı → None
    assert supp.rating_taste is None         # sayı değil → None
    assert supp.rating_price == 4
    assert supp.price_paid == 900.5
    assert supp.review_text == "fena değil"


def test_first_supplement_awards_xp_and_quest(client, auth_user):
    db.session.add(DailyQuest(title="Update Your Stack", points_reward=25,
                              quest_type="supplement_added"))
    db.session.commit()

    body = _add(client).get_json()
    assert body["quest_awarded"]["xp"] == 25

    db.session.expire_all()
    user = db.session.get(User, auth_user.id)
    assert user.rank_points == 25 + 25       # ilk-ürün bonusu + quest

    # İkinci ürün: bonus ve quest tekrarlanmaz.
    _add(client, product_name="Kreatin")
    db.session.expire_all()
    assert db.session.get(User, auth_user.id).rank_points == 50
    assert Supplement.query.filter_by(user_id=auth_user.id).count() == 2


def test_supplements_page_lists_own_stack(client, auth_user, make_user):
    other = make_user("digeri")
    db.session.add(Supplement(user_id=other.id, product_name="GizliUrun", brand="X"))
    db.session.commit()
    _add(client, product_name="BenimUrunum")

    page = client.get("/supplements").get_data(as_text=True)
    assert "BenimUrunum" in page
    assert "GizliUrun" not in page           # başka kullanıcının stack'i sızmaz


# ---------------------------------------------------------------------------
# Düzenleme / silme — alan temizliği ve sahiplik kapısı.
# ---------------------------------------------------------------------------

def test_edit_updates_fields_and_sanitizes(client, auth_user):
    sid = _add(client).get_json()["id"]
    resp = client.post(f"/supplement/edit/{sid}", json={
        "product_name": "  Yeni İsim  ", "brand": "  ", "category": "Creatine",
        "status": "Bilinmeyen", "rating_effect": 5, "rating_taste": 9,
        "review_text": "  harika  ", "price_paid": "120.5", "is_public": True,
    })
    assert resp.status_code == 200

    db.session.expire_all()
    supp = db.session.get(Supplement, sid)
    assert supp.product_name == "Yeni İsim"   # trim'lendi
    assert supp.brand == "ON"                 # boş brand yok sayıldı (eski değer kalır)
    assert supp.category == "Creatine"
    assert supp.status != "Bilinmeyen"        # geçersiz durum yazılmadı
    assert supp.rating_effect == 5
    assert supp.rating_taste is None          # 1-5 dışı → None
    assert supp.review_text == "harika"
    assert supp.price_paid == 120.5
    assert supp.is_public is True


def test_edit_foreign_supplement_forbidden(client, auth_user, make_user):
    other = make_user("baskasi")
    supp = Supplement(user_id=other.id, product_name="Onunki", brand="X")
    db.session.add(supp)
    db.session.commit()
    resp = client.post(f"/supplement/edit/{supp.id}", json={"product_name": "Çaldım"})
    assert resp.status_code == 403
    db.session.expire_all()
    assert db.session.get(Supplement, supp.id).product_name == "Onunki"  # değişmedi


def test_delete_removes_own_supplement(client, auth_user):
    sid = _add(client).get_json()["id"]
    assert client.post(f"/supplement/delete/{sid}").status_code == 200
    assert db.session.get(Supplement, sid) is None


def test_delete_foreign_supplement_forbidden(client, auth_user, make_user):
    other = make_user("sahibi")
    supp = Supplement(user_id=other.id, product_name="Onunki", brand="X")
    db.session.add(supp)
    db.session.commit()
    assert client.post(f"/supplement/delete/{supp.id}").status_code == 403
    assert db.session.get(Supplement, supp.id) is not None  # silinmedi
