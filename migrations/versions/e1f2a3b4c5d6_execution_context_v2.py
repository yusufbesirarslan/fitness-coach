"""TI-01A additive canonical execution context and immutable prescription.

Downgrade is destructive and only appropriate on disposable databases after
an explicit retention decision. Operational rollback keeps these columns.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = 'd0e1f2a3b4c5'
branch_labels = None
depends_on = None


def upgrade():
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('workout_session')}
    for name in ('execution_context_data', 'prescription_data'):
        if name not in existing:
            op.add_column('workout_session', sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    for name in ('prescription_data', 'execution_context_data'):
        op.drop_column('workout_session', name)
