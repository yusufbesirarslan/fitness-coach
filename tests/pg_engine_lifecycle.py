"""Own PostgreSQL pools for one disposable-test lifetime.

Connection.close()/Session.remove() return connections to a pool; they do not
close that pool. Some schema-cleanup helpers also reconnect after disposing.
Keep pooling and concurrent checkouts intact until all test fixtures finish,
then release every pool used by the test, including replacement pools.
"""
from contextlib import contextmanager
from threading import Lock

import sqlalchemy as sa


@contextmanager
def disposable_pg_engine_scope():
    engines = set()
    lock = Lock()

    def remember(connection):
        if connection.dialect.name == "postgresql":
            with lock:
                engines.add(connection.engine)

    sa.event.listen(sa.engine.Engine, "engine_connect", remember)
    try:
        yield
    finally:
        sa.event.remove(sa.engine.Engine, "engine_connect", remember)
        # The caller is the outermost function fixture: request contexts and
        # race workers have already ended. Dispose here, after schema teardown,
        # rather than before a DROP that would populate a replacement pool.
        for engine in engines:
            engine.dispose()
