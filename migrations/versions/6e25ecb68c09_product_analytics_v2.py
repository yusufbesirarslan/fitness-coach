"""Product analytics v2: independent consent, consent-fenced outbox, erasure jobs.

Revision ID: 6e25ecb68c09
Revises: e6a4b91d702c

Expand only: three new, empty tables (erasure rows carry the full vendor
receipt lifecycle; consent carries the vendor-exposure marker). Nothing existing changes and no code
path writes them while FITX_PRODUCT_ANALYTICS_ENABLED is OFF (default).
INTEGRATION NOTE: this lane was cut from #568 (head e6a4b91d702c); the
integration owner re-points ``down_revision`` once, at composition, so the
final graph keeps a single head. Downgrade refuses to drop any row.
"""

import sqlalchemy as sa
from alembic import op


revision = "6e25ecb68c09"
down_revision = "e6a4b91d702c"
branch_labels = None
depends_on = None
expand_contract = "expand"

_TABLES = ("product_analytics_consent", "product_analytics_outbox", "product_analytics_erasure")


def _create_consent():
    op.create_table(
        "product_analytics_consent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(36)),
        sa.Column("grant_id", sa.String(36)),
        sa.Column("notice_version", sa.String(40)),
        sa.Column("granted_at", sa.DateTime()),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("withdrawn_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("vendor_exposed_at", sa.DateTime()),
        sa.Column("subject_issued_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", name="uq_product_analytics_consent_user"),
        sa.CheckConstraint("status IN ('granted', 'withdrawn')",
                           name="ck_product_analytics_consent_status"),
    )


def _create_outbox():
    op.create_table(
        "product_analytics_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("authority", sa.String(8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("subject", "event_id", name="uq_product_analytics_outbox_event"),
        sa.CheckConstraint("authority IN ('server', 'client')",
                           name="ck_product_analytics_outbox_authority"),
    )
    op.create_index("ix_product_analytics_outbox_due", "product_analytics_outbox",
                    ["next_attempt_at", "id"])
    op.create_index("ix_product_analytics_outbox_user", "product_analytics_outbox", ["user_id"])


def _create_erasure():
    op.create_table(
        "product_analytics_erasure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("vendor_person_ref", sa.String(64)),
        sa.Column("vendor_requested_at", sa.DateTime()),
        sa.Column("last_outcome", sa.String(24)),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint("subject", name="uq_product_analytics_erasure_subject"),
        sa.CheckConstraint("reason IN ('withdrawn', 'account_deleted', 'rotated')",
                           name="ck_product_analytics_erasure_reason"),
        sa.CheckConstraint("status IN ('pending', 'materialized', 'requested', 'verified', "
                           "'not_required')",
                           name="ck_product_analytics_erasure_status"),
    )
    op.create_index("ix_product_analytics_erasure_due", "product_analytics_erasure",
                    ["status", "next_attempt_at"])


_EXPECTED_UNIQUE = {
    "product_analytics_consent": {"uq_product_analytics_consent_user": ["user_id"]},
    "product_analytics_outbox": {"uq_product_analytics_outbox_event": ["subject", "event_id"]},
    "product_analytics_erasure": {"uq_product_analytics_erasure_subject": ["subject"]},
}
_EXPECTED_COLUMNS = {
    "product_analytics_consent": {"id", "user_id", "status", "generation", "subject", "grant_id",
                                  "notice_version", "granted_at", "expires_at", "withdrawn_at",
                                  "updated_at", "vendor_exposed_at", "subject_issued_at"},
    "product_analytics_outbox": {"id", "user_id", "generation", "subject", "event_id", "name",
                                 "authority", "occurred_at", "received_at", "properties",
                                 "attempts", "next_attempt_at"},
    "product_analytics_erasure": {"id", "subject", "reason", "status", "attempts",
                                  "requested_at", "next_attempt_at", "vendor_person_ref",
                                  "vendor_requested_at", "last_outcome", "completed_at"},
}


def upgrade():
    bind = op.get_bind()
    # Fresh create_all-first boots may already hold these tables from the
    # current models: verify instead of replaying CREATE TABLE.
    if not sa.inspect(bind).has_table("product_analytics_consent"):
        _create_consent()
    if not sa.inspect(bind).has_table("product_analytics_outbox"):
        _create_outbox()
    if not sa.inspect(bind).has_table("product_analytics_erasure"):
        _create_erasure()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        columns = {column["name"] for column in inspector.get_columns(table)}
        unique = {item["name"]: item["column_names"]
                  for item in inspector.get_unique_constraints(table)}
        if columns != _EXPECTED_COLUMNS[table] or any(
                unique.get(name) != cols for name, cols in _EXPECTED_UNIQUE[table].items()):
            raise RuntimeError(f"{table} schema mismatch")


def downgrade():
    connection = op.get_bind()
    for table in _TABLES:
        if connection.dialect.name == "postgresql":
            connection.execute(sa.text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).scalar():
            raise RuntimeError(f"{table} downgrade would lose analytics consent or erasure state")
    op.drop_index("ix_product_analytics_erasure_due", table_name="product_analytics_erasure")
    op.drop_index("ix_product_analytics_outbox_user", table_name="product_analytics_outbox")
    op.drop_index("ix_product_analytics_outbox_due", table_name="product_analytics_outbox")
    for table in reversed(_TABLES):
        op.drop_table(table)
