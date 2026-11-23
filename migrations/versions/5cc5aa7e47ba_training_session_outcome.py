"""Record the outcome of an independent cardio session (G4, #530).

Revision ID: 5cc5aa7e47ba
Revises: 19cb998acd2f

Expand only: one new, empty table. No existing row, column or constraint
changes, so pre-G4 code keeps working against this schema (it never reads the
table). Downgrade refuses to destroy any recorded outcome; with outcomes
present, roll back code and keep the schema.
"""

import sqlalchemy as sa
from alembic import op


revision = "5cc5aa7e47ba"
down_revision = "19cb998acd2f"
branch_labels = None
depends_on = None
expand_contract = "expand"

_TABLE = "training_session_outcome"
_UNIQUE = "uq_training_session_outcome_day"
_CHECKS = {
    "ck_training_session_outcome_kind": "session_kind IN ('cardio')",
    "ck_training_session_outcome_outcome": "outcome IN ('completed', 'skipped')",
    "ck_training_session_outcome_version": "mutation_version >= 0",
}
_COLUMNS = {
    "id": sa.Integer(),
    "user_id": sa.Integer(),
    "date_key": sa.String(10),
    "session_kind": sa.String(16),
    "outcome": sa.String(16),
    "plan_lineage_id": sa.String(64),
    "mutation_version": sa.Integer(),
    "created_at": sa.DateTime(),
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
            sa.UniqueConstraint("user_id", "date_key", "session_kind", name=_UNIQUE),
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
            or unique.get(_UNIQUE) != ["user_id", "date_key", "session_kind"]
            or not set(_CHECKS) <= checks
            or len(keys) != 1
            or keys[0]["constrained_columns"] != ["user_id"]
            or keys[0]["referred_table"] != "user"
            or keys[0]["referred_columns"] != ["id"]
            or keys[0]["options"].get("ondelete") != "CASCADE"):
        raise RuntimeError("training session outcome schema mismatch")


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE training_session_outcome IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM training_session_outcome LIMIT 1")).scalar():
        raise RuntimeError("training session outcome downgrade would lose recorded outcomes")
    op.drop_table(_TABLE)
