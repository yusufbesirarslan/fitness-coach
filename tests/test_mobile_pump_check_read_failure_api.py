"""A storage fault on a Pump Check READ is not an authentication outcome.

Triage 2026-09-30 #2. The two GET routes caught only their domain exceptions, so a
database/storage fault fell through to the blueprint-wide handler and was answered
`AUTH_TEMPORARILY_UNAVAILABLE` -- the code a native client reads as "my session is
bad" and answers by discarding a good session. Every sibling read (Progress, Today,
Nutrition, Training) wraps its own fault; these two did not and were untested.
"""
import json
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import PumpCheck
from app.services import mobile_auth
from app.services.mobile_pump_checks import history, service


LIST_PATH = "/api/v1/pump-checks"
TOKEN = "readfail0000000000000001"
SECRET_FRAGMENT = "postgres-internal-host.example:5432"


@pytest.fixture
def mobile_user(make_user):
    return make_user("pump-read-failure")


@pytest.fixture
def headers(monkeypatch, mobile_user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            mobile_user, SimpleNamespace(id=1), {"sub": mobile_user.cognito_sub}))
    return {"Authorization": "Bearer opaque-access-credential"}


@pytest.fixture
def stored_check(mobile_user):
    row = PumpCheck(
        user_id=mobile_user.id, public_id=TOKEN, body_region="upper_body",
        analysis_status="completed", captured_at=datetime(2026, 8, 13, 5, 0, 0))
    db.session.add(row)
    db.session.commit()
    return row


def _storage_fault(*_args, **_kwargs):
    raise OperationalError(
        "SELECT pump_check", {}, Exception(f"could not connect to {SECRET_FRAGMENT}"))


def _assert_pump_check_unavailable(response):
    assert response.status_code == 503
    body = response.get_json()
    error = body.get("error", body)
    assert error["code"] == "PUMP_CHECK_TEMPORARILY_UNAVAILABLE"
    assert error["retryable"] is True
    assert error["code"] != "AUTH_TEMPORARILY_UNAVAILABLE"
    assert SECRET_FRAGMENT not in json.dumps(body)   # no driver/host detail on the wire


def test_history_storage_fault_is_a_pump_check_503_not_an_auth_outcome(
        client, headers, monkeypatch):
    monkeypatch.setattr(history, "list_history", _storage_fault)

    _assert_pump_check_unavailable(client.get(LIST_PATH, headers=headers))


def test_detail_storage_fault_is_a_pump_check_503_not_an_auth_outcome(
        client, headers, stored_check, monkeypatch):
    monkeypatch.setattr(service, "get_owned", _storage_fault)

    _assert_pump_check_unavailable(client.get(f"{LIST_PATH}/{TOKEN}", headers=headers))


def test_detail_serialization_fault_is_also_contained(
        client, headers, stored_check, monkeypatch):
    # Presigning the image URL happens AFTER the row is loaded; a fault there must
    # be answered the same way, not escape to the auth-flavoured catch-all.
    monkeypatch.setattr(service, "serialize_pump_check", _storage_fault)

    _assert_pump_check_unavailable(client.get(f"{LIST_PATH}/{TOKEN}", headers=headers))


def test_read_fault_keeps_the_session_usable_for_the_next_request(
        client, headers, stored_check, monkeypatch):
    with monkeypatch.context() as patched:
        patched.setattr(history, "list_history", _storage_fault)
        assert client.get(LIST_PATH, headers=headers).status_code == 503

    # The same credential still authenticates and the very next read succeeds:
    # the failed request rolled its transaction back instead of poisoning it.
    recovered = client.get(LIST_PATH, headers=headers)
    assert recovered.status_code == 200
    assert [item["id"] for item in recovered.get_json()["pump_checks"]] == [TOKEN]


def test_read_fault_logs_only_a_type_name_and_request_id(
        client, headers, monkeypatch, caplog):
    monkeypatch.setattr(history, "list_history", _storage_fault)

    with caplog.at_level(logging.ERROR):
        client.get(LIST_PATH, headers=headers)

    line = next(r.getMessage() for r in caplog.records
                if "mobile_pump_check event=list_failed" in r.getMessage())
    assert "error_type=OperationalError" in line
    assert "request_id=" in line
    assert SECRET_FRAGMENT not in caplog.text
    assert TOKEN not in caplog.text


def test_expected_domain_answers_are_unchanged(client, headers, stored_check):
    not_found = client.get(f"{LIST_PATH}/unknowntoken00000000000", headers=headers)
    assert not_found.status_code == 404
    assert (not_found.get_json().get("error") or not_found.get_json())["code"] == \
        "PUMP_CHECK_NOT_FOUND"

    bad_limit = client.get(f"{LIST_PATH}?limit=0", headers=headers)
    assert bad_limit.status_code == 400

    bad_cursor = client.get(f"{LIST_PATH}?cursor=not-a-cursor", headers=headers)
    assert bad_cursor.status_code == 400
    assert (bad_cursor.get_json().get("error") or bad_cursor.get_json())["code"] == \
        "INVALID_PAGE_CURSOR"
