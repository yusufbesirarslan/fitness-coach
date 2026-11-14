"""Add the account-deletion destruction record (one empty additive table).

Revision ID: ad17de1e0001
Revises: f01a4e000003

`account_deletion.py` and `cleanup-test-users` write one row here in the same
transaction that purges an account (app/services/deletion_audit.py). The row
holds an operation reference, the path, the time, counts, the tombstone
fingerprint and a keyed MAC; no health data, consent payload, name, e-mail or
user id. There is deliberately NO foreign key to `user`: the record must
outlive the account it describes, so no cascade may remove it.

The fresh-database boot path may run ``db.create_all()`` before Alembic, so an
existing table is verified, never repaired. No backfill: deletions that
happened before this revision have no record and none is invented.
"""
from alembic import op
import sqlalchemy as sa

revision = "ad17de1e0001"
down_revision = "f01a4e000003"
branch_labels = None
depends_on = None
expand_contract = "expand"

_TABLE = "account_deletion_record"
_CHECKS = ("ck_account_deletion_channel", "ck_account_deletion_outcome",
           "ck_account_deletion_identity_step", "ck_account_deletion_scope",
           "ck_account_deletion_retention")
# name -> (type affinity, length, nullable)
_COLUMNS = {
    "id": (sa.Integer, None, False),
    "operation_ref": (sa.String, 32, False),
    "request_id": (sa.String, 80, True),
    "channel": (sa.String, 24, False),
    "outcome": (sa.String, 24, False),
    "identity_step": (sa.String, 24, False),
    "subject_fingerprint": (sa.String, 64, True),
    "scope": (sa.String, 24, False),
    "counts": (sa.JSON, None, False),
    "completed_at": (sa.DateTime, None, False),
    "retain_until": (sa.DateTime, None, False),
    "integrity": (sa.String, 64, False),
}


def _verify_existing(bind):
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns(_TABLE)}
    if set(columns) != set(_COLUMNS):
        raise RuntimeError("account_deletion_record has unexpected columns")
    for name, (kind, length, nullable) in _COLUMNS.items():
        actual = columns[name]
        if (actual["nullable"] != nullable
                or actual["type"]._type_affinity is not kind()._type_affinity
                or (length is not None and actual["type"].length != length)):
            raise RuntimeError("account_deletion_record has an incompatible column")
    if inspector.get_pk_constraint(_TABLE)["constrained_columns"] != ["id"]:
        raise RuntimeError("account_deletion_record lacks its identity")
    if inspector.get_foreign_keys(_TABLE):
        raise RuntimeError("account_deletion_record must not cascade with the account")
    unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(_TABLE)}
    if ("operation_ref",) not in unique:
        raise RuntimeError("account_deletion_record lacks its operation reference key")
    checks = {c["name"] for c in inspector.get_check_constraints(_TABLE)}
    if not set(_CHECKS) <= checks:
        raise RuntimeError("account_deletion_record lacks its checks")


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        _verify_existing(bind)
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("operation_ref", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=True),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("identity_step", sa.String(24), nullable=False),
        sa.Column("subject_fingerprint", sa.String(64), nullable=True),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("counts", sa.JSON, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=False),
        sa.Column("retain_until", sa.DateTime, nullable=False),
        sa.Column("integrity", sa.String(64), nullable=False),
        sa.UniqueConstraint("operation_ref", name="uq_account_deletion_operation"),
        sa.CheckConstraint("channel IN ('self_service', 'operator_cleanup')",
                           name="ck_account_deletion_channel"),
        sa.CheckConstraint("outcome IN ('completed')",
                           name="ck_account_deletion_outcome"),
        sa.CheckConstraint(
            "identity_step IN ('deleted', 'already_absent', 'not_attempted')",
            name="ck_account_deletion_identity_step"),
        sa.CheckConstraint("scope IN ('full_account')",
                           name="ck_account_deletion_scope"),
        sa.CheckConstraint("retain_until > completed_at",
                           name="ck_account_deletion_retention"),
    )


def downgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    if bind.execute(sa.text("SELECT 1 FROM account_deletion_record LIMIT 1")).first():
        raise RuntimeError(
            "account_deletion_record holds destruction records that must be kept "
            "three years; retain the schema on rollback")
    op.drop_table(_TABLE)
