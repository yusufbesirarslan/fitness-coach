"""AXISAI-FATSECRET-RETENTION-MVP-PHASE1-01: provider retention provenance.

Expand only:

* ``meal_log.provider_items`` / ``provider_acquired_at`` / ``provider_expired_at``
  (nullable): server-written FatSecret references, the provider acquisition
  instant the 24h deadline runs from, and the instant the restricted per-item
  values were removed;
* ``custom_meal_item.provider_acquired_at`` / ``provider_expired_at`` (nullable);
* non-unique index ``ix_meal_log_provider_acquired_at`` for the expiry pass;
* new table ``meal_log_retained_total`` (PK owner + Istanbul day, CASCADE with
  its owner): the permitted daily aggregate of expired single-item values.

No backfill and no row is rewritten. Legacy rows keep NULL provenance and are
never touched by the expiry pass (separate founder-approved disposition). The
previous revision never reads or writes these objects, so it keeps working
against the expanded schema. Reentrant: a create_all-first boot already has the
objects, which are verified rather than recreated.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e1a5f0c3d2b4"
down_revision = "f01c0a1e0001"
branch_labels = None
depends_on = None
expand_contract = "expand"

MEAL_LOG = "meal_log"
ITEM = "custom_meal_item"
TOTAL = "meal_log_retained_total"
INDEX = "ix_meal_log_provider_acquired_at"


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _total_columns():
    return [
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tarih", sa.String(length=10), nullable=False),
        sa.Column("energy_kcal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("protein_g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("carbohydrate_g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fat_g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "tarih"),
    ]


def _fail(what):
    raise RuntimeError(f"FatSecret retention schema has incompatible {what}")


def _existing(bind, table):
    return {c["name"]: c for c in sa.inspect(bind).get_columns(table)}


def _verify_nullable(columns, table, name):
    if columns[name]["nullable"] is not True:
        _fail(f"{table}.{name} (must be nullable)")


def upgrade():
    bind = op.get_bind()
    columns = _existing(bind, MEAL_LOG)
    if "provider_items" in columns:
        _verify_nullable(columns, MEAL_LOG, "provider_items")
    else:
        op.add_column(MEAL_LOG, sa.Column("provider_items", _json_type(), nullable=True))
    if "provider_acquired_at" in columns:
        _verify_nullable(columns, MEAL_LOG, "provider_acquired_at")
    else:
        op.add_column(MEAL_LOG, sa.Column("provider_acquired_at", sa.DateTime(), nullable=True))
    if "provider_expired_at" in columns:
        _verify_nullable(columns, MEAL_LOG, "provider_expired_at")
    else:
        op.add_column(MEAL_LOG, sa.Column("provider_expired_at", sa.DateTime(), nullable=True))

    columns = _existing(bind, ITEM)
    if "provider_acquired_at" in columns:
        _verify_nullable(columns, ITEM, "provider_acquired_at")
    else:
        op.add_column(ITEM, sa.Column("provider_acquired_at", sa.DateTime(), nullable=True))
    if "provider_expired_at" in columns:
        _verify_nullable(columns, ITEM, "provider_expired_at")
    else:
        op.add_column(ITEM, sa.Column("provider_expired_at", sa.DateTime(), nullable=True))

    indexes = {i["name"]: i for i in sa.inspect(bind).get_indexes(MEAL_LOG)}
    if INDEX not in indexes:
        op.create_index(INDEX, MEAL_LOG, ["provider_acquired_at"], unique=False)
    elif (indexes[INDEX]["column_names"] != ["provider_acquired_at"]
            or indexes[INDEX].get("unique")):
        _fail(f"index {INDEX}")

    inspector = sa.inspect(bind)
    if not inspector.has_table(TOTAL):
        op.create_table(TOTAL, *_total_columns())
        return
    expected = {c.name: c for c in _total_columns() if isinstance(c, sa.Column)}
    columns = {c["name"]: c for c in inspector.get_columns(TOTAL)}
    if set(columns) != set(expected) or any(
            columns[name]["nullable"] is not col.nullable for name, col in expected.items()):
        _fail(f"table {TOTAL} columns")
    pk = inspector.get_pk_constraint(TOTAL).get("constrained_columns") or []
    if sorted(pk) != ["tarih", "user_id"]:
        _fail(f"table {TOTAL} primary key")


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {TOTAL} LIMIT 1")).first() is not None:
        raise RuntimeError("retained FatSecret totals exist; refusing downgrade")
    if bind.execute(sa.text(
            f"SELECT 1 FROM {MEAL_LOG} WHERE provider_acquired_at IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("provider provenance exists; refusing downgrade")
    op.drop_table(TOTAL)
    op.drop_index(INDEX, table_name=MEAL_LOG)
    with op.batch_alter_table(ITEM) as batch:
        batch.drop_column("provider_expired_at")
        batch.drop_column("provider_acquired_at")
    with op.batch_alter_table(MEAL_LOG) as batch:
        batch.drop_column("provider_expired_at")
        batch.drop_column("provider_acquired_at")
        batch.drop_column("provider_items")
