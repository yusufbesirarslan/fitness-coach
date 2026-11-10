"""Add M4 retry evidence, without changing consent or existing migration bytes."""
from alembic import op
import sqlalchemy as sa

revision = "f01a4e000003"
down_revision = "f01e2a5e0002"
branch_labels = None
depends_on = None
expand_contract = "expand"


def upgrade():
    bind = op.get_bind()
    expected = sa.Table("health_erasure_retry", sa.MetaData(),
        sa.Column("erasure_id", sa.Integer, primary_key=True),
        sa.Column("passes", sa.Integer, nullable=False),
        sa.Column("last_slot", sa.BigInteger, nullable=False))
    inspector = sa.inspect(bind)
    if inspector.has_table(expected.name):
        columns = {c["name"]: c for c in inspector.get_columns(expected.name)}
        if set(columns) != set(expected.c.keys()) or any(
            columns[c.name]["nullable"] != c.nullable or
            columns[c.name]["type"]._type_affinity != c.type._type_affinity for c in expected.c):
            raise RuntimeError("incompatible erasure retry monitoring schema")
        if inspector.get_pk_constraint(expected.name)["constrained_columns"] != ["erasure_id"]:
            raise RuntimeError("missing erasure retry identity")
        if not any(f["constrained_columns"] == ["erasure_id"] and
                   f["referred_table"] == "health_erasure" and f["referred_columns"] == ["id"] and
                   f.get("options", {}).get("ondelete") == "CASCADE"
                   for f in inspector.get_foreign_keys(expected.name)):
            raise RuntimeError("missing erasure retry retention cascade")
        return
    op.create_table(expected.name,
        sa.Column("erasure_id", sa.Integer, sa.ForeignKey("health_erasure.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("passes", sa.Integer, nullable=False),
        sa.Column("last_slot", sa.BigInteger, nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM health_erasure_retry LIMIT 1")).first():
        raise RuntimeError("retain populated retry evidence on rollback")
    op.drop_table("health_erasure_retry")
