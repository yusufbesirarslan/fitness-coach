import sqlalchemy as sa

from app.extensions import db
from app.models import TrainingPlanGenerationOperation


def _unique_columns(table, name):
    constraint = next(
        item for item in table.constraints
        if isinstance(item, sa.UniqueConstraint) and item.name == name
    )
    return tuple(column.name for column in constraint.columns)


def test_generation_operation_has_bounded_durable_contract():
    table = TrainingPlanGenerationOperation.__table__

    assert table.name == "training_plan_generation_operation"
    assert _unique_columns(table, "uq_training_plan_generation_user_key") == (
        "user_id", "idempotency_key")
    assert table.c.idempotency_key.type.length == 64
    assert table.c.request_fingerprint.type.length == 64
    assert table.c.status.type.length == 20
    assert table.c.candidate_plan_data.nullable is True
    assert table.c.training_plan_id.foreign_keys == set()
    assert table.c.plan_lineage_id.type.length == 64
    assert table.c.quota_reserved.nullable is False


def test_generation_operation_has_active_owner_uniqueness_on_both_dialects():
    table = TrainingPlanGenerationOperation.__table__
    active = next(
        index for index in table.indexes
        if index.name == "uq_training_plan_generation_active_owner"
    )

    assert active.unique is True
    assert tuple(column.name for column in active.columns) == ("user_id",)
    sqlite_where = str(active.dialect_options["sqlite"]["where"])
    postgres_where = str(active.dialect_options["postgresql"]["where"])
    assert "IN_PROGRESS" in sqlite_where and "GENERATED" in sqlite_where
    assert "IN_PROGRESS" in postgres_where and "GENERATED" in postgres_where


def test_generation_operation_cascades_with_user(app, make_user):
    user = make_user("generation-operation-cascade")
    operation = TrainingPlanGenerationOperation(
        user_id=user.id,
        idempotency_key="generation-operation-key",
        request_fingerprint="f" * 64,
        status="FAILED",
    )
    db.session.add(operation)
    db.session.commit()
    operation_id = operation.id

    db.session.delete(user)
    db.session.commit()

    assert db.session.get(TrainingPlanGenerationOperation, operation_id) is None
