"""Durable deletion record and legal-hold guard (SQLite; real service).

Task AXISAI-V1-DELETION-AUDIT-LEGAL-HOLD-SOURCE-CLOSURE-20261114. Only Cognito
and the S3 client are faked (`account_deletion_support`). The concurrent
hold-versus-deletion cases need real locks and live in
`tests/test_account_deletion_audit_pg.py`.

    python -m pytest tests/test_account_deletion_audit.py -v
"""
import ast
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from account_deletion_support import (
    census, install_fakes, issue_bearer, seed_account, seed_cross_links,
    session_rows, user_exists,
)
from app.extensions import db
from app.health_consent_models import (
    HealthConsentEvent, HealthConsentState, HealthErasure, HealthLegalHold,
)
from app.models import AccountDeletionRecord, DeletedIdentityTombstone, User
from app.services import (
    account_deletion, deleted_identity, deletion_audit, health_erasure,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = "/api/v1/account"


@pytest.fixture
def world(app, make_user, monkeypatch):
    cognito, s3 = install_fakes(monkeypatch)
    alice = make_user("alice_audit")
    bob = make_user("bob_audit")
    _, a_keys = seed_account(alice, s3, "A")
    _, b_keys = seed_account(bob, s3, "B")
    seed_cross_links(alice, bob)

    class World:
        pass
    w = World()
    w.cognito, w.s3 = cognito, s3
    w.a_id, w.b_id = alice.id, bob.id
    w.a_name, w.a_sub, w.a_email = alice.username, alice.cognito_sub, alice.email
    w.a_keys, w.b_keys = a_keys, b_keys
    w.a_bearer = issue_bearer(cognito, alice)
    w.b_bearer = issue_bearer(cognito, bob)
    return w


def _records():
    db.session.rollback()
    return AccountDeletionRecord.query.order_by(AccountDeletionRecord.id).all()


def _hold(owner, released=False):
    now = datetime.utcnow()
    hold = HealthLegalHold(
        owner_id=owner, categories=["coach"], matter_ref="MATTER-PRIVATE-7",
        created_at=now, review_at=now + timedelta(days=30),
        released_at=now if released else None)
    db.session.add(hold)
    db.session.commit()
    return hold.id


def _intact(w):
    db.session.rollback()
    assert user_exists(w.a_id)
    assert session_rows(w.a_id)["families"] >= 1
    assert set(w.a_keys.values()) <= w.s3.objects
    assert w.s3.deleted == []


# -- 1, 2, 8, 9: the record of an ordinary deletion ---------------------------
def test_successful_deletion_commits_one_minimal_sealed_record(
        world, raw_client, caplog):
    w = world
    before = datetime.utcnow().replace(microsecond=0)
    with caplog.at_level(logging.INFO):
        response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 204

    (row,) = _records()
    assert row.channel == "self_service" and row.outcome == "completed"
    assert row.identity_step == "deleted" and row.scope == "full_account"
    assert len(row.operation_ref) == 32 and int(row.operation_ref, 16) >= 0
    assert row.request_id
    assert before <= row.completed_at <= datetime.utcnow()
    assert row.completed_at.microsecond == 0
    assert row.retain_until == row.completed_at.replace(
        year=row.completed_at.year + 3)
    # The subject is present only as the tombstone's keyed fingerprint.
    assert row.subject_fingerprint == deleted_identity.fingerprint(w.a_sub)
    (tombstone,) = DeletedIdentityTombstone.query.all()
    assert tombstone.fingerprint == row.subject_fingerprint

    counts = row.counts
    assert set(counts) == {"rows", "objects_released", "unmanaged_references",
                           "health_erasures_closed"}
    assert counts["objects_released"] == len(w.a_keys)
    assert counts["unmanaged_references"] == 0
    assert counts["rows"]["user"] == 1
    assert counts["rows"]["pump_check"] == 2
    assert all(type(v) is int and v > 0 for v in counts["rows"].values())

    result = deletion_audit.verify()
    assert result == {"records": 1, "integrity_failures": [],
                      "tombstones_without_record": 0,
                      "first_record_at": row.completed_at.isoformat()}
    # The line outside the database: operation, never the subject.
    (line,) = [r.getMessage() for r in caplog.records
               if r.getMessage().startswith("account_deletion_audit")]
    assert f"operation_ref={row.operation_ref}" in line
    assert f"integrity={row.integrity}" in line
    assert row.subject_fingerprint not in line and w.a_sub not in line


def test_account_consent_and_health_rows_are_gone_while_the_record_stays(
        world, raw_client):
    w = world
    assert HealthConsentState.query.filter_by(owner_id=w.a_id).count() == 1
    assert HealthConsentEvent.query.filter_by(owner_id=w.a_id).count() >= 1

    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204

    db.session.rollback()
    assert not user_exists(w.a_id)
    assert {k: v for k, v in census(w.a_id).items() if v} == {}
    assert HealthConsentState.query.filter_by(owner_id=w.a_id).count() == 0
    assert HealthConsentEvent.query.filter_by(owner_id=w.a_id).count() == 0
    assert not set(w.a_keys.values()) & w.s3.objects
    assert len(_records()) == 1
    # The other account is untouched and still has its consent.
    assert user_exists(w.b_id)
    assert HealthConsentState.query.filter_by(owner_id=w.b_id).count() == 1
    assert set(w.b_keys.values()) <= w.s3.objects


def test_record_holds_no_identity_health_consent_or_credential_material(
        world, raw_client):
    w = world
    token = w.a_bearer["Authorization"].split(" ", 1)[1]
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204

    columns = {c.name for c in AccountDeletionRecord.__table__.columns}
    assert columns == {
        "id", "operation_ref", "request_id", "channel", "outcome",
        "identity_step", "subject_fingerprint", "scope", "counts",
        "completed_at", "retain_until", "integrity"}
    assert not AccountDeletionRecord.__table__.foreign_keys
    (row,) = _records()
    stored = json.dumps(deletion_audit.receipt(row), sort_keys=True)
    for private in (w.a_sub, w.a_name, w.a_email, "Private Name", "private A",
                    "private food", "did A", token, f"access|{w.a_sub}",
                    "MATTER", "granted", "locale", *w.a_keys.values()):
        assert private not in stored and private.upper() not in stored.upper()
    # Count keys are table names of the schema, nothing free-form.
    assert set(row.counts["rows"]) <= set(db.metadata.tables) | {"activity_clap"}


@pytest.mark.parametrize("counts", [
    {"rows": {"user": 1}},
    {"rows": {"user": 1}, "objects_released": 0, "unmanaged_references": 0,
     "health_erasures_closed": 0, "note": "left knee"},
    {"rows": {"user": "alice@example.com"}, "objects_released": 0,
     "unmanaged_references": 0, "health_erasures_closed": 0},
    {"rows": {"celiac disease": 1}, "objects_released": 0,
     "unmanaged_references": 0, "health_erasures_closed": 0},
    {"rows": {"user": True}, "objects_released": 0, "unmanaged_references": 0,
     "health_erasures_closed": 0},
    {"rows": {"user": 1}, "objects_released": -1, "unmanaged_references": 0,
     "health_erasures_closed": 0},
    {"rows": ["user"], "objects_released": 0, "unmanaged_references": 0,
     "health_erasures_closed": 0},
])
def test_record_refuses_anything_but_counts(app, counts):
    with pytest.raises(deletion_audit.InvalidDeletionRecord):
        deletion_audit.record(channel="self_service", identity_step="deleted",
                              subject="sub-x", counts=counts)
    db.session.rollback()
    assert AccountDeletionRecord.query.count() == 0


@pytest.mark.parametrize("kwargs", [
    {"channel": "support_ticket"}, {"identity_step": "skipped"},
    {"request_id": "x" * 81}, {"request_id": "line\nbreak"}, {"request_id": 7},
])
def test_record_refuses_unknown_vocabulary(app, kwargs):
    base = dict(channel="self_service", identity_step="deleted", subject="sub-x",
                counts={"rows": {"user": 1}, "objects_released": 0,
                        "unmanaged_references": 0, "health_erasures_closed": 0})
    with pytest.raises(deletion_audit.InvalidDeletionRecord):
        deletion_audit.record(**{**base, **kwargs})
    db.session.rollback()


# -- 3, 4: retry and failure semantics ----------------------------------------
def test_failed_purge_records_nothing_and_the_retry_records_once(
        world, raw_client, monkeypatch):
    import app.cli as cli
    w = world
    real_purge = cli._purge_user

    def broken(user):
        real_purge(user)                      # deletes are staged, then lost
        raise RuntimeError("purge failed late")
    monkeypatch.setattr(cli, "_purge_user", broken)

    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    assert _records() == []                   # no "completed" without a purge
    assert DeletedIdentityTombstone.query.count() == 0
    assert user_exists(w.a_id)

    monkeypatch.setattr(cli, "_purge_user", real_purge)
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    (row,) = _records()
    # The provider step had already run on the first attempt.
    assert row.identity_step == "already_absent" and row.outcome == "completed"
    assert deletion_audit.verify()["tombstones_without_record"] == 0


def test_record_failure_fails_the_deletion_and_keeps_the_account(
        world, raw_client, monkeypatch):
    """The record is part of the purge transaction, not an afterthought."""
    w = world

    def refuse(**_kwargs):
        raise deletion_audit.InvalidDeletionRecord("no")
    monkeypatch.setattr(deletion_audit, "record", refuse)
    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    db.session.rollback()
    assert user_exists(w.a_id) and DeletedIdentityTombstone.query.count() == 0
    assert census(w.a_id)[("PumpCheck", "user_id")] == 2


def test_repeated_invocation_for_a_deleted_account_adds_no_second_outcome(
        world):
    from types import SimpleNamespace
    from app.models import MobileAuthSession
    w = world
    alice = db.session.get(User, w.a_id)
    family = MobileAuthSession.query.filter_by(user_id=w.a_id).first()
    claims = {"sub": alice.cognito_sub}
    snapshot = (
        SimpleNamespace(id=alice.id, cognito_sub=alice.cognito_sub),
        SimpleNamespace(user_id=family.user_id, cognito_sub=family.cognito_sub,
                        cognito_access_token=family.cognito_access_token),
        claims)
    account_deletion.delete_account(alice, family, claims)
    account_deletion.delete_account(*snapshot)
    account_deletion.delete_account(*snapshot)
    (row,) = _records()
    assert row.identity_step == "deleted"
    assert DeletedIdentityTombstone.query.count() == 1


# -- 5: a legal hold refuses self-service deletion ----------------------------
def test_active_hold_refuses_before_the_provider_and_changes_nothing(
        world, raw_client, caplog):
    w = world
    _hold(w.a_id)
    before = census(w.a_id)
    with caplog.at_level(logging.INFO):
        response = raw_client.delete(PATH, headers=w.a_bearer)

    assert response.status_code == 503
    error = response.get_json()["error"]
    # The existing "nothing deleted, retry" contract; no new code or field.
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert error["code"] == "ACCOUNT_DELETION_UNAVAILABLE"
    body = response.get_data(as_text=True).lower()
    assert "hold" not in body and "legal" not in body and "matter" not in body
    assert w.cognito.delete_calls == []
    assert f"sub-{w.a_name}" in w.cognito.live
    _intact(w)
    assert census(w.a_id) == before
    assert _records() == [] and DeletedIdentityTombstone.query.count() == 0
    assert HealthLegalHold.query.filter_by(owner_id=w.a_id).count() == 1
    # The account keeps working and the controller can find the request.
    assert raw_client.get("/api/v1/account/me",
                          headers=w.a_bearer).status_code == 200
    refused = [r.getMessage() for r in caplog.records
               if "event=refused" in r.getMessage()]
    assert refused and "stage=legal_hold" in refused[0]
    assert f"user_id={w.a_id}" in refused[0]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "MATTER-PRIVATE-7" not in logged


def test_released_hold_no_longer_blocks_and_the_retry_succeeds(
        world, raw_client):
    w = world
    hold_id = _hold(w.a_id)
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 503
    hold = db.session.get(HealthLegalHold, hold_id)
    hold.released_at = datetime.utcnow()
    db.session.commit()

    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    db.session.rollback()
    assert not user_exists(w.a_id)
    assert HealthLegalHold.query.count() == 0
    (row,) = _records()
    assert row.identity_step == "deleted"


def test_hold_on_another_account_does_not_block_this_one(world, raw_client):
    w = world
    _hold(w.b_id)
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    db.session.rollback()
    assert user_exists(w.b_id)
    assert HealthLegalHold.query.filter_by(owner_id=w.b_id).count() == 1


def test_hold_found_at_the_purge_boundary_is_never_reported_as_success(
        world, raw_client, monkeypatch):
    """Defence in depth. With the real authority no hold can appear after the
    first check; if one does, the recheck under the owner row lock stops the
    purge and the answer is INCOMPLETE, never 204."""
    w = world
    real = account_deletion._delete_identity

    def identity_then_hold(provider_access):
        step = real(provider_access)
        _hold(w.a_id)
        return step
    monkeypatch.setattr(account_deletion, "_delete_identity", identity_then_hold)

    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    db.session.rollback()
    assert user_exists(w.a_id)
    assert census(w.a_id)[("PumpCheck", "user_id")] == 2
    assert _records() == [] and DeletedIdentityTombstone.query.count() == 0
    assert HealthLegalHold.query.filter_by(owner_id=w.a_id).count() == 1


def test_unavailable_hold_authority_deletes_nothing(
        world, raw_client, monkeypatch):
    from contextlib import contextmanager
    from app.services import health_consent
    w = world

    @contextmanager
    def busy(owner, *, exclusive=False):
        assert exclusive is True and owner == w.a_id
        raise health_consent.HealthConsentDenied(
            "HEALTH_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        yield
    monkeypatch.setattr(health_consent, "_authority", busy)

    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ACCOUNT_DELETION_UNAVAILABLE"
    assert w.cognito.delete_calls == []
    _intact(w)


def test_the_whole_lifecycle_runs_inside_the_exclusive_authority(
        world, raw_client, monkeypatch):
    from contextlib import contextmanager
    from app.services import health_consent
    w = world
    state = {"held": False, "entered": 0}

    @contextmanager
    def tracking(owner, *, exclusive=False):
        assert exclusive is True and owner == w.a_id
        state["held"] = True
        state["entered"] += 1
        try:
            yield
        finally:
            state["held"] = False
    monkeypatch.setattr(health_consent, "_authority", tracking)
    seen = []
    real_delete = w.cognito.delete_user
    real_purge = account_deletion._purge_if_released
    monkeypatch.setattr(
        account_deletion.cognito_service, "delete_user",
        lambda token: (seen.append(("identity", state["held"])),
                       real_delete(token))[1])
    monkeypatch.setattr(
        account_deletion, "_purge_if_released",
        lambda *a: (seen.append(("purge", state["held"])), real_purge(*a))[1])
    real_release = account_deletion._release
    monkeypatch.setattr(
        account_deletion, "_release",
        lambda *a: (seen.append(("storage", state["held"])),
                    real_release(*a))[1])

    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    assert state["entered"] == 1 and state["held"] is False
    assert {stage for stage, _ in seen} == {"identity", "storage", "purge"}
    assert all(held for _, held in seen)


# -- 6: operator cleanup ------------------------------------------------------
def test_operator_cleanup_refuses_the_whole_run_when_one_account_is_held(
        app, make_user):
    held = make_user("test")
    free = make_user("test_free")
    _hold(held.id)
    held_id, free_id = held.id, free.id

    listing = app.test_cli_runner().invoke(args=["cleanup-test-users"])
    assert "LEGAL HOLD" in listing.output and listing.exit_code == 0

    result = app.test_cli_runner().invoke(
        args=["cleanup-test-users", "--yes"])
    assert result.exit_code == 2
    assert "REFUSED" in result.output and "silindi" not in result.output
    assert "MATTER-PRIVATE-7" not in result.output
    db.session.rollback()
    assert user_exists(held_id) and user_exists(free_id)
    assert HealthLegalHold.query.count() == 1
    assert AccountDeletionRecord.query.count() == 0

    targeted = app.test_cli_runner().invoke(
        args=["cleanup-test-users", "--username", "test", "--yes"])
    assert targeted.exit_code == 2
    db.session.rollback()
    assert user_exists(held_id)


def test_operator_cleanup_records_each_deleted_account(app, make_user):
    first = make_user("test")
    second = make_user("test2")
    keep = make_user("gercek")
    _hold(first.id, released=True)             # a released hold does not block
    subs = {first.cognito_sub, second.cognito_sub}
    keep_id = keep.id

    result = app.test_cli_runner().invoke(args=["cleanup-test-users", "--yes"])
    assert result.exit_code == 0, result.output
    rows = _records()
    assert len(rows) == 2
    assert {r.channel for r in rows} == {"operator_cleanup"}
    assert {r.identity_step for r in rows} == {"not_attempted"}
    assert {r.request_id for r in rows} == {None}
    expected = {deleted_identity.fingerprint(s) for s in subs if s}
    assert {r.subject_fingerprint for r in rows if r.subject_fingerprint} == expected
    for row in rows:
        assert row.counts["rows"]["user"] == 1
        assert row.counts["objects_released"] is None
    assert user_exists(keep_id)
    assert deletion_audit.verify()["integrity_failures"] == []


def test_canonical_purge_refuses_a_held_owner_for_every_caller(app, make_user):
    from app.cli import _purge_user
    user = make_user("purge_backstop")
    _hold(user.id)
    with pytest.raises(health_erasure.LegalHoldActive):
        _purge_user(db.session.get(User, user.id))
    db.session.rollback()
    assert user_exists(user.id)


# -- 10: withdrawal / erasure are not this path -------------------------------
def test_consent_withdrawal_and_erasure_write_no_deletion_record(
        world, raw_client):
    from app.services import health_consent
    from app.services import health_disclosure as disclosure
    w = world
    generation = health_consent.status(w.a_id)["generation"]
    health_consent.mutate(w.a_id, dict(
        purpose=disclosure.PURPOSE, action="withdraw",
        version=disclosure.VERSION, locale="en",
        text_digest=disclosure.DIGESTS["en"], expected_generation=generation,
        request_id="audit-withdraw-0001"))
    assert HealthErasure.query.filter_by(owner_id=w.a_id).count() == 1
    assert health_erasure.run(w.a_id) == "completed"
    db.session.rollback()
    assert user_exists(w.a_id)
    assert AccountDeletionRecord.query.count() == 0
    assert DeletedIdentityTombstone.query.count() == 0


# -- 11: nothing ordinary removes a record ------------------------------------
def test_later_deletions_cleanup_and_maintenance_keep_earlier_records(
        world, raw_client, app, make_user):
    w = world
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    (first,) = _records()
    first_ref = first.operation_ref

    assert raw_client.delete(PATH, headers=w.b_bearer).status_code == 204
    make_user("test")
    assert app.test_cli_runner().invoke(
        args=["cleanup-test-users", "--yes"]).exit_code == 0
    assert deletion_audit.purge_expired() == 0
    refs = [r.operation_ref for r in _records()]
    assert len(refs) == 3 and refs[0] == first_ref
    assert User.query.count() == 0


def test_only_expired_records_are_disposed(app):
    counts = {"rows": {"user": 1}, "objects_released": 0,
              "unmanaged_references": 0, "health_erasures_closed": 0}
    old = deletion_audit.record(
        channel="self_service", identity_step="deleted", subject="sub-old",
        counts=counts, now=datetime.utcnow() - timedelta(days=3 * 366 + 2))
    kept = deletion_audit.record(
        channel="self_service", identity_step="deleted", subject="sub-new",
        counts=counts, now=datetime.utcnow() - timedelta(days=3 * 365 - 5))
    extended = deletion_audit.record(
        channel="self_service", identity_step="deleted", subject="sub-ext",
        counts=counts, now=datetime.utcnow() - timedelta(days=3 * 366 + 2))
    extended.retain_until = datetime.utcnow() + timedelta(days=400)
    old_ref, kept_ref, ext_ref = (old.operation_ref, kept.operation_ref,
                                  extended.operation_ref)
    db.session.commit()

    assert deletion_audit.purge_expired() == 1
    assert {r.operation_ref for r in _records()} == {kept_ref, ext_ref}
    assert old_ref not in {r.operation_ref for r in _records()}


def test_daily_maintenance_runs_the_disposal(app):
    source = (ROOT / "app/jobs/tasks.py").read_text(encoding="utf-8")
    assert '("deletion_audit", deletion_audit.purge_expired)' in source


def test_retention_is_three_years_including_a_leap_day():
    assert deletion_audit.RECORD_YEARS == 3
    assert deletion_audit.retain_until(datetime(2026, 11, 14, 9, 0)) == datetime(
        2029, 11, 14, 9, 0)
    assert deletion_audit.retain_until(datetime(2028, 2, 29, 9, 0)) == datetime(
        2031, 3, 1, 9, 0)


# -- integrity and the operator tools -----------------------------------------
def test_verify_detects_a_changed_row_and_a_missing_one(
        world, raw_client, app):
    w = world
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    assert raw_client.delete(PATH, headers=w.b_bearer).status_code == 204
    first, second = _records()
    first_ref = first.operation_ref
    first.counts = {**first.counts, "objects_released": 0}
    db.session.commit()
    result = deletion_audit.verify()
    assert result["integrity_failures"] == [first_ref]
    assert app.test_cli_runner().invoke(
        args=["account-deletion-audit-verify"]).exit_code == 2

    db.session.delete(db.session.get(AccountDeletionRecord, second.id))
    db.session.commit()
    assert deletion_audit.verify()["tombstones_without_record"] == 1


def test_export_then_reconcile_names_an_account_a_restore_brought_back(
        world, raw_client, app, make_user):
    w = world
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    exported = app.test_cli_runner().invoke(
        args=["account-deletion-audit-export"])
    assert exported.exit_code == 0
    (line,) = exported.output.strip().splitlines()
    values = json.loads(line)
    assert deletion_audit.sealed(values)
    assert w.a_sub not in line and w.a_name not in line

    # Nothing to reconcile while the account stays deleted.
    quiet = app.test_cli_runner().invoke(
        args=["account-deletion-reconcile"], input=line + "\n")
    assert quiet.exit_code == 0 and quiet.output.strip() == ""

    # A restore brings the local row back with the SAME provider subject.
    restored = make_user("alice_restored")
    restored.cognito_sub = w.a_sub
    db.session.commit()
    restored_id = restored.id
    found = app.test_cli_runner().invoke(
        args=["account-deletion-reconcile"], input=line + "\n")
    assert found.exit_code == 0
    assert json.loads(found.output)["user_id"] == restored_id
    db.session.rollback()
    assert user_exists(restored_id)                     # lists, never deletes

    forged = json.dumps({**values, "completed_at": "2020-01-01T00:00:00"})
    refused = app.test_cli_runner().invoke(
        args=["account-deletion-reconcile"], input=forged + "\n")
    assert refused.exit_code != 0 and "integrity" in refused.output


# -- architecture -------------------------------------------------------------
def _sources():
    for base in ("app", "fitx_mcp"):
        for path in sorted((ROOT / base).rglob("*.py")):
            yield path
    yield from sorted(ROOT.glob("*.py"))


def _mentions(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
        if isinstance(node, ast.ImportFrom) and any(
                alias.name == name for alias in node.names):
            return True
    return False


def test_only_the_audit_module_touches_the_record_table():
    users = {p.relative_to(ROOT).as_posix() for p in _sources()
             if _mentions(p, "AccountDeletionRecord")}
    # (the model's own class statement is a definition, not a reference)
    assert users == {"app/services/deletion_audit.py"}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if path.name != "deletion_audit.py":
            assert "account_deletion_record" not in text or path.name == "models.py"


def test_only_the_two_purge_callers_write_records():
    writers = {p.relative_to(ROOT).as_posix() for p in _sources()
               if "deletion_audit.record(" in p.read_text(encoding="utf-8")}
    assert writers == {"app/services/account_deletion.py", "app/cli.py"}


def _function(relative, name):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    (found,) = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name]
    return found


def _lines(function, dotted):
    return sorted(n.lineno for n in ast.walk(function)
                  if isinstance(n, ast.Call) and ast.unparse(n.func) == dotted)


def test_self_service_checks_the_hold_before_the_provider_and_before_the_purge():
    lifecycle = _function("app/services/account_deletion.py", "delete_account")
    (fence,) = [n for n in ast.walk(lifecycle) if isinstance(n, ast.With)]
    assert ast.unparse(fence.items[0].context_expr) == "_hold_fence(user_id)"
    inside = [ast.unparse(n.value.func) for n in fence.body]
    assert inside == ["_refuse_if_held", "_delete_and_purge"]

    purge = _function("app/services/account_deletion.py", "_purge_if_released")
    (lock,) = [n.lineno for n in ast.walk(purge)
               if isinstance(n, ast.Attribute) and n.attr == "with_for_update"]
    (check,) = _lines(purge, "health_erasure.active_hold")
    (purged,) = _lines(purge, "_purge_user")
    (recorded,) = _lines(purge, "deletion_audit.record")
    commits = _lines(purge, "db.session.commit")
    (announced,) = _lines(purge, "deletion_audit.announce")
    assert lock <= check < purged < recorded < commits[-1] < announced


def test_operator_cleanup_locks_then_checks_then_purges_then_records():
    cleanup = _function("app/cli.py", "cleanup_test_users")
    (lock,) = [n.lineno for n in ast.walk(cleanup)
               if isinstance(n, ast.Attribute) and n.attr == "with_for_update"]
    holds = _lines(cleanup, "health_erasure.active_hold")
    (purged,) = _lines(cleanup, "_purge_user")
    (recorded,) = _lines(cleanup, "deletion_audit.record")
    commits = _lines(cleanup, "db.session.commit")
    assert lock < holds[-1] < purged < recorded < commits[0]

    canonical = _function("app/cli.py", "_purge_user")
    (guard,) = _lines(canonical, "health_erasure.active_hold")
    deletes = [n.lineno for n in ast.walk(canonical)
               if isinstance(n, ast.Attribute) and n.attr == "delete"]
    assert guard < min(deletes)


def test_the_delete_route_stays_outside_the_shared_health_scope():
    """The lifecycle takes the EXCLUSIVE authority itself; a shared scope
    held by its own request would make every deletion wait on itself."""
    from app import health_boundary
    assert "mobile_api.delete_account" in health_boundary.SAFE_ENDPOINTS
