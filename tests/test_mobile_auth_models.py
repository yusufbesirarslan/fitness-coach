from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    MobileAccessCredential,
    MobileAuthSession,
    MobileRefreshCredential,
)


NOW = datetime(2026, 7, 28, 12, 0, 0)
HASH_A = 'a' * 64
HASH_B = 'b' * 64
HASH_C = 'c' * 64


def _family(user, **overrides):
    values = {
        'family_id': 'family-1',
        'user_id': user.id,
        'cognito_username': user.username,
        'cognito_sub': user.cognito_sub,
        'cognito_access_token': 'fernet-access',
        'cognito_refresh_token': 'fernet-refresh',
        'cognito_access_expires_at': NOW + timedelta(hours=1),
        'absolute_expires_at': NOW + timedelta(days=7),
        'version': 1,
        'created_at': NOW,
        'last_used_at': NOW,
        'updated_at': NOW,
    }
    values.update(overrides)
    return MobileAuthSession(**values)


def _access(family, generation=0, credential_hash=HASH_A):
    return MobileAccessCredential(
        session_id=family.id,
        credential_hash=credential_hash,
        generation=generation,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def _refresh(family, generation=0, credential_hash=HASH_B, **overrides):
    values = {
        'session_id': family.id,
        'credential_hash': credential_hash,
        'generation': generation,
        'issued_at': NOW,
        'expires_at': family.absolute_expires_at,
    }
    values.update(overrides)
    return MobileRefreshCredential(**values)


def test_models_persist_only_hashes_and_approved_derivation_metadata(app):
    access_columns = {column.name for column in MobileAccessCredential.__table__.columns}
    refresh_columns = {column.name for column in MobileRefreshCredential.__table__.columns}

    assert 'credential_hash' in access_columns
    assert 'credential_hash' in refresh_columns
    assert not ({'credential', 'raw_credential', 'token'} & access_columns)
    assert not ({'credential', 'raw_credential', 'token'} & refresh_columns)
    assert {
        'replacement_key_version', 'replacement_generation',
        'replacement_access_id', 'replacement_refresh_id',
        'replacement_issued_at', 'replacement_access_expires_at',
        'replacement_refresh_expires_at',
    } <= refresh_columns


def test_revoked_family_allows_cognito_ciphertext_to_be_cleared(app, make_user):
    family = _family(
        make_user('revoked-mobile'),
        cognito_access_token=None,
        cognito_refresh_token=None,
        revoked_at=NOW,
        revoked_reason='logout',
    )
    db.session.add(family)
    db.session.commit()

    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None


def test_family_and_credential_hashes_are_uniquely_indexed(app):
    inspector = inspect(db.engine)

    family_indexes = {item['name']: item for item in inspector.get_indexes(
        MobileAuthSession.__tablename__)}
    access_indexes = {item['name']: item for item in inspector.get_indexes(
        MobileAccessCredential.__tablename__)}
    refresh_indexes = {item['name']: item for item in inspector.get_indexes(
        MobileRefreshCredential.__tablename__)}

    assert family_indexes['uq_mobile_auth_session_family_id']['unique'] == 1
    assert access_indexes['uq_mobile_access_credential_hash']['unique'] == 1
    assert refresh_indexes['uq_mobile_refresh_credential_hash']['unique'] == 1


def test_refresh_parent_can_have_exactly_one_child(app, make_user):
    family = _family(make_user('one-mobile-child'))
    db.session.add(family)
    db.session.flush()
    parent = _refresh(family)
    db.session.add(parent)
    db.session.flush()
    db.session.add_all([
        _refresh(family, 1, HASH_C, parent_id=parent.id),
        _refresh(family, 2, 'd' * 64, parent_id=parent.id),
    ])

    with pytest.raises(IntegrityError):
        db.session.commit()


@pytest.mark.parametrize('model_factory', [_access, _refresh])
def test_generation_must_be_non_negative(app, make_user, model_factory):
    family = _family(make_user('negative-' + model_factory.__name__))
    db.session.add(family)
    db.session.flush()
    db.session.add(model_factory(family, generation=-1))

    with pytest.raises(IntegrityError):
        db.session.commit()


def test_user_delete_cascades_mobile_session_family_and_credentials(app, make_user):
    user = make_user('mobile-cascade')
    family = _family(user)
    db.session.add(family)
    db.session.flush()
    db.session.add_all([_access(family), _refresh(family)])
    db.session.commit()

    db.session.delete(user)
    db.session.commit()

    assert MobileAuthSession.query.count() == 0
    assert MobileAccessCredential.query.count() == 0
    assert MobileRefreshCredential.query.count() == 0
