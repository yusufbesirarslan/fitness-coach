"""Nonblocking owner locks for native training-plan generation."""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager

from app.extensions import db


_LOCK_DOMAIN = b"axisai:training-plan-generation-owner-lock:v1\0"
_LOCAL_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[int, threading.Lock] = {}


def _advisory_key(user_id: int) -> int:
    digest = hashlib.sha256(_LOCK_DOMAIN + str(int(user_id)).encode("ascii")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def _local_owner_lock(user_id: int):
    with _LOCAL_GUARD:
        lock = _LOCAL_LOCKS.setdefault(int(user_id), threading.Lock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


@contextmanager
def try_owner_lock(user_id: int):
    """Yield whether this worker acquired the owner's nonblocking lock."""
    if db.engine.dialect.name != "postgresql":
        with _local_owner_lock(user_id) as acquired:
            yield acquired
        return

    key = _advisory_key(user_id)
    connection = db.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    acquired = False
    try:
        acquired = bool(connection.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (key,)).scalar())
        yield acquired
    finally:
        try:
            if acquired:
                connection.exec_driver_sql(
                    "SELECT pg_advisory_unlock(%s)", (key,))
        finally:
            connection.close()
