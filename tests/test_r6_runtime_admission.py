"""Loaded runtime admission failures never collapse into truthy PASS."""
import json
from types import SimpleNamespace

import pytest

from tests.test_r6_deploy_transaction import CAND, Recorder, host, tx


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


@pytest.mark.parametrize("timezone", ["UTC", "Europe/Istanbul", None])
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
    if timezone == "UTC":
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
