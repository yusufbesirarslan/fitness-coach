"""Tests for the S3 helper (s3_helper.py).

Anahtar üretimi, etkin/devre-dışı durumları ve fail-open sözleşmesi
(yüklemede S3Error, presign'da None). boto3 client'ı mock'lu — AWS yok.

    python -m pytest tests/test_s3_helper.py -v
"""
import re

import pytest
from botocore.exceptions import ClientError

import s3_helper
from s3_helper import S3Error, _build_key, generate_presigned_url, is_enabled, upload_image


class _FakeS3:
    def __init__(self, fail=False):
        self.put_calls = []
        self.fail = fail

    def put_object(self, **kwargs):
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        self.put_calls.append(kwargs)

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"


@pytest.fixture
def s3_on(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(s3_helper, "_client", fake)
    return fake


def test_disabled_without_bucket(monkeypatch):
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", "")
    assert is_enabled() is False
    with pytest.raises(S3Error, match="yapılandırılmadı"):
        upload_image(b"img")


def test_build_key_layout_and_unknown_mime():
    key = _build_key("meals", "image/png", 42)
    assert re.fullmatch(r"meals/42/\d{4}/\d{2}/[0-9a-f]{32}\.png", key)
    assert _build_key("x", "application/pdf", None).startswith("x/anon/")
    assert _build_key("x", "application/pdf", None).endswith(".bin")


def test_upload_image_puts_encrypted_object(s3_on):
    key = upload_image(b"resim", content_type="image/jpeg",
                       prefix="pump-checks", user_id=7)
    call = s3_on.put_calls[0]
    assert call["Bucket"] == "test-bucket"
    assert call["Key"] == key
    assert call["Body"] == b"resim"
    assert call["ServerSideEncryption"] == "AES256"  # özel bucket sözleşmesi


def test_upload_image_failures(s3_on, monkeypatch):
    with pytest.raises(S3Error, match="boş"):
        upload_image(b"")
    monkeypatch.setattr(s3_helper, "_client", _FakeS3(fail=True))
    with pytest.raises(S3Error):
        upload_image(b"resim")


def test_presigned_url_roundtrip_and_fail_open(s3_on, monkeypatch):
    url = generate_presigned_url("meals/42/a.png", expires_in=600)
    assert url == "https://s3.example/test-bucket/meals/42/a.png?expires=600"
    assert generate_presigned_url("") is None
    monkeypatch.setattr(s3_helper, "_client", _FakeS3(fail=True))
    assert generate_presigned_url("meals/x.png") is None  # hata → None (fail-open)
