"""Mobile diary slot mutation and reconciliation-based hard delete contract."""
from datetime import date, datetime
import logging
import re
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog, MealPhotoCleanup
from app.services import mobile_auth
from app.services.mobile_nutrition.serialization import SLOT_BY_MEAL_LABEL
from app.timeutil import APP_TZ, audit_clock


FIXED_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=APP_TZ)
FIXED_DAY = date(2026, 8, 11)
DIARY_PATH = "/api/v1/nutrition/diary/today"
MEAL_LABELS = {slot: label for label, slot in SLOT_BY_MEAL_LABEL.items()}


@pytest.fixture(autouse=True)
def fixed_diary_clock():
    with audit_clock(FIXED_NOW):
        yield


@pytest.fixture
def mobile_user(make_user):
    return make_user("mobile-diary-mutation")


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user, revision=None):
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        headers = {"Authorization": "Bearer opaque-access-credential"}
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        return headers
    return _headers


def log_meal(user, *, slot="kahvalti", description="Yulaf", calories=320.0):
    row = MealLog(
        user_id=user.id,
        ogun=MEAL_LABELS[slot],
        yemekler=description,
        kalori=calories,
        protein=12.0,
        karb=40.0,
        yag=9.0,
        tarih=FIXED_DAY.isoformat(),
        source="manual",
        created_at=datetime(2026, 8, 11, 5, 24),
    )
    db.session.add(row)
    db.session.commit()
    return row


def diary(client, headers):
    with audit_clock(FIXED_NOW):
        return client.get(DIARY_PATH, headers=headers)


def mutation_target(client, headers):
    meal = diary(client, headers).json["meals"][0]
    return f"/api/v1/nutrition/logs/{meal['id']}", meal


def patch_slot(client, path, headers, slot="ogle", **extra):
    return client.patch(
        path,
        headers=headers,
        json={"operation": "set_slot", "slot": slot, **extra},
    )


def test_slot_move_returns_the_canonical_entry_and_preserves_totals(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    auth = as_mobile(mobile_user)
    path, before = mutation_target(raw_client, auth)

    response = patch_slot(
        raw_client, path, as_mobile(mobile_user, before["revision"]))

    assert response.status_code == 200
    after = response.json["meal"]
    assert after["id"] == before["id"]
    assert after["revision"] != before["revision"]
    assert after["slot"] == "ogle"
    reconciled = diary(raw_client, as_mobile(mobile_user)).json
    assert reconciled["meals"] == [after]
    assert reconciled["totals"] == {
        "energy_kcal": 320.0,
        "protein_g": 12.0,
        "carbohydrate_g": 40.0,
        "fat_g": 9.0,
    }


def test_missing_malformed_and_stale_preconditions_are_distinct(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    auth = as_mobile(mobile_user)
    path, meal = mutation_target(raw_client, auth)

    missing = patch_slot(raw_client, path, auth)
    malformed_headers = as_mobile(mobile_user)
    malformed_headers["If-Match"] = meal["revision"]
    malformed = patch_slot(raw_client, path, malformed_headers)
    stale = patch_slot(
        raw_client, path, as_mobile(mobile_user, "x" * 24))

    assert (missing.status_code, missing.json["error"]["code"]) == (
        428, "DIARY_PRECONDITION_REQUIRED")
    assert (malformed.status_code, malformed.json["error"]["code"]) == (
        400, "INVALID_DIARY_PRECONDITION")
    assert (stale.status_code, stale.json["error"]["code"]) == (
        412, "STALE_DIARY_ENTRY")


def test_old_revision_fails_and_returned_revision_can_move_again(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    path, initial = mutation_target(raw_client, as_mobile(mobile_user))
    first = patch_slot(
        raw_client, path, as_mobile(mobile_user, initial["revision"]), "ogle")

    stale = patch_slot(
        raw_client, path, as_mobile(mobile_user, initial["revision"]), "aksam")
    fresh = patch_slot(
        raw_client, path,
        as_mobile(mobile_user, first.json["meal"]["revision"]), "aksam")

    assert stale.status_code == 412
    assert fresh.status_code == 200
    assert fresh.json["meal"]["slot"] == "aksam"


def test_setting_the_current_slot_is_idempotent_without_revision_churn(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    path, before = mutation_target(raw_client, as_mobile(mobile_user))

    response = patch_slot(
        raw_client, path, as_mobile(mobile_user, before["revision"]),
        "kahvalti")

    assert response.status_code == 200
    assert response.json["meal"] == before


@pytest.mark.parametrize("field", [
    "description", "nutrition", "quantity", "serving_id", "food_id",
    "provider", "user_id", "day", "timestamp", "source", "fingerprint",
])
def test_unsupported_or_protected_fields_fail_without_mutation(
        raw_client, as_mobile, mobile_user, field):
    log_meal(mobile_user)
    path, before = mutation_target(raw_client, as_mobile(mobile_user))

    response = patch_slot(
        raw_client, path, as_mobile(mobile_user, before["revision"]),
        **{field: "forbidden"})

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_DIARY_MUTATION"
    assert diary(raw_client, as_mobile(mobile_user)).json["meals"][0] == before


def test_cross_user_and_malformed_entry_ids_share_private_not_found(
        raw_client, as_mobile, mobile_user, make_user):
    other = make_user("mobile-diary-other")
    log_meal(other, description="Other meal")
    other_path, other_meal = mutation_target(raw_client, as_mobile(other))

    cross_user = patch_slot(
        raw_client, other_path,
        as_mobile(mobile_user, other_meal["revision"]))
    malformed = patch_slot(
        raw_client, "/api/v1/nutrition/logs/not-a-valid-id",
        as_mobile(mobile_user, other_meal["revision"]))

    for response in (cross_user, malformed):
        assert response.status_code == 404
        assert response.json["error"]["code"] == "DIARY_ENTRY_NOT_FOUND"


def test_web_cookie_without_bearer_cannot_mutate(client, auth_user):
    row = log_meal(auth_user)
    response = client.patch(
        "/api/v1/nutrition/logs/not-a-valid-id",
        headers={"If-Match": '"' + "x" * 24 + '"'},
        json={"operation": "set_slot", "slot": "ogle"},
    )
    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert db.session.get(MealLog, row.id).ogun == MEAL_LABELS["kahvalti"]


def test_delete_removes_entry_and_server_totals_reconcile(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user, calories=320.0)
    log_meal(
        mobile_user, slot="ogle", description="Lunch", calories=500.0)
    path, target = mutation_target(raw_client, as_mobile(mobile_user))

    response = raw_client.delete(
        path, headers=as_mobile(mobile_user, target["revision"]))

    assert response.status_code == 204
    assert response.data == b""
    reconciled = diary(raw_client, as_mobile(mobile_user)).json
    assert [meal["description"] for meal in reconciled["meals"]] == ["Lunch"]
    assert reconciled["totals"]["energy_kcal"] == 500.0


def test_stale_delete_fails_and_second_delete_is_private_not_found(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    path, target = mutation_target(raw_client, as_mobile(mobile_user))

    stale = raw_client.delete(
        path, headers=as_mobile(mobile_user, "x" * 24))
    first = raw_client.delete(
        path, headers=as_mobile(mobile_user, target["revision"]))
    second = raw_client.delete(
        path, headers=as_mobile(mobile_user, target["revision"]))

    assert (stale.status_code, stale.json["error"]["code"]) == (
        412, "STALE_DIARY_ENTRY")
    assert first.status_code == 204
    assert (second.status_code, second.json["error"]["code"]) == (
        404, "DIARY_ENTRY_NOT_FOUND")
    assert diary(raw_client, as_mobile(mobile_user)).json["meals"] == []


def test_mobile_http_retry_converges_after_post_commit_photo_failure(
        raw_client, as_mobile, mobile_user, monkeypatch):
    """The published 503 retryable=True envelope is now a true claim.

    A post-commit S3 failure used to strand the next DELETE on a permanent
    404. The same request must instead finish the pending release.
    """
    import s3_helper

    key = f"meals/{mobile_user.id}/2026/08/{'ab' * 16}.jpg"
    row = log_meal(mobile_user, description="Fotoğraflı")
    row.photo_key = key
    db.session.commit()

    class FakeS3Client:
        def __init__(self):
            self.deleted = []
            self.delete_error = None

        def delete_object(self, **kwargs):
            self.deleted.append(kwargs)
            if self.delete_error is not None:
                raise self.delete_error
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    fake = FakeS3Client()
    monkeypatch.setattr(s3_helper, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", "axisai-test-bucket")
    monkeypatch.setattr(s3_helper, "_client", fake)

    path, target = mutation_target(raw_client, as_mobile(mobile_user))
    fake.delete_error = s3_helper.BotoCoreError()
    first = raw_client.delete(
        path, headers=as_mobile(mobile_user, target["revision"]))

    assert first.status_code == 503
    assert first.json["error"]["code"] == "NUTRITION_TEMPORARILY_UNAVAILABLE"
    assert first.json["error"]["retryable"] is True
    assert MealLog.query.filter_by(id=row.id).count() == 0
    assert MealPhotoCleanup.query.count() == 1

    fake.delete_error = None
    second = raw_client.delete(
        path, headers=as_mobile(mobile_user, target["revision"]))

    assert second.status_code == 204
    assert MealPhotoCleanup.query.count() == 0
    assert fake.deleted == [
        {"Bucket": "axisai-test-bucket", "Key": key},
        {"Bucket": "axisai-test-bucket", "Key": key},
    ]


def test_delete_rejects_a_body_instead_of_ignoring_it(
        raw_client, as_mobile, mobile_user):
    log_meal(mobile_user)
    path, target = mutation_target(raw_client, as_mobile(mobile_user))

    response = raw_client.delete(
        path,
        headers=as_mobile(mobile_user, target["revision"]),
        json={"user_id": 999},
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_DIARY_MUTATION"
    assert diary(raw_client, as_mobile(mobile_user)).json["meals"] == [target]


def test_unexpected_mutation_failure_is_bounded_and_redacted(
        raw_client, as_mobile, mobile_user, monkeypatch, caplog):
    from app.services import mobile_diary_mutation

    log_meal(mobile_user, description="Private nutrition description")
    path, target = mutation_target(raw_client, as_mobile(mobile_user))

    def fail(*args, **kwargs):
        raise RuntimeError("raw database SELECT meal_log secret detail")

    monkeypatch.setattr(mobile_diary_mutation, "set_slot", fail)
    response = patch_slot(
        raw_client, path, as_mobile(mobile_user, target["revision"]))

    assert response.status_code == 503
    assert response.json["error"] == {
        "code": "NUTRITION_TEMPORARILY_UNAVAILABLE",
        "message": "Nutrition data is temporarily unavailable.",
        "retryable": True,
        "request_id": response.json["error"]["request_id"],
    }
    assert "raw database" not in caplog.text
    assert "Private nutrition description" not in caplog.text
    assert target["id"] not in caplog.text
    assert target["revision"] not in caplog.text


def test_matched_mutation_request_log_is_structured_and_redacted(
        raw_client, as_mobile, mobile_user, caplog):
    entry_token = "opaque-sensitive-diary-item"
    revision = "sensitive-revision-token"
    path = f"/api/v1/nutrition/logs/{entry_token}"
    headers = as_mobile(mobile_user)
    headers["If-Match"] = f'"{revision}"'
    caplog.clear()

    with caplog.at_level(logging.INFO):
        response = raw_client.patch(
            path,
            headers=headers,
            json={"operation": "set_slot", "slot": "ogle"},
        )

    line = next((record.getMessage() for record in caplog.records
                 if "method=PATCH" in record.getMessage()), None)
    assert response.status_code == 404
    assert line is not None
    assert "path=/api/v1/nutrition/logs/<entry_token>" in line
    assert "status=404" in line
    assert "dur_ms=" in line
    assert re.search(r"\bid=[0-9a-f]{16}\b", line)
    assert "user=-" in line
    assert f"user={mobile_user.id}" not in line
    for sensitive in (
        entry_token,
        revision,
        "opaque-access-credential",
        "set_slot",
        "ogle",
    ):
        assert sensitive not in caplog.text
