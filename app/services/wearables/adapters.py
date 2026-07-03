import os
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import current_app

from app.models import WearableActivityLog, WearableSleepLog, WearableWorkoutLog
from app.services.wearables.tokens import get_wearable_connection, save_wearable_tokens
from app.timeutil import app_date_of, utc_day_bounds


class WearableError(RuntimeError):
    pass


def _utcnow():
    return datetime.utcnow()


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # tz-aware ise UTC'ye ÇEVİRİP naive'e indir; düz .replace(tzinfo=None) bir
    # +03:00 damgasını 3 saat kaydırıp saklardı (B12). tz-naive ise olduğu gibi.
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _minutes_between(start, end):
    if not start or not end:
        return None
    return max((end - start).total_seconds() / 60.0, 0)


def _ms_to_min(value):
    try:
        return round(float(value) / 60000.0, 2)
    except (TypeError, ValueError):
        return None


def _num(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_rollup(payload):
    rows = payload.get("rollupDataPoints") or payload.get("dataPoints") or []
    return rows[0] if rows else {}


class BaseWearableAdapter:
    provider = ""
    auth_url = ""
    token_url = ""
    api_base = ""
    scopes = ()
    env_prefix = ""

    def client_id(self):
        return os.environ.get(f"{self.env_prefix}_CLIENT_ID", "").strip()

    def client_secret(self):
        return os.environ.get(f"{self.env_prefix}_CLIENT_SECRET", "").strip()

    def redirect_uri(self):
        return os.environ.get(f"{self.env_prefix}_REDIRECT_URI", "").strip()

    def require_credentials(self):
        if not self.client_id() or not self.client_secret() or not self.redirect_uri():
            raise WearableError(f"{self.env_prefix}_CLIENT_ID/SECRET/REDIRECT_URI not set")

    def authorization_url(self, state=None):
        self.require_credentials()
        state = state or secrets.token_urlsafe(32)
        params = {
            "client_id": self.client_id(),
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
        }
        params.update(self.extra_auth_params())
        return f"{self.auth_url}?{urlencode(params)}", state

    def extra_auth_params(self):
        return {}

    def exchange_code(self, code):
        self.require_credentials()
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri(),
                "client_id": self.client_id(),
                "client_secret": self.client_secret(),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("access_token"):
            raise WearableError(f"{self.provider} token response missing access_token")
        return data

    def refresh_access_token(self, user_id, force=False):
        conn = get_wearable_connection(user_id, self.provider)
        if conn is None:
            raise WearableError(f"{self.provider} is not connected")
        if not force and conn.token_expiry and conn.token_expiry > _utcnow() + timedelta(minutes=5):
            return conn.access_token
        # B22: expiry bilinmiyor (None) ama geçerli olabilecek bir erişim token'ı
        # var → her istekte gereksiz yenileme yapma; onu kullan. Gerçekten
        # geçersizse çağıran (_authed_json) 401'de force=True ile yeniler.
        if not force and not conn.token_expiry and conn.access_token:
            return conn.access_token
        if not conn.refresh_token:
            raise WearableError(f"{self.provider} refresh_token missing")

        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": conn.refresh_token,
                "client_id": self.client_id(),
                "client_secret": self.client_secret(),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("access_token"):
            raise WearableError(f"{self.provider} refresh response missing access_token")
        save_wearable_tokens(user_id, self.provider, data)
        return data["access_token"]

    def _authed_json(self, method, url, user_id, access_token=None, **kwargs):
        """Bearer'lı HTTP çağrısı + 401→zorla-yenile→tek retry, JSON döndürür.

        access_token AÇIKÇA verildiyse (örn. testler/tek-seferlik) doğrudan
        kullanılır (yenileme/retry yok). Aksi halde token refresh_access_token'dan
        gelir ve 401'de force refresh ile bir kez yeniden denenir. Tüm dış çağrılar
        (WHOOP GET + Google GET/POST) bu tek kapıdan geçer ki bayat token sync'i
        düşürmesin (B3)."""
        http = requests.get if method.upper() == "GET" else requests.post
        if access_token is not None:
            headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {access_token}"}
            resp = http(url, headers=headers, timeout=10, **kwargs)
            resp.raise_for_status()
            return resp.json()
        headers = {**kwargs.pop("headers", {})}
        token = self.refresh_access_token(user_id)
        headers["Authorization"] = f"Bearer {token}"
        resp = http(url, headers=headers, timeout=10, **kwargs)
        if resp.status_code == 401:
            token = self.refresh_access_token(user_id, force=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = http(url, headers=headers, timeout=10, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def request(self, endpoint, user_id, params=None):
        url = endpoint if endpoint.startswith("http") else f"{self.api_base}{endpoint}"
        return self._authed_json("GET", url, user_id, params=params or {})

    def fetch_activity(self, user_id, target_date, access_token=None, workouts=None):
        raise NotImplementedError

    def fetch_sleep(self, user_id, target_date, access_token=None):
        raise NotImplementedError

    def fetch_workouts(self, user_id, target_date, access_token=None):
        return []


class WhoopAdapter(BaseWearableAdapter):
    provider = "whoop"
    env_prefix = "WHOOP"
    auth_url = "https://api.prod.whoop.com/oauth/oauth2/auth"
    token_url = "https://api.prod.whoop.com/oauth/oauth2/token"
    api_base = "https://api.prod.whoop.com"
    scopes = (
        "read:profile", "read:body_measurement", "read:recovery",
        "read:sleep", "read:workout", "read:cycles",
    )
    resource_endpoints = {
        "profile": "/developer/v2/user/profile/basic",
        "body": "/developer/v2/user/measurement/body",
        "recovery": "/developer/v2/recovery",
        "sleep": "/developer/v2/activity/sleep",
        "workout": "/developer/v2/activity/workout",
    }

    def fetch_sleep(self, user_id, target_date, access_token=None):
        payload = self.request(self.resource_endpoints["sleep"], user_id, params=self._range_params(target_date))
        sleeps = []
        for idx, item in enumerate(payload.get("records") or []):
            start = _parse_dt(item.get("start"))
            end = _parse_dt(item.get("end"))
            score = item.get("score") or {}
            stages = score.get("stage_summary") or {}
            sleeps.append(WearableSleepLog(
                user_id=user_id, provider=self.provider,
                source_id=str(item.get("id") or item.get("sleep_id") or f"whoop-sleep-{target_date.isoformat()}-{idx}"),
                date_key=target_date.isoformat(),
                start_at=start, end_at=end,
                duration_min=_ms_to_min(stages.get("total_in_bed_time_milli")) or _minutes_between(start, end),
                awake_min=_ms_to_min(stages.get("total_awake_time_milli")),
                light_sleep_min=_ms_to_min(stages.get("total_light_sleep_time_milli")),
                deep_sleep_min=_ms_to_min(stages.get("total_slow_wave_sleep_time_milli")),
                rem_sleep_min=_ms_to_min(stages.get("total_rem_sleep_time_milli")),
                sleep_score=score.get("sleep_performance_percentage"),
                sleep_efficiency=score.get("sleep_efficiency_percentage"),
                raw_payload=item,
            ))
        return sleeps

    def fetch_workouts(self, user_id, target_date, access_token=None):
        payload = self.request(self.resource_endpoints["workout"], user_id, params=self._range_params(target_date))
        workouts = []
        for idx, item in enumerate(payload.get("records") or []):
            start = _parse_dt(item.get("start"))
            end = _parse_dt(item.get("end"))
            score = item.get("score") or {}
            workouts.append(WearableWorkoutLog(
                user_id=user_id, provider=self.provider,
                source_id=str(item.get("id") or item.get("workout_id") or f"whoop-workout-{target_date.isoformat()}-{idx}"),
                date_key=target_date.isoformat(),
                sport_name=item.get("sport_name") or item.get("sport_id"),
                start_at=start, end_at=end,
                calories_burned=round(_num(score.get("kilojoule")) * 0.239005736, 1),
                strain=score.get("strain"),
                average_heart_rate=score.get("average_heart_rate"),
                max_heart_rate=score.get("max_heart_rate"),
                distance_km=round(_num(score.get("distance_meter")) / 1000.0, 2) if score.get("distance_meter") is not None else None,
                raw_payload=item,
            ))
        return workouts

    def fetch_activity(self, user_id, target_date, access_token=None, workouts=None):
        # B10: sync zaten fetch_workouts'u çağırıp listeyi geçirir → WHOOP
        # workout uç noktasını sync başına iki kez çağırıp rate-limit'i boşa
        # harcamayalım. Doğrudan çağrıda (liste verilmezse) eskisi gibi çek.
        if workouts is None:
            workouts = self.fetch_workouts(user_id, target_date, access_token)
        recovery = self.request(self.resource_endpoints["recovery"], user_id, params=self._range_params(target_date))
        score = {}
        records = recovery.get("records") or []
        if records:
            score = records[0].get("score") or {}
        return WearableActivityLog(
            user_id=user_id, provider=self.provider, date_key=target_date.isoformat(),
            source_id=f"whoop-{target_date.isoformat()}",
            steps=0,
            active_minutes=round(sum(_minutes_between(w.start_at, w.end_at) or 0 for w in workouts), 1),
            calories_burned=round(sum(w.calories_burned or 0 for w in workouts), 1),
            resting_heart_rate=score.get("resting_heart_rate"),
            hrv_rmssd=score.get("hrv_rmssd_milli"),
            raw_payload={"recovery": recovery, "workout_count": len(workouts)},
        )

    def _range_params(self, target_date):
        start, end = utc_day_bounds(target_date)
        return {
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
        }


class GoogleHealthAdapter(BaseWearableAdapter):
    provider = "google_health"
    env_prefix = "GOOGLE_HEALTH"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    api_base = "https://healthdata.googleapis.com"
    scopes = (
        "https://www.googleapis.com/auth/googlehealth.profile.readonly",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
        "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    )

    def extra_auth_params(self):
        return {"access_type": "offline", "prompt": "consent"}

    def fetch_activity(self, user_id, target_date, access_token=None, workouts=None):
        start, end = utc_day_bounds(target_date)
        base = f"{self.api_base}/v1/users/me/dataTypes"

        def rollup(data_type):
            # B3: doğrudan requests.post yerine _authed_json → 401'de token
            # yenilenip yeniden denenir (bayat Google token'ı sync'i düşürmesin).
            data = self._authed_json(
                "POST", f"{base}/{data_type}/dailyRollup", user_id,
                access_token=access_token,
                json={"startTime": start.isoformat() + "Z", "endTime": end.isoformat() + "Z"},
            )
            return _first_rollup(data)

        steps = rollup("steps").get("steps", {}).get("countSum", 0)
        active = rollup("active-minutes").get("activeMinutes", {}).get("durationSumMillis", 0)
        energy = rollup("active-energy-burned").get("activeEnergyBurned", {}).get("energy", {}).get("kilocalories", 0)
        rhr = rollup("daily-resting-heart-rate").get("dailyRestingHeartRate", {}).get("beatsPerMinute")
        hrv = rollup("daily-heart-rate-variability").get("dailyHeartRateVariability", {}).get("rmssdMillis")
        return WearableActivityLog(
            user_id=user_id, provider=self.provider, date_key=target_date.isoformat(),
            source_id=f"google-health-{target_date.isoformat()}",
            steps=int(_num(steps)),
            active_minutes=round(_num(active) / 60000.0, 1),
            calories_burned=round(_num(energy), 1),
            resting_heart_rate=rhr,
            hrv_rmssd=hrv,
            raw_payload={"source": "google_health_daily_rollup"},
        )

    def fetch_sleep(self, user_id, target_date, access_token=None):
        start, end = utc_day_bounds(target_date)
        # B3: _authed_json ile 401→yenile→retry (doğrudan requests.get değil).
        data = self._authed_json(
            "GET", f"{self.api_base}/v1/users/me/dataTypes/sleep/dataPoints", user_id,
            access_token=access_token,
            params={"startTime": start.isoformat() + "Z", "endTime": end.isoformat() + "Z"},
        )
        sleeps = []
        for idx, item in enumerate(data.get("dataPoints") or []):
            sleep = item.get("sleep") or {}
            interval = sleep.get("interval") or {}
            summary = sleep.get("summary") or {}
            stages = {str(s.get("type", "")).upper(): _num(s.get("minutes")) for s in summary.get("stagesSummary") or []}
            start_at = _parse_dt(interval.get("startTime"))
            end_at = _parse_dt(interval.get("endTime"))
            sleeps.append(WearableSleepLog(
                user_id=user_id, provider=self.provider,
                source_id=str(item.get("name") or f"google-sleep-{target_date.isoformat()}-{idx}"),
                date_key=target_date.isoformat(),
                start_at=start_at, end_at=end_at,
                duration_min=_num(summary.get("minutesAsleep")) or _minutes_between(start_at, end_at),
                awake_min=_num(summary.get("minutesAwake")),
                light_sleep_min=stages.get("LIGHT"),
                deep_sleep_min=stages.get("DEEP"),
                rem_sleep_min=stages.get("REM"),
                raw_payload=item,
            ))
        return sleeps


ADAPTERS = {
    "whoop": WhoopAdapter,
    "google_health": GoogleHealthAdapter,
}


def get_adapter(provider):
    cls = ADAPTERS.get((provider or "").strip().lower())
    if cls is None:
        raise WearableError(f"unsupported wearable provider: {provider}")
    return cls()
