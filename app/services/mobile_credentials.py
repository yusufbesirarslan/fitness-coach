'''Opaque mobile credential generation, hashing, and replay derivation.'''

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_CREDENTIAL_BYTES = 32
_DERIVATION_SALT = b'axisai/mobile-auth/credential-derivation/salt/v1'
_SUBKEY_LABEL = b'axisai/mobile-auth/replacement-subkey/v1'
_ACCESS_LABEL = b'axisai/mobile-auth/access/v1'
_REFRESH_LABEL = b'axisai/mobile-auth/refresh/v1'
_RATE_LIMIT_LABEL = b'axisai/mobile-auth/rate-limit/v1'


class InvalidMobileCredential(ValueError):
    '''The wire value is not a canonical 256-bit mobile credential.'''


class CredentialConfigurationError(RuntimeError):
    '''Mobile credential derivation configuration is unsafe or malformed.'''


@dataclass(frozen=True)
class MobileCredentialPair:
    access: str
    refresh: str


def _wire(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def canonical_credential(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 43:
        raise InvalidMobileCredential('invalid mobile credential')
    try:
        raw = base64.b64decode(value + '=', altchars=b'-_', validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidMobileCredential('invalid mobile credential') from exc
    if len(raw) != _CREDENTIAL_BYTES or not hmac.compare_digest(_wire(raw), value):
        raise InvalidMobileCredential('invalid mobile credential')
    return raw


def generate_credential() -> str:
    return _wire(secrets.token_bytes(_CREDENTIAL_BYTES))


def hash_credential(value: str) -> str:
    return hashlib.sha256(canonical_credential(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CredentialConfigurationError('duplicate derivation key version')
        result[key] = value
    return result


def _canonical_root_key(value: str) -> bytes:
    if not isinstance(value, str) or not value or '=' in value:
        raise InvalidMobileCredential('invalid derivation root key')
    padding = '=' * (-len(value) % 4)
    try:
        raw = base64.b64decode(value + padding, altchars=b'-_', validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidMobileCredential('invalid derivation root key') from exc
    if len(raw) < _CREDENTIAL_BYTES or not hmac.compare_digest(_wire(raw), value):
        raise InvalidMobileCredential('invalid derivation root key')
    return raw


def credential_rate_limit_key(value: str, keyring: dict[str, bytes],
                              key_version: str) -> str:
    raw = canonical_credential(value)
    try:
        root = keyring[key_version]
    except KeyError as exc:
        raise CredentialConfigurationError('derivation key version unavailable') from exc
    digest = hmac.new(
        root, _RATE_LIMIT_LABEL + b'\0' + raw, hashlib.sha256).hexdigest()
    return f'mobile-refresh:{digest[:32]}'


def parse_keyring(raw: str) -> dict[str, bytes]:
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except (TypeError, ValueError) as exc:
        raise CredentialConfigurationError('invalid mobile derivation keyring') from exc
    if not isinstance(data, dict) or not data:
        raise CredentialConfigurationError('invalid mobile derivation keyring')
    parsed = {}
    for version, wire_value in data.items():
        if (not isinstance(version, str)
                or re.fullmatch(r'[A-Za-z0-9._-]{1,32}', version) is None):
            raise CredentialConfigurationError('invalid derivation key version')
        try:
            key = _canonical_root_key(wire_value)
        except InvalidMobileCredential as exc:
            raise CredentialConfigurationError('invalid derivation root key') from exc
        parsed[version] = key
    return parsed


def derive_replacement_pair(parent_refresh: str, family_id: str,
                            parent_generation: int, child_generation: int,
                            key_version: str,
                            keyring: dict[str, bytes]) -> MobileCredentialPair:
    parent = canonical_credential(parent_refresh)
    try:
        root = keyring[key_version]
    except KeyError as exc:
        raise CredentialConfigurationError('derivation key version unavailable') from exc
    family = family_id.encode('ascii')
    info = (_SUBKEY_LABEL + b'\0' + len(family).to_bytes(2, 'big') + family
            + int(parent_generation).to_bytes(8, 'big')
            + int(child_generation).to_bytes(8, 'big'))
    subkey = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=_DERIVATION_SALT, info=info,
    ).derive(root)
    access = hmac.new(
        subkey, _ACCESS_LABEL + b'\0' + parent, hashlib.sha256).digest()
    refresh = hmac.new(
        subkey, _REFRESH_LABEL + b'\0' + parent, hashlib.sha256).digest()
    return MobileCredentialPair(_wire(access), _wire(refresh))


def _integer_setting(name: str, default: int, *, positive: bool,
                     maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise CredentialConfigurationError(f'invalid {name}') from exc
    if ((positive and value <= 0) or (not positive and value < 0)
            or value > maximum):
        raise CredentialConfigurationError(f'invalid {name}')
    return value


def validate_mobile_auth_config(app) -> None:
    access_ttl = _integer_setting('MOBILE_AUTH_ACCESS_TTL_SECONDS', 900,
                                  positive=True, maximum=86400)
    absolute_days = _integer_setting('MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS', 7,
                                     positive=True, maximum=7)
    grace = _integer_setting('MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS', 10,
                             positive=True, maximum=60)
    leeway = _integer_setting('MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS', 60,
                              positive=False, maximum=3600)
    skew = _integer_setting('MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS', 0,
                            positive=False, maximum=300)
    retention = _integer_setting(
        'MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS', 300,
        positive=False, maximum=86400)
    if access_ttl >= absolute_days * 86400:
        raise CredentialConfigurationError('invalid mobile credential lifetimes')
    if grace >= access_ttl:
        raise CredentialConfigurationError('invalid mobile refresh retry grace')

    keyring = parse_keyring(os.environ.get('MOBILE_AUTH_DERIVATION_KEYRING', ''))
    active_version = os.environ.get(
        'MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION', '').strip()
    if active_version not in keyring:
        raise CredentialConfigurationError('active derivation key unavailable')

    app.config.update(
        MOBILE_AUTH_ACCESS_TTL_SECONDS=access_ttl,
        MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS=absolute_days,
        MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS=grace,
        MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS=leeway,
        MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS=skew,
        MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS=retention,
        MOBILE_AUTH_REFRESH_RATELIMIT=os.environ.get(
            'MOBILE_AUTH_REFRESH_RATELIMIT', '30 per minute; 300 per hour'),
        MOBILE_AUTH_LOGOUT_RATELIMIT=os.environ.get(
            'MOBILE_AUTH_LOGOUT_RATELIMIT', '30 per minute'),
        MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION=active_version,
        MOBILE_AUTH_DERIVATION_KEYRING=keyring,
    )
