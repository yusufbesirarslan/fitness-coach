"""Add empty Apple ownership tables; preserve User and legacy Premium state."""
from alembic import op
import sqlalchemy as sa

revision = "c2b9d0e1f2a3"
down_revision = "b1a8c9d0e1f2"
branch_labels = None
depends_on = None
expand_contract = "expand"


def _tables():
    metadata = sa.MetaData()
    sa.Table("user", metadata, sa.Column("id", sa.Integer, primary_key=True))
    account = sa.Table(
        "apple_account_association", metadata,
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.String(36), nullable=False),
        sa.UniqueConstraint("token", name="uq_apple_account_token"))
    subscription = sa.Table(
        "apple_subscription", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("original_transaction_id", sa.String(64), nullable=False),
        sa.Column("latest_transaction_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(255), nullable=False),
        sa.Column("subscription_group_id", sa.String(64), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True)),
        sa.Column("auto_renew", sa.Boolean, nullable=False),
        sa.Column("transaction_signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewal_signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("environment", "original_transaction_id", name="uq_apple_subscription_identity"),
        sa.CheckConstraint("environment IN ('Sandbox', 'Production')", name="ck_apple_subscription_environment"),
        sa.CheckConstraint("status IN (1, 2, 3, 4, 5)", name="ck_apple_subscription_status"))
    sa.Index("ix_apple_subscription_owner", subscription.c.user_id, subscription.c.environment)
    return account, subscription


def _create_account():
    op.create_table(
        "apple_account_association",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.String(36), nullable=False),
        sa.UniqueConstraint("token", name="uq_apple_account_token"))


def _create_subscription():
    op.create_table(
        "apple_subscription",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("original_transaction_id", sa.String(64), nullable=False),
        sa.Column("latest_transaction_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(255), nullable=False),
        sa.Column("subscription_group_id", sa.String(64), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True)),
        sa.Column("auto_renew", sa.Boolean, nullable=False),
        sa.Column("transaction_signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewal_signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("environment", "original_transaction_id", name="uq_apple_subscription_identity"),
        sa.CheckConstraint("environment IN ('Sandbox', 'Production')", name="ck_apple_subscription_environment"),
        sa.CheckConstraint("status IN (1, 2, 3, 4, 5)", name="ck_apple_subscription_status"))
    op.create_index("ix_apple_subscription_owner", "apple_subscription", ["user_id", "environment"], unique=False)


def upgrade():
    bind = op.get_bind()
    for table in _tables():
        inspector = sa.inspect(bind)
        if not inspector.has_table(table.name):
            if table.name == "apple_account_association":
                _create_account()
            else:
                _create_subscription()
            continue
        # create_all-first boot is supported only with the complete safe schema.
        columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError("Apple subscription table has incompatible columns")
        for column in table.c:
            actual = columns[column.name]
            if (actual["nullable"] != column.nullable
                    or actual["type"]._type_affinity != column.type._type_affinity
                    or (isinstance(column.type, sa.String)
                        and actual["type"].length != column.type.length)
                    or (bind.dialect.name == "postgresql" and isinstance(column.type, sa.DateTime)
                        and actual["type"].timezone is not True)):
                raise RuntimeError("Apple subscription table has incompatible types")
        unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)}
        required = {tuple(c.name for c in constraint.columns) for constraint in table.constraints
                    if isinstance(constraint, sa.UniqueConstraint)}
        checks = {c["name"] for c in inspector.get_check_constraints(table.name)}
        required_checks = {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)}
        foreign = inspector.get_foreign_keys(table.name)
        primary = inspector.get_pk_constraint(table.name)["constrained_columns"]
        if (not required <= unique or not required_checks <= checks
                or primary != [c.name for c in table.primary_key]
                or not any(f["constrained_columns"] == ["user_id"] and f["referred_table"] == "user"
                           and f["referred_columns"] == ["id"]
                           and f.get("options", {}).get("ondelete") == "CASCADE" for f in foreign)):
            raise RuntimeError("Apple subscription table lacks ownership constraints")
        if table.name == "apple_subscription" and not any(
                i["name"] == "ix_apple_subscription_owner" and i["column_names"] == ["user_id", "environment"]
                for i in inspector.get_indexes(table.name)):
            raise RuntimeError("Apple subscription table lacks owner index")


def downgrade():
    op.drop_table("apple_subscription")
    op.drop_table("apple_account_association")
