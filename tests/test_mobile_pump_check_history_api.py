"""HTTP contract of GET /api/v1/pump-checks — the canonical history surface."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import PumpCheck
from app.services import mobile_auth
from app.services.mobile_pump_checks import service


PATH = "/api/v1/pump-checks"
BASE = datetime(2026, 8, 13, 5, 0, 0)


@pytest.fixture
def mobile_user(make_user):
    return make_user("pump-history-api")


@pytest.fixture
def stranger(make_user):
    return make_user("pump-history-api-stranger")


@pytest.fixture
def headers(monkeypatch, mobile_user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            mobile_user, SimpleNamespace(id=1), {"sub": mobile_user.cognito_sub}))
    return {"Authorization": "Bearer opaque-access-credential"}


@pytest.fixture
def as_stranger(monkeypatch, stranger):
    def _switch():
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                stranger, SimpleNamespace(id=2), {"sub": stranger.cognito_sub}))
        return {"Authorization": "Bearer opaque-access-credential"}
    return _switch


def _add(user, captured_at, token, **fields):
    fields.setdefault("body_region", "upper_body")
    fields.setdefault("analysis_status", "completed")
    row = PumpCheck(
        user_id=user.id, captured_at=captured_at, public_id=token, **fields)
    db.session.add(row)
    db.session.commit()
    return row


def _ids(payload):
    return [item["id"] for item in payload["pump_checks"]]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_history_requires_bearer_authentication(client):
    assert client.get(PATH).status_code == 401
    assert client.get(
        PATH, headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_history_is_registered_as_a_mobile_bearer_route():
    from app import create_app

    rules = {
        (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in create_app().url_map.iter_rules()
        if rule.endpoint.startswith("mobile_api.")
    }

    assert (PATH, ("GET",)) in rules
    assert (PATH, ("POST",)) in rules


# ---------------------------------------------------------------------------
# Owner scope
# ---------------------------------------------------------------------------

def test_history_returns_only_the_authenticated_owners_rows(
        client, headers, mobile_user, stranger):
    _add(mobile_user, BASE, "history00000000000000001")
    _add(stranger, BASE.replace(day=14), "history00000000000000002")

    payload = client.get(PATH, headers=headers).get_json()

    assert _ids(payload) == ["history00000000000000001"]


def test_a_client_supplied_user_id_is_not_authority(
        client, headers, mobile_user, stranger):
    _add(mobile_user, BASE, "history00000000000000001")
    _add(stranger, BASE.replace(day=14), "history00000000000000002")

    payload = client.get(
        f"{PATH}?user_id={stranger.id}", headers=headers).get_json()

    assert _ids(payload) == ["history00000000000000001"]


def test_a_feed_shared_check_stays_out_of_another_private_history(
        client, mobile_user, as_stranger):
    _add(mobile_user, BASE, "history00000000000000001", visibility="feed")

    payload = client.get(PATH, headers=as_stranger()).get_json()

    assert payload["pump_checks"] == []


def test_a_cursor_minted_for_one_owner_is_refused_for_another(
        client, headers, mobile_user, as_stranger):
    _add(mobile_user, BASE, "history00000000000000001")
    _add(mobile_user, BASE.replace(day=14), "history00000000000000002")
    foreign = client.get(
        f"{PATH}?limit=1", headers=headers).get_json()["next_cursor"]

    response = client.get(
        f"{PATH}?limit=1&cursor={foreign}", headers=as_stranger())

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "INVALID_PAGE_CURSOR"


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

def test_an_empty_history_is_an_empty_page(client, headers):
    response = client.get(PATH, headers=headers)

    assert response.status_code == 200
    assert response.get_json() == {
        "pump_checks": [], "next_cursor": None, "has_more": False}


def test_history_responses_are_never_cached(client, headers):
    assert client.get(
        PATH, headers=headers).headers["Cache-Control"] == "no-store"


def test_a_history_item_is_the_lightweight_contract(
        client, headers, mobile_user):
    _add(mobile_user, BASE, "history00000000000000001",
         analysis={"quality": "sufficient", "summary": "s" * 300},
         analysis_version="pump-check-analysis/v1",
         description="private note",
         location_type="gym",
         image_key="pump-checks/1/2026/08/private.jpg")

    payload = client.get(PATH, headers=headers).get_json()

    assert payload["pump_checks"] == [{
        "id": "history00000000000000001",
        "captured_at": "2026-08-13T05:00:00Z",
        "body_region": "upper_body",
        "analysis_status": "completed",
        "analysis_quality": "sufficient",
    }]


def test_history_never_publishes_storage_or_database_internals(
        client, headers, mobile_user):
    row = _add(mobile_user, BASE, "history00000000000000001",
               analysis={"quality": "sufficient"},
               description="private note",
               image_key="pump-checks/9/2026/08/private.jpg")

    body = client.get(PATH, headers=headers).get_data(as_text=True)

    assert "pump-checks/9" not in body
    assert "image_key" not in body
    assert "image_url" not in body
    assert "private note" not in body
    assert f'"{row.id}"' not in body
    assert "user_id" not in body
    assert "summary" not in body


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

def test_the_default_page_size_is_twenty(client, headers, mobile_user):
    for index in range(21):
        _add(mobile_user, BASE.replace(minute=index % 60),
             "historydefault00000000%02d" % index)

    payload = client.get(PATH, headers=headers).get_json()

    assert len(payload["pump_checks"]) == 20
    assert payload["has_more"] is True


def test_an_explicit_page_size_is_honoured(client, headers, mobile_user):
    for index in range(4):
        _add(mobile_user, BASE.replace(minute=index),
             "history0000000000000000%d" % index)

    payload = client.get(f"{PATH}?limit=2", headers=headers).get_json()

    assert len(payload["pump_checks"]) == 2


def test_walking_every_page_over_http_yields_each_row_once(
        client, headers, mobile_user):
    for index in range(5):
        _add(mobile_user, BASE.replace(minute=index),
             "history0000000000000000%d" % index)

    seen = []
    query = f"{PATH}?limit=2"
    while True:
        payload = client.get(query, headers=headers).get_json()
        seen.extend(_ids(payload))
        if not payload["next_cursor"]:
            break
        query = f"{PATH}?limit=2&cursor={payload['next_cursor']}"

    assert seen == ["history0000000000000000%d" % i for i in range(4, -1, -1)]


@pytest.mark.parametrize("limit", ["0", "51", "abc", "-1", "1.5", "", "²"])
def test_an_unusable_page_size_is_a_permanent_client_error(
        client, headers, limit):
    response = client.get(f"{PATH}?limit={limit}", headers=headers)

    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "INVALID_PAGE_SIZE"
    assert error["retryable"] is False


@pytest.mark.parametrize("cursor", [
    "nonsense", "!!!", "a" * 900, "v1|1|2026-08-13T05:00:00.000000|1",
])
def test_an_unusable_cursor_is_a_permanent_client_error(
        client, headers, cursor):
    response = client.get(f"{PATH}?cursor={cursor}", headers=headers)

    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "INVALID_PAGE_CURSOR"
    assert error["retryable"] is False


def test_a_bad_cursor_never_becomes_a_server_error(client, headers):
    """A retryable 5xx would make a client retry a permanently bad request."""
    assert client.get(
        f"{PATH}?cursor=%00%01%02", headers=headers).status_code == 400


# ---------------------------------------------------------------------------
# Side effects
# ---------------------------------------------------------------------------

def test_history_signs_no_media_and_calls_no_provider(
        client, headers, mobile_user, monkeypatch):
    calls = {"presign": 0, "analysis": 0, "upload": 0}

    def presign(*args, **kwargs):
        calls["presign"] += 1
        raise AssertionError("history must not presign private media")

    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(service.s3_helper, "generate_presigned_url", presign)
    monkeypatch.setattr(
        service.s3_helper, "upload_image",
        lambda *a, **k: calls.__setitem__("upload", calls["upload"] + 1))
    monkeypatch.setattr(
        service, "analyze_image",
        lambda *a, **k: calls.__setitem__("analysis", calls["analysis"] + 1))
    for index in range(3):
        _add(mobile_user, BASE.replace(minute=index),
             "history0000000000000000%d" % index,
             image_key="pump-checks/1/2026/08/private.jpg")

    assert client.get(PATH, headers=headers).status_code == 200
    assert calls == {"presign": 0, "analysis": 0, "upload": 0}


def test_history_creates_no_comparison_and_mutates_nothing(
        client, headers, mobile_user):
    from app.models import PumpCheckComparison, PumpCheckComparisonRequest

    _add(mobile_user, BASE, "history00000000000000001")
    before = PumpCheck.query.one().analysis_status

    assert client.get(PATH, headers=headers).status_code == 200

    assert PumpCheckComparison.query.count() == 0
    assert PumpCheckComparisonRequest.query.count() == 0
    assert PumpCheck.query.count() == 1
    assert PumpCheck.query.one().analysis_status == before


# ---------------------------------------------------------------------------
# Legacy rows
# ---------------------------------------------------------------------------

def test_legacy_rows_are_absent_from_history_but_readable_individually(
        client, headers, mobile_user, monkeypatch):
    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: False)
    legacy = _add(mobile_user, None, "history00000000000000001",
                  body_region=None, analysis_status=None)

    assert client.get(PATH, headers=headers).get_json()["pump_checks"] == []

    single = client.get(f"{PATH}/{legacy.public_id}", headers=headers)
    assert single.status_code == 200
    assert single.get_json()["pump_check"]["captured_at"] is None
