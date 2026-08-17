"""Add the canonical mobile Pump Check history index.

Sprint 10 PR4A. `GET /api/v1/pump-checks` pages with
`WHERE user_id = ? ORDER BY captured_at DESC, id DESC`. No existing index could
order by `captured_at`, so every page scanned all of the owner's rows and sorted
them in a temporary b-tree.

Both sort keys are DESC, so a plain ascending composite index is scanned
backwards to satisfy the order; a DESC index would buy nothing and would make
the definition dialect-sensitive.

Additive only: one index, no column change, no table change, no data rewrite,
and nothing touched on the PR3 comparison tables.

The guard exists because a fresh database boots through `db.create_all()`, which
already builds this index from the model, before Alembic replays the chain
(app/db_init.py). Creating it again would abort the boot.

Revision ID: c1d2e3f4a5b6
Revises: fa1b2c3d4e5f
"""
import sqlalchemy as sa
from alembic import op


revision = "c1d2e3f4a5b6"
down_revision = "fa1b2c3d4e5f"
branch_labels = None
depends_on = None


TABLE = "pump_check"
INDEX = "ix_pump_check_user_captured"
COLUMNS = ["user_id", "captured_at", "id"]


def _has_index(bind):
    inspector = sa.inspect(bind)
    return any(
        item["name"] == INDEX for item in inspector.get_indexes(TABLE))


def upgrade():
    bind = op.get_bind()
    if _has_index(bind):
        return
    op.create_index(INDEX, TABLE, COLUMNS, unique=False)


def downgrade():
    bind = op.get_bind()
    if not _has_index(bind):
        return
    op.drop_index(INDEX, table_name=TABLE)
