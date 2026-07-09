"""Barcode lookup (FatSecret food.find_id_for_barcode) — Phase 4.

Hermetik: FatSecret çağrıları monkeypatch'lenir, ağ'a çıkılmaz.
"""
import app.services.fatsecret as fs
from app.services import barcode as barcode_svc


class FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_normalize_and_find(monkeypatch):
    # find_id_for_barcode bir food_id döndürür; food.get ad/marka verir.
    calls = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        method = params.get("method")
        calls[method] = params
        if method == "food.find_id_for_barcode":
            return FakeResp({"food_id": {"value": "77777"}})
        if method.startswith("food.get"):
            return FakeResp({"food": {"food_id": "77777",
                                      "food_name": "Protein Bar",
                                      "brand_name": "Acme"}})
        return FakeResp({})

    monkeypatch.setattr(fs, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fs, "_fs_get", fake_get)
    monkeypatch.setattr(fs, "_food_get_servings",
                        lambda fid: [{"serving_description": "1 bar"}])

    out = fs._food_find_by_barcode("5000159407236")
    assert out["food_id"] == "77777"
    assert out["name"] == "Protein Bar"
    assert out["brand"] == "Acme"
    assert out["servings"] == [{"serving_description": "1 bar"}]
    # 13 haneli GTIN olduğu gibi geçer
    assert calls["food.find_id_for_barcode"]["barcode"] == "5000159407236"


def test_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(fs, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fs, "_fs_get",
                        lambda *a, **k: FakeResp({"food_id": {"value": "0"}}))
    assert fs._food_find_by_barcode("12345678") is None


def test_short_code_left_padded_to_13(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["barcode"] = params.get("barcode")
        return FakeResp({"food_id": {"value": "0"}})

    monkeypatch.setattr(fs, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fs, "_fs_get", fake_get)
    fs._food_find_by_barcode("40822938")   # UPC-ish, <13 hane
    assert seen["barcode"] == "0000040822938"


def test_non_digits_stripped_and_empty_is_none(monkeypatch):
    # Rakam içermeyen girdi → None (ağ'a hiç çıkmadan)
    monkeypatch.setattr(fs, "_get_fatsecret_token",
                        lambda: (_ for _ in ()).throw(AssertionError("no net")))
    assert fs._food_find_by_barcode("abc-!!") is None
    assert fs._food_find_by_barcode("") is None


# ---------------------------------------------------------------------------
# Route: GET /api/food/barcode
# ---------------------------------------------------------------------------

def test_route_invalid_barcode_400(client, auth_user):
    r = client.get("/api/food/barcode?code=")
    assert r.status_code == 400


def test_route_not_found_404(client, auth_user, monkeypatch):
    monkeypatch.setattr(barcode_svc, "get_barcode_product", lambda c: None)
    r = client.get("/api/food/barcode?code=5000159407236")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_route_success_200(client, auth_user, monkeypatch):
    payload = {"food_id": "77777", "name": "Protein Bar", "brand": "Acme",
               "servings": [{"serving_description": "1 bar"}]}
    monkeypatch.setattr(barcode_svc, "_food_find_by_barcode", lambda c: payload)
    r = client.get("/api/food/barcode?code=5000159407236")
    assert r.status_code == 200
    data = r.get_json()
    assert data["food_id"] == payload["food_id"]
    assert data["name"] == payload["name"]
    assert data["brand"] == payload["brand"]
    assert data["servings"][0]["serving_description"] == "1 bar"
    assert data["food"]["name"] == payload["name"]
    assert "analysis" in data


def test_route_requires_login(raw_client):
    # Girişsiz istek login'e yönlenir (302) veya 401 — 200 OLMAMALI.
    r = raw_client.get("/api/food/barcode?code=5000159407236")
    assert r.status_code != 200
