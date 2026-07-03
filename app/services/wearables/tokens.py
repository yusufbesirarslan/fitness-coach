from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import UserWearableConnection
from app.services.wearables.crypto import decrypt_token, encrypt_token


@dataclass
class WearableConnectionView:
    row: UserWearableConnection
    access_token: str | None
    refresh_token: str | None

    def __getattr__(self, name):
        return getattr(self.row, name)


def _coerce_datetime(value):
    """B21: token_expiry DateTime kolonuna ham string/epoch yazılmasın —
    yalnızca datetime döndür (aksi halde None), böylece parse edilebilenler
    dışında çöp değer kolona sızmaz."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Unix epoch (saniye) → naive UTC
        try:
            return datetime.utcfromtimestamp(value)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _expiry_from_payload(token_data):
    expires_at = token_data.get("expires_at") or token_data.get("token_expiry")
    if expires_at:
        coerced = _coerce_datetime(expires_at)
        if coerced is not None:
            return coerced
        # parse edilemedi → expires_in'e düş
    expires_in = token_data.get("expires_in")
    if expires_in is None:
        return None
    try:
        return datetime.utcnow() + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def save_wearable_tokens(user_id, provider, token_data):
    provider = provider.strip().lower()
    row = UserWearableConnection.query.filter_by(
        user_id=user_id, provider=provider
    ).first()
    if row is None:
        row = UserWearableConnection(
            user_id=user_id,
            provider=provider,
            access_token_encrypted=encrypt_token(token_data.get("access_token", "")),
        )
        db.session.add(row)

    if token_data.get("access_token"):
        row.access_token_encrypted = encrypt_token(token_data["access_token"])
    if token_data.get("refresh_token"):
        row.refresh_token_encrypted = encrypt_token(token_data["refresh_token"])
    expiry = _expiry_from_payload(token_data)
    if expiry is not None:
        row.token_expiry = expiry
    row.scopes = token_data.get("scope") or token_data.get("scopes") or row.scopes
    row.external_user_id = token_data.get("user_id") or token_data.get("external_user_id") or row.external_user_id
    row.status = "connected"
    row.updated_at = datetime.utcnow()

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        row = UserWearableConnection.query.filter_by(
            user_id=user_id, provider=provider
        ).one()
        if token_data.get("access_token"):
            row.access_token_encrypted = encrypt_token(token_data["access_token"])
        if token_data.get("refresh_token"):
            row.refresh_token_encrypted = encrypt_token(token_data["refresh_token"])
        if expiry is not None:
            row.token_expiry = expiry
        row.scopes = token_data.get("scope") or token_data.get("scopes") or row.scopes
        row.updated_at = datetime.utcnow()
        db.session.commit()
    return row


def get_wearable_connection(user_id, provider):
    row = UserWearableConnection.query.filter_by(
        user_id=user_id, provider=provider.strip().lower()
    ).first()
    if row is None:
        return None
    return WearableConnectionView(
        row=row,
        access_token=decrypt_token(row.access_token_encrypted),
        refresh_token=decrypt_token(row.refresh_token_encrypted),
    )
