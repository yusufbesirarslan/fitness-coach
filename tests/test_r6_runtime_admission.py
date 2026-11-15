"""Loaded runtime admission failures never collapse into truthy PASS."""
import json
import re
from types import SimpleNamespace

import pytest

from tests.test_r6_deploy_transaction import CAND, PREV, Recorder, host, tx


@pytest.mark.parametrize("key,value", [
    ("policy", None), ("policy", "age_only"), ("revision", "a"*40),
    ("deletion_acl", False), ("deletion_acl", 1),
    ("pooled_utc", False), ("pooled_utc", None), ("boot", ""),
])
def test_serving_runtime_refuses_missing_or_false_proof(tmp_path, key, value):
    ops = host(tmp_path, Recorder())
    proof = {"revision": CAND, "policy": "dob_18_plus_v1", "deletion_acl": True,
             "pooled_utc": True, "boot": "f"*32, "closed": True}
    proof[key] = value
    ops._docker = lambda *a, **k: json.dumps(proof)
    with pytest.raises(tx.OpError, match="loaded adult"):
        ops._adult_web_proof("cid", 10)


def test_serving_runtime_accepts_only_complete_loaded_proof(tmp_path):
    ops = host(tmp_path, Recorder())
    proof = {"revision": CAND, "policy": "dob_18_plus_v1", "deletion_acl": True,
             "pooled_utc": True, "boot": "f"*32, "closed": True}
    ops._docker = lambda *a, **k: json.dumps(proof)
    assert ops._adult_web_proof("cid", 10) == proof


@pytest.mark.parametrize("timezone", ["UTC", "Etc/UTC", "Europe/Istanbul", None])
def test_pool_checkout_rejects_and_invalidates_non_utc(timezone):
    from app.extensions import _require_postgres_pool_utc
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def execute(self, sql):
            assert sql == "SHOW TIME ZONE"
        def fetchone(self):
            return (timezone,)
    class Connection:
        __module__ = "psycopg2.extensions"
        def cursor(self):
            return Cursor()
        def rollback(self):
            pass
    invalidated = []
    record = SimpleNamespace(invalidate=lambda: invalidated.append(True))
    if timezone in ("UTC", "Etc/UTC"):
        _require_postgres_pool_utc(Connection(), record, None)
        assert not invalidated
    else:
        with pytest.raises(RuntimeError, match="R6_POOLED_UTC_REQUIRED"):
            _require_postgres_pool_utc(Connection(), record, None)
        assert invalidated


def test_process_restart_invalidates_registration_open_receipt(app, monkeypatch, tmp_path):
    from app.services import r6_runtime_admission as a
    p = tmp_path / "open"
    monkeypatch.setattr(a, "WEB_RECEIPT", str(p))
    app.config.update(R6_REGISTRATION_CLOSED=True, BUILD_REVISION=CAND,
                      R6_ADMISSION_BOOT="first")
    p.write_text(json.dumps({"boot": "first", "revision": CAND}))
    with app.app_context():
        assert a.is_open()
        app.config["R6_ADMISSION_BOOT"] = "second"
        assert not a.is_open()


def test_failed_candidate_acl_proof_is_sanitized(app, monkeypatch):
    from app.services import r6_runtime_admission as a
    from app.services import bedrock_health
    def unavailable():
        raise RuntimeError("secret-connection-string")
    monkeypatch.setattr(a, "database_proof", unavailable)
    monkeypatch.setattr(bedrock_health, "probe", lambda: {"configured": False})
    app.config["R6_REGISTRATION_CLOSED"] = True
    response = app.test_client().get("/health?deep=1")
    assert response.status_code == 503
    assert response.get_json()["r6_admission"] == {"ready": False}
    assert "secret-connection-string" not in response.get_data(as_text=True)


@pytest.mark.parametrize("value", [None, False, 1, "1", True])
def test_loaded_adult_policy_requires_exact_enabled_boolean(app, monkeypatch, value):
    from app.services import r6_runtime_admission as a
    monkeypatch.setattr(a, "database_proof", lambda: {"deletion_acl": True, "pooled_utc": True})
    monkeypatch.setattr(a, "process_identity", lambda: {"pid": 7, "start_ticks": 10})
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = value
    with app.app_context():
        assert a.web_proof()["policy"] == (a.POLICY if value is True else None)


@pytest.mark.parametrize("published", [False, True])
def test_worker_exposed_port_is_not_a_direct_origin(tmp_path, published):
    ops = host(tmp_path, Recorder())
    worker = "1"*64
    web = "2"*64
    rows = [
        [worker, {"com.docker.compose.service": "worker"},
         {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5000"}] if published else None}],
        [web, {"com.docker.compose.service": "web", "com.docker.compose.project": "axisai-web-blue"},
         {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5001"}]}],
    ]
    def docker(timeout, *args, **kwargs):
        if args[0] == "ps":
            return worker + "\n" + web
        if args[0] == "inspect":
            return "\n".join(json.dumps(row) for row in rows)
        raise AssertionError(args)
    ops._docker = docker
    ops.container_build_revision = lambda *a: PREV
    if published:
        with pytest.raises(tx.OpError, match="foreign application"):
            ops.adult_web_inventory("blue", PREV, "green", CAND, False, 20)
    else:
        assert ops.adult_web_inventory("blue", PREV, "green", CAND, False, 20)


@pytest.mark.parametrize("state", [[False, 0], [False, 21], [True, 21]])
def test_stopped_recovery_candidate_needs_no_exec_but_proves_absence(tmp_path, state):
    ops = host(tmp_path, Recorder())
    ops.slot_inspect = lambda *a: [{"id": "candidate", "app_revision": CAND, "status": "exited"}]
    calls = []
    def docker(timeout, *args, **kwargs):
        calls.append(args)
        assert args[0] == "inspect"  # no exec into a stopped container
        return json.dumps(state)
    ops._docker = docker
    if state == [False, 0]:
        ops.registration_latch("green", CAND, "close", 20)
    else:
        with pytest.raises(tx.OpError, match="absence unproven"):
            ops.registration_latch("green", CAND, "close", 20)
    assert len(calls) == 1


@pytest.mark.parametrize("path", ["/register", "/api/v1/auth/register"])
def test_closed_registration_preserves_transport_error_contract(app, path):
    app.config.update(R6_REGISTRATION_CLOSED=True, R6_ADMISSION_BOOT="never-opened")
    response = app.test_client().post(path, data="not-json", content_type="application/json")
    assert response.status_code == 503
    error = response.get_json()["error"]
    if path == "/register":
        assert isinstance(error, str) and error
    else:
        assert set(error) == {"code", "message", "retryable", "request_id"}
        assert error["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
        assert isinstance(error["message"], str) and error["message"]
        assert error["retryable"] is True
        assert re.fullmatch("[0-9a-f]{16}", error["request_id"])
