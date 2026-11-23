"""Store immutable owner-private exercises.

Revision ID: e5b27aa4101c
Revises: ad17de1e0001
"""

import sqlalchemy as sa
from alembic import op


revision = "e5b27aa4101c"
down_revision = "ad17de1e0001"
branch_labels = None
depends_on = None
expand_contract = "expand"

_TABLE = "private_exercise"
_UNIQUE = "uq_private_exercise_owner_name"
_INDEX = "ix_private_exercise_owner_id"
_COLUMNS = {
    "exercise_id": sa.String(64),
    "user_id": sa.Integer(),
    "name_key": sa.String(480),
    "display_name": sa.String(120),
    "equipment": sa.JSON(),
    "movement": sa.String(32),
    "primary_region": sa.String(32),
    "target_region": sa.String(32),
}


def upgrade():
    bind = op.get_bind()
    # Self-migrating fresh boots may already have created the table from
    # current models. Verify that shape instead of replaying CREATE TABLE.
    if not sa.inspect(bind).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("exercise_id", _COLUMNS["exercise_id"], primary_key=True),
            sa.Column("user_id", _COLUMNS["user_id"],
                      sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            *(sa.Column(name, kind, nullable=False) for name, kind in _COLUMNS.items()
              if name not in {"exercise_id", "user_id"}),
            sa.UniqueConstraint("user_id", "name_key", name=_UNIQUE),
        )
        op.create_index(_INDEX, _TABLE, ["user_id", "exercise_id"])

    inspector = sa.inspect(bind)
    columns = {column["name"]: (column["type"].compile(dialect=bind.dialect), column["nullable"])
               for column in inspector.get_columns(_TABLE)}
    expected = {name: (kind.compile(dialect=bind.dialect), False)
                for name, kind in _COLUMNS.items()}
    unique = {item["name"]: item["column_names"] for item in inspector.get_unique_constraints(_TABLE)}
    indexes = {item["name"]: (item["column_names"], item["unique"])
               for item in inspector.get_indexes(_TABLE)}
    keys = inspector.get_foreign_keys(_TABLE)
    if (columns != expected
            or inspector.get_pk_constraint(_TABLE)["constrained_columns"] != ["exercise_id"]
            or unique.get(_UNIQUE) != ["user_id", "name_key"]
            or indexes.get(_INDEX) != (["user_id", "exercise_id"], False)
            or len(keys) != 1
            or keys[0]["constrained_columns"] != ["user_id"]
            or keys[0]["referred_table"] != "user"
            or keys[0]["referred_columns"] != ["id"]
            or keys[0]["options"].get("ondelete") != "CASCADE"):
        raise RuntimeError("private exercise schema mismatch")


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE private_exercise IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM private_exercise LIMIT 1")).scalar():
        raise RuntimeError("private exercise downgrade would lose durable identities")
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
