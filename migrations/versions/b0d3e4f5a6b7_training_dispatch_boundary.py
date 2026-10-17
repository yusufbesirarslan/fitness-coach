"""Conservative native Training dispatch marker; historical rows remain unknown."""
from alembic import op
import sqlalchemy as sa

revision = "b0d3e4f5a6b7"
down_revision = "a9c1d2e3f4b5"
branch_labels = None
depends_on = None
expand_contract = "expand"


def upgrade():
    columns = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns(
        "training_plan_generation_operation")}
    existing = columns.get("provider_dispatch_possible")
    if existing is None:
        op.add_column("training_plan_generation_operation", sa.Column(
            "provider_dispatch_possible", sa.Boolean(), nullable=True))
    elif not isinstance(existing["type"], sa.Boolean) or not existing["nullable"]:
        raise RuntimeError("incompatible Training dispatch marker")


def downgrade():
    # Removing the marker would restore unsafe redispatch of existing work.
    raise RuntimeError("dispatch evidence must survive runtime rollback")
