"""NUTR-PR7 — native Supplements over the ONE cabinet (``Supplement``).

    python -m pytest tests/test_nutrition_vnext_pr7_native_supplements.py -q

empty != unavailable · create under the cabinet precondition (duplicate tap /
lost response / second device → 412, never a second row) · update/delete under
the item revision · owner-bound opaque ids · private not-found · no second
cabinet authority.
"""
import pytest

from app.extensions import db
from app.models import Supplement, User
from app.services.nutrition_native import supplements as native_supp

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    StatementCounter, bearer, error_of, native, no_provider, quoted,
)

PATH = "/api/v1/nutrition/supplements"
NEW = {"product_name": "Whey Gold", "brand": "ON", "category": "protein",
       "is_public": False}


def listing(native, headers):
    response = native.get(PATH, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["cabinet"]


def create(native, headers, revision, body=NEW):
    hdrs = dict(headers)
    if revision is not None:
        hdrs["If-Match"] = quoted(revision)
    return native.post(PATH, headers=hdrs, json=body)


def rows(user_id):
    return Supplement.query.filter_by(user_id=user_id).order_by(Supplement.id).all()


def test_empty_cabinet_is_a_success(app, native, bearer, make_user, no_provider):
    user = make_user("supp-empty")
    cabinet = listing(native, bearer(user))
    assert cabinet["state"] == "empty" and cabinet["supplements"] == []
    assert cabinet["revision"] and cabinet["truncated"] is False


def test_unavailable_cabinet_is_not_empty(app, native, bearer, make_user, monkeypatch):
    user = make_user("supp-503")
    monkeypatch.setattr(native_supp, "read_cabinet",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("db")))
    response = native.get(PATH, headers=bearer(user))
    assert response.status_code == 503
    assert error_of(response)["code"] == "SUPPLEMENTS_UNAVAILABLE"


def test_create_returns_the_canonical_row_and_next_cabinet_revision(
        app, native, bearer, make_user, no_provider):
    user = make_user("supp-create")
    headers = bearer(user)
    revision = listing(native, headers)["revision"]
    response = create(native, headers, revision)
    assert response.status_code == 201, response.get_data(as_text=True)
    body = response.get_json()
    supp = body["supplement"]
    assert supp["product_name"] == "Whey Gold" and supp["category"] == "protein"
    assert supp["status"] == "active" and supp["is_public"] is False
    assert supp["ratings"] == {"effect": None, "taste": None, "digestion": None, "price": None}
    cabinet = listing(native, headers)
    assert cabinet["revision"] == body["cabinet_revision"]
    assert cabinet["supplements"] == [supp]
    assert rows(user.id)[0].category == "Protein"
    # Same side effects as the browser: first-supplement bonus once.
    assert (db.session.get(User, user.id).rank_points or 0) >= 25


def test_duplicate_tap_and_lost_response_never_create_twice(
        app, native, bearer, make_user, no_provider):
    user = make_user("supp-dup")
    headers = bearer(user)
    revision = listing(native, headers)["revision"]
    assert create(native, headers, revision).status_code == 201
    again = create(native, headers, revision)
    assert again.status_code == 412
    assert error_of(again)["code"] == "STALE_SUPPLEMENT_CABINET"
    assert error_of(again)["retryable"] is False
    assert len(rows(user.id)) == 1
    # Recovery: the fresh read shows the created item.
    assert [s["product_name"] for s in listing(native, headers)["supplements"]] == ["Whey Gold"]


def test_chained_creates_use_the_returned_revision(app, native, bearer, make_user, no_provider):
    user = make_user("supp-chain")
    headers = bearer(user)
    revision = listing(native, headers)["revision"]
    first = create(native, headers, revision).get_json()
    second = create(native, headers, first["cabinet_revision"],
                    dict(NEW, product_name="Creatine", category="creatine"))
    assert second.status_code == 201
    assert len(rows(user.id)) == 2


@pytest.mark.parametrize("body", [
    {}, {"product_name": "x"}, dict(NEW, category="Protein"), dict(NEW, status="Active"),
    dict(NEW, user_id=1), dict(NEW, id="x"), dict(NEW, is_public="yes"),
    dict(NEW, ratings={"effect": 6}), dict(NEW, ratings={"mood": 3}),
    dict(NEW, price_paid=-1), dict(NEW, price_paid="9"),
    dict(NEW, product_name="  "), dict(NEW, product_name="x" * 151),
    dict(NEW, brand="a\nb"), {k: v for k, v in NEW.items() if k != "is_public"},
])
def test_create_body_is_closed_and_validated(app, native, bearer, make_user, no_provider, body):
    user = make_user("supp-bad")
    headers = bearer(user)
    response = create(native, headers, listing(native, headers)["revision"], body)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_SUPPLEMENT_COMMAND"
    assert rows(user.id) == []


def test_create_requires_the_cabinet_precondition(app, native, bearer, make_user, no_provider):
    user = make_user("supp-428")
    response = create(native, bearer(user), None)
    assert response.status_code == 428
    assert rows(user.id) == []


def test_update_with_current_revision_then_stale(app, native, bearer, make_user, no_provider):
    user = make_user("supp-update")
    headers = bearer(user)
    created = create(native, headers, listing(native, headers)["revision"]).get_json()["supplement"]
    path = f"{PATH}/{created['id']}"
    updated = native.patch(path, headers=dict(headers, **{"If-Match": quoted(created["revision"])}),
                           json={"status": "low_stock", "ratings": {"taste": 4},
                                 "price_paid": 39.9})
    assert updated.status_code == 200
    body = updated.get_json()["supplement"]
    assert body["status"] == "low_stock" and body["ratings"]["taste"] == 4
    assert body["revision"] != created["revision"] and body["id"] == created["id"]
    stale = native.patch(path, headers=dict(headers, **{"If-Match": quoted(created["revision"])}),
                         json={"status": "finished"})
    assert stale.status_code == 412
    assert error_of(stale)["code"] == "STALE_SUPPLEMENT"
    assert rows(user.id)[0].status == "Low Stock"


def test_delete_with_current_revision_then_private_not_found(
        app, native, bearer, make_user, no_provider):
    user = make_user("supp-delete")
    headers = bearer(user)
    created = create(native, headers, listing(native, headers)["revision"]).get_json()["supplement"]
    path = f"{PATH}/{created['id']}"
    stale = native.delete(path, headers=dict(headers, **{"If-Match": quoted("A" * 24)}))
    assert stale.status_code == 412
    assert len(rows(user.id)) == 1
    gone = native.delete(path, headers=dict(headers, **{"If-Match": quoted(created["revision"])}))
    assert gone.status_code == 204 and gone.get_data() == b""
    retry = native.delete(path, headers=dict(headers, **{"If-Match": quoted(created["revision"])}))
    assert retry.status_code == 404
    assert error_of(retry)["code"] == "SUPPLEMENT_NOT_FOUND"
    assert rows(user.id) == []


def test_owner_isolation_and_no_existence_oracle(app, native, bearer, make_user, no_provider):
    alice = make_user("supp-alice")
    bob = make_user("supp-bob")
    a_headers, b_headers = bearer(alice), bearer(bob)
    a_supp = create(native, a_headers, listing(native, a_headers)["revision"]).get_json()["supplement"]
    assert listing(native, b_headers)["supplements"] == []
    responses = [
        native.patch(f"{PATH}/{a_supp['id']}",
                     headers=dict(b_headers, **{"If-Match": quoted(a_supp["revision"])}),
                     json={"status": "finished"}),
        native.delete(f"{PATH}/{a_supp['id']}",
                      headers=dict(b_headers, **{"If-Match": quoted(a_supp["revision"])})),
        native.delete(f"{PATH}/{'Z' * 24}",
                      headers=dict(b_headers, **{"If-Match": quoted(a_supp["revision"])})),
    ]
    for response in responses:
        assert response.status_code == 404
        assert error_of(response)["code"] == "SUPPLEMENT_NOT_FOUND"
    assert {r.get_json()["error"]["message"] for r in responses} == {"Supplement was not found."}
    assert rows(alice.id)[0].status == "Active"
    # Alice's cabinet revision never authorises a create in Bob's cabinet.
    assert create(native, b_headers, listing(native, a_headers)["revision"]).status_code == 412
    assert rows(bob.id) == []


def test_ids_are_opaque_owner_bound_tokens(app):
    a = native_supp.supplement_id("k", 1, 7)
    assert a != native_supp.supplement_id("k", 2, 7)
    assert "7" != a and len(a) == 24


def test_web_and_native_share_one_cabinet(app, client, native, bearer, make_user, login, no_provider):
    user = make_user("supp-parity")
    login("supp-parity")
    assert client.post("/supplement/add", json={"product_name": "Zinc", "brand": "X",
                                                "category": "Vitamin/Health"}).status_code == 200
    cabinet = listing(native, bearer(user))
    assert [(s["product_name"], s["category"]) for s in cabinet["supplements"]] == [
        ("Zinc", "vitamin_health")]
    assert Supplement.query.filter_by(user_id=user.id).count() == 1


def test_list_is_one_bounded_select(app, native, bearer, make_user, no_provider):
    user = make_user("supp-q")
    for n in range(5):
        db.session.add(Supplement(user_id=user.id, product_name=f"P{n}", brand="B"))
    db.session.commit()
    headers = bearer(user)
    with StatementCounter() as counter:
        assert len(listing(native, headers)["supplements"]) == 5
    assert len([s for s in counter.selects() if "supplement" in s]) == 1
