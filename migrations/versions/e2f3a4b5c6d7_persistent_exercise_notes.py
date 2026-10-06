"""TI-01B private revisioned catalog notes; downgrade erases all note data.

Catalog membership is validated against the bundled asset; no catalog SQL FK.
"""
from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def _create_table():
    op.create_table(
        "exercise_note",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.String(120), nullable=False),
        sa.Column("text", sa.String(500), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_exercise_note"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_exercise_note_user", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "exercise_id", name="uq_exercise_note_owner_exercise"),
        sa.CheckConstraint("revision >= 0 AND revision <= 999999999", name="ck_exercise_note_revision"),
        sa.CheckConstraint("text IS NULL OR length(text) <= 500", name="ck_exercise_note_text_length"),
    )


def _verify_existing(bind):
    # Fresh boot creates current metadata before the remaining Alembic chain,
    # exactly as the predecessor tombstone migration documents. Never silently
    # accept a table with missing ownership/uniqueness/bounds protections.
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns("exercise_note")}
    if set(columns) != {"id", "user_id", "exercise_id", "text", "revision", "created_at", "updated_at"}:
        raise RuntimeError("exercise_note exists with unexpected columns")
    for name in ("id", "user_id", "revision"):
        if not isinstance(columns[name]["type"], sa.Integer):
            raise RuntimeError("exercise_note exists with unexpected column types")
    for name, length in (("exercise_id", 120), ("text", 500)):
        if not isinstance(columns[name]["type"], sa.String) or columns[name]["type"].length != length:
            raise RuntimeError("exercise_note exists with unexpected text bounds")
    for name in ("created_at", "updated_at"):
        if not isinstance(columns[name]["type"], sa.DateTime):
            raise RuntimeError("exercise_note exists with unexpected timestamp types")
    if any(c["nullable"] != (name == "text") for name, c in columns.items()):
        raise RuntimeError("exercise_note exists with unexpected nullability")
    if inspector.get_pk_constraint("exercise_note").get("constrained_columns") != ["id"]:
        raise RuntimeError("exercise_note exists with unexpected primary key")
    unique = inspector.get_unique_constraints("exercise_note")
    foreign = inspector.get_foreign_keys("exercise_note")
    checks = {c["name"] for c in inspector.get_check_constraints("exercise_note")}
    if not any(c["name"] == "uq_exercise_note_owner_exercise" and c["column_names"] == ["user_id", "exercise_id"] for c in unique):
        raise RuntimeError("exercise_note owner/exercise uniqueness missing")
    if not any(c["name"] == "fk_exercise_note_user" and c["constrained_columns"] == ["user_id"]
               and c["referred_table"] == "user" and c["referred_columns"] == ["id"]
               and c.get("options", {}).get("ondelete") == "CASCADE" for c in foreign):
        raise RuntimeError("exercise_note owner cascade missing")
    if not {"ck_exercise_note_revision", "ck_exercise_note_text_length"} <= checks:
        raise RuntimeError("exercise_note bounds checks missing")


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("exercise_note"):
        _verify_existing(bind)
    else:
        _create_table()


def downgrade():
    op.drop_table("exercise_note")
