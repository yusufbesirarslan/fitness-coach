"""Record an owner's explicit "start without a plan" decision (PD2 Skip).

Revision ID: b7d2e9f4a1c3
Revises: a7d3c9e1f2b4

Expand only: one new, empty table. No existing row, column or constraint
changes, so pre-PD2 code keeps working against this schema (it never reads the
table). It is not a plan store: it holds one closed token per owner and never
plan content. Downgrade refuses to destroy a recorded decision; with decisions
present, roll back code and keep the schema.
"""

import sqlalchemy as sa
from alembic import op


revision = "b7d2e9f4a1c3"
down_revision = "a7d3c9e1f2b4"
branch_labels = None
depends_on = None
expand_contract = "expand"

_TABLE = "training_start_decision"
_UNIQUE = "uq_training_start_decision_user"
_CHECKS = {
    "ck_training_start_decision_decision": "decision IN ('skip')",
}
_COLUMNS = {
    "id": sa.Integer(),
    "user_id": sa.Integer(),
    "decision": sa.String(16),
    "created_at": sa.DateTime(),
    "updated_at": sa.DateTime(),
}


def upgrade():
    bind = op.get_bind()
    # Self-migrating fresh boots may already have created the table from
    # current models. Verify that shape instead of replaying CREATE TABLE.
    if not sa.inspect(bind).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", _COLUMNS["id"], primary_key=True),
            sa.Column("user_id", _COLUMNS["user_id"],
                      sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            *(sa.Column(name, kind, nullable=False) for name, kind in _COLUMNS.items()
              if name not in {"id", "user_id"}),
            sa.UniqueConstraint("user_id", name=_UNIQUE),
            *(sa.CheckConstraint(sql, name=name) for name, sql in _CHECKS.items()),
        )

    inspector = sa.inspect(bind)
    columns = {column["name"]: (column["type"].compile(dialect=bind.dialect), column["nullable"])
               for column in inspector.get_columns(_TABLE)}
    expected = {name: (kind.compile(dialect=bind.dialect), False)
                for name, kind in _COLUMNS.items()}
    unique = {item["name"]: item["column_names"] for item in inspector.get_unique_constraints(_TABLE)}
    checks = {item["name"] for item in inspector.get_check_constraints(_TABLE)}
    keys = inspector.get_foreign_keys(_TABLE)
    if (columns != expected
            or inspector.get_pk_constraint(_TABLE)["constrained_columns"] != ["id"]
            or unique.get(_UNIQUE) != ["user_id"]
            or not set(_CHECKS) <= checks
            or len(keys) != 1
            or keys[0]["constrained_columns"] != ["user_id"]
            or keys[0]["referred_table"] != "user"
            or keys[0]["referred_columns"] != ["id"]
            or keys[0]["options"].get("ondelete") != "CASCADE"):
        raise RuntimeError("training start decision schema mismatch")


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE training_start_decision IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM training_start_decision LIMIT 1")).scalar():
        raise RuntimeError("training start decision downgrade would lose recorded decisions")
    op.drop_table(_TABLE)
