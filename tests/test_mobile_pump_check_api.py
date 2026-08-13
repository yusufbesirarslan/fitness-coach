import json
from io import BytesIO
from types import SimpleNamespace
from datetime import datetime, timedelta

import pytest
from PIL import Image

from app.extensions import db
from app.models import PumpCheck
from app.services import mobile_auth
from app.services import ai_gate
from app.services.mobile_pump_checks import service
from app.services.mobile_pump_checks.analysis import InvalidAnalysis


PATH = "/api/v1/pump-checks"


def _image():
    stream = BytesIO()
    Image.new("RGB", (12, 12), "blue").save(stream, format="JPEG")
    stream.seek(0)
    return stream


def _analysis():
    return {
        "summary": "Visible definition can be assessed in this image.",
        "observations": ["Shoulder outline is visible."],
        "strengths": ["Consistent framing."],
        "focus_areas": ["Keep lighting even."],
        "limitations": ["One image cannot establish progress."],
        "next_check_guidance": "Repeat this framing next time.",
        "quality": "sufficient",
    }


@pytest.fixture
def mobile_user(make_user):
    return make_user("pump-mobile")


@pytest.fixture
def headers(monkeypatch, mobile_user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            mobile_user, SimpleNamespace(id=1), {"sub": mobile_user.cognito_sub}))
    return {
        "Authorization": "Bearer opaque-access-credential",
        "Idempotency-Key": "pump-operation-0001",
    }


@pytest.fixture
def dependencies(monkeypatch, mobile_user):
    calls = {"upload": 0, "analysis": 0, "presign": 0}

    def upload(*args, **kwargs):
        assert db.session().in_transaction() is False
        calls["upload"] += 1
        return f"pump-checks/{mobile_user.id}/2026/08/private.jpg"

    def analyze(*args, **kwargs):
        assert db.session().in_transaction() is False
        calls["analysis"] += 1
        return _analysis()

    def presign(key, expires_in=3600, expected_user_id=None):
        calls["presign"] += 1
        assert expected_user_id == mobile_user.id
        return "https://media.example.test/temporary"

    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(service.s3_helper, "upload_image", upload)
    monkeypatch.setattr(service.s3_helper, "generate_presigned_url", presign)
    monkeypatch.setattr(service, "analyze_image", analyze)
    return calls


def _command(description="progress"):
    return {
        "image": (_image(), "pump.jpg", "image/jpeg"),
        "body_region": "upper_body",
        "environment": "gym",
        "description": description,
        "captured_at": "2026-08-13T05:00:00Z",
    }


def test_mobile_create_requires_bearer_and_valid_idempotency_key(client):
    assert client.post(PATH, data=_command()).status_code == 401
    assert client.post(
        PATH,
        data=_command(),
        headers={"Authorization": "Bearer invalid"},
    ).status_code == 401


def test_mobile_ai_capacity_rejection_preserves_error_envelope(
        client, headers, monkeypatch):
    class FullGate:
        def acquire(self, timeout=0):
            return False

        def release(self):
            raise AssertionError("unacquired gate must not release")

    monkeypatch.setattr(ai_gate, "_ai_slots", FullGate())
    response = client.post(PATH, data=_command(), headers=headers)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "15"
    assert response.get_json()["error"]["code"] == "PUMP_CHECK_PROVIDER_BUSY"
    assert response.get_json()["error"]["retryable"] is True


def test_mobile_create_and_get_share_one_private_canonical_shape(
        client, headers, dependencies):
    created = client.post(PATH, data=_command(), headers=headers)

    assert created.status_code == 201
    check = created.get_json()["pump_check"]
    assert set(check) == {
        "id", "captured_at", "created_at", "body_region", "environment",
        "description", "image_url", "analysis_status", "analysis_version", "analysis",
    }
    assert isinstance(check["id"], str)
    assert check["analysis_status"] == "completed"
    assert check["analysis_version"] == "pump-check-analysis/v1"
    assert check["analysis"] == _analysis()
    assert check["image_url"] == "https://media.example.test/temporary"
    assert "image_key" not in check
    assert "user_id" not in check

    fetched = client.get(f'{PATH}/{check["id"]}', headers=headers)
    assert fetched.status_code == 200
    assert fetched.get_json()["pump_check"] == check
    assert dependencies["analysis"] == 1


def test_same_key_same_command_replays_without_duplicate_or_second_analysis(
        client, headers, dependencies):
    first = client.post(PATH, data=_command(), headers=headers)
    second = client.post(PATH, data=_command(), headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.get_json()["pump_check"]["id"] == second.get_json()["pump_check"]["id"]
    assert PumpCheck.query.count() == 1
    assert dependencies["upload"] == 1
    assert dependencies["analysis"] == 1


def test_same_key_different_command_is_conflict(client, headers, dependencies):
    assert client.post(PATH, data=_command("first"), headers=headers).status_code == 201
    conflict = client.post(PATH, data=_command("different"), headers=headers)
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert PumpCheck.query.count() == 1


def test_cross_user_and_unknown_ids_have_same_private_not_found(
        client, headers, dependencies, make_user, monkeypatch):
    created = client.post(PATH, data=_command(), headers=headers)
    token = created.get_json()["pump_check"]["id"]
    other = make_user("pump-other")
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            other, SimpleNamespace(id=2), {"sub": other.cognito_sub}))

    cross = client.get(f"{PATH}/{token}", headers=headers)
    unknown = client.get(f"{PATH}/AAAAAAAAAAAAAAAAAAAAAAAA", headers=headers)
    assert cross.status_code == unknown.status_code == 404
    assert cross.get_json()["error"]["code"] == "PUMP_CHECK_NOT_FOUND"
    assert unknown.get_json()["error"]["code"] == "PUMP_CHECK_NOT_FOUND"
    assert dependencies["presign"] == 1


def test_provider_failure_persists_one_failed_row_and_same_key_retries_it(
        client, headers, dependencies, monkeypatch):
    attempts = {"count": 0}

    def flaky(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("provider timeout")
        return _analysis()

    monkeypatch.setattr(service, "analyze_image", flaky)

    failed = client.post(PATH, data=_command(), headers=headers)
    assert failed.status_code == 503
    assert failed.get_json()["error"]["code"] == "PUMP_CHECK_PROVIDER_UNAVAILABLE"
    row = PumpCheck.query.one()
    assert row.analysis_status == "failed"
    assert row.image_key is not None

    retried = client.post(PATH, data=_command(), headers=headers)
    assert retried.status_code == 200
    assert retried.get_json()["pump_check"]["analysis_status"] == "completed"
    assert PumpCheck.query.count() == 1
    assert dependencies["upload"] == 1
    assert attempts["count"] == 2


def test_stale_analysis_lease_is_reclaimed_with_new_generation(
        client, headers, dependencies):
    first = client.post(PATH, data=_command(), headers=headers)
    row = PumpCheck.query.one()
    row.analysis_status = "analyzing"
    row.analysis = None
    row.analysis_version = None
    row.analysis_started_at = datetime.utcnow() - timedelta(minutes=20)
    old_attempt = row.analysis_attempt
    db.session.commit()

    replay = client.post(PATH, data=_command(), headers=headers)
    db.session.refresh(row)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert row.analysis_status == "completed"
    assert row.analysis_attempt == old_attempt + 1
    assert service._finalize_success(
        row.id, row.user_id, old_attempt, _analysis()) is False
    db.session.refresh(row)
    assert row.analysis_attempt == old_attempt + 1
    assert row.analysis_status == "completed"


def test_analysis_lease_exceeds_provider_and_storage_retry_budget():
    assert service.ANALYSIS_LEASE_SECONDS >= 600


def test_unreconciled_finalization_is_never_classified_as_provider_failure(
        app, mobile_user, monkeypatch):
    row = PumpCheck(
        user_id=mobile_user.id,
        public_id="A" * 24,
        analysis_status="analyzing",
        analysis_attempt=1,
        analysis_started_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.commit()

    def unavailable_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db.session, "commit", unavailable_commit)
    with pytest.raises(service.PersistenceFinalizationUncertain):
        service._finalize_success(
            row.id, row.user_id, 1, _analysis())


def test_storage_and_invalid_analysis_have_distinct_stable_errors(
        client, headers, dependencies, monkeypatch):
    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: False)
    storage = client.post(PATH, data=_command(), headers=headers)
    assert storage.status_code == 503
    assert storage.get_json()["error"]["code"] == "PUMP_CHECK_STORAGE_UNAVAILABLE"

    headers["Idempotency-Key"] = "pump-operation-0002"
    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(
        service, "analyze_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(InvalidAnalysis("bad")))
    invalid = client.post(PATH, data=_command(), headers=headers)
    assert invalid.status_code == 503
    assert invalid.get_json()["error"]["code"] == "PUMP_CHECK_ANALYSIS_INVALID"
