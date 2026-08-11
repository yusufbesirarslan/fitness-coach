"""Contract tests for the mobile nutrition read surface.

These pin the answers the Flutter client is allowed to depend on, and they are
written against the failure modes the mobile Sprint 9 PR1 audit found in the web
payloads: a day label with no year, entries with no identity, naive timestamps,
and unknown values arriving as measured zeroes. Every one of those has a test
here that fails if the semantic regresses.

    python -m pytest tests/test_mobile_nutrition_api.py -v
"""
import ast
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog, UserSession
from app.services import mobile_auth, mobile_nutrition
from app.timeutil import APP_TZ, audit_clock


DIARY_PATH = "/api/v1/nutrition/diary/today"

# One fixed Istanbul day for every test that cares about the calendar, so a run
# at 00:30 local time cannot make the suite disagree with itself.
FIXED_NOW = datetime(2026, 8, 9, 12, 0, tzinfo=APP_TZ)
FIXED_DAY = date(2026, 8, 9)


@pytest.fixture
def mobile_user(make_user):
    return make_user("nutrition-mobile")


@pytest.fixture
def as_mobile(monkeypatch):
    """Authenticate a request as one user through the real Bearer boundary.

    The credential store itself is exercised by tests/test_mobile_auth_api.py;
    what matters here is that these routes go through `require_mobile_auth` and
    resolve their user from it, so the principal is stubbed and the boundary is
    not.
    """
    def _headers(user):
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        return {"Authorization": "Bearer opaque-access-credential"}
    return _headers


def log_meal(user, ogun="Kahvaltı", yemekler="Yulaf ezmesi (50 g)",
             kalori=320.0, protein=12.0, karb=40.0, yag=9.0,
             source="manual", created_at=datetime(2026, 8, 9, 5, 24),
             day=FIXED_DAY):
    """Write one canonical ledger row. `created_at` is naive UTC, as stored."""
    entry = MealLog(
        user_id=user.id, ogun=ogun, yemekler=yemekler, kalori=kalori,
        protein=protein, karb=karb, yag=yag, tarih=day.isoformat(),
        source=source, created_at=created_at)
    db.session.add(entry)
    db.session.commit()
    return entry


def read_diary(client, headers):
    with audit_clock(FIXED_NOW):
        return client.get(DIARY_PATH, headers=headers)


# ---------------------------------------------------------------------------
# Authentication boundary
# ---------------------------------------------------------------------------

def test_missing_bearer_returns_mobile_json_and_never_an_html_redirect(
        raw_client):
    response = raw_client.get(DIARY_PATH)

    assert response.status_code == 401
    assert response.is_json
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert set(response.json["error"]) == {
        "code", "message", "retryable", "request_id"}
    assert "Location" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("header", [
    None, "", "Basic abc", "Bearer", "Bearer one two",
])
def test_malformed_bearer_never_reaches_credential_resolution(
        raw_client, monkeypatch, header):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: pytest.fail("malformed header must not be resolved"))

    headers = {} if header is None else {"Authorization": header}
    response = raw_client.get(DIARY_PATH, headers=headers)

    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_rejected_credential_is_json_not_html(raw_client, monkeypatch):
    failure = mobile_auth.MobileAuthFailure(
        "AUTH_SESSION_EXPIRED", 401, False, "revoked")
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: (_ for _ in ()).throw(failure))

    response = raw_client.get(
        DIARY_PATH, headers={"Authorization": "Bearer stale"})

    assert response.status_code == 401
    assert response.is_json
    assert response.json["error"]["retryable"] is False


def test_web_session_cookie_alone_cannot_read_the_mobile_diary(
        client, make_user):
    """A browser login is not a mobile credential; there is one boundary only."""
    user = make_user("cookie-only-nutrition")
    log_meal(user)
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user.id)
        browser_session["_fresh"] = True

    response = client.get(DIARY_PATH)

    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_authenticated_read_returns_the_callers_own_ledger(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)

    response = read_diary(raw_client, as_mobile(mobile_user))

    assert response.status_code == 200
    assert set(response.json) == {"day", "meals", "totals", "goal"}
    assert len(response.json["meals"]) == 1
    assert "Set-Cookie" not in response.headers
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"


def test_another_users_ledger_is_never_visible(
        raw_client, as_mobile, mobile_user, make_user):
    other = make_user("other-nutrition")
    log_meal(other, yemekler="Başkasının öğünü")
    log_meal(mobile_user, yemekler="Kendi öğünüm")

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert [meal["description"] for meal in payload["meals"]] == ["Kendi öğünüm"]


# ---------------------------------------------------------------------------
# Diary day
# ---------------------------------------------------------------------------

def test_day_is_a_full_iso_date_with_the_server_zone(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["day"] == {"date": "2026-08-09", "timezone": "Europe/Istanbul"}


def test_client_supplied_day_or_zone_cannot_move_the_diary_day(
        raw_client, as_mobile, mobile_user):
    """The day boundary is the server's. Nothing on the request can shift it."""
    headers = as_mobile(mobile_user)
    headers["X-Timezone"] = "Pacific/Auckland"

    with audit_clock(FIXED_NOW):
        response = raw_client.get(
            f"{DIARY_PATH}?day=2020-01-01&timezone=UTC&tz=UTC", headers=headers)

    assert response.json["day"]["date"] == "2026-08-09"
    assert response.json["day"]["timezone"] == "Europe/Istanbul"


def test_day_comes_from_the_canonical_server_clock(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user, day=date(2026, 12, 31),
             created_at=datetime(2026, 12, 31, 18, 0))

    with audit_clock(datetime(2026, 12, 31, 23, 30, tzinfo=APP_TZ)):
        payload = raw_client.get(DIARY_PATH, headers=as_mobile(mobile_user)).json

    assert payload["day"]["date"] == "2026-12-31"
    assert len(payload["meals"]) == 1


# ---------------------------------------------------------------------------
# Entry identity
# ---------------------------------------------------------------------------

def test_every_entry_carries_an_opaque_identity(
        raw_client, as_mobile, mobile_user):
    row = log_meal(mobile_user)

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    identity = payload["meals"][0]["id"]
    assert isinstance(identity, str) and identity
    assert identity != str(row.id)
    assert not identity.isdigit()


def test_every_entry_carries_a_stable_opaque_mutation_revision(
        raw_client, as_mobile, mobile_user):
    row = log_meal(mobile_user)
    headers = as_mobile(mobile_user)

    first = read_diary(raw_client, headers).json["meals"][0]["revision"]
    second = read_diary(raw_client, headers).json["meals"][0]["revision"]

    assert isinstance(first, str) and first
    assert first == second
    assert first != str(row.id)
    assert not first.isdigit()


def test_entry_identity_is_stable_across_reads(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    headers = as_mobile(mobile_user)

    first = read_diary(raw_client, headers).json["meals"][0]["id"]
    second = read_diary(raw_client, headers).json["meals"][0]["id"]

    assert first == second


def test_entry_identity_is_bound_to_its_owner(app):
    secret = app.config["SECRET_KEY"]

    owner_token = mobile_nutrition.diary_entry_id(secret, 1, 7)

    assert owner_token != mobile_nutrition.diary_entry_id(secret, 2, 7)
    assert mobile_nutrition.matches_diary_entry_id(secret, 1, 7, owner_token)
    assert not mobile_nutrition.matches_diary_entry_id(secret, 2, 7, owner_token)
    assert not mobile_nutrition.matches_diary_entry_id(secret, 1, 8, owner_token)


def test_two_users_sharing_a_row_number_never_share_an_identity(
        raw_client, as_mobile, mobile_user, make_user):
    """Row 1 of one account must not be addressable as row 1 of another."""
    other = make_user("identity-neighbour")
    log_meal(mobile_user)
    log_meal(other)

    mine = read_diary(raw_client, as_mobile(mobile_user)).json["meals"][0]["id"]
    theirs = read_diary(raw_client, as_mobile(other)).json["meals"][0]["id"]

    assert mine != theirs


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def test_logged_at_carries_an_explicit_offset(
        raw_client, as_mobile, mobile_user):
    """05:24 stored as naive UTC is 08:24+03:00, and says so on the wire."""
    log_meal(mobile_user, created_at=datetime(2026, 8, 9, 5, 24))

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["meals"][0]["logged_at"] == "2026-08-09T08:24:00+03:00"


def test_missing_timestamp_stays_missing(raw_client, as_mobile, mobile_user):
    row = log_meal(mobile_user)
    MealLog.query.filter_by(id=row.id).update({"created_at": None})
    db.session.commit()

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["meals"][0]["logged_at"] is None


# ---------------------------------------------------------------------------
# Unknown is not zero
# ---------------------------------------------------------------------------

def test_null_macros_survive_the_boundary_as_null(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user, kalori=320.0, protein=None, karb=None, yag=None)

    nutrition = read_diary(
        raw_client, as_mobile(mobile_user)).json["meals"][0]["nutrition"]

    assert nutrition == {
        "energy_kcal": 320.0, "protein_g": None,
        "carbohydrate_g": None, "fat_g": None}


def test_a_measured_zero_is_not_a_missing_value(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user, kalori=0.0, protein=0.0, karb=0.0, yag=0.0)

    nutrition = read_diary(
        raw_client, as_mobile(mobile_user)).json["meals"][0]["nutrition"]

    assert nutrition == {
        "energy_kcal": 0.0, "protein_g": 0.0,
        "carbohydrate_g": 0.0, "fat_g": 0.0}


def test_no_onboarding_session_means_no_goal(
        raw_client, as_mobile, mobile_user):
    assert read_diary(raw_client, as_mobile(mobile_user)).json["goal"] is None


@pytest.mark.parametrize("stored", [None, 0, -50.0])
def test_unset_or_impossible_target_is_published_as_no_goal(
        raw_client, as_mobile, mobile_user, stored):
    db.session.add(UserSession(user_id=mobile_user.id, target_calories=stored))
    db.session.commit()

    assert read_diary(raw_client, as_mobile(mobile_user)).json["goal"] is None


def test_configured_target_is_published_as_a_goal(
        raw_client, as_mobile, mobile_user):
    db.session.add(UserSession(user_id=mobile_user.id, target_calories=2200.0))
    db.session.commit()

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["goal"] == {"target_energy_kcal": 2200.0}


def test_newest_onboarding_session_owns_the_target(
        raw_client, as_mobile, mobile_user):
    db.session.add(UserSession(
        user_id=mobile_user.id, target_calories=1800.0,
        created_at=datetime(2026, 1, 1)))
    db.session.add(UserSession(
        user_id=mobile_user.id, target_calories=2400.0,
        created_at=datetime(2026, 8, 1)))
    db.session.commit()

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["goal"] == {"target_energy_kcal": 2400.0}


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------

def test_totals_are_server_authoritative_and_count_null_as_zero(
        raw_client, as_mobile, mobile_user):
    """Pins the documented asymmetry: entries keep nulls, totals do not."""
    log_meal(mobile_user, kalori=320.0, protein=12.0, karb=40.0, yag=9.0)
    log_meal(mobile_user, ogun="Öğle", kalori=500.0, protein=None,
             karb=50.0, yag=None, created_at=datetime(2026, 8, 9, 10, 0))

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["totals"] == {
        "energy_kcal": 820.0, "protein_g": 12.0,
        "carbohydrate_g": 90.0, "fat_g": 9.0}
    assert payload["meals"][1]["nutrition"]["protein_g"] is None


def test_an_empty_day_totals_zero_rather_than_null(
        raw_client, as_mobile, mobile_user):
    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["meals"] == []
    assert payload["totals"] == {
        "energy_kcal": 0, "protein_g": 0, "carbohydrate_g": 0, "fat_g": 0}


def test_the_ledger_and_the_diary_builder_are_never_summed(
        raw_client, as_mobile, mobile_user):
    """Committing a builder meal writes it to the ledger; only one may count."""
    meal = CustomMeal(user_id=mobile_user.id, meal_name="Kahvaltı",
                      date_key=FIXED_DAY.isoformat(), is_logged=True)
    db.session.add(meal)
    db.session.flush()
    db.session.add(CustomMealItem(
        custom_meal_id=meal.id, food_name="Yumurta", grams=100.0,
        calories=155.0, protein=13.0, carbs=1.1, fat=11.0))
    db.session.commit()
    log_meal(mobile_user, yemekler="Yumurta (100g)", kalori=155.0,
             protein=13.0, karb=1.1, yag=11.0, source="diary")

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert len(payload["meals"]) == 1
    assert payload["totals"]["energy_kcal"] == 155.0


# ---------------------------------------------------------------------------
# Slot and source vocabularies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("meal_label", "slot"), [
    ("Kahvaltı", "kahvalti"),
    ("Öğle", "ogle"),
    ("Akşam", "aksam"),
    ("Ara Öğün", "ara_ogun"),
    ("AI Koç", "unknown"),
    ("Ayşe'den alınan öneri", "unknown"),
])
def test_meal_label_maps_onto_a_stable_slot_token(
        raw_client, as_mobile, mobile_user, meal_label, slot):
    log_meal(mobile_user, ogun=meal_label)

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["meals"][0]["slot"] == slot


@pytest.mark.parametrize(("stored", "published"), [
    ("manual", "manual"),
    ("diary", "diary"),
    ("ai_plan", "ai_plan"),
    ("barcode", "barcode"),
    ("coach", "coach"),
    (None, "unknown"),
    ("wearable", "unknown"),
])
def test_source_is_published_verbatim_or_as_unknown(
        raw_client, as_mobile, mobile_user, stored, published):
    """An unrecognised or absent source is never folded into `manual`."""
    row = log_meal(mobile_user)
    MealLog.query.filter_by(id=row.id).update({"source": stored})
    db.session.commit()

    payload = read_diary(raw_client, as_mobile(mobile_user)).json

    assert payload["meals"][0]["source"] == published


# ---------------------------------------------------------------------------
# Failure, privacy and performance
# ---------------------------------------------------------------------------

def test_storage_failure_is_a_json_503_that_leaks_no_diary_content(
        raw_client, as_mobile, mobile_user, monkeypatch, caplog):
    log_meal(mobile_user, yemekler="Kuzu pirzola ve pilav")
    monkeypatch.setattr(
        mobile_nutrition, "build_diary_day",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("raw database failure: SELECT meal_log")))

    response = read_diary(raw_client, as_mobile(mobile_user))

    assert response.status_code == 503
    assert response.is_json
    assert response.json["error"]["code"] == "NUTRITION_TEMPORARILY_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert "request_id" in response.json["error"]
    assert "raw database failure" not in caplog.text
    assert "Kuzu pirzola" not in caplog.text
    assert "event=diary_read_failed" in caplog.text


def test_a_successful_read_logs_no_diary_content(
        raw_client, as_mobile, mobile_user, caplog):
    log_meal(mobile_user, yemekler="Mercimek çorbası ve ekmek")

    read_diary(raw_client, as_mobile(mobile_user))

    assert "Mercimek çorbası" not in caplog.text
    assert "opaque-access-credential" not in caplog.text


@pytest.mark.parametrize("entry_count", [1, 5])
def test_the_read_does_not_grow_a_query_per_entry(
        app, raw_client, as_mobile, mobile_user, entry_count):
    for index in range(entry_count):
        log_meal(mobile_user, created_at=datetime(2026, 8, 9, 5, index))
    db.session.add(UserSession(user_id=mobile_user.id, target_calories=2200.0))
    db.session.commit()

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert read_diary(raw_client, as_mobile(mobile_user)).status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    reads = [s for s in statements
             if "meal_log" in s or "user_session" in s]
    assert len(reads) == 2


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", [
    "app/services/mobile_nutrition/serialization.py",
    "app/services/mobile_nutrition/identity.py",
])
def test_the_projection_layers_stay_pure(module):
    """A "pure" layer that quietly grows an ORM import stops being testable
    without a database, and the whole contract becomes harder to reason about.
    `app.timeutil` is allowed: it is the canonical day authority and is itself
    stdlib-only."""
    tree = ast.parse(Path(module).read_text(encoding="utf-8"), filename=module)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not {name for name in imported
                if name.startswith(("app.models", "app.extensions", "flask",
                                    "sqlalchemy"))}
    assert imported <= {"app.timeutil", "base64", "hashlib", "hmac"}


def test_the_legacy_web_ledger_route_is_left_exactly_as_it_was(
        client, auth_user):
    """The mobile contract is an addition, not a rewrite of the web payload.

    The web UI parses `DD.MM` and has no use for an entry identity, so both stay
    as they are; the ambiguities they carry are corrected at the versioned
    mobile boundary instead of underneath a live browser surface.
    """
    log_meal(auth_user, day=FIXED_DAY)

    with audit_clock(FIXED_NOW):
        payload = client.get("/meal-log/today").get_json()

    assert payload["tarih"] == "09.08"
    assert set(payload) == {"meals", "totals", "tarih"}
    assert "id" not in payload["meals"][0]
    assert set(payload["totals"]) == {"kalori", "protein", "karb", "yag"}
