import base64
import json
import re

import pytest
from flask import Flask

from app.services import mobile_credentials


ROOT_KEY = b'k' * 32
ROOT_WIRE = 'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s'
PARENT_WIRE = 'cHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHA'


def _keyring(**entries):
    return json.dumps(entries)


def test_generate_credential_is_canonical_256_bit_value():
    value = mobile_credentials.generate_credential()

    assert len(base64.urlsafe_b64decode(value + '=')) == 32
    assert len(value) == 43
    assert '=' not in value
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}', value)


def test_hash_credential_is_lowercase_sha256_and_not_plaintext():
    digest = mobile_credentials.hash_credential(PARENT_WIRE)

    assert digest == 'a7cbbfdfe39c7df7502aa43b785e40940817b89fbff7834b364d2e343c80c25c'
    assert re.fullmatch(r'[0-9a-f]{64}', digest)
    assert PARENT_WIRE not in digest


def test_replacement_derivation_matches_reviewed_vector_and_separates_labels():
    pair = mobile_credentials.derive_replacement_pair(
        PARENT_WIRE,
        'family-1',
        4,
        5,
        'v1',
        {'v1': ROOT_KEY},
    )

    assert pair.access == '0TpIB9tNbXNzTlGt0INLwEdpRR9x5Z6bk1-9F53ZQcc'
    assert pair.refresh == '84nW-cI6wfKeG9RgZPK7eorK13zi66lsjYj4em7ugYk'
    assert pair.access != pair.refresh


def test_replacement_derivation_changes_with_context():
    baseline = mobile_credentials.derive_replacement_pair(
        PARENT_WIRE, 'family-1', 4, 5, 'v1', {'v1': ROOT_KEY})

    assert mobile_credentials.derive_replacement_pair(
        PARENT_WIRE, 'family-2', 4, 5, 'v1', {'v1': ROOT_KEY}) != baseline
    assert mobile_credentials.derive_replacement_pair(
        PARENT_WIRE, 'family-1', 5, 6, 'v1', {'v1': ROOT_KEY}) != baseline


@pytest.mark.parametrize('value', [
    '',
    PARENT_WIRE + '=',
    '!' + PARENT_WIRE[1:],
    'c2hvcnQ',
])
def test_canonical_credential_rejects_noncanonical_or_non_256_bit_values(value):
    with pytest.raises(mobile_credentials.InvalidMobileCredential):
        mobile_credentials.canonical_credential(value)


def test_parse_keyring_decodes_canonical_keys():
    parsed = mobile_credentials.parse_keyring(_keyring(v1=ROOT_WIRE))

    assert parsed == {'v1': ROOT_KEY}


def test_parse_keyring_accepts_canonical_roots_longer_than_256_bits():
    root = b'z' * 48
    wire = base64.urlsafe_b64encode(root).rstrip(b'=').decode('ascii')

    assert mobile_credentials.parse_keyring(_keyring(v2=wire)) == {'v2': root}


def test_parse_keyring_rejects_duplicate_versions():
    quote = chr(34)
    raw = ('{' + quote + 'v1' + quote + ':' + quote + ROOT_WIRE + quote
           + ',' + quote + 'v1' + quote + ':' + quote + ROOT_WIRE + quote + '}')

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.parse_keyring(raw)


@pytest.mark.parametrize('version', ['v 1', 'v' * 33])
def test_parse_keyring_rejects_unsafe_persisted_version_labels(version):
    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.parse_keyring(_keyring(**{version: ROOT_WIRE}))


@pytest.mark.parametrize('raw', [
    '',
    'not-json',
    json.dumps([]),
    _keyring(),
    _keyring(v1='short'),
    _keyring(v1=ROOT_WIRE + '='),
])
def test_parse_keyring_rejects_missing_malformed_or_noncanonical_keys(raw):
    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.parse_keyring(raw)


def test_rate_limit_key_is_stable_and_exposes_neither_credential_nor_hash():
    value = mobile_credentials.credential_rate_limit_key(
        PARENT_WIRE, {'v1': ROOT_KEY}, 'v1')

    assert value == mobile_credentials.credential_rate_limit_key(
        PARENT_WIRE, {'v1': ROOT_KEY}, 'v1')
    assert value.startswith('mobile-refresh:')
    assert PARENT_WIRE not in value
    assert mobile_credentials.hash_credential(PARENT_WIRE) not in value


def _valid_mobile_env(monkeypatch):
    monkeypatch.setenv('MOBILE_AUTH_DERIVATION_KEYRING', _keyring(v1=ROOT_WIRE))
    monkeypatch.setenv('MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION', 'v1')


def test_mobile_auth_config_loads_approved_defaults(monkeypatch):
    _valid_mobile_env(monkeypatch)
    app = Flask(__name__)

    mobile_credentials.validate_mobile_auth_config(app)

    assert app.config['MOBILE_AUTH_ACCESS_TTL_SECONDS'] == 900
    assert app.config['MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS'] == 7
    assert app.config['MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS'] == 10
    assert app.config['MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS'] == 60
    assert app.config['MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS'] == 0
    assert app.config['MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS'] == 300
    assert app.config['MOBILE_AUTH_REFRESH_RATELIMIT'] == '30 per minute; 300 per hour'
    assert app.config['MOBILE_AUTH_LOGOUT_RATELIMIT'] == '30 per minute'
    assert app.config['MOBILE_AUTH_DERIVATION_KEYRING'] == {'v1': ROOT_KEY}


@pytest.mark.parametrize(('name', 'value'), [
    ('MOBILE_AUTH_ACCESS_TTL_SECONDS', '0'),
    ('MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS', '0'),
    ('MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS', '0'),
    ('MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS', '-1'),
    ('MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS', '-1'),
    ('MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS', '-1'),
])
def test_mobile_auth_config_rejects_unsafe_numeric_values(monkeypatch, name, value):
    _valid_mobile_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.validate_mobile_auth_config(Flask(__name__))


@pytest.mark.parametrize(('name', 'value'), [
    ('MOBILE_AUTH_ACCESS_TTL_SECONDS', '86401'),
    ('MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS', '8'),
    ('MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS', '61'),
    ('MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS', '3601'),
    ('MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS', '301'),
    ('MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS', '86401'),
])
def test_mobile_auth_config_rejects_values_above_reviewed_bounds(
        monkeypatch, name, value):
    _valid_mobile_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.validate_mobile_auth_config(Flask(__name__))


def test_mobile_auth_config_rejects_grace_not_shorter_than_access(monkeypatch):
    _valid_mobile_env(monkeypatch)
    monkeypatch.setenv('MOBILE_AUTH_ACCESS_TTL_SECONDS', '10')
    monkeypatch.setenv('MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS', '10')

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.validate_mobile_auth_config(Flask(__name__))


def test_mobile_auth_config_rejects_missing_active_key(monkeypatch):
    _valid_mobile_env(monkeypatch)
    monkeypatch.setenv('MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION', 'v2')

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_credentials.validate_mobile_auth_config(Flask(__name__))


def test_configure_app_loads_mobile_auth_settings_at_startup(monkeypatch):
    from app import config

    _valid_mobile_env(monkeypatch)
    monkeypatch.setenv('MOBILE_AUTH_ACCESS_TTL_SECONDS', '321')
    app = Flask(__name__)

    config.configure_app(app)

    assert app.config['MOBILE_AUTH_ACCESS_TTL_SECONDS'] == 321
    assert app.config['MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION'] == 'v1'
