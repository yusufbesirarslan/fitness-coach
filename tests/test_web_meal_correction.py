"""Sprint 13 PR4 — the web correction primitive and the stored-photo lifecycle.

Three findings meet here.

* **F1** — the web could create a canonical ``MealLog`` through six paths and
  correct none of them. Sprint 13 chose exactly one correction primitive:
  a **current-day hard delete** (C4, C5). Not a slot move, not a macro edit.
* **F14** — deleting a ledger row released no stored meal photo, and
  ``s3_helper`` owned no deletion primitive at all, so no caller could have
  closed the lifecycle even if it wanted to.
* **N9** — every first-party client that can write a current-day entry must
  have a truthful correction path for it.

The tests below are the acceptance evidence for all three. They deliberately
mock at the **boto3 client boundary**, never at ``s3_helper.delete_meal_photo``:
the claim being proved is that the correction domain asks for one exact
bucket/key, and a mock of the helper itself could not prove that.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import s3_helper
from app.extensions import db
from app.models import MealLog
from app.services import mobile_diary_mutation
from app.services.mobile_diary_mutation import service as mutation_service


REPO_ROOT = Path(__file__).resolve().parent.parent
NUTRITION_JS = (REPO_ROOT / "static" / "nutrition.js").read_text(encoding="utf-8")

BUCKET = "axisai-test-bucket"
TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# S3 boundary — the real network edge, faked
# ---------------------------------------------------------------------------

class FakeS3Client:
    """Stands in for the boto3 S3 client — the actual network boundary."""

    def __init__(self):
        self.deleted = []
        self.delete_error = None

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        if self.delete_error is not None:
            raise self.delete_error
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    def generate_presigned_url(self, *args, **kwargs):
        return "https://example.invalid/signed"

    def put_object(self, **kwargs):  # pragma: no cover - not exercised here
        return {}


@pytest.fixture
def s3(monkeypatch):
    client = FakeS3Client()
    monkeypatch.setattr(s3_helper, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(s3_helper, "_client", client)
    return client


def _boto_failure():
    """A transport error of the family ``s3_helper`` is written to catch."""
    return s3_helper.BotoCoreError()


def photo_key(user_id, when=None):
    """A key in exactly the grammar ``s3_helper._build_key`` mints."""
    when = when or datetime(2026, 9, 2)
    return f"meals/{user_id}/{when:%Y/%m}/{'a1' * 16}.jpg"


# ---------------------------------------------------------------------------
# Ledger fixtures
# ---------------------------------------------------------------------------

def make_entry(user_id, **fields):
    fields.setdefault("ogun", "Kahvaltı")
    fields.setdefault("yemekler", "Yanlış öğün")
    fields.setdefault("kalori", 900.0)
    fields.setdefault("protein", 10.0)
    fields.setdefault("karb", 100.0)
    fields.setdefault("yag", 40.0)
    fields.setdefault("tarih", TODAY)
    fields.setdefault("source", "manual")
    entry = MealLog(user_id=user_id, **fields)
    db.session.add(entry)
    db.session.commit()
    return entry


def today_payload(client):
    response = client.get("/meal-log/today")
    assert response.status_code == 200
    return response.get_json()


def identity_of(client, description):
    """Read the correction identity the browser itself would use."""
    payload = today_payload(client)
    row = next(m for m in payload["meals"] if m["yemekler"] == description)
    return row["entry_token"], row["revision"]


def web_delete(client, token, revision, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if revision is not None:
        headers["If-Match"] = f'"{revision}"'
    return client.delete(f"/meal-log/entry/{token}", headers=headers, **kwargs)


def _identity(app, user_id, entry):
    """Identity minted outside the web read — for rows the read never shows."""
    from app.services.mobile_nutrition.identity import diary_entry_id
    from app.services.mobile_nutrition.revision import diary_entry_revision

    secret = app.config["SECRET_KEY"]
    return (diary_entry_id(secret, user_id, entry.id),
            diary_entry_revision(secret, mutation_service._state(entry)))


# ===========================================================================
# The web read payload — §18: opaque identity, nothing more
# ===========================================================================

def test_the_current_day_web_read_publishes_opaque_identity_and_no_raw_state(
        app, client, auth_user, s3):
    """F1/N9: the browser cannot issue ``DELETE + If-Match`` without these."""
    make_entry(auth_user.id, yemekler="Tavuk", photo_key=photo_key(auth_user.id))

    row = today_payload(client)["meals"][0]

    assert isinstance(row["entry_token"], str) and row["entry_token"]
    assert isinstance(row["revision"], str) and row["revision"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", row["entry_token"])
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", row["revision"])
    # The browser gets identity, never internal state.
    assert "id" not in row and "user_id" not in row and "photo_key" not in row


def test_the_web_identity_is_the_one_canonical_projection(app, client, auth_user):
    """§3/§18: no second entry-token or revision algorithm ships for the web."""
    from app.services.mobile_nutrition.identity import matches_diary_entry_id
    from app.services.mobile_nutrition.revision import (
        matches_diary_entry_revision,
    )

    entry = make_entry(auth_user.id, yemekler="Yulaf")
    token, revision = identity_of(client, "Yulaf")

    secret = app.config["SECRET_KEY"]
    assert matches_diary_entry_id(secret, auth_user.id, entry.id, token)
    assert matches_diary_entry_revision(
        secret, mutation_service._state(entry), revision)


def test_the_history_surface_publishes_no_correction_identity(
        app, client, auth_user):
    """§6: N9 is current-day. Past days must not look correctable."""
    make_entry(auth_user.id, yemekler="Dün", tarih=YESTERDAY)

    days = client.get("/meal-log/history").get_json()
    rows = [meal for day in days for meal in day["meals"]]

    assert rows, "history returned nothing; the assertion below would be vacuous"
    assert not any("entry_token" in row or "revision" in row for row in rows), (
        "The history surface published a correction identity. N9 is scoped to "
        "current-day entries; publishing tokens there invites a historical "
        "ledger-management API this sprint did not authorise.")


# ===========================================================================
# §26 — the web happy path
# ===========================================================================

def test_web_delete_removes_the_row_and_the_canonical_totals_follow(
        app, client, auth_user):
    keep = make_entry(auth_user.id, yemekler="Kalsın", kalori=300.0, protein=20.0,
                      karb=30.0, yag=5.0, ogun="Öğle")
    target = make_entry(auth_user.id, yemekler="Silinsin", kalori=900.0,
                        protein=10.0, karb=100.0, yag=40.0)
    target_id, keep_id = target.id, keep.id
    token, revision = identity_of(client, "Silinsin")

    response = web_delete(client, token, revision)

    assert response.status_code == 204
    assert response.get_data() == b""
    assert db.session.get(MealLog, target_id) is None
    assert db.session.get(MealLog, keep_id) is not None

    after = today_payload(client)
    assert [m["yemekler"] for m in after["meals"]] == ["Kalsın"]
    assert after["totals"] == {
        "kalori": 300.0, "protein": 20.0, "karb": 30.0, "yag": 5.0}


def test_repeated_web_delete_converges_on_not_found(app, client, auth_user):
    """§33: a retry after a successful delete is safe and deterministic."""
    make_entry(auth_user.id, yemekler="Tekrar")
    token, revision = identity_of(client, "Tekrar")

    assert web_delete(client, token, revision).status_code == 204
    assert web_delete(client, token, revision).status_code == 404
    assert MealLog.query.count() == 0


# ===========================================================================
# §7 — precondition transport
# ===========================================================================

def test_web_delete_without_if_match_is_refused(app, client, auth_user):
    entry = make_entry(auth_user.id, yemekler="Korunmalı")
    entry_id = entry.id
    token, _ = identity_of(client, "Korunmalı")

    response = web_delete(client, token, None)

    assert response.status_code == 428
    assert db.session.get(MealLog, entry_id) is not None


@pytest.mark.parametrize("value", ["", "not-quoted", '"short"', '"' + "x" * 200 + '"'])
def test_web_delete_rejects_a_malformed_precondition(
        app, client, auth_user, value):
    entry = make_entry(auth_user.id, yemekler="Bozuk")
    entry_id = entry.id
    token, _ = identity_of(client, "Bozuk")

    response = client.delete(
        f"/meal-log/entry/{token}", headers={"If-Match": value})

    assert response.status_code == 400
    assert db.session.get(MealLog, entry_id) is not None


# ===========================================================================
# §28 — stale revision
# ===========================================================================

def test_a_stale_revision_deletes_nothing_and_releases_nothing(
        app, client, auth_user, s3):
    entry = make_entry(auth_user.id, yemekler="Eski",
                       photo_key=photo_key(auth_user.id))
    entry_id = entry.id
    token, stale = identity_of(client, "Eski")

    entry.kalori = 123.0            # the row moves on; the revision does not
    db.session.commit()

    response = web_delete(client, token, stale)

    assert response.status_code == 412
    assert db.session.get(MealLog, entry_id) is not None
    assert s3.deleted == [], "a refused correction must not touch the object"


# ===========================================================================
# §29 / §15 — ownership
# ===========================================================================

def test_another_users_entry_is_indistinguishable_from_an_absent_one(
        app, client, auth_user, make_user, s3):
    """No cross-user deletion, and no cross-user existence oracle."""
    from app.services.mobile_nutrition.identity import diary_entry_id

    other = make_user("kurban", email="kurban@example.com")
    victim = make_entry(other.id, yemekler="Başkasının")
    victim_id = victim.id
    victim_token, victim_revision = _identity(app, other.id, victim)
    secret = app.config["SECRET_KEY"]

    owned = web_delete(client, victim_token, victim_revision)
    absent = web_delete(client, diary_entry_id(secret, other.id, 999999),
                        victim_revision)

    assert owned.status_code == 404
    assert absent.status_code == 404
    assert owned.get_json() == absent.get_json(), (
        "The response distinguished another user's entry from a non-existent "
        "one. That is a cross-user existence oracle.")
    assert db.session.get(MealLog, victim_id) is not None
    assert s3.deleted == []


def test_a_tampered_token_is_not_found(app, client, auth_user):
    entry = make_entry(auth_user.id, yemekler="Kurcalanmış")
    entry_id = entry.id
    token, revision = identity_of(client, "Kurcalanmış")
    tampered = ("B" if token[0] != "B" else "C") + token[1:]

    assert web_delete(client, tampered, revision).status_code == 404
    assert db.session.get(MealLog, entry_id) is not None


# ===========================================================================
# §30 — the day boundary is the server's
# ===========================================================================

def test_a_historical_entry_is_not_deletable_through_the_web_path(
        app, client, auth_user, s3):
    entry = make_entry(auth_user.id, yemekler="Dünkü", tarih=YESTERDAY,
                       photo_key=photo_key(auth_user.id))
    entry_id = entry.id
    token, revision = _identity(app, auth_user.id, entry)

    response = web_delete(client, token, revision)

    assert response.status_code == 404
    assert db.session.get(MealLog, entry_id) is not None
    assert s3.deleted == []


def test_the_web_delete_takes_its_day_from_the_server_not_the_request(
        app, client, auth_user):
    """§6: no client date, query-string date or browser timezone gets a vote."""
    entry = make_entry(auth_user.id, yemekler="Dünkü", tarih=YESTERDAY)
    entry_id = entry.id
    token, revision = _identity(app, auth_user.id, entry)

    response = client.delete(
        f"/meal-log/entry/{token}?date={YESTERDAY}&tarih={YESTERDAY}",
        headers={"If-Match": f'"{revision}"'})

    assert response.status_code == 404
    assert db.session.get(MealLog, entry_id) is not None


# ===========================================================================
# §37 / §38 — auth, CSRF, and the shape of the route
# ===========================================================================

def test_the_web_delete_requires_an_authenticated_session(app, client):
    response = client.delete(
        "/meal-log/entry/anything", headers={"If-Match": '"' + "a" * 24 + '"'})
    assert response.status_code in (302, 401)


def test_the_web_delete_is_covered_by_the_established_csrf_mechanism(
        app, raw_client, auth_user):
    """§38: the global browser-mutation guard applies; nothing was weakened."""
    entry = make_entry(auth_user.id, yemekler="CSRF")
    entry_id = entry.id
    token, revision = _identity(app, auth_user.id, entry)

    response = raw_client.delete(
        f"/meal-log/entry/{token}", headers={"If-Match": f'"{revision}"'})

    assert response.status_code == 403
    assert db.session.get(MealLog, entry_id) is not None


def test_the_web_correction_route_is_the_only_ledger_mutation_added(app):
    """§8: delete is the primitive. No slot move, no edit, no date move."""
    ledger_rules = {
        (rule.rule, frozenset(rule.methods) - {"HEAD", "OPTIONS"})
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("nutrition.")
        and rule.methods & {"PATCH", "PUT", "DELETE"}
    }
    assert ledger_rules == {
        ("/api/diary/item/<int:item_id>", frozenset({"PATCH"})),
        ("/api/diary/item/<int:item_id>", frozenset({"DELETE"})),
        ("/meal-log/entry/<entry_token>", frozenset({"DELETE"})),
    }


def test_no_web_route_accepts_a_storage_key(app):
    """§11: object identity is derived server-side, never supplied."""
    for rule in app.url_map.iter_rules():
        assert "key" not in rule.arguments, rule


# ===========================================================================
# §27 / §24 — the photo lifecycle, on every canonical deletion transport
# ===========================================================================

def test_web_delete_releases_the_exact_owned_object(app, client, auth_user, s3):
    key = photo_key(auth_user.id)
    entry = make_entry(auth_user.id, yemekler="Fotoğraflı", photo_key=key)
    entry_id = entry.id
    token, revision = identity_of(client, "Fotoğraflı")

    response = web_delete(client, token, revision)

    assert response.status_code == 204
    assert db.session.get(MealLog, entry_id) is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}], (
        "The correction domain must ask for exactly one server-derived "
        "bucket/key — no prefix delete, no user-folder delete.")


def test_mobile_delete_releases_the_same_owned_object(app, auth_user, s3):
    """§24: F14 is closed repository-wide or it is not closed at all.

    Web-only cleanup while the shared canonical deletion authority still
    orphans the object would leave F14 open.
    """
    key = photo_key(auth_user.id)
    entry = make_entry(auth_user.id, yemekler="Mobil", photo_key=key)
    entry_id = entry.id
    token, revision = _identity(app, auth_user.id, entry)

    mobile_diary_mutation.delete_entry(
        auth_user.id, TODAY, token, revision, app.config["SECRET_KEY"])

    assert db.session.get(MealLog, entry_id) is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}]


def test_deleting_a_photo_less_entry_makes_no_object_call(
        app, client, auth_user, s3):
    """§13/§25: the lifecycle is conditional on the row owning an object."""
    make_entry(auth_user.id, yemekler="Fotoğrafsız", source="provider")
    token, revision = identity_of(client, "Fotoğrafsız")

    assert web_delete(client, token, revision).status_code == 204
    assert s3.deleted == []


# ===========================================================================
# §14 — legacy and malformed stored references
# ===========================================================================

@pytest.mark.parametrize("stored", [
    "https://other-bucket.s3.amazonaws.com/meals/1/2026/09/deadbeef.jpg",
    "../../etc/passwd",
    "pump/1/2026/09/" + "a1" * 16 + ".jpg",
    "meals/2026/09/" + "a1" * 16 + ".jpg",
    "meals/1/2026/09/" + "a1" * 16 + ".jpg?versionId=1",
    "meals/",
])
def test_an_unrecognised_stored_reference_fails_closed(
        app, client, auth_user, s3, stored):
    """§14: permission to delete an object is never derived from row text.

    Chosen semantics: **fail closed**. A row whose stored reference is not one
    this application minted cannot have its lifecycle closed, so the ledger
    deletion is refused outright rather than either orphaning the object
    silently or guessing at a key. The row survives, the object survives, and
    the operator sees a deterministic conflict instead of a silent leak.
    """
    entry = make_entry(auth_user.id, yemekler="Eski kayıt", photo_key=stored)
    entry_id = entry.id
    token, revision = identity_of(client, "Eski kayıt")

    response = web_delete(client, token, revision)

    assert response.status_code == 409
    assert db.session.get(MealLog, entry_id) is not None
    assert s3.deleted == [], (
        "A malformed reference reached the object store. Database text must "
        "never become permission to delete an arbitrary object.")


def test_a_foreign_owner_key_cannot_be_deleted_through_the_ledger(
        app, client, auth_user, make_user, s3):
    other = make_user("komsu", email="komsu@example.com")
    entry = make_entry(auth_user.id, yemekler="Komşunun anahtarı",
                       photo_key=photo_key(other.id))
    entry_id = entry.id
    token, revision = identity_of(client, "Komşunun anahtarı")

    response = web_delete(client, token, revision)

    assert response.status_code == 409
    assert db.session.get(MealLog, entry_id) is not None
    assert s3.deleted == []


# ===========================================================================
# §11 / §36 — the bounded stored-object primitive itself
# ===========================================================================

def test_the_deletion_primitive_is_bounded_to_a_server_controlled_bucket(s3):
    import inspect

    signature = inspect.signature(s3_helper.delete_meal_photo)
    assert "bucket" not in signature.parameters, (
        "The caller must not choose the bucket; that is how a bounded "
        "primitive becomes a generic 'delete any S3 object' API.")

    s3_helper.delete_meal_photo(photo_key(7), 7)
    assert s3.deleted == [{"Bucket": BUCKET, "Key": photo_key(7)}]


def test_the_deletion_primitive_refuses_anything_it_did_not_mint(s3):
    for key in ("meals/7/2026/09/x.jpg", "https://x/y",
                "meals/8/2026/09/" + "a1" * 16 + ".jpg", None, ""):
        with pytest.raises(s3_helper.UnsafeObjectKey):
            s3_helper.delete_meal_photo(key, 7)
    assert s3.deleted == []


def test_the_deletion_primitive_is_idempotent_for_an_absent_object(s3):
    """§33: S3 DeleteObject succeeds for a key that is already gone."""
    key = photo_key(7)
    s3_helper.delete_meal_photo(key, 7)
    s3_helper.delete_meal_photo(key, 7)
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}] * 2


# ===========================================================================
# §12 / §31 / §32 — the bounded failure model
# ===========================================================================

def test_the_row_is_committed_deleted_before_the_object_is_released(
        app, client, auth_user, s3, monkeypatch):
    """§12: the ordering decision, asserted rather than described.

    The database delete commits **first**. That is what guarantees a released
    object can never have a surviving row pointing at it — the state Sprint 13's
    rollback plan says must never exist.
    """
    key = photo_key(auth_user.id)
    entry = make_entry(auth_user.id, yemekler="Sıra", photo_key=key)
    entry_id = entry.id
    token, revision = identity_of(client, "Sıra")

    observed = {}
    original = FakeS3Client.delete_object

    def observing_delete(self, **kwargs):
        observed["row_gone_at_call_time"] = (
            MealLog.query.filter_by(id=entry_id).count() == 0)
        return original(self, **kwargs)

    monkeypatch.setattr(FakeS3Client, "delete_object", observing_delete)

    assert web_delete(client, token, revision).status_code == 204
    assert observed["row_gone_at_call_time"] is True, (
        "The object was released while the row still existed. A crash there "
        "leaves a surviving row pointing at a deleted object.")


def test_object_release_failure_is_never_reported_as_success(
        app, client, auth_user, s3):
    """§31: the durable outcome, stated exactly.

    Durable state after this request: **row deleted, object retained**. The
    ledger correction the user asked for is done and irreversible; the object
    is a *known* orphan, logged at error level. The response is therefore an
    explicit failure — a 204 here would be a false success hiding a leak.
    A retry converges (404) and never touches an unrelated object.
    """
    key = photo_key(auth_user.id)
    entry = make_entry(auth_user.id, yemekler="Sızıntı", photo_key=key)
    entry_id = entry.id
    token, revision = identity_of(client, "Sızıntı")
    s3.delete_error = _boto_failure()

    response = web_delete(client, token, revision)

    assert response.status_code == 500
    assert response.get_json()["error"]
    assert db.session.get(MealLog, entry_id) is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}]

    s3.delete_error = None
    retry = web_delete(client, token, revision)
    assert retry.status_code == 404
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}], (
        "The retry touched the object store again after the row was gone.")


def test_object_release_failure_is_logged_so_the_leak_is_visible(
        app, client, auth_user, s3, caplog):
    key = photo_key(auth_user.id)
    make_entry(auth_user.id, yemekler="Görünür", photo_key=key)
    token, revision = identity_of(client, "Görünür")
    s3.delete_error = _boto_failure()

    with caplog.at_level("ERROR"):
        web_delete(client, token, revision)

    assert any("meal_photo_orphaned" in record.getMessage()
               for record in caplog.records), (
        "An orphan must be visible rather than silent — that is the whole "
        "difference between this and F14.")


def test_a_persistence_failure_leaves_the_object_untouched(
        app, auth_user, s3, monkeypatch):
    """§32: the converse direction of the ordering decision.

    Because the row commits first, a persistence failure happens *before* any
    object call. Nothing is released, nothing is deleted, and the request is
    safely retryable — there is no destructive inconsistency to examine.
    """
    key = photo_key(auth_user.id)
    entry = make_entry(auth_user.id, yemekler="Kalıcılık", photo_key=key)
    entry_id = entry.id
    token, revision = _identity(app, auth_user.id, entry)

    class FailingSession:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def commit(self):
            raise RuntimeError("persistence failure")

    class FailingDb:
        def __init__(self, real):
            self.session = FailingSession(real.session)

    monkeypatch.setattr(mutation_service, "db", FailingDb(db))

    with pytest.raises(RuntimeError):
        mobile_diary_mutation.delete_entry(
            auth_user.id, TODAY, token, revision, app.config["SECRET_KEY"])

    monkeypatch.undo()
    db.session.rollback()
    assert db.session.get(MealLog, entry_id) is not None
    assert s3.deleted == [], (
        "The object was released for a row that still exists.")


# ===========================================================================
# §3 / §23 — one mutation authority, one set of semantics
# ===========================================================================

def test_the_web_transport_reuses_the_canonical_mutation_authority():
    """§3: no second delete implementation, revision or entry-token algorithm."""
    import ast

    source = (REPO_ROOT / "app" / "blueprints" / "nutrition" / "meallog.py"
              ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    route = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and "delete" in node.name and "meal" in node.name)
    called = {node.func.attr for node in ast.walk(route)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}

    assert "delete_entry" in called, (
        "The web route must call the shared mutation authority.")
    assert "delete_object" not in called and "delete_meal_photo" not in called, (
        "The web transport reached past the domain into the object store. "
        "Web and mobile may have separate transports; they must not have "
        "separate ledger-mutation semantics.")
    assert not (REPO_ROOT / "app" / "services" / "web_delete_meal_service.py").exists()


def test_the_mobile_delete_contract_is_unchanged(app):
    """§23: shared-domain work did not move the mobile transport."""
    rules = {
        (rule.rule, frozenset(rule.methods) - {"HEAD", "OPTIONS"})
        for rule in app.url_map.iter_rules()
        if "nutrition/logs" in rule.rule
    }
    assert ("/api/v1/nutrition/logs/<entry_token>",
            frozenset({"DELETE"})) in rules
    assert ("/api/v1/nutrition/logs/<entry_token>",
            frozenset({"PATCH"})) in rules


# ===========================================================================
# §19 / §20 — the browser
# ===========================================================================

def _js_function_body(name):
    """Return the balanced body of a named function in nutrition.js."""
    start = NUTRITION_JS.index(f"function {name}(")
    open_brace = NUTRITION_JS.index("{", start)
    depth, index, quote = 0, open_brace, None
    while index < len(NUTRITION_JS):
        char = NUTRITION_JS[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return NUTRITION_JS[open_brace:index + 1]
        index += 1
    raise AssertionError(f"unbalanced body for {name}")


def test_the_meal_card_carries_a_delete_action_bound_to_opaque_identity():
    body = _js_function_body("mealCardHTML")
    assert 'data-action=\\"deleteMeal\\"' in body or "deleteMeal" in body
    assert "entry_token" in body and "revision" in body
    assert "m.id" not in body


def test_the_browser_confirms_and_says_what_deletion_costs():
    body = _js_function_body("deleteMeal")
    assert "confirm(" in body, "a destructive action needs a confirmation"
    assert "nutrition.delete_meal_confirm" in body
    assert "nutrition.delete_meal_photo_note" in body, (
        "the confirmation must name the stored photo where one exists")


def test_the_browser_delete_re_reads_canonical_state_and_subtracts_nothing():
    """§19/§20: the server is the only authority on totals after a mutation."""
    body = _js_function_body("deleteMeal")
    assert "loadTodayData()" in body, (
        "After any delete outcome — success, 404, 412 or a network fault — "
        "the browser must re-read canonical state.")
    for local_authority in ("updateRing(", "updateMacroBars(", "targetCalories",
                            "-=", "splice(", "totals"):
        assert local_authority not in body, (
            f"deleteMeal manipulates {local_authority!r} locally. Browser "
            "arithmetic must never be the final authority on canonical totals.")


def test_the_browser_delete_is_protected_against_a_double_submit():
    body = _js_function_body("deleteMeal")
    assert "_mealDeleteInFlight" in body
    assert "disabled" in body


def test_the_browser_never_optimistically_removes_before_the_server_agrees():
    body = _js_function_body("deleteMeal")
    fetch_index = body.index("fetch(")
    assert "remove()" not in body[:fetch_index]
    assert "innerHTML" not in body[:fetch_index]


def test_no_user_facing_copy_promises_that_deletion_is_reversible():
    """§9: the architecture withdrew 're-logging is exact'."""
    import json

    for locale in ("tr", "en"):
        catalog = json.loads(
            (REPO_ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
        copy = " ".join(
            value for key, value in catalog.items()
            if key.startswith("nutrition.delete_meal"))
        assert copy, f"{locale}: correction copy is missing"
        for promise in ("geri al", "undo", "restore", "geri yükle"):
            assert promise not in copy.lower(), (
                f"{locale}: correction copy implies a durable undo that does "
                "not exist. Deletion is lossy (C4).")
