"""Opt-in PostgreSQL proof for Pump Check idempotency uniqueness."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency


def _url():
    return os.environ.get("PG_TEST_DATABASE_URL")


@pytest.mark.skipif(
    os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1" or not _url(),
    reason="requires isolated PostgreSQL concurrency database",
)
def test_same_user_same_key_race_has_one_database_winner():
    engine = sa.create_engine(_url(), pool_size=4)
    table = "pump_check_idem_probe"
    with engine.begin() as connection:
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
        connection.execute(sa.text(f"""
            CREATE TABLE {table} (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                operation_key VARCHAR(64) NOT NULL,
                fingerprint VARCHAR(64) NOT NULL,
                UNIQUE (user_id, operation_key)
            )
        """))

    def insert():
        try:
            with engine.begin() as connection:
                connection.execute(
                    sa.text(f"""INSERT INTO {table}
                        (user_id, operation_key, fingerprint)
                        VALUES (7, 'same-operation', 'same-command')"""))
            return "created"
        except sa.exc.IntegrityError:
            return "replay"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: insert(), range(2)))
    with engine.begin() as connection:
        count = connection.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        connection.execute(sa.text(f"DROP TABLE {table}"))
    assert outcomes == ["created", "replay"]
    assert count == 1


@pytest.mark.skipif(
    os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1" or not _url(),
    reason="requires isolated PostgreSQL concurrency database",
)
def test_same_user_same_key_different_command_has_one_winner_and_conflict():
    engine = sa.create_engine(_url(), pool_size=4)
    table = "pump_check_conflict_probe"
    with engine.begin() as connection:
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
        connection.execute(sa.text(f"""
            CREATE TABLE {table} (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                operation_key VARCHAR(64) NOT NULL,
                fingerprint VARCHAR(64) NOT NULL,
                UNIQUE (user_id, operation_key)
            )
        """))

    def insert(fingerprint):
        try:
            with engine.begin() as connection:
                connection.execute(
                    sa.text(f"""INSERT INTO {table}
                        (user_id, operation_key, fingerprint)
                        VALUES (7, 'same-operation', :fingerprint)"""),
                    {"fingerprint": fingerprint})
            return "created"
        except sa.exc.IntegrityError:
            with engine.begin() as connection:
                winner = connection.scalar(sa.text(
                    f"""SELECT fingerprint FROM {table}
                        WHERE user_id=7 AND operation_key='same-operation'"""))
            return "replay" if winner == fingerprint else "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(insert, ("command-a", "command-b")))
    with engine.begin() as connection:
        count = connection.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        connection.execute(sa.text(f"DROP TABLE {table}"))
    assert outcomes == ["conflict", "created"]
    assert count == 1


@pytest.mark.skipif(
    os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1" or not _url(),
    reason="requires isolated PostgreSQL concurrency database",
)
def test_cross_user_same_key_race_is_independent():
    engine = sa.create_engine(_url(), pool_size=4)
    table = "pump_check_owner_probe"
    with engine.begin() as connection:
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
        connection.execute(sa.text(f"""
            CREATE TABLE {table} (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                operation_key VARCHAR(64) NOT NULL,
                fingerprint VARCHAR(64) NOT NULL,
                UNIQUE (user_id, operation_key)
            )
        """))

    def insert(user_id):
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"""INSERT INTO {table}
                    (user_id, operation_key, fingerprint)
                    VALUES (:user_id, 'same-operation', 'same-command')"""),
                {"user_id": user_id})
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(insert, (7, 8)))
    with engine.begin() as connection:
        count = connection.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        connection.execute(sa.text(f"DROP TABLE {table}"))
    assert outcomes == ["created", "created"]
    assert count == 2
