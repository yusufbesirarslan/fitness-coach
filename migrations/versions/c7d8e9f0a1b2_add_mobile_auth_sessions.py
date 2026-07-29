'''Add opaque mobile authentication session families and hashed credentials.

Revision ID: c7d8e9f0a1b2
Revises: a994f9bed783
Create Date: 2026-07-28

Expand-only and backward-compatible. Existing web authentication does not read
these tables. Normal rollback disables the mobile blueprint and leaves the
tables unused; downgrade is reserved for disposable/test databases.
'''

from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9f0a1b2'
down_revision = 'a994f9bed783'
branch_labels = None
depends_on = None


_SESSION = 'mobile_auth_session'
_ACCESS = 'mobile_access_credential'
_REFRESH = 'mobile_refresh_credential'

_REQUIRED_COLUMNS = {
    _SESSION: {
        'id', 'family_id', 'user_id', 'cognito_username', 'cognito_sub',
        'cognito_access_token', 'cognito_refresh_token',
        'cognito_access_expires_at', 'absolute_expires_at', 'revoked_at',
        'revoked_reason', 'version', 'created_at', 'last_used_at', 'updated_at',
    },
    _ACCESS: {
        'id', 'session_id', 'credential_hash', 'generation', 'issued_at',
        'expires_at', 'revoked_at',
    },
    _REFRESH: {
        'id', 'session_id', 'credential_hash', 'generation', 'parent_id',
        'issued_at', 'expires_at', 'consumed_at', 'grace_expires_at',
        'replacement_key_version', 'replacement_generation',
        'replacement_access_id', 'replacement_refresh_id',
        'replacement_issued_at', 'replacement_access_expires_at',
        'replacement_refresh_expires_at', 'revoked_at',
    },
}


def _create_session_table():
    op.create_table(
        _SESSION,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('family_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cognito_username', sa.String(length=80), nullable=False),
        sa.Column('cognito_sub', sa.String(length=64), nullable=False),
        sa.Column('cognito_access_token', sa.Text(), nullable=True),
        sa.Column('cognito_refresh_token', sa.Text(), nullable=True),
        sa.Column('cognito_access_expires_at', sa.DateTime(), nullable=True),
        sa.Column('absolute_expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_reason', sa.String(length=40), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            'version >= 1', name='ck_mobile_auth_session_version'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def _create_access_table():
    op.create_table(
        _ACCESS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('credential_hash', sa.String(length=64), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            'generation >= 0', name='ck_mobile_access_generation'),
        sa.ForeignKeyConstraint(
            ['session_id'], [_SESSION + '.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'session_id', 'generation',
            name='uq_mobile_access_session_generation'),
    )


def _create_refresh_table():
    op.create_table(
        _REFRESH,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('credential_hash', sa.String(length=64), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('grace_expires_at', sa.DateTime(), nullable=True),
        sa.Column('replacement_key_version', sa.String(length=32), nullable=True),
        sa.Column('replacement_generation', sa.Integer(), nullable=True),
        sa.Column('replacement_access_id', sa.Integer(), nullable=True),
        sa.Column('replacement_refresh_id', sa.Integer(), nullable=True),
        sa.Column('replacement_issued_at', sa.DateTime(), nullable=True),
        sa.Column('replacement_access_expires_at', sa.DateTime(), nullable=True),
        sa.Column('replacement_refresh_expires_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            'generation >= 0', name='ck_mobile_refresh_generation'),
        sa.CheckConstraint(
            'replacement_generation IS NULL OR replacement_generation >= 0',
            name='ck_mobile_refresh_replacement_generation'),
        sa.ForeignKeyConstraint(
            ['session_id'], [_SESSION + '.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['parent_id'], [_REFRESH + '.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['replacement_access_id'], [_ACCESS + '.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['replacement_refresh_id'], [_REFRESH + '.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'session_id', 'generation',
            name='uq_mobile_refresh_session_generation'),
        sa.UniqueConstraint('parent_id', name='uq_mobile_refresh_parent'),
    )


_INDEXES = {
    _SESSION: (
        ('uq_mobile_auth_session_family_id', ('family_id',), True),
        ('ix_mobile_auth_session_user_id', ('user_id',), False),
        ('ix_mobile_auth_session_expiry_revoked',
         ('absolute_expires_at', 'revoked_at'), False),
    ),
    _ACCESS: (
        ('uq_mobile_access_credential_hash', ('credential_hash',), True),
        ('ix_mobile_access_expiry_revoked', ('expires_at', 'revoked_at'), False),
    ),
    _REFRESH: (
        ('uq_mobile_refresh_credential_hash', ('credential_hash',), True),
        ('ix_mobile_refresh_expiry_revoked', ('expires_at', 'revoked_at'), False),
    ),
}


def _existing_named_objects(inspector, table):
    names = {
        item['name'] for item in inspector.get_indexes(table) if item.get('name')
    }
    try:
        names |= {
            item['name'] for item in inspector.get_unique_constraints(table)
            if item.get('name')
        }
    except Exception:
        pass
    return names


def _ensure_indexes(inspector, table):
    existing = _existing_named_objects(inspector, table)
    for name, columns, unique in _INDEXES[table]:
        if name not in existing:
            op.create_index(name, table, list(columns), unique=unique)


_REQUIRED_UNIQUES = {
    _SESSION: set(),
    _ACCESS: {'uq_mobile_access_session_generation'},
    _REFRESH: {
        'uq_mobile_refresh_session_generation',
        'uq_mobile_refresh_parent',
    },
}

_REQUIRED_FOREIGN_KEYS = {
    _SESSION: {(('user_id',), 'user')},
    _ACCESS: {(('session_id',), _SESSION)},
    _REFRESH: {
        (('session_id',), _SESSION),
        (('parent_id',), _REFRESH),
        (('replacement_access_id',), _ACCESS),
        (('replacement_refresh_id',), _REFRESH),
    },
}


def _incompatible(table, detail):
    raise RuntimeError(
        f'{table} exists but is incompatible: {detail}. '
        'Refusing an incomplete mobile-auth invariant.')


def _verify_existing(inspector, table):
    columns = {item['name'] for item in inspector.get_columns(table)}
    missing_columns = _REQUIRED_COLUMNS[table] - columns
    if missing_columns:
        _incompatible(table, f'missing columns {sorted(missing_columns)}')

    unique_names = {
        item['name'] for item in inspector.get_unique_constraints(table)
        if item.get('name')
    }
    missing_uniques = _REQUIRED_UNIQUES[table] - unique_names
    if missing_uniques:
        _incompatible(table, f'missing unique constraints {sorted(missing_uniques)}')

    foreign_keys = {
        (tuple(item['constrained_columns']), item['referred_table'])
        for item in inspector.get_foreign_keys(table)
    }
    missing_foreign_keys = _REQUIRED_FOREIGN_KEYS[table] - foreign_keys
    if missing_foreign_keys:
        _incompatible(table, f'missing foreign keys {sorted(missing_foreign_keys)}')


def upgrade():
    bind = op.get_bind()
    creators = (
        (_SESSION, _create_session_table),
        (_ACCESS, _create_access_table),
        (_REFRESH, _create_refresh_table),
    )
    for table, creator in creators:
        inspector = sa.inspect(bind)
        if inspector.has_table(table):
            _verify_existing(inspector, table)
        else:
            creator()
        _ensure_indexes(sa.inspect(bind), table)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in (_REFRESH, _ACCESS, _SESSION):
        if inspector.has_table(table):
            op.drop_table(table)
            inspector = sa.inspect(op.get_bind())
