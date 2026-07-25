"""add workout_session (Sprint 7 PR3)

Revision ID: a994f9bed783
Revises: bb88cc99dd00
Create Date: 2026-07-25

Persisted workout-session lifecycle table (docs/WORKOUT_STATE.md "Sprint 7 PR3").
Expand-only / backward-compatible: purely additive, no destructive backfill, no
fabricated historical sessions. Old code runs without this table (the feature is
default-OFF), so a code-only rollback stays safe (A2).

FAIL-CLOSED, VERIFY-OR-CREATE (correction #5): the fresh-DB boot path runs
``db.create_all()`` before stamping+upgrading, so this revision frequently runs
against a ``workout_session`` table create_all ALREADY built from the model. It
therefore does NOT blanket-skip when the table exists — it verifies each required
object (columns, status check constraint, public-id uniqueness, the active-owner
partial unique index, supporting indexes) and creates whatever is missing. If an
existing table is structurally incompatible (a required column is absent) it
raises rather than reporting a successful upgrade with an incomplete invariant.
On a pure Alembic path (empty DB, e.g. the CI schema-drift guard on PostgreSQL)
the table does not exist yet and is created in full — matching the model exactly
so ``flask db check`` reports zero drift.
"""
from alembic import op
import sqlalchemy as sa


revision = "a994f9bed783"
down_revision = "bb88cc99dd00"
branch_labels = None
depends_on = None

_TABLE = "workout_session"
_ACTIVE_WHERE = "status = 'active'"

# Every column the model (app/models.py WorkoutSession) requires. A pre-existing
# table missing any of these is incompatible → fail closed.
_REQUIRED_COLUMNS = {
    "id", "public_id", "user_id", "status", "workout_date", "weekday_slot",
    "source", "planned_training_plan_id", "plan_fingerprint", "started_at",
    "last_activity_at", "completed_at", "abandoned_at", "terminal_reason",
    "version", "created_at", "updated_at",
}

# name -> callable(existing_index_names) that creates the index if absent.
_INDEXES = ("ix_workout_session_user_id", "ix_workout_session_user_status",
            "uq_workout_session_active_owner", "uq_workout_session_public_id")


def _create_table():
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="active", nullable=False),
        sa.Column("workout_date", sa.String(length=10), nullable=False),
        sa.Column("weekday_slot", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("planned_training_plan_id", sa.Integer(), nullable=True),
        sa.Column("plan_fingerprint", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.Column("terminal_reason", sa.String(length=40), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'abandoned')",
            name="ck_workout_session_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_missing_indexes(existing):
    if "ix_workout_session_user_id" not in existing:
        op.create_index("ix_workout_session_user_id", _TABLE, ["user_id"], unique=False)
    if "ix_workout_session_user_status" not in existing:
        op.create_index(
            "ix_workout_session_user_status", _TABLE, ["user_id", "status"], unique=False)
    if "uq_workout_session_active_owner" not in existing:
        # THE active-session invariant: at most one active row per user. Partial
        # unique index on both SQLite (>=3.8) and PostgreSQL — dialect `where` each.
        op.create_index(
            "uq_workout_session_active_owner", _TABLE, ["user_id"], unique=True,
            sqlite_where=sa.text(_ACTIVE_WHERE),
            postgresql_where=sa.text(_ACTIVE_WHERE),
        )
    if "uq_workout_session_public_id" not in existing:
        op.create_index(
            "uq_workout_session_public_id", _TABLE, ["public_id"], unique=True)


def _ensure_check_constraint(insp):
    """Verify (and on a backend that supports in-place addition, create) the
    status check constraint. create_all always emits it, so the verify path
    normally finds it present; this only backfills a table that somehow lacks it.
    """
    try:
        checks = {c.get("name") for c in insp.get_check_constraints(_TABLE)}
    except Exception:  # noqa: BLE001 — dialect can't introspect checks; create_all had it
        return
    if "ck_workout_session_status" in checks:
        return
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_workout_session_status", _TABLE,
            "status IN ('active', 'completed', 'abandoned')",
        )
    # SQLite cannot add a named CHECK in place without a table rebuild; create_all
    # is authoritative there, so a missing one is not backfilled (documented).


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        _create_table()
        _create_missing_indexes(existing=frozenset())
        return

    # Table already exists (create_all boot path or a partial prior state):
    # verify-or-create, fail closed on structural incompatibility.
    columns = {col["name"] for col in insp.get_columns(_TABLE)}
    missing = _REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            "workout_session exists but is incompatible: missing required "
            "column(s) %s. Refusing to report a successful upgrade with an "
            "incomplete invariant (Sprint 7 PR3, correction #5). Inspect the "
            "table and reconcile it with app/models.py WorkoutSession."
            % sorted(missing)
        )
    existing_idx = {ix["name"] for ix in insp.get_indexes(_TABLE) if ix.get("name")}
    # PostgreSQL surfaces unique indexes under get_unique_constraints too; fold
    # those names in so we never try to recreate an existing unique object.
    try:
        existing_idx |= {
            uc["name"] for uc in insp.get_unique_constraints(_TABLE) if uc.get("name")
        }
    except Exception:  # noqa: BLE001 — best-effort; get_indexes already covers it
        pass
    _create_missing_indexes(existing_idx)
    _ensure_check_constraint(insp)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(_TABLE):
        return
    existing_idx = {ix["name"] for ix in insp.get_indexes(_TABLE) if ix.get("name")}
    for name in _INDEXES:
        if name in existing_idx:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
