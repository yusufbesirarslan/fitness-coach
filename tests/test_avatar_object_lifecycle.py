"""F4 — avatar replace/reset must close the stored-object lifecycle.

Upload the new object first, persist the new reference, commit, then delete
the superseded managed object. The live avatar is never the cleanup target.
"""
import logging

import pytest
from botocore.exceptions import ClientError
from sqlalchemy.exc import IntegrityError

import s3_helper
from app.extensions import db
from app.models import User
from tests.test_validators import _image_data_url


BUCKET = "axisai-test-bucket"


class FakeS3Client:
    def __init__(self):
        self.deleted = []
        self.puts = []
        self.delete_error = None
        self.put_error = None

    def put_object(self, **kwargs):
        if self.put_error is not None:
            raise self.put_error
        self.puts.append(kwargs)
        return {}

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        if self.delete_error is not None:
            raise self.delete_error
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        key = (Params or {}).get("Key", "")
        return f"https://example.invalid/signed/{key}"


@pytest.fixture
def s3(monkeypatch):
    client = FakeS3Client()
    monkeypatch.setattr(s3_helper, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(s3_helper, "_client", client)
    return client


def _key(user_id, stem="aa"):
    return f"avatars/{user_id}/2026/09/{stem * 16}.png"


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _set_avatar(user, key):
    user.profile_picture_key = key
    user.profile_picture = None
    db.session.commit()


def test_successful_replacement_persists_new_then_deletes_old(
        client, auth_user, s3, monkeypatch):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)
    observed = {}
    original_delete = FakeS3Client.delete_object
    original_commit = db.session.commit
    mutation_committed = {"done": False}

    def tracking_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        live = db.session.get(User, auth_user.id).profile_picture_key
        if live and live != old_key:
            mutation_committed["done"] = True
        return result

    def observing_delete(self, **kwargs):
        live = db.session.get(User, auth_user.id).profile_picture_key
        observed["mutation_committed_at_call_time"] = mutation_committed["done"]
        observed["live_key_at_delete"] = live
        observed["deleted_key"] = kwargs.get("Key")
        return original_delete(self, **kwargs)

    monkeypatch.setattr(db.session, "commit", tracking_commit)
    monkeypatch.setattr(FakeS3Client, "delete_object", observing_delete)

    response = client.post(
        "/edit-profile", json={"profile_picture": _image_data_url("PNG")})

    user = _fresh(auth_user.id)
    new_key = user.profile_picture_key
    assert response.status_code == 200
    assert new_key and new_key != old_key
    assert new_key.startswith(f"avatars/{auth_user.id}/")
    assert user.profile_picture is None
    assert s3.puts and s3.puts[0]["Key"] == new_key
    assert observed["mutation_committed_at_call_time"] is True
    assert observed["live_key_at_delete"] == new_key
    assert observed["deleted_key"] == old_key
    assert s3.deleted == [{"Bucket": BUCKET, "Key": old_key}]
    assert user.avatar_src.startswith("https://example.invalid/signed/")


def test_new_upload_validation_failure_leaves_old_avatar_untouched(
        client, auth_user, s3):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)

    response = client.post(
        "/edit-profile", json={"profile_picture": "https://evil.example/x.png"})

    user = _fresh(auth_user.id)
    assert response.status_code == 400
    assert user.profile_picture_key == old_key
    assert s3.puts == []
    assert s3.deleted == []


def test_s3_fail_open_to_base64_releases_the_superseded_object(
        client, auth_user, s3):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)
    s3.put_error = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "PutObject")

    response = client.post(
        "/edit-profile", json={"profile_picture": _image_data_url("PNG")})

    user = _fresh(auth_user.id)
    assert response.status_code == 200
    # Existing fail-open contract: S3 upload error stores the data URL and
    # clears the key. The superseded managed object is released after commit.
    assert user.profile_picture_key is None
    assert user.profile_picture
    assert s3.deleted == [{"Bucket": BUCKET, "Key": old_key}]


def test_db_commit_failure_after_upload_keeps_old_and_drops_the_new_object(
        client, auth_user, s3, monkeypatch):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)
    original_commit = db.session.commit

    def commit_or_fail():
        # before_request streak maintenance commits first; fail only the
        # mutation that runs after the new object exists.
        if s3.puts:
            raise IntegrityError("commit", {}, Exception("boom"))
        return original_commit()

    monkeypatch.setattr(db.session, "commit", commit_or_fail)

    with pytest.raises(IntegrityError):
        client.post(
            "/edit-profile",
            json={"profile_picture": _image_data_url("PNG")})

    monkeypatch.setattr(db.session, "commit", original_commit)
    db.session.rollback()
    user = _fresh(auth_user.id)
    assert user.profile_picture_key == old_key
    assert len(s3.puts) == 1
    uploaded = s3.puts[0]["Key"]
    assert uploaded != old_key
    assert s3.deleted == [{"Bucket": BUCKET, "Key": uploaded}]


def test_old_object_cleanup_failure_keeps_new_avatar_authoritative(
        client, auth_user, s3, caplog):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)
    s3.delete_error = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "DeleteObject")

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/edit-profile", json={"profile_picture": _image_data_url("PNG")})

    user = _fresh(auth_user.id)
    new_key = user.profile_picture_key
    assert response.status_code == 200
    assert new_key and new_key != old_key
    assert user.profile_picture is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": old_key}]
    assert new_key not in [call["Key"] for call in s3.deleted]
    assert "object_release_pending" in caplog.text
    assert "X-Amz" not in caplog.text
    assert BUCKET not in caplog.text


def test_same_key_replacement_does_not_delete_the_live_object(
        client, auth_user, s3, monkeypatch):
    live_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, live_key)
    monkeypatch.setattr(
        s3_helper, "upload_image",
        lambda *args, **kwargs: live_key)

    response = client.post(
        "/edit-profile", json={"profile_picture": _image_data_url("PNG")})

    user = _fresh(auth_user.id)
    assert response.status_code == 200
    assert user.profile_picture_key == live_key
    assert s3.deleted == []


def test_avatar_reset_commits_empty_state_then_deletes_managed_object(
        client, auth_user, s3, monkeypatch):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)
    observed = {}
    original = FakeS3Client.delete_object

    def observing_delete(self, **kwargs):
        live = db.session.get(User, auth_user.id)
        observed["key"] = live.profile_picture_key
        observed["picture"] = live.profile_picture
        return original(self, **kwargs)

    monkeypatch.setattr(FakeS3Client, "delete_object", observing_delete)

    response = client.post(
        "/edit-profile", json={"profile_picture": ""})

    user = _fresh(auth_user.id)
    assert response.status_code == 200
    assert user.profile_picture_key is None
    assert user.profile_picture is None
    assert user.avatar_src is None
    assert observed["key"] is None
    assert s3.deleted == [{"Bucket": BUCKET, "Key": old_key}]


@pytest.mark.parametrize("stored_key", [
    None,
    "",
    "https://cdn.example/avatar.png",
    "/static/img/default-avatar.png",
    "avatars/not-minted.png",
])
def test_default_static_external_or_null_avatar_skips_storage_delete(
        client, auth_user, s3, stored_key):
    auth_user.profile_picture_key = stored_key
    auth_user.profile_picture = None
    db.session.commit()

    response = client.post(
        "/edit-profile", json={"profile_picture": ""})

    user = _fresh(auth_user.id)
    assert response.status_code == 200
    assert user.profile_picture_key is None
    assert s3.deleted == []


def test_full_profile_update_still_replaces_avatar_after_commit(
        client, auth_user, s3):
    old_key = _key(auth_user.id, "aa")
    _set_avatar(auth_user, old_key)

    response = client.post("/edit-profile", json={
        "username": "testuser",
        "full_name": "Test User",
        "profile_picture": _image_data_url("PNG"),
    })

    user = _fresh(auth_user.id)
    assert response.status_code == 200
    assert user.full_name == "Test User"
    assert user.profile_picture_key != old_key
    assert s3.deleted == [{"Bucket": BUCKET, "Key": old_key}]
