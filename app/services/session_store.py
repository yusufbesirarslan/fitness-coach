# app/services/session_store.py
"""CognitoSession token deposu — Fernet şifreleme + süre-dolumunda yenileme.

Ham access/refresh token'lar ASLA düz metin saklanmaz/loglanmaz. Anahtar
COGNITO_TOKEN_ENC_KEY (geçerli Fernet anahtarı) olmalı; yalnız dev/test'te
SECRET_KEY'den deterministik türetmeye düşülür (S2 — wearable anahtarıyla
aynı kural: SECRET_KEY oturumları da imzalar, sızarsa DB'deki OAuth
token'ları da çözmemeli).
"""
import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from app.config import (COGNITO_REFRESH_SKEW_SECONDS,
                        COGNITO_SESSION_ABSOLUTE_DAYS,
                        COGNITO_SESSION_IDLE_HOURS,
                        COGNITO_TOKEN_ENC_KEY)
from app.extensions import db
from app.models import CognitoSession
from app.services import cognito_service

_logger = logging.getLogger(__name__)
_fernet = None


class SessionInvalid(Exception):
    """Oturum KESİN olarak geçersiz — satır silinir, kullanıcı çıkışa düşer."""
    pass


class SessionTransient(Exception):
    """Cognito GEÇİCİ olarak ulaşılamadı — oturum SAĞLAM, satır KORUNUR.

    H1: refresh yolundaki her hata "bu oturum ölü" demek DEĞİLDİR. Throttle
    (TooManyRequests), Cognito iç hatası veya bir ağ/timeout kesintisi geçicidir;
    bunlarda satırı silmek kullanıcıyı geri dönüşsüz olarak dışarı atar. Access
    token'lar ~1 saatte bir yenilendiği için bu, tek bir Cognito throttle
    olayında KORELE bir toplu logout üretir. Çağıran (require_auth) bunu 503 +
    Retry-After'a çevirir; kullanıcı oturumunu KAYBETMEZ.
    """
    pass


# Cognito hata kodu → GEÇİCİ mi? Boş kod (_wrap'in "beklenmeyen hata" dalı) tam
# olarak botocore connect/read timeout ve ağ kesintilerinin düştüğü yerdir —
# geçici sayılır. Listede olmayan her kod (NotAuthorizedException,
# UserNotFoundException, RefreshFailed, ...) KESİN reddir: satır silinir.
_TRANSIENT_COGNITO_CODES = frozenset({
    "",  # kodsuz: ağ/timeout/beklenmeyen — bkz. cognito_service._wrap
    "InternalErrorException",
    "LimitExceededException",
    "ServiceUnavailableException",
    "TooManyRequestsException",
})


def _is_transient(exc):
    return (getattr(exc, "code", "") or "") in _TRANSIENT_COGNITO_CODES


def _get_fernet():
    global _fernet
    if _fernet is None:
        if COGNITO_TOKEN_ENC_KEY:
            key = COGNITO_TOKEN_ENC_KEY.encode()
        else:
            from flask import current_app
            is_dev = (current_app.config.get("TESTING") or current_app.debug
                      or os.environ.get("FLASK_ENV") == "development")
            if not is_dev:
                # Asıl kapı boot'tadır (config._enforce_cognito_token_key);
                # bu, o kapı atlanırsa prod'da sessiz SECRET_KEY türetmesini
                # kesen ikinci savunma hattı.
                raise RuntimeError(
                    "COGNITO_TOKEN_ENC_KEY must be set outside debug/test "
                    "environments (see wearables/crypto.py precedent)")
            secret = current_app.config["SECRET_KEY"].encode()
            key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        _fernet = Fernet(key)
    return _fernet


def encrypt_token(value):
    return _get_fernet().encrypt((value or "").encode()).decode()


def decrypt_token(value):
    return _get_fernet().decrypt(value.encode()).decode()


def _enc(value):
    return encrypt_token(value)


def _dec(value):
    return decrypt_token(value)


def create(user, tokens, cognito_username):
    sid = secrets.token_urlsafe(32)
    exp = datetime.utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    row = CognitoSession(
        session_id=sid, user_id=user.id, cognito_username=cognito_username,
        access_token=_enc(tokens["access_token"]),
        refresh_token=_enc(tokens["refresh_token"]),
        access_token_exp=exp,
    )
    db.session.add(row)
    db.session.commit()
    return sid


def get(session_id):
    if not session_id:
        return None
    return CognitoSession.query.filter_by(session_id=session_id).first()


def current_access_token(session_id):
    row = get(session_id)
    return _dec(row.access_token) if row else None


@dataclass(frozen=True)
class _RefreshSnapshot:
    """Detached scalars of the row a refresh decision was made from (F12).

    Only authentication material identifies "the same state": the ciphertexts
    and the access expiry. Fernet ciphertext is randomized, so any rewrite of a
    token column changes it even when the plaintext is identical — which is
    what makes this comparison sufficient without a version column.
    `last_used_at` is deliberately absent: `touch()` bumps it on every request,
    and activity telemetry must not turn into refresh conflicts.
    """
    row_id: int
    session_id: str
    user_id: int
    cognito_username: str
    encrypted_access_token: str
    encrypted_refresh_token: str
    access_token_exp: datetime


def _check_timeouts(row, now):
    """Delete the row and raise if it outlived its absolute or idle deadline."""
    absolute_deadline = timedelta(days=COGNITO_SESSION_ABSOLUTE_DAYS)
    if row.created_at and now - row.created_at > absolute_deadline:
        _delete_row_and_commit(row)
        raise SessionInvalid("absolute_timeout")
    idle_deadline = timedelta(hours=COGNITO_SESSION_IDLE_HOURS)
    if row.last_used_at and now - row.last_used_at > idle_deadline:
        _delete_row_and_commit(row)
        raise SessionInvalid("idle_timeout")


def _delete_row_and_commit(row):
    db.session.delete(row)
    db.session.commit()


def _is_fresh(access_token_exp, now):
    skew = timedelta(seconds=COGNITO_REFRESH_SKEW_SECONDS)
    return bool(access_token_exp) and (access_token_exp - now) > skew


def get_valid_access_token(session_id, expected_user_id=None):
    """Return a currently valid provider access token for one web session.

    Fresh token: one read, no lock, no write (the hot path).

    Refresh (F12) runs in three phases so Cognito latency never pins a
    transaction, a pooled connection or a row lock:
      1. a plain read → detached `_RefreshSnapshot` → transaction ended;
      2. `cognito_service.refresh_tokens` with NO database transaction open;
      3. a short `SELECT ... FOR UPDATE` that re-validates the row against the
         snapshot before anything is written or deleted.
    A missing row in phase 3 is a committed logout/credential revocation and
    always wins: nothing is re-inserted. A row whose auth material changed was
    advanced by a concurrent winner: its token is returned and the winner is
    never overwritten or deleted. Duplicate provider calls stay possible (two
    requests can snapshot the same expired row); only one result survives.
    """
    row = get(session_id)
    if not row:
        raise SessionInvalid("no_session")
    if expected_user_id is not None and row.user_id != expected_user_id:
        delete(session_id)
        raise SessionInvalid("user_mismatch")
    now = datetime.utcnow()
    _check_timeouts(row, now)
    if _is_fresh(row.access_token_exp, now):
        return _dec(row.access_token)
    # süresi dolmuş / dolmak üzere → yenile
    snapshot = _RefreshSnapshot(
        row_id=row.id,
        session_id=row.session_id,
        user_id=row.user_id,
        cognito_username=row.cognito_username,
        encrypted_access_token=row.access_token,
        encrypted_refresh_token=row.refresh_token,
        access_token_exp=row.access_token_exp,
    )
    # Phase 1 ends here: the read transaction is released before any network
    # I/O, and the ORM row is no longer an authority for anything below.
    db.session.rollback()
    refresh_token = _dec(snapshot.encrypted_refresh_token)
    if db.session().in_transaction():
        raise RuntimeError(
            "web session refresh attempted during database transaction")
    try:
        refreshed = cognito_service.refresh_tokens(
            refresh_token, snapshot.cognito_username)
    except cognito_service.CognitoServiceError as e:
        # H1: geçici Cognito kesintisi oturumu ÖLDÜRMEZ. Yalnızca KESİN ret
        # (NotAuthorized = refresh token iptal/süresi dolmuş) satırı siler —
        # ve yalnız ret hâlâ GÜNCEL satırı tarif ediyorsa (F12).
        if _is_transient(e):
            _logger.warning(
                "[SESSION] Cognito geçici olarak ulaşılamadı (%s) — oturum korunuyor",
                e.code or type(e).__name__)
            raise SessionTransient("cognito_unavailable")
        return _reconcile(snapshot, expected_user_id, refresh_token, None)
    return _reconcile(snapshot, expected_user_id, refresh_token, refreshed)


def _reconcile(snapshot, expected_user_id, refresh_token, refreshed):
    """Phase 3: re-lock the row and apply `refreshed` (None = definitive reject).

    One short transaction; every exit commits or rolls back, so the row lock
    never outlives this call.
    """
    try:
        now = datetime.utcnow()
        row = (CognitoSession.query
               .filter_by(id=snapshot.row_id)
               .populate_existing()
               .with_for_update()
               .one_or_none())
        if (row is None or row.session_id != snapshot.session_id
                or row.user_id != snapshot.user_id
                or (expected_user_id is not None
                    and row.user_id != expected_user_id)):
            # Deleted by logout / credential revocation while the provider
            # call was in flight: revocation wins, nothing is recreated.
            db.session.rollback()
            _logger.info("[SESSION] refresh reconciled against a revoked session")
            raise SessionInvalid("no_session")
        # A fresh clock: a session must not outlive its deadlines just because
        # the provider answered late.
        _check_timeouts(row, now)
        if (row.access_token != snapshot.encrypted_access_token
                or row.refresh_token != snapshot.encrypted_refresh_token
                or row.access_token_exp != snapshot.access_token_exp):
            # A concurrent request already advanced this session. Its state is
            # authoritative: never overwrite it with ours, and never delete it
            # on a rejection that described the older state.
            if _is_fresh(row.access_token_exp, now):
                winner_access = _dec(row.access_token)
                db.session.rollback()
                return winner_access
            db.session.rollback()
            _logger.warning("[SESSION] refresh conflict — oturum korunuyor")
            raise SessionTransient("refresh_conflict")
        if refreshed is None:
            _delete_row_and_commit(row)
            raise SessionInvalid("refresh_failed")
        row.access_token = _enc(refreshed["access_token"])
        rotated = refreshed.get("refresh_token")
        if rotated and rotated != refresh_token:
            # The provider rotated the refresh token: it lands in the same
            # commit as the access token it belongs to, never one without the
            # other. An unrotated token keeps its ciphertext byte-for-byte.
            row.refresh_token = _enc(rotated)
        row.access_token_exp = now + timedelta(
            seconds=int(refreshed.get("expires_in", 3600)))
        db.session.commit()
        return refreshed["access_token"]
    except (SessionInvalid, SessionTransient):
        raise
    except Exception:
        db.session.rollback()
        raise


def touch(session_id):
    """Best-effort activity stamp (F12): one UPDATE, so it never resurrects a
    deleted row, never rewrites auth material and never fails when the row is
    already gone."""
    if not session_id:
        return
    (CognitoSession.query
     .filter_by(session_id=session_id)
     .update({CognitoSession.last_used_at: datetime.utcnow()},
             synchronize_session=False))
    db.session.commit()


def delete(session_id):
    row = get(session_id)
    if row:
        db.session.delete(row)
        db.session.commit()


def delete_for_user(user_id):
    """Delete every application-managed Cognito session for one local user."""
    removed = (CognitoSession.query
               .filter_by(user_id=user_id)
               .delete(synchronize_session=False))
    db.session.commit()
    return removed


def provider_refresh_tokens_for_user(user_id):
    """Return the decryptable provider refresh tokens of one user's web sessions.

    Read BEFORE `delete_for_user` so a credential change can revoke the provider
    material it is about to drop: deleting the row ends the session for THIS app
    (require_auth needs the row) but leaves the refresh token itself usable at
    Cognito, which `ConfirmForgotPassword` does not revoke.

    Rows whose ciphertext no longer decrypts (rotated COGNITO_TOKEN_ENC_KEY,
    corrupted value) are skipped rather than raised on: there is nothing
    revocable in them, and a credential change must not fail over one.
    """
    tokens = []
    rows = (db.session.query(CognitoSession.refresh_token)
            .filter_by(user_id=user_id)
            .all())
    for (ciphertext,) in rows:
        if not ciphertext:
            continue
        try:
            token = _dec(ciphertext)
        except Exception:
            _logger.warning(
                "[SESSION] refresh token ciphertext unreadable — atlandı (user=%s)",
                user_id)
            continue
        if token:
            tokens.append(token)
    return tokens


# Cognito refresh token'ının varsayılan geçerliliği 30 gündür; bu kadar süre
# dokunulmamış bir oturum zaten yenilenemez — satırı tutmanın tek etkisi
# tablonun sınırsız büyümesi ve süresi geçmiş şifreli token saklamaktır (I5).
PURGE_AFTER_DAYS = 30


def purge_expired(older_than_days=PURGE_AFTER_DAYS):
    """last_used_at'i eşikten eski oturum satırlarını sil; silinen sayıyı döndür.

    Satırlar normalde yalnız logout/refresh-hatasında silinir; sessiz terk
    edilen oturumlar için periyodik süpürme gerekir (weekly-reset CLI çağırır).
    """
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    removed = (CognitoSession.query
               .filter(CognitoSession.last_used_at < cutoff)
               .delete(synchronize_session=False))
    db.session.commit()
    if removed:
        _logger.info("[SESSION] %d süresi geçmiş Cognito oturumu silindi", removed)
    return removed
