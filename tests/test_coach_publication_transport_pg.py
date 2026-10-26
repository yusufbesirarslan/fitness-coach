"""Real PostgreSQL + running Gunicorn26 gthread + actual TCP native route.

Only identity/provider facts are synthetic. No socket send, consent decision,
database fence, WSGI response or transport is mocked. Events park execution
at named boundaries; PostgreSQL proves the competing withdrawal lock wait.
"""
import contextlib
import json
import multiprocessing
import os
import signal
import subprocess
import tempfile
from pathlib import Path
import socket
import struct
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests.test_mobile_training_generation_pg import pg_generation_app as _pg_generation_app

pytestmark = pytest.mark.pg_concurrency


@pytest.fixture
def pg_generation_app(monkeypatch):
    # macOS libpq GSS initialization is not fork safe. Disposable local PG has
    # no Kerberos; explicitly disable GSS before creating any worker connection.
    url = os.environ["PG_TEST_DATABASE_URL"]
    monkeypatch.setenv("PG_TEST_DATABASE_URL", url + ("&" if "?" in url else "?") + "gssencmode=disable")
    yield from _pg_generation_app.__wrapped__(monkeypatch)


@pytest.fixture(params=[False, True], ids=['legacy', 'metered'])
def server_rig(pg_generation_app, request):
    from app import consent_publication as publication
    from app.extensions import db, limiter
    from app.models import User
    from app.services import ai_consent as consent, ai_recovery, account_eligibility, ai_coach, ai_pipeline
    from app import mobile_auth_middleware
    from app.blueprints import mobile_coach as route
    from consent_projection_support import publish_synthetic_texts

    app, owners, patch = pg_generation_app
    if request.param:
        from ai_allowance_support import enable_subscriptions, premium_row
        from app.services.ai_allowances import execution
        from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
        enable_subscriptions(app, patch)
        app.config.update(AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
            AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
            AI_ALLOWANCE_POLICY=AllowancePolicy('synthetic-transport',
                premium=ScopePolicy('WEEKLY', {f:2 for f in SUPPORTED_FEATURES},1)))
        execution.install(app)
        with app.app_context():
            premium_row(owners[0])
    app.config.update(AI_CHAT_QUOTA_ENABLED=False, RATELIMIT_ENABLED=False)
    limiter.init_app(app)
    patch.setattr(limiter, "enabled", False)
    patch.setattr(route, "_check_rate_limits", lambda: None)
    patch.setattr(ai_recovery, "ai_cooldown_remaining", lambda _: 0)
    patch.setattr(ai_recovery, "clear_ai_failures", lambda _: None)
    patch.setattr(account_eligibility, "enforce_request", lambda _: None)

    def principal():
        from flask import g
        g.mobile_user = db.session.get(User, owners[0])
        return SimpleNamespace(user=g.mobile_user), None
    patch.setattr(mobile_auth_middleware, "_load_mobile_principal", principal)
    policy = consent.Disclosure("synthetic-coach-transport", "synthetic.coach",
                                ("synthetic",), (route.ai.heavy_primary_route(),), ())
    policy = publish_synthetic_texts(patch, {"coach": policy})["coach"]
    patch.setattr(consent, "POLICIES", {policy.version: policy})
    patch.setattr(consent, "BINDINGS", {"coach": (policy.purpose, policy.categories)})
    counter = 0

    def mutate(action="revoke"):
        nonlocal counter
        counter += 1
        with app.app_context():
            return consent.mutate(owners[0], dict(
                action=action, purpose=policy.purpose, version=policy.version,
                locale="en", text_digest=dict(policy.texts)["en"],
                contract_digest=consent.contract_digest(policy),
                expected_generation=consent.generation(owners[0]),
                request_id=f"transport-{counter:08d}"))
    mutate("affirmative_grant")
    # Native route, pipeline, conversation writes and projection stay real.
    # Only provider output/context facts are synthetic; no SDK is invoked.
    size = [64]
    switch = [False]

    def answer(*args, **kwargs):
        if switch[0]:
            from flask import g
            g.mobile_user = db.session.get(User, owners[1])
        return "SYNTHETIC-" + "x" * size[0]
    patch.setattr(ai_coach, "_run_coach_conversation", answer)
    patch.setattr(ai_pipeline, "_context_stage", lambda *a, **kw: {})
    app.add_url_rule("/api/v1/coach/messages", view_func=route.send_coach_message, methods=["POST"])
    app.add_url_rule("/health", view_func=lambda: "ok")
    app.wsgi_app = publication.ConsentPublication(app.wsgi_app)
    ctx = multiprocessing.get_context("fork")
    ready, release, headers, closed = [ctx.Event() for _ in range(4)]
    worker = ctx.Value("i", 0)
    publisher_pid = ctx.Value("i", 0)
    parent_pid = os.getpid()
    original_transaction = consent._transaction

    @contextlib.contextmanager
    def transaction(owner):
        with original_transaction(owner) as connection:
            if os.getpid() != parent_pid:
                publisher_pid.value = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            yield connection
    patch.setattr(consent, "_transaction", transaction)
    mode = ["normal"]
    original_protect = publication.protect_response
    original_send = publication._DeadlineSocket.sendall
    original_init = publication._DeadlineSocket.__init__
    original_close = publication._close

    def protect(response):
        result = original_protect(response)
        if mode[0] == "before":
            ready.set()
            assert release.wait(5)
        return result

    def init(self, sock, deadline):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        original_init(self, sock, deadline)

    def sendall(self, data):
        worker.value = os.getpid()
        if not ready.is_set() and mode[0] == "during":
            ready.set()
            assert release.wait(2)
        result = original_send(self, data)
        if bytes(data).startswith(b"HTTP/"):
            headers.set()  # actual header sendall completed
        return result

    def close(sock):
        original_close(sock)
        closed.set()

    patch.setattr(publication, "protect_response", protect)
    patch.setattr(publication._DeadlineSocket, "__init__", init)
    patch.setattr(publication._DeadlineSocket, "sendall", sendall)
    patch.setattr(publication, "_close", close)

    @contextlib.contextmanager
    def running(selected="normal", body_size=64, account_switch=False, proxy=False):
        from gunicorn.app.base import BaseApplication
        mode[0], size[0], switch[0] = selected, body_size, account_switch
        for event in (ready, release, headers, closed):
            event.clear()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()

        class Application(BaseApplication):
            def load_config(self):
                for key, value in dict(bind=f"127.0.0.1:{port}", workers=1,
                                       threads=8, timeout=300, graceful_timeout=2,
                                       accesslog=None, errorlog="/dev/null").items():
                    self.cfg.set(key, value)

            def load(self):
                return app

        def run():
            with app.app_context():
                db.engine.dispose(close=False)
            Application().run()

        process = ctx.Process(target=run)
        process.start()
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), .2) as probe:
                        probe.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                        assert b"200" in probe.recv(4096)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.01)
            nginx = None
            proxy_dir = None
            if proxy:
                import shutil
                binary = shutil.which("nginx")
                if binary is None:
                    pytest.skip("real nginx binary unavailable; direct TCP remains qualified")
                proxy_dir = tempfile.TemporaryDirectory(prefix="coach-consent-nginx-")
                chooser = socket.socket()
                chooser.bind(("127.0.0.1", 0))
                proxy_port = chooser.getsockname()[1]
                chooser.close()
                config = Path(proxy_dir.name) / "nginx.conf"
                config.write_text(f"""daemon off;
master_process off;
error_log {proxy_dir.name}/error.log;
pid {proxy_dir.name}/nginx.pid;
events {{ worker_connections 32; }}
http {{ access_log off; client_body_temp_path {proxy_dir.name}/body;
proxy_temp_path {proxy_dir.name}/proxy;
server {{ listen 127.0.0.1:{proxy_port};
location / {{ proxy_pass http://127.0.0.1:{port};
proxy_http_version 1.1; proxy_set_header Connection close;
proxy_buffering off; proxy_request_buffering off; }} }} }}
""")
                nginx = subprocess.Popen([binary, "-p", proxy_dir.name, "-c", str(config)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 3
                while True:
                    try:
                        with socket.create_connection(("127.0.0.1", proxy_port), .1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.01)
                port = proxy_port
            client = socket.socket()
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            client.settimeout(4)
            client.connect(("127.0.0.1", port))
            body = b'{"message":"synthetic question"}'
            allowance_header = b'Idempotency-Key: synthetic-transport-action\r\n' if request.param else b''
            client.sendall(b"POST /api/v1/coach/messages HTTP/1.1\r\nHost: localhost\r\n"
                           b"Authorization: Bearer synthetic\r\nContent-Type: application/json\r\n"
                           + allowance_header
                           + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            try:
                yield client
            finally:
                client.close()
                if nginx is not None:
                    nginx.terminate()
                    nginx.wait(timeout=3)
                if proxy_dir is not None:
                    proxy_dir.cleanup()
        finally:
            release.set()
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(2)

    def wait_for_lock():
        deadline = time.monotonic() + 1.5
        with app.app_context():
            while time.monotonic() < deadline:
                with db.engine.connect() as connection:
                    blocked = connection.execute(text("""
                        SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                        WHERE datname=current_database() AND wait_event_type='Lock'
                        AND :publisher = ANY(pg_blocking_pids(pid)))
                    """), {"publisher": publisher_pid.value}).scalar_one()
                if blocked:
                    return
                time.sleep(.005)
        pytest.fail("withdrawal did not reach real PostgreSQL lock wait")

    return SimpleNamespace(app=app, owners=owners, policy=policy, patch=patch, mutate=mutate, running=running,
                           metered=request.param,
                           ready=ready, release=release, headers=headers,
                           closed=closed, worker=worker, wait_for_lock=wait_for_lock)


def test_native_publication_ledger_mode(server_rig):
    from app.models import AiUsageReservation
    r=server_rig
    with r.running() as client:
        wire=receive(client)
        assert b'200 OK' in wire and b'SYNTHETIC-' in wire
    with r.app.app_context():
        rows=AiUsageReservation.query.all()
        if r.metered:
            assert len(rows)==1 and rows[0].feature=='coach' and rows[0].state=='COMMITTED'
        else:
            assert rows==[]


def receive(client):
    result = bytearray()
    while True:
        try:
            part = client.recv(65536)
        except ConnectionResetError:
            break
        if not part:
            break
        result.extend(part)
    return bytes(result)


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("regrant", [False, True])
def test_ack_first_sends_zero_bytes(server_rig, proxy, regrant):
    r = server_rig
    with r.running("before", proxy=proxy) as client:
        assert r.ready.wait(5)
        assert r.mutate()["generation"] == 2
        if regrant:
            assert r.mutate("affirmative_grant")["generation"] == 3
        r.release.set()
        wire = receive(client)
        assert b"SYNTHETIC-" not in wire
        # nginx may emit its own 502 after upstream terminal close.
        assert b"200 OK" not in wire
        if not proxy:
            assert wire == b""
        assert r.closed.wait(2)


@pytest.mark.parametrize("proxy", [False, True])
def test_publication_first_preserves_accepted_response(server_rig, proxy):
    r = server_rig
    results = []
    with r.running("during", proxy=proxy) as client:
        assert r.ready.wait(5)
        t = threading.Thread(target=lambda: results.append(r.mutate()))
        t.start()
        r.wait_for_lock()
        assert not results
        r.release.set()
        t.join(3)
        assert results[0]["generation"] == 2
        wire = receive(client)
        assert b"200 OK" in wire and b"SYNTHETIC-" in wire
        assert not r.closed.is_set()


@pytest.mark.parametrize("failure", ["saturated", "slow_reader", "disconnect", "worker_kill"])
def test_partial_write_then_withdrawal_is_bounded(server_rig, failure):
    r = server_rig
    results, errors = [], []
    with r.running(body_size=900000) as client:
        assert r.headers.wait(5)  # a real socket accepted a prefix
        started = time.monotonic()

        def withdraw():
            try:
                results.append(r.mutate())
                if failure != "worker_kill":
                    assert r.closed.is_set(), "withdrawal ACK preceded terminal socket close"
            except BaseException as error:
                errors.append(error)
        reader_stop = threading.Event()
        reader = None
        if failure == "slow_reader":
            def read_slowly():
                while not reader_stop.is_set():
                    try:
                        if not client.recv(256):
                            return
                    except OSError:
                        return
                    reader_stop.wait(.03)
            reader = threading.Thread(target=read_slowly)
            reader.start()
        t = threading.Thread(target=withdraw)
        t.start()
        r.wait_for_lock()
        if failure == "disconnect":
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.close()
        elif failure == "worker_kill":
            os.kill(r.worker.value, signal.SIGKILL)
        t.join(3)
        assert not t.is_alive() and not errors
        assert results[0]["generation"] == 2
        assert time.monotonic() - started < 2
        reader_stop.set()
        if reader is not None:
            reader.join(2)
            assert not reader.is_alive()
        if failure != "worker_kill":
            assert r.closed.wait(1)
        if failure == "saturated":
            wire = receive(client)
            assert b"HTTP/1.1 200 OK" in wire
            assert len(wire) < 900000  # no success after a timed-out partial send


def test_expired_deadline_sends_zero_bytes(server_rig, monkeypatch):
    from app import consent_publication
    monkeypatch.setattr(consent_publication, "SEND_SECONDS", 0)
    with server_rig.running() as client:
        assert receive(client) == b""
        assert server_rig.closed.wait(2)
        assert server_rig.mutate()["generation"] == 2


def test_concurrent_account_switch_cannot_publish(server_rig):
    with server_rig.running(account_switch=True) as client:
        wire = receive(client)
        assert b"SYNTHETIC-" not in wire
        assert b"403" in wire


def test_real_proxy_zero_reader_preserves_backend_accepted_bytes(server_rig):
    r = server_rig
    with r.running(body_size=900000, proxy=True) as client:
        assert r.headers.wait(5)
        started = time.monotonic()
        assert r.mutate()["generation"] == 2
        assert time.monotonic() - started < 2
        wire = receive(client)
        assert b"200 OK" in wire and b"SYNTHETIC-" in wire
        # Proxy/kernel accepted bytes can arrive after revoke ACK. If backend
        # deadline cut the body short, the response remains visibly incomplete.
        assert len(wire) > 100
