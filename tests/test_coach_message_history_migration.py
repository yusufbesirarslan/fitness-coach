"""Contract for the bounded Coach-message history access path."""
import importlib.util
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models import CoachConversation, CoachMessage
from app.services import memory_manager


VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "b7c8d9e0f1a2_add_coach_message_history_index.py"
INDEX = "ix_coach_message_conversation_id_id"
TABLE = "coach_message"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "coach_message_history_index_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _legacy_engine(tmp_path, name="coach-history-index.db"):
    engine = sa.create_engine(f"sqlite:///{tmp_path / name}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("""
                CREATE TABLE coach_message (
                    id INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    role VARCHAR(12) NOT NULL,
                    content TEXT NOT NULL,
                    token_estimate INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    interrupted BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL
                )
            """))
            connection.execute(sa.text(
                "CREATE INDEX ix_coach_message_conversation_id "
                "ON coach_message (conversation_id)"))
            yield connection
    finally:
        engine.dispose()


def _run(connection, operation):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        operation()


def _indexes(connection):
    return {
        item["name"]: tuple(item["column_names"])
        for item in sa.inspect(connection).get_indexes(TABLE)
    }


def test_the_model_declares_the_coach_history_index():
    declared = {
        index.name: tuple(column.name for column in index.columns)
        for index in CoachMessage.__table__.indexes
    }

    assert declared[INDEX] == ("conversation_id", "id")


def test_the_migration_descends_from_the_current_head():
    migration = _load_migration()

    assert migration.revision == "b7c8d9e0f1a2"
    assert migration.down_revision == "a6b7c8d9e0f1"


def test_the_migration_adds_nothing_but_the_index():
    source = MIGRATION.read_text(encoding="utf-8")

    for forbidden in (
            "add_column", "drop_column", "alter_column", "create_table",
            "drop_table", "execute(", "UPDATE ", "DELETE "):
        assert forbidden not in source, forbidden


def test_upgrade_creates_the_index_on_a_legacy_schema(tmp_path):
    with _legacy_engine(tmp_path) as connection:
        assert INDEX not in _indexes(connection)

        _run(connection, _load_migration().upgrade)

        assert _indexes(connection)[INDEX] == ("conversation_id", "id")


def test_upgrade_is_re_runnable_over_a_create_all_schema(app):
    with app.app_context():
        with db.engine.begin() as connection:
            migration = _load_migration()
            _run(connection, migration.upgrade)
            _run(connection, migration.upgrade)

            assert _indexes(connection)[INDEX] == ("conversation_id", "id")


def test_downgrade_removes_only_the_new_index_and_keeps_data(tmp_path):
    with _legacy_engine(tmp_path, "coach-history-downgrade.db") as connection:
        migration = _load_migration()
        connection.execute(sa.text("""
            INSERT INTO coach_message
                (id, conversation_id, role, content, created_at)
            VALUES (1, 7, 'user', 'keep me', CURRENT_TIMESTAMP)
        """))
        _run(connection, migration.upgrade)

        _run(connection, migration.downgrade)

        indexes = _indexes(connection)
        assert INDEX not in indexes
        assert indexes["ix_coach_message_conversation_id"] == (
            "conversation_id",)
        assert connection.execute(sa.text(
            "SELECT content FROM coach_message WHERE id = 1")).scalar() == (
                "keep me")
        assert sa.inspect(connection).has_table(TABLE)


def test_downgrade_is_re_runnable(tmp_path):
    with _legacy_engine(tmp_path, "coach-history-downgrade-twice.db") as connection:
        migration = _load_migration()
        _run(connection, migration.upgrade)

        _run(connection, migration.downgrade)
        _run(connection, migration.downgrade)

        indexes = _indexes(connection)
        assert INDEX not in indexes
        assert "ix_coach_message_conversation_id" in indexes


def test_recent_messages_selects_newest_n_then_returns_chronologically(
        app, make_user):
    with app.app_context():
        user = make_user("coach-history-order")
        conversation = CoachConversation(user_id=user.id)
        db.session.add(conversation)
        db.session.flush()
        for number in range(1, 6):
            db.session.add(CoachMessage(
                conversation_id=conversation.id,
                role="user" if number % 2 else "assistant",
                content=f"message-{number}",
            ))
        db.session.commit()

        selected_conversation, messages = memory_manager.recent_messages(
            user.id, limit=3)

        assert selected_conversation.id == conversation.id
        assert [message.content for message in messages] == [
            "message-3", "message-4", "message-5"]
