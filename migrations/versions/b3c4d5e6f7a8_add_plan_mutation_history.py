"""add durable plan versioning + mutation journal (Adaptive Coaching S1 PR2)

Revision ID: b3c4d5e6f7a8
Revises: f0a1b2c3d4e5
Create Date: 2026-08-14

Two additive columns on ``training_plan`` (``lineage_id``, ``mutation_version``)
and one new table, ``plan_mutation_record``. Expand-only and backward compatible:
nothing is dropped, renamed or rewritten, and code that predates this revision
runs unchanged against the new schema — which is what makes a code-only rollback
safe (CLAUDE.md A2, since boot applies migrations automatically and rollback does
not un-apply them).

EXISTING ROWS GET DETERMINISTIC INITIAL SEMANTICS, NOT INVENTED HISTORY. Every
plan that already exists is "version 0, never mutated through this boundary":
``mutation_version`` starts at 0 and the journal starts empty. No historical
mutation events are fabricated — the application has never recorded them, so any
row written here would be fiction, and an undo standing on fiction would restore
a snapshot that was never real. ``lineage_id`` is backfilled with a fresh random
token PER ROW (never one shared constant, which would make two users' plans look
like one lineage and let history cross between them).

FAIL-SAFE, VERIFY-OR-CREATE, mirroring a994f9bed783: the fresh-DB boot path runs
``db.create_all()`` before stamping and upgrading, so this revision frequently
runs against objects create_all already built from the model. It therefore checks
each object and creates only what is missing, instead of blanket-skipping (which
would silently leave a half-built invariant) or unconditionally creating (which
would fail).

Uniqueness on ``training_plan.lineage_id`` is a unique INDEX rather than a table
constraint, because an index can be added to a live table on both PostgreSQL and
SQLite. Only the NOT NULL tightening needs a batch rebuild on SQLite, and it is
guarded so it runs solely when the reflected column is actually nullable — the
create_all boot path already carries NOT NULL and must not pay for a rebuild.
"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = "b3c4d5e6f7a8"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None

_PLAN = "training_plan"
_JOURNAL = "plan_mutation_record"

_UQ_LINEAGE = "uq_training_plan_lineage_id"

# Every column app/models.py PlanMutationRecord requires. A pre-existing table
# missing any of these is structurally incompatible → fail closed rather than
# report a successful upgrade with an incomplete journal.
_REQUIRED_JOURNAL_COLUMNS = {
    "id", "public_id", "user_id", "plan_lineage_id", "operation_kind",
    "command_type", "command_fingerprint", "actor", "reason", "outcome",
    "before_version", "after_version", "before_snapshot", "after_snapshot",
    "before_fingerprint", "after_fingerprint", "idempotency_key",
    "reverts_mutation_id", "created_at",
}

_JOURNAL_INDEXES = ("ix_plan_mutation_record_user_id", "ix_plan_mutation_lineage")


def _columns(insp, table):
    return {col["name"] for col in insp.get_columns(table)}


def _unique_names(insp, table):
    try:
        return {uc["name"] for uc in insp.get_unique_constraints(table) if uc.get("name")}
    except Exception:  # noqa: BLE001 — dialect cannot introspect; treat as unknown
        return set()


def _index_names(insp, table):
    names = {ix["name"] for ix in insp.get_indexes(table) if ix.get("name")}
    # PostgreSQL backs unique constraints with indexes; fold both name spaces in
    # so nothing is ever created twice under a different lens.
    return names | _unique_names(insp, table)


def _create_journal():
    op.create_table(
        _JOURNAL,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_lineage_id", sa.String(length=64), nullable=False),
        sa.Column("operation_kind", sa.String(length=20), nullable=False),
        sa.Column("command_type", sa.String(length=60), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("before_version", sa.Integer(), nullable=False),
        sa.Column("after_version", sa.Integer(), nullable=False),
        sa.Column("before_snapshot", sa.Text(), nullable=False),
        sa.Column("after_snapshot", sa.Text(), nullable=False),
        sa.Column("before_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("after_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        # Soft self-reference on purpose (see app/models.py): a hard self-FK makes
        # owner purge order-dependent, and both cascade options corrupt the audit
        # trail. Uniqueness below is the invariant that matters.
        sa.Column("reverts_mutation_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_plan_mutation_public_id"),
        # THE race arbiter: one operation key per user resolves to one row, so a
        # retry that arrives while its twin is still in flight loses the INSERT
        # and replays instead of mutating a second time.
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_plan_mutation_user_key"),
        # A mutation can be reversed at most once, whatever the application
        # believes. NULLs do not collide, so original mutations are unaffected.
        sa.UniqueConstraint(
            "reverts_mutation_id", name="uq_plan_mutation_reverts"),
    )


def _create_missing_journal_indexes(existing):
    if "ix_plan_mutation_record_user_id" not in existing:
        op.create_index(
            "ix_plan_mutation_record_user_id", _JOURNAL, ["user_id"], unique=False)
    if "ix_plan_mutation_lineage" not in existing:
        # The undo selector's access path: newest entry of one lineage, owner-scoped.
        op.create_index(
            "ix_plan_mutation_lineage", _JOURNAL,
            ["user_id", "plan_lineage_id", "id"], unique=False)


def _upgrade_plan_columns(bind):
    insp = sa.inspect(bind)
    columns = {col["name"]: col for col in insp.get_columns(_PLAN)}

    if "lineage_id" not in columns:
        # Added nullable so the backfill below can give every existing row its
        # OWN identity; a shared server_default would merge distinct plans into
        # one lineage and is exactly the mistake this column exists to prevent.
        op.add_column(_PLAN, sa.Column("lineage_id", sa.String(length=64), nullable=True))

    if "mutation_version" not in columns:
        op.add_column(_PLAN, sa.Column(
            "mutation_version", sa.Integer(), nullable=False, server_default="0"))

    pending = bind.execute(
        sa.text("SELECT id FROM %s WHERE lineage_id IS NULL" % _PLAN)).fetchall()
    for row in pending:
        bind.execute(
            sa.text("UPDATE %s SET lineage_id = :lineage WHERE id = :plan_id" % _PLAN),
            {"lineage": secrets.token_urlsafe(32), "plan_id": row[0]},
        )

    # Only when the column is genuinely nullable: on SQLite this is a full table
    # rebuild, and the schema create_all builds already carries NOT NULL, so the
    # common boot path must not pay for it.
    existing = columns.get("lineage_id")
    if existing is None or existing.get("nullable", True):
        with op.batch_alter_table(_PLAN) as batch_op:
            batch_op.alter_column(
                "lineage_id", existing_type=sa.String(length=64), nullable=False)

    # A unique INDEX, not a table constraint: it can be added in place on both
    # backends, so uniqueness is enforced everywhere rather than on PostgreSQL
    # alone.
    if _UQ_LINEAGE not in _index_names(sa.inspect(bind), _PLAN):
        op.create_index(_UQ_LINEAGE, _PLAN, ["lineage_id"], unique=True)


def upgrade():
    bind = op.get_bind()
    _upgrade_plan_columns(bind)

    insp = sa.inspect(bind)
    if not insp.has_table(_JOURNAL):
        _create_journal()
        _create_missing_journal_indexes(existing=frozenset())
        return

    missing = _REQUIRED_JOURNAL_COLUMNS - _columns(insp, _JOURNAL)
    if missing:
        raise RuntimeError(
            "%s exists but is incompatible: missing required column(s) %s. "
            "Refusing to report a successful upgrade with an incomplete audit "
            "journal. Reconcile the table with app/models.py PlanMutationRecord."
            % (_JOURNAL, sorted(missing))
        )
    _create_missing_journal_indexes(_index_names(insp, _JOURNAL))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table(_JOURNAL):
        existing = {ix["name"] for ix in insp.get_indexes(_JOURNAL) if ix.get("name")}
        for name in _JOURNAL_INDEXES:
            if name in existing:
                op.drop_index(name, table_name=_JOURNAL)
        op.drop_table(_JOURNAL)

    insp = sa.inspect(bind)
    columns = _columns(insp, _PLAN)
    if _UQ_LINEAGE in {ix["name"] for ix in insp.get_indexes(_PLAN) if ix.get("name")}:
        # Dropped before the column it covers, and outside the batch block: a
        # rebuild would try to recreate an index over a column that is going away.
        op.drop_index(_UQ_LINEAGE, table_name=_PLAN)
    if not (columns & {"lineage_id", "mutation_version"}):
        return

    # One batch block = one SQLite table rebuild rather than two.
    with op.batch_alter_table(_PLAN) as batch_op:
        if "mutation_version" in columns:
            batch_op.drop_column("mutation_version")
        if "lineage_id" in columns:
            batch_op.drop_column("lineage_id")
