"""F4 — Pump Check gallery delete must close the stored-object lifecycle.

The database stays authoritative. Storage delete runs only after a successful
commit, and only for an object this application minted for that owner.
"""
import logging

import pytest
from botocore.exceptions import ClientError

import s3_helper
from app.extensions import db
from app.models import PumpCheck


BUCKET = "axisai-test-bucket"


class FakeS3Client:
    def __init__(self):
        self.deleted = []
        self.delete_error = None

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        if self.delete_error is not None:
            raise self.delete_error
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    def put_object(self, **kwargs):
        return {}

    def generate_presigned_url(self, *args, **kwargs):
        return "https://example.invalid/signed"


@pytest.fixture
def s3(monkeypatch):
    client = FakeS3Client()
    monkeypatch.setattr(s3_helper, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(s3_helper, "_client", client)
    return client


def _key(user_id, stem="a1"):
    return f"pump-checks/{user_id}/2026/09/{stem * 16}.jpg"


def _check(user_id, image_key, date_key="d1", **fields):
    row = PumpCheck(
        user_id=user_id,
        image_key=image_key,
        date_key=date_key,
        description=fields.pop("description", "Chest day"),
        valid=True,
        **fields,
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_successful_gallery_delete_removes_the_row_then_the_object(
        client, auth_user, s3, monkeypatch):
    key = _key(auth_user.id)
    row = _check(auth_user.id, key)
    row_id = row.id
    observed = {}
    original_delete = FakeS3Client.delete_object
    original_commit = db.session.commit
    mutation_committed = {"done": False}

    def tracking_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        if PumpCheck.query.filter_by(id=row_id).count() == 0:
            mutation_committed["done"] = True
        return result

    def observing_delete(self, **kwargs):
        observed["mutation_committed_at_call_time"] = mutation_committed["done"]
        observed["row_gone_at_call_time"] = (
            PumpCheck.query.filter_by(id=row_id).count() == 0)
        return original_delete(self, **kwargs)

    monkeypatch.setattr(db.session, "commit", tracking_commit)
    monkeypatch.setattr(FakeS3Client, "delete_object", observing_delete)

    response = client.delete(f"/pump-check-gallery/{row_id}")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert db.session.get(PumpCheck, row_id) is None
    assert observed["mutation_committed_at_call_time"] is True
    assert observed["row_gone_at_call_time"] is True
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}]


def test_db_commit_failure_never_deletes_storage(
        client, auth_user, s3, monkeypatch):
    key = _key(auth_user.id)
    row = _check(auth_user.id, key)
    row_id = row.id
    original_commit = db.session.commit
    calls = {"n": 0}

    def commit_or_fail():
        calls["n"] += 1
        # before_request streak maintenance commits first; fail the
        # gallery-delete transaction itself.
        if calls["n"] > 1:
            raise RuntimeError("db down")
        return original_commit()

    monkeypatch.setattr(db.session, "commit", commit_or_fail)

    with pytest.raises(RuntimeError):
        client.delete(f"/pump-check-gallery/{row_id}")

    monkeypatch.setattr(db.session, "commit", original_commit)
    db.session.rollback()
    assert db.session.get(PumpCheck, row_id) is not None
    assert s3.deleted == []


def test_storage_delete_failure_after_commit_keeps_the_row_gone(
        client, auth_user, s3, caplog):
    key = _key(auth_user.id)
    row = _check(auth_user.id, key)
    row_id = row.id
    s3.delete_error = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "DeleteObject")

    with caplog.at_level(logging.ERROR):
        response = client.delete(f"/pump-check-gallery/{row_id}")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert db.session.get(PumpCheck, row_id) is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}]
    assert "image_release_pending" in caplog.text
    assert "X-Amz" not in caplog.text
    assert BUCKET not in caplog.text
    assert "AccessDenied" not in caplog.text or "error_type" in caplog.text


def test_already_missing_storage_object_is_idempotent(client, auth_user, s3):
    key = _key(auth_user.id)
    row = _check(auth_user.id, key)
    row_id = row.id
    s3.delete_error = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "DeleteObject")

    response = client.delete(f"/pump-check-gallery/{row_id}")

    assert response.status_code == 200
    assert db.session.get(PumpCheck, row_id) is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key}]


def test_other_user_cannot_delete_another_users_object(
        client, auth_user, make_user, login, s3):
    owner_key = _key(auth_user.id)
    row = _check(auth_user.id, owner_key)
    row_id = row.id
    make_user("bob")
    login("bob")

    response = client.delete(f"/pump-check-gallery/{row_id}")

    assert response.status_code == 404
    assert db.session.get(PumpCheck, row_id) is not None
    assert s3.deleted == []


def test_deleting_one_pump_check_cannot_release_anothers_object(
        client, auth_user, s3):
    key_a = _key(auth_user.id, "aa")
    key_b = _key(auth_user.id, "bb")
    row_a = _check(auth_user.id, key_a, date_key="d-a")
    row_b = _check(auth_user.id, key_b, date_key="d-b", description="Back day")

    response = client.delete(f"/pump-check-gallery/{row_a.id}")

    assert response.status_code == 200
    assert db.session.get(PumpCheck, row_a.id) is None
    kept = db.session.get(PumpCheck, row_b.id)
    assert kept is not None
    assert kept.image_key == key_b
    assert s3.deleted == [{"Bucket": BUCKET, "Key": key_a}]


def test_still_referenced_key_is_not_deleted(client, auth_user, s3):
    shared = _key(auth_user.id, "cc")
    row_a = _check(auth_user.id, shared, date_key="d-a")
    row_b = _check(auth_user.id, shared, date_key="d-b", description="Shared")

    response = client.delete(f"/pump-check-gallery/{row_a.id}")

    assert response.status_code == 200
    assert db.session.get(PumpCheck, row_a.id) is None
    assert db.session.get(PumpCheck, row_b.id) is not None
    assert s3.deleted == []


@pytest.mark.parametrize("image_key", [
    None,
    "",
    "https://cdn.example/pump.jpg",
    "pump-checks/owned.jpg",
])
def test_unmanaged_or_empty_image_key_skips_storage_delete(
        client, auth_user, s3, image_key):
    row = _check(auth_user.id, image_key)
    row_id = row.id

    response = client.delete(f"/pump-check-gallery/{row_id}")

    assert response.status_code == 200
    assert db.session.get(PumpCheck, row_id) is None
    assert s3.deleted == []


def test_gallery_listing_still_returns_remaining_owned_photos(
        client, auth_user, s3, monkeypatch):
    kept_key = _key(auth_user.id, "dd")
    gone = _check(auth_user.id, _key(auth_user.id, "ee"), date_key="gone")
    kept = _check(
        auth_user.id, kept_key, date_key="kept", description="Still here")
    monkeypatch.setattr(
        s3_helper, "generate_presigned_url",
        lambda key, **kw: f"https://media.example.test/{key}")

    assert client.delete(f"/pump-check-gallery/{gone.id}").status_code == 200
    payload = client.get("/pump-check-gallery/data").get_json()

    assert [item["id"] for item in payload["items"]] == [kept.id]
    assert payload["items"][0]["imageUrl"] == (
        f"https://media.example.test/{kept_key}")
    assert "image_key" not in payload["items"][0]
