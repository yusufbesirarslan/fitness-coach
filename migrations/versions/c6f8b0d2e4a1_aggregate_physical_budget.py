"""Expand reservation evidence without backfilling execution authority."""
from alembic import op
import sqlalchemy as sa

revision = 'c6f8b0d2e4a1'
down_revision = 'b5e7a9c1d3f0'
branch_labels = None
depends_on = None
expand_contract = 'expand'

FIELDS = {'physical_budget': sa.Text,
          'physical_input_reserved': sa.BigInteger,
          'physical_output_reserved': sa.BigInteger,
          'physical_images_reserved': sa.BigInteger,
          'physical_cost_reserved': sa.BigInteger}


def upgrade():
    columns = {c['name']: c for c in sa.inspect(op.get_bind()).get_columns('ai_usage_reservation')}
    # Validate every pre-existing field before any DDL, including integer width.
    for name, kind in FIELDS.items():
        c = columns.get(name)
        if c is not None and (not isinstance(c['type'], kind) or not c['nullable']
                or c.get('default') is not None or c.get('computed') or c.get('identity')):
            raise RuntimeError('aggregate physical budget schema mismatch')
    for name, kind in FIELDS.items():
        if name not in columns:
            op.add_column('ai_usage_reservation', sa.Column(name, kind(), nullable=True))


def downgrade():
    # Financial evidence is forward-only; rollback never erases reservations.
    pass
