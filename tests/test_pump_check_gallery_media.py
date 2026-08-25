"""Pump Check Gallery media delivery regression.

The gallery already persisted stable S3 object keys and minted a presigned URL
at read time. Photos still rendered as native broken-image icons because the
minted URL host was not in CSP img-src, and a failed <img> had no fallback.
"""
from urllib.parse import urlparse

import s3_helper
from app import hooks

from app.extensions import db
from app.models import PumpCheck


def test_gallery_refreshes_historical_owned_image_key_on_every_read(
        client, auth_user, monkeypatch):
    """A legacy web row has only the original stable-key fields, yet every
    history read must resolve it to a newly signed URL."""
    row = PumpCheck(
        user_id=auth_user.id,
        image_key=f"pump-checks/{auth_user.id}/2026/08/owned.jpg",
        description="Chest day",
        valid=True,
    )
    db.session.add(row)
    db.session.commit()
    calls = []

    def sign(key, expires_in=3600, expected_user_id=None):
        calls.append((key, expires_in, expected_user_id))
        return f"https://media.example.test/{key}?sig=fresh-{len(calls)}"

    monkeypatch.setattr(s3_helper, "generate_presigned_url", sign)

    first = client.get("/pump-check-gallery/data").get_json()
    second = client.get("/pump-check-gallery/data").get_json()

    assert first["items"][0]["imageUrl"] == (
        f"https://media.example.test/pump-checks/{auth_user.id}/2026/08/owned.jpg?sig=fresh-1"
    )
    assert second["items"][0]["imageUrl"] == (
        f"https://media.example.test/pump-checks/{auth_user.id}/2026/08/owned.jpg?sig=fresh-2"
    )
    assert "image_key" not in first["items"][0]
    assert calls == [
        (row.image_key, 3600, auth_user.id),
        (row.image_key, 3600, auth_user.id),
    ]


def test_gallery_presigned_url_origin_is_allowed_by_actual_response_csp(
        client, auth_user, monkeypatch):
    """Regression: boto3's default global S3 host was absent from the gallery's
    CSP, so the browser blocked otherwise valid presigned URLs before requesting
    the object."""
    bucket = "axisai-test-media"
    region = "eu-central-1"
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", bucket)
    monkeypatch.setattr(s3_helper, "AWS_REGION", region)
    monkeypatch.setattr(s3_helper, "_client", None)
    monkeypatch.setattr(
        hooks,
        "CSP_IMG_S3_HOSTS",
        f"https://{bucket}.s3.{region}.amazonaws.com "
        f"https://s3.{region}.amazonaws.com",
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )

    image_url = s3_helper.generate_presigned_url(
        f"pump-checks/{auth_user.id}/2026/08/abc.jpg",
        expected_user_id=auth_user.id,
    )
    response = client.get("/pump-check-gallery")
    csp = response.headers["Content-Security-Policy"]
    img_src = next(
        directive for directive in csp.split(";")
        if directive.strip().startswith("img-src ")
    ).split()
    parsed = urlparse(image_url)

    assert f"{parsed.scheme}://{parsed.netloc}" in img_src
    assert parsed.hostname != f"{bucket}.s3.amazonaws.com"


def test_gallery_does_not_return_a_persisted_expiring_url_as_src(client, auth_user):
    stale = (
        "https://axisai-test-media.s3.amazonaws.com/pump-checks/"
        f"{auth_user.id}/2026/01/old.jpg?X-Amz-Expires=3600&X-Amz-Signature=dead"
    )
    db.session.add(PumpCheck(
        user_id=auth_user.id,
        image_key=stale,
        description="Legacy URL row",
        valid=True,
    ))
    db.session.commit()

    item = client.get("/pump-check-gallery/data").get_json()["items"][0]

    assert item["imageUrl"] is None
    assert stale not in str(item)


def test_gallery_page_replaces_failed_photos_without_inline_onerror(client, auth_user):
    html = client.get("/pump-check-gallery").get_data(as_text=True)

    assert "onerror=" not in html
    assert "gallery.photo_unavailable" in html
    assert "data-gallery-photo" in html
    assert "addEventListener('error'" in html or 'addEventListener("error"' in html
