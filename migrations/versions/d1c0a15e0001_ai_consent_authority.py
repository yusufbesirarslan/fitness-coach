"""D1 account consent authority; draft, HOLD for #436 ancestry reconciliation.

Revision ID: d1c0a15e0001
Revises: b1a8c9d0e1f2 (authoritative main at 36d833b)
No customer backfill, inferred grants or modifications to #436 migrations.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd1c0a15e0001'
down_revision = 'b1a8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_consent_state',
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('generation', sa.BigInteger(), nullable=False),
        sa.Column('grants', sa.JSON(), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('generation > 0', name='ck_ai_consent_generation'))
    op.create_table('ai_consent_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_id', sa.String(80), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('generation', sa.BigInteger(), nullable=False),
        sa.Column('purpose', sa.String(80), nullable=False),
        sa.Column('version', sa.String(80), nullable=False),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('locale', sa.String(8), nullable=False),
        sa.Column('text_digest', sa.String(64), nullable=False),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('owner_id', 'request_id', name='uq_ai_consent_request'),
        sa.UniqueConstraint('owner_id', 'generation', name='uq_ai_consent_event_generation'),
        sa.CheckConstraint('generation > 0', name='ck_ai_consent_event_generation'),
        sa.CheckConstraint("action IN ('affirmative_grant', 'revoke')", name='ck_ai_consent_action'))


def downgrade():
    connection = op.get_bind()
    for table in ('ai_consent_event', 'ai_consent_state'):
        if connection.execute(sa.text('SELECT 1 FROM ' + table + ' LIMIT 1')).first():
            raise RuntimeError('D1 downgrade refuses to erase consent evidence; retain schema on rollback')
    op.drop_table('ai_consent_event')
    op.drop_table('ai_consent_state')
