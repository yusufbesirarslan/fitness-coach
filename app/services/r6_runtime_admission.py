"""R6 candidate admission: local process proofs, never provider/business calls.

Slot web processes start with registration closed. Only the protected host
transaction can publish a boot-bound local receipt after its other gates.
No HTTP endpoint, client value or launch eligibility flag opens this latch.
"""
import json
import os
import socket
import threading
import uuid
from pathlib import Path

from flask import current_app

WEB_RECEIPT = "/tmp/axisai-r6-registration-open.json"
WORKER_RECEIPT = "/tmp/axisai-r6-worker-ready.json"
POLICY = "dob_18_plus_v1"


def process_identity():
    pid = os.getpid()
    ticks = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[19]
    return {"pid": pid, "start_ticks": int(ticks)}


def install(app):
    # The slot runtime supplies this value and validates the constructed
    # container. Ordinary non-slot development retains its existing behavior.
    app.config["R6_REGISTRATION_CLOSED"] = os.environ.get(
        "R6_REGISTRATION_ADMISSION") == "closed"
    if app.config["R6_REGISTRATION_CLOSED"] or os.environ.get("R6_RUNTIME_ADMISSION") == "required":
        app.extensions["r6_coach_containment"] = True
    app.config["R6_ADMISSION_BOOT"] = uuid.uuid4().hex
    @app.before_request
    def registration_boundary():
        from flask import request
        # Both canonical web/mobile account creation endpoints. Checking the
        # resolved endpoint covers aliases without parsing user-controlled URLs.
        if request.endpoint in ("auth.register", "mobile_api.register") and not is_open():
            if request.endpoint == "mobile_api.register":
                from app.blueprints.mobile_api import mobile_error
                from app.mobile_auth_middleware import _safe_message
                code = "AUTH_TEMPORARILY_UNAVAILABLE"
                return mobile_error(code, _safe_message(code), 503, True)
            from flask import jsonify
            from app.i18n import t
            return jsonify({"error": t("auth.login_unavailable")}), 503


def is_open():
    if not current_app.config.get("R6_REGISTRATION_CLOSED"):
        return True
    try:
        receipt = json.loads(Path(WEB_RECEIPT).read_text())
        if current_app.extensions.get("r6_coach_containment") and not coach_contained():
            return False
        return receipt == {"boot": current_app.config["R6_ADMISSION_BOOT"],
                           "revision": current_app.config["BUILD_REVISION"]}
    except (OSError, ValueError):
        return False


def database_proof():
    """Real application pool checkout AND re-checkout, READ ONLY, sanitized.

    No create_app/one-off reader is substituted for a serving pool. The worker
    invokes this on its task application's pool and disposes before forking;
    the checkout fence also protects all later work-horse connections.
    """
    from app.extensions import db
    if db.engine.dialect.name != "postgresql":
        raise RuntimeError("R6_POSTGRES_REQUIRED")
    for _ in range(2):
        with db.engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                if conn.exec_driver_sql("SHOW TIME ZONE").scalar() not in ("UTC", "Etc/UTC"):
                    raise RuntimeError("R6_POOLED_UTC_REQUIRED")
                from app.services import deletion_audit
                deletion_audit.verify_runtime_privileges(conn)
            finally:
                tx.rollback()
    return {"deletion_acl": True, "pooled_utc": True}


def web_proof():
    from app.services import account_eligibility as age
    policy = (age.enabled() and age.ADULT_AGE == 18
              and age.APPROVED_TR_MINOR_NOTICE_VERSION is None
              and age.APPROVED_TR_MINOR_TRAINING_AI_VERSION is None)
    return {**process_identity(), "boot": current_app.config["R6_ADMISSION_BOOT"],
            "revision": current_app.config["BUILD_REVISION"],
            "policy": POLICY if policy else None,
            "closed": not is_open(), **database_proof()}


def coach_contained():
    from app.services.coach_plan_tools.executor import FLAG_KEY, plan_mutation_tools_enabled
    return (current_app.extensions.get("r6_coach_containment") is True
            and current_app.config.get(FLAG_KEY) is False
            and plan_mutation_tools_enabled() is False)


def loaded_coach_proof(app, role, *, cached_worker_app=None, worker_heads=None):
    """Capture the serving app and build a fresh, sanitized observer."""
    from app.schema_safety import verify_schema_ready
    from scripts import r6_coach_attestation as wire

    with app.app_context():
        if not coach_contained():
            raise RuntimeError("R6_COACH_CONTAINMENT_REQUIRED")
    identity = process_identity()
    boot = wire.boot_digest(app.config["R6_ADMISSION_BOOT"])
    revision = app.config["BUILD_REVISION"]

    def observe():
        if role == "worker" and cached_worker_app() is not app:
            raise ValueError("R6_COACH_WORKER_CACHE_CHANGED")
        # RQ's long-lived parent must not reopen a DB connection after startup.
        # Its initial pooled read is joined with the web's fresh schema read.
        heads = worker_heads if role == "worker" else verify_schema_ready(app)
        with app.app_context():
            if (not coach_contained() or wire.boot_digest(app.config["R6_ADMISSION_BOOT"]) != boot
                    or app.config["BUILD_REVISION"] != revision):
                raise ValueError("R6_COACH_CONTAINMENT_REQUIRED")
        proof = {**identity, "version": wire.VERSION, "role": role, "boot": boot,
                 "revision": revision, "heads": sorted(heads), "flag": False, "guard": False}
        return wire.validate_process(proof, role=role, revision=revision)

    return identity["pid"], observe


def start_coach_attestation(app, role, **worker_context):
    from scripts import r6_coach_attestation as wire

    pid, observe = loaded_coach_proof(app, role, **worker_context)
    path = wire.socket_path(pid)
    path.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(2)
    if role == "worker":
        os.register_at_fork(after_in_child=listener.close)

    def serve():
        while True:
            connection, _ = listener.accept()
            with connection:
                try:
                    connection.settimeout(5)
                    with connection.makefile("rb") as request:
                        challenge = request.readline(34).decode("ascii").removesuffix("\n")
                    if not wire.NONCE.fullmatch(challenge):
                        continue
                    response = {**observe(), "challenge": challenge}
                    connection.sendall(json.dumps(response).encode("ascii") + b"\n")
                except Exception:
                    # Refusal is EOF. Never send configuration, SQL errors or request content.
                    pass

    threading.Thread(target=serve, name="r6-coach-attestation", daemon=True).start()
