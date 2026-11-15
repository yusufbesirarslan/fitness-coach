"""R6 candidate admission: local process proofs, never provider/business calls.

Slot web processes start with registration closed. Only the protected host
transaction can publish a boot-bound local receipt after its other gates.
No HTTP endpoint, client value or launch eligibility flag opens this latch.
"""
import json
import os
import uuid
from pathlib import Path

from flask import current_app
from sqlalchemy import text

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
                table = conn.execute(text(
                    "SELECT to_regclass('public.account_deletion_record') IS NOT NULL"
                )).scalar()
                if table is not True:
                    raise RuntimeError("R6_DELETION_TABLE_REQUIRED")
                acl = conn.execute(text("""
                    SELECT has_table_privilege(current_user,
                               'public.account_deletion_record', 'SELECT')
                       AND has_table_privilege(current_user,
                               'public.account_deletion_record', 'INSERT')
                       AND has_table_privilege(current_user,
                               'public.account_deletion_record', 'DELETE')
                       AND COALESCE(has_sequence_privilege(current_user,
                               pg_get_serial_sequence('public.account_deletion_record', 'id'),
                               'USAGE'), false)
                """)).scalar()
                if acl is not True:
                    raise RuntimeError("R6_DELETION_ACL_REQUIRED")
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
