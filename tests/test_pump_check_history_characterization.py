"""Pins the Pump Check boundary that PR4A must not move.

These are characterization tests: they describe what the canonical mobile Pump
Check surface already does before a collection read is added next to it. Their
job is to fail if adding history changes the single-item contract, the create
contract, or the PR3 comparison contract — none of which PR4A is allowed to
touch.
"""
from types import SimpleNamespace

import pytest

from app import create_app
from app.models import PumpCheck
from app.services import mobile_auth
from app.services.mobile_pump_checks import service


SINGLE = "/api/v1/pump-checks/%s"


@pytest.fixture
def mobile_user(make_user):
    return make_user("pump-history-characterization")


@pytest.fixture
def headers(monkeypatch, mobile_user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            mobile_user, SimpleNamespace(id=1), {"sub": mobile_user.cognito_sub}))
    return {"Authorization": "Bearer opaque-access-credential"}


def _row(user_id, **fields):
    from app.extensions import db

    fields.setdefault("public_id", "characterization00000001")
    fields.setdefault("analysis_status", "completed")
    row = PumpCheck(user_id=user_id, **fields)
    db.session.add(row)
    db.session.commit()
    return row


def test_single_item_payload_keys_are_exactly_the_published_contract(
        client, headers, mobile_user, monkeypatch):
    """The detailed surface keeps every field PR4A's list deliberately omits."""
    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: False)
    row = _row(mobile_user.id, captured_at=None, body_region=None)

    response = client.get(SINGLE % row.public_id, headers=headers)

    assert response.status_code == 200
    assert set(response.get_json()["pump_check"]) == {
        "id", "captured_at", "created_at", "body_region", "environment",
        "description", "image_url", "analysis_status", "analysis_version",
        "analysis",
    }


def test_single_item_still_serves_legacy_rows_without_captured_at(
        client, headers, mobile_user, monkeypatch):
    """PR4A excludes these from the LIST; it must not hide them from the ITEM."""
    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: False)
    row = _row(mobile_user.id, captured_at=None, body_region=None)

    payload = client.get(SINGLE % row.public_id, headers=headers).get_json()

    assert payload["pump_check"]["captured_at"] is None
    assert payload["pump_check"]["id"] == row.public_id


def test_single_item_stays_owner_scoped(client, headers, make_user, monkeypatch):
    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: False)
    stranger = make_user("pump-history-stranger")
    foreign = _row(stranger.id, captured_at=None)

    assert client.get(SINGLE % foreign.public_id, headers=headers).status_code == 404


def test_mobile_pump_check_route_inventory():
    """PR4A may ADD the collection GET and nothing else on this prefix."""
    rules = {
        (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in create_app().url_map.iter_rules()
        if rule.rule.startswith("/api/v1/pump-check")
    }

    assert rules == {
        ("/api/v1/pump-checks", ("POST",)),
        ("/api/v1/pump-checks/<pump_check_token>", ("GET",)),
        ("/api/v1/pump-check-comparisons", ("POST",)),
        ("/api/v1/pump-check-comparisons/<comparison_id>", ("GET",)),
    }


def test_create_and_comparison_authorities_are_untouched_by_history():
    """PR4A adds a read model; the write authorities keep their entry points."""
    assert hasattr(service, "create_or_replay")
    assert hasattr(service, "get_owned")

    from app.services.mobile_pump_check_comparisons import service as comparisons

    assert hasattr(comparisons, "resolve_eligible_sources")
