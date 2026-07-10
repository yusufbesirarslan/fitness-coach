"""Wearable integration tests.

These cover the provider-neutral wearable layer: encrypted token storage,
OAuth routes, provider refresh/retry behavior, normalized sync upserts, and
dashboard activity fallback.
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.timeutil import app_today


def _complete_user(user):
    user.profile_complete = True
    user.weight, user.height, user.age = 80, 180, 30
    user.gender, user.goal = "male", "kilo verme"
    user.current_activity = "active"
    db.session.commit()
    return user


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_token_storage_encrypts_and_upserts(app, auth_user):
    from app.models import UserWearableConnection
    from app.services.wearables.tokens import get_wearable_connection, save_wearable_tokens

    expires_at = datetime.utcnow() + timedelta(hours=1)
    conn = save_wearable_tokens(auth_user.id, "whoop", {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_at": expires_at,
        "scope": "read:profile read:sleep",
    })

    row = db.session.get(UserWearableConnection, conn.id)
    assert row.access_token_encrypted != "access-1"
    assert row.refresh_token_encrypted != "refresh-1"

    loaded = get_wearable_connection(auth_user.id, "whoop")
    assert loaded.access_token == "access-1"
    assert loaded.refresh_token == "refresh-1"
    assert loaded.scopes == "read:profile read:sleep"

    save_wearable_tokens(auth_user.id, "whoop", {
        "access_token": "access-2",
        "expires_in": 7200,
    })
    assert UserWearableConnection.query.filter_by(user_id=auth_user.id, provider="whoop").count() == 1
    loaded = get_wearable_connection(auth_user.id, "whoop")
    assert loaded.access_token == "access-2"
    assert loaded.refresh_token == "refresh-1"


@pytest.mark.parametrize("corrupt_field", ["access_token_encrypted", "refresh_token_encrypted"])
def test_corrupt_wearable_token_requires_reauthentication(app, auth_user, corrupt_field):
    from app.models import UserWearableConnection
    from app.services.wearables.tokens import get_wearable_connection, save_wearable_tokens

    conn = save_wearable_tokens(auth_user.id, "whoop", {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
    })
    setattr(conn, corrupt_field, "not-valid-fernet-ciphertext")
    db.session.commit()

    assert get_wearable_connection(auth_user.id, "whoop") is None

    db.session.expire_all()
    row = db.session.get(UserWearableConnection, conn.id)
    assert row.status == "reauth_required"


def test_wearable_status_exposes_reauthentication_state(client, auth_user):
    from app.services.wearables.tokens import get_wearable_connection, save_wearable_tokens

    conn = save_wearable_tokens(auth_user.id, "whoop", {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
    })
    conn.access_token_encrypted = "not-valid-fernet-ciphertext"
    db.session.commit()
    assert get_wearable_connection(auth_user.id, "whoop") is None

    payload = client.get("/api/wearables/status").get_json()

    assert payload["connections"][0]["status"] == "reauth_required"
    assert payload["connections"][0]["connected"] is False


def test_whoop_refreshes_expiring_token_and_retries_401(app, auth_user, monkeypatch):
    from app.services.wearables import tokens
    from app.services.wearables.adapters import WhoopAdapter

    tokens.save_wearable_tokens(auth_user.id, "whoop", {
        "access_token": "old-access",
        "refresh_token": "refresh-1",
        "expires_at": datetime.utcnow() + timedelta(minutes=1),
    })

    calls = {"post": 0, "get": []}

    def fake_post(url, **kwargs):
        calls["post"] += 1
        return _Resp({"access_token": "new-access", "refresh_token": "refresh-2", "expires_in": 3600})

    def fake_get(url, **kwargs):
        calls["get"].append(kwargs["headers"]["Authorization"])
        if len(calls["get"]) == 1:
            return _Resp({"error": "expired"}, status_code=401)
        return _Resp({"ok": True})

    monkeypatch.setattr("app.services.wearables.adapters.requests.post", fake_post)
    monkeypatch.setattr("app.services.wearables.adapters.requests.get", fake_get)

    body = WhoopAdapter().request("/developer/v2/user/profile/basic", auth_user.id)

    assert body == {"ok": True}
    assert calls["post"] == 2
    assert calls["get"] == ["Bearer new-access", "Bearer new-access"]
    loaded = tokens.get_wearable_connection(auth_user.id, "whoop")
    assert loaded.refresh_token == "refresh-2"


def test_whoop_proxy_forwards_only_resource_allowlisted_params(client, auth_user, monkeypatch):
    from app.blueprints import wearables as wearables_bp

    captured = []

    class _Adapter:
        resource_endpoints = {
            "profile": "/profile",
            "recovery": "/recovery",
        }

        def request(self, endpoint, user_id, params=None):
            captured.append((endpoint, user_id, params))
            return {"ok": True}

    monkeypatch.setattr(wearables_bp, "get_adapter", lambda provider: _Adapter())

    profile = client.get(
        "/api/wearables/whoop/profile?start=accepted-nowhere&limit=25&cursor=injected"
    )
    recovery = client.get(
        "/api/wearables/whoop/recovery?start=2026-07-01&end=2026-07-02"
        "&limit=25&cursor=injected&user_id=999"
    )

    assert profile.status_code == recovery.status_code == 200
    assert captured[0][2] == {}
    assert captured[1][2] == {
        "start": "2026-07-01",
        "end": "2026-07-02",
        "limit": "25",
    }


@pytest.mark.parametrize("query", [
    "limit=0",
    "limit=26",
    "limit=not-an-integer",
    f"start={'x' * 65}",
    f"end={'x' * 65}",
])
def test_whoop_proxy_rejects_invalid_allowlisted_params(
        client, auth_user, monkeypatch, query):
    from app.blueprints import wearables as wearables_bp

    class _Adapter:
        resource_endpoints = {"sleep": "/sleep"}

        def request(self, endpoint, user_id, params=None):
            raise AssertionError("invalid parameters must not reach WHOOP")

    monkeypatch.setattr(wearables_bp, "get_adapter", lambda provider: _Adapter())

    response = client.get(f"/api/wearables/whoop/sleep?{query}")

    assert response.status_code == 400


def test_oauth_routes_validate_state_and_store_tokens(client, auth_user, monkeypatch):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "client-id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("WHOOP_REDIRECT_URI", "http://localhost/auth/callback/whoop")

    token_calls = []

    def fake_post(url, **kwargs):
        token_calls.append(kwargs["data"]["code"])
        return _Resp({"access_token": "tok", "refresh_token": "ref", "expires_in": 3600})

    monkeypatch.setattr("app.services.wearables.adapters.requests.post", fake_post)

    login = client.get("/api/auth/wearable/whoop")
    assert login.status_code == 302
    assert "api.prod.whoop.com/oauth/oauth2/auth" in login.headers["Location"]

    bad = client.get("/api/auth/callback/whoop?code=abc&state=wrong")
    assert bad.status_code == 400

    with client.session_transaction() as sess:
        state = sess["_wearable_oauth_state_whoop"]
    ok = client.get(f"/api/auth/callback/whoop?code=abc&state={state}")
    assert ok.status_code == 302
    assert token_calls == ["abc"]

    status = client.get("/api/wearables/status").get_json()
    assert status["connections"][0]["provider"] == "whoop"
    assert status["connections"][0]["connected"] is True


def test_whoop_sync_upserts_normalized_sleep_workout_and_activity(app, auth_user, monkeypatch):
    from app.models import WearableActivityLog, WearableSleepLog, WearableWorkoutLog
    from app.services.wearables.tokens import save_wearable_tokens
    from app.services.wearables.sync import sync_provider_day

    save_wearable_tokens(auth_user.id, "whoop", {
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_at": datetime.utcnow() + timedelta(hours=1),
    })

    def fake_get(url, **kwargs):
        if url.endswith("/developer/v2/activity/sleep"):
            return _Resp({"records": [{
                "id": "sleep-1",
                "start": "2026-06-30T21:00:00Z",
                "end": "2026-07-01T05:00:00Z",
                "score": {
                    "stage_summary": {
                        "total_in_bed_time_milli": 8 * 60 * 60 * 1000,
                        "total_awake_time_milli": 30 * 60 * 1000,
                        "total_light_sleep_time_milli": 240 * 60 * 1000,
                        "total_slow_wave_sleep_time_milli": 90 * 60 * 1000,
                        "total_rem_sleep_time_milli": 120 * 60 * 1000,
                    },
                    "sleep_performance_percentage": 88,
                    "sleep_efficiency_percentage": 92,
                },
            }]})
        if url.endswith("/developer/v2/activity/workout"):
            return _Resp({"records": [{
                "id": "workout-1",
                "start": "2026-07-01T10:00:00Z",
                "end": "2026-07-01T11:00:00Z",
                "sport_name": "running",
                "score": {
                    "strain": 9.5,
                    "kilojoule": 2100,
                    "average_heart_rate": 130,
                    "max_heart_rate": 170,
                    "distance_meter": 5000,
                },
            }]})
        if url.endswith("/developer/v2/recovery"):
            return _Resp({"records": [{
                "cycle_id": 1,
                "score": {
                    "resting_heart_rate": 55,
                    "hrv_rmssd_milli": 64.5,
                },
            }]})
        return _Resp({})

    monkeypatch.setattr("app.services.wearables.adapters.requests.get", fake_get)

    result = sync_provider_day(auth_user.id, "whoop", app_today())
    again = sync_provider_day(auth_user.id, "whoop", app_today())

    assert result["sleep"] == 1
    assert result["workouts"] == 1
    assert result["activity"] == 1
    assert again == result
    assert WearableSleepLog.query.filter_by(user_id=auth_user.id, provider="whoop").count() == 1
    assert WearableWorkoutLog.query.filter_by(user_id=auth_user.id, provider="whoop").count() == 1
    activity = WearableActivityLog.query.filter_by(user_id=auth_user.id, provider="whoop").one()
    assert activity.calories_burned == 501.9
    assert activity.resting_heart_rate == 55
    assert activity.hrv_rmssd == 64.5


def test_google_health_adapter_normalizes_daily_activity_and_sleep(app, auth_user, monkeypatch):
    from app.services.wearables.adapters import GoogleHealthAdapter

    requests_seen = []

    def fake_post(url, **kwargs):
        requests_seen.append(url)
        if "/steps/" in url:
            return _Resp({"rollupDataPoints": [{"steps": {"countSum": "8000"}}]})
        if "/active-minutes/" in url:
            return _Resp({"rollupDataPoints": [{"activeMinutes": {"durationSumMillis": "2700000"}}]})
        if "/active-energy-burned/" in url:
            return _Resp({"rollupDataPoints": [{"activeEnergyBurned": {"energy": {"kilocalories": 320}}}]})
        if "/daily-resting-heart-rate/" in url:
            return _Resp({"rollupDataPoints": [{"dailyRestingHeartRate": {"beatsPerMinute": 58}}]})
        if "/daily-heart-rate-variability/" in url:
            return _Resp({"rollupDataPoints": [{"dailyHeartRateVariability": {"rmssdMillis": 42}}]})
        return _Resp({})

    def fake_get(url, **kwargs):
        return _Resp({"dataPoints": [{
            "name": "users/me/dataTypes/sleep/dataPoints/sleep-1",
            "sleep": {
                "interval": {"startTime": "2026-06-30T21:00:00Z", "endTime": "2026-07-01T05:00:00Z"},
                "summary": {
                    "minutesAsleep": "420",
                    "minutesAwake": "40",
                    "stagesSummary": [
                        {"type": "LIGHT", "minutes": "230"},
                        {"type": "DEEP", "minutes": "80"},
                        {"type": "REM", "minutes": "110"},
                    ],
                },
            },
        }]})

    monkeypatch.setattr("app.services.wearables.adapters.requests.post", fake_post)
    monkeypatch.setattr("app.services.wearables.adapters.requests.get", fake_get)

    adapter = GoogleHealthAdapter()
    activity = adapter.fetch_activity(auth_user.id, app_today(), "access-token")
    sleeps = adapter.fetch_sleep(auth_user.id, app_today(), "access-token")

    assert activity.steps == 8000
    assert activity.active_minutes == 45
    assert activity.calories_burned == 320
    assert activity.resting_heart_rate == 58
    assert activity.hrv_rmssd == 42
    assert sleeps[0].source_id.endswith("sleep-1")
    assert sleeps[0].duration_min == 420
    assert sleeps[0].deep_sleep_min == 80
    assert len(requests_seen) == 5


def test_today_activity_prefers_wearable_over_manual(client, auth_user):
    from app.models import DailyActivity, WearableActivityLog

    complete_user = _complete_user(auth_user)
    today = app_today().isoformat()
    db.session.add(DailyActivity(user_id=complete_user.id, steps=1000, intensity="moderate",
                                 calories_burned=50, distance_km=0.8, duration_min=10,
                                 date_key=today))
    db.session.add(WearableActivityLog(user_id=complete_user.id, provider="whoop",
                                       date_key=today, steps=9000, active_minutes=55,
                                       calories_burned=430, resting_heart_rate=57,
                                       hrv_rmssd=61))
    db.session.commit()

    body = client.get("/api/activity/today").get_json()

    assert body["source"] == "wearable"
    assert body["provider"] == "whoop"
    assert body["total_steps"] == 9000
    assert body["total_calories"] == 430
