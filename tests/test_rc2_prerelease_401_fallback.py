"""RC2 pre-release Production lock: exact 401 to Sandbox for named purchase subjects only.

The real AppleAdapter checks run over fake Apple endpoints. Nothing here calls Apple.
Remove this file with the seam in the D1 launch commit.
"""
import ast
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import requests
from appstoreserverlibrary.api_client import APIException
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus

from app.config import _utc_deadline
from app.extensions import db
from app.feature_flags import FeatureFlagConfigurationError, resolve_rollout_flags
from app.models import AppleAccountAssociation, AppleSubscription, User
from app.services import account_eligibility as policy, ai_consent, ai_launch
from app.services import apple_reconciliation as r, subscriptions as s
from app.services.apple_subscriptions import AppleAdapter, SubscriptionError
from subscription_support import adult_account
from test_apple_dual_environment import NOT_FOUND, Dual

TEMPORARY = "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"
A, B, C, UNLISTED = ("11111111-2222-3333-4444-55555555555" + str(n) for n in range(4))


def _future():
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _locked(token=None):
    dual = Dual(token or str(uuid4()))
    dual.p.client.fail = APIException(401)
    return dual


def _refused(call):
    with pytest.raises(SubscriptionError) as error:
        call()
    return error.value


# ── Adapter: what moves, and what the answer may claim ─────────────────────

@pytest.mark.parametrize("rejection", [APIException(401), APIException(401, 4010001, "Unauthorized")])
def test_exact_production_401_moves_to_sandbox_verification(rejection):
    dual = Dual(str(uuid4()))
    dual.p.client.fail = rejection
    result = dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)
    assert (result.environment, result.original_id, result.transaction_id) == ("Sandbox", "200", "201")
    assert dual.p.client.calls == [("info", "201")]
    assert dual.s.client.calls == [("info", "201"), ("status", "200")]


def test_plain_verify_keeps_production_401_final():
    dual = _locked()
    assert _refused(lambda: dual.p.verify("201", dual.stx.appAccountToken)).code == TEMPORARY
    assert dual.s.client.calls == []


@pytest.mark.parametrize("failure,code", [
    (APIException(403), TEMPORARY),
    (APIException(429, 4290000, "Rate limit exceeded."), TEMPORARY),
    (APIException(500, 5000000, "Internal server error."), TEMPORARY),
    (APIException(503), TEMPORARY),
    (APIException(400, 4000006, "Invalid transaction id."), TEMPORARY),
    (APIException("401"), TEMPORARY),
    (APIException(404), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (APIException(404, 4040001, "Account not found."), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (requests.Timeout("private"), TEMPORARY),
    (requests.ConnectionError("private"), TEMPORARY),
])
def test_unexpected_production_answers_never_reach_sandbox(failure, code):
    dual = Dual(str(uuid4()))
    dual.p.client.fail = failure
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)).code == code
    assert dual.s.client.calls == []


def test_production_answering_keeps_the_existing_paths():
    dual = Dual(str(uuid4()))
    production = dual.p.verify_prerelease_401_subject("101", dual.ptx.appAccountToken)
    assert production.environment == "Production" and dual.s.client.calls == []

    dual = Dual(str(uuid4()))
    sandbox = dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)
    assert sandbox.environment == "Sandbox" and dual.p.client.calls == [("info", "201")]


def test_not_found_in_both_answering_environments_stays_invalid_evidence():
    dual = Dual(str(uuid4()))
    error = _refused(lambda: dual.p.verify_prerelease_401_subject("999", dual.stx.appAccountToken))
    assert (error.code, error.status, error.reason) == ("SUBSCRIPTION_INVALID_EVIDENCE", 422, "APPLE_NOT_FOUND")


def test_sandbox_rejecting_us_after_4040010_is_final_and_not_retried():
    dual = Dual(str(uuid4()))
    dual.s.client.fail = APIException(401)
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)).code == TEMPORARY
    assert dual.s.client.calls == [("info", "201")]


def test_production_401_on_statuses_never_moves_a_production_transaction():
    dual = Dual(str(uuid4()))
    dual.p.client.status_fail = APIException(401)
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("101", dual.ptx.appAccountToken)).code == TEMPORARY
    assert dual.s.client.calls == []


@pytest.mark.parametrize("failure", [APIException(401), APIException(401, 4010001, "Unauthorized")])
def test_sandbox_authentication_rejection_is_final(failure):
    dual = _locked()
    dual.s.client.fail = failure
    error = _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken))
    assert (error.code, error.status) == (TEMPORARY, 503)
    assert dual.s.client.calls == [("info", "201")]


@pytest.mark.parametrize("failure", [
    APIException(404, NOT_FOUND, "Transaction id not found."), APIException(404),
    APIException(404, 4040001, "Account not found."),
])
def test_sandbox_not_found_after_production_401_proves_nothing(failure):
    dual = _locked()
    dual.s.client.fail = failure
    error = _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken))
    assert (error.code, error.status, error.reason) == (TEMPORARY, 503, None)


def test_sandbox_statuses_not_found_after_production_401_proves_nothing():
    dual = _locked()
    dual.s.client.status_fail = APIException(404, 4040005, "Original transaction id not found.")
    error = _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken))
    assert (error.code, error.status, error.reason) == (TEMPORARY, 503, None)


@pytest.mark.parametrize("failure", [APIException(500), APIException(429), requests.Timeout("private")])
def test_sandbox_outage_after_production_401_stays_unavailable(failure):
    dual = _locked()
    dual.s.client.fail = failure
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)).code == TEMPORARY


def test_missing_fallback_or_wrong_environment_keeps_401_final():
    dual = _locked()
    dual.p.fallback = None
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)).code == TEMPORARY

    dual = _locked()
    dual.p.environment = "Sandbox"
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken)).code == TEMPORARY
    assert dual.s.client.calls == []


def _signature_failure(dual):
    def refuse(_):
        raise VerificationException(VerificationStatus.VERIFICATION_FAILURE)
    dual.s.verifier.verify_and_decode_signed_transaction = refuse


@pytest.mark.parametrize("break_evidence,code", [
    (_signature_failure, "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.stx, "environment", Environment.PRODUCTION), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.stx, "bundleId", "other.bundle"), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.stx, "productId", "other.product"), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.stx, "transactionId", "other"), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.srenewal, "environment", Environment.PRODUCTION), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.sresponse, "environment", Environment.PRODUCTION), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (lambda d: setattr(d.stx, "appAccountToken", str(uuid4())), "SUBSCRIPTION_OWNERSHIP_CONFLICT"),
])
def test_every_sandbox_check_still_applies(break_evidence, code):
    token = str(uuid4())
    dual = _locked(token)
    break_evidence(dual)
    assert _refused(lambda: dual.p.verify_prerelease_401_subject("201", token)).code == code


def test_log_lines_are_closed(caplog):
    dual = _locked()
    dual.s.client.fail = APIException(401)
    with caplog.at_level(logging.DEBUG):
        _refused(lambda: dual.p.verify_prerelease_401_subject("201", dual.stx.appAccountToken))
    assert "event=sandbox_fallback kind=verify trigger=prerelease_401" in caplog.text
    assert "event=prerelease_401 outcome=sandbox_auth_rejected" in caplog.text
    assert dual.stx.appAccountToken not in caplog.text and "201" not in caplog.text


# ── Service: who, and when ─────────────────────────────────────────────────

@pytest.fixture
def world(app, make_user, monkeypatch):
    """A Production deployment before its first release, with every window condition open."""
    app.config.update(
        APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
        APPLE_SANDBOX_FALLBACK_ENABLED=True, ACCOUNT_ELIGIBILITY_ENABLED=False,
        APPLE_PURCHASE_QUALIFICATION_SUBJECTS=frozenset({A, B}),
        APPLE_QUALIFICATION_CONTROL_SUBJECTS=frozenset({C}),
        APPLE_PRERELEASE_401_FALLBACK_ENABLED=True,
        APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT=_future())
    with app.app_context():
        ids = {}
        for name, subject in (("a", A), ("b", B), ("c", C), ("unlisted", UNLISTED)):
            user = make_user(f"rc2-401-{name}")
            user.cognito_sub = subject
            adult_account(user)
            db.session.add(AppleAccountAssociation(user_id=user.id, token=str(uuid4())))
            db.session.flush()
            ids[name] = user.id
        db.session.commit()
        tokens = {name: db.session.get(AppleAccountAssociation, uid).token for name, uid in ids.items()}
        dual = _locked(tokens["a"])
        monkeypatch.setattr(s, "AppleAdapter", lambda _: dual.p)
        monkeypatch.setattr(r, "AppleAdapter", lambda _: dual.p)
        yield NS(app=app, ids=ids, tokens=tokens, dual=dual)
        db.session.rollback()


def _rows():
    return [(row.environment, row.user_id) for row in AppleSubscription.query.order_by(AppleSubscription.id)]


def test_named_subject_gets_a_sandbox_row_and_no_ai(world):
    assert s.verify_purchase(world.ids["a"], "201")["effective_premium"] is True
    assert _rows() == [("Sandbox", world.ids["a"])]
    assert s.entitlement(world.ids["a"])["tier"] == "PREMIUM"
    assert not db.session.get(User, world.ids["a"]).is_premium
    assert world.dual.p.client.calls == [("info", "201")]

    assert ai_launch.premium_ready() is False
    with pytest.raises(ai_consent.ConsentDenied):
        ai_launch.require_runtime()
    with pytest.raises(ai_consent.ConsentDenied):
        ai_launch.require_cohort(db.session.get(User, world.ids["a"]))


def test_second_named_subject_cannot_claim_the_transaction(world):
    assert _refused(lambda: s.verify_purchase(world.ids["b"], "201")).code == "SUBSCRIPTION_OWNERSHIP_CONFLICT"
    assert _rows() == []


def test_owner_conflict_survives_the_exception(world):
    s.verify_purchase(world.ids["a"], "201")
    world.dual.stx.appAccountToken = world.tokens["b"]
    world.dual.sign()
    refused = _refused(lambda: s.verify_purchase(world.ids["b"], "201"))
    assert refused.code == "SUBSCRIPTION_OWNERSHIP_CONFLICT"
    assert _rows() == [("Sandbox", world.ids["a"])]


@pytest.mark.parametrize("name", ["c", "unlisted"])
def test_control_and_unlisted_accounts_never_use_it(world, name):
    world.dual.stx.appAccountToken = world.tokens[name]
    assert _refused(lambda: s.verify_purchase(world.ids[name], "201")).code == TEMPORARY
    assert world.dual.s.client.calls == [] and _rows() == []


def test_subject_on_both_lists_is_a_control(world):
    world.app.config["APPLE_QUALIFICATION_CONTROL_SUBJECTS"] = frozenset({A, C})
    assert _refused(lambda: s.verify_purchase(world.ids["a"], "201")).code == TEMPORARY
    assert world.dual.s.client.calls == []


@pytest.mark.parametrize("change", [
    {"APPLE_PRERELEASE_401_FALLBACK_ENABLED": False},
    {"APPLE_PRERELEASE_401_FALLBACK_ENABLED": 1},
    {"APPLE_PRERELEASE_401_FALLBACK_ENABLED": "1"},
    {"APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT": None},
    {"APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT": "2099-01-01T00:00:00Z"},
    {"APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT": datetime(2099, 1, 1)},
    {"APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT": datetime(2020, 1, 1, tzinfo=timezone.utc)},
    {"APPLE_PURCHASE_QUALIFICATION_SUBJECTS": frozenset()},
    {"APPLE_SANDBOX_FALLBACK_ENABLED": False},
    {"APPLE_SUBSCRIPTION_ENVIRONMENT": "Sandbox"},
])
def test_any_invalid_window_condition_keeps_401_final(world, change):
    world.app.config.update(change)
    assert policy.prerelease_401_window_open() is False
    assert _refused(lambda: s.verify_purchase(world.ids["a"], "201")).code == TEMPORARY
    assert world.dual.s.client.calls == [] and _rows() == []


def test_deadline_is_checked_at_each_verify(world):
    assert policy.prerelease_401_window_open() is True
    world.app.config["APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT"] = datetime.now(timezone.utc)
    assert policy.prerelease_401_window_open() is False


def test_flag_off_reads_no_subject_and_uses_plain_verify(world, monkeypatch):
    world.app.config["APPLE_PRERELEASE_401_FALLBACK_ENABLED"] = False
    def unused(_):
        raise AssertionError("subject read with the exception off")
    monkeypatch.setattr(policy, "qualification_subject", unused)
    monkeypatch.setattr(AppleAdapter, "verify_prerelease_401_subject", unused)
    assert _refused(lambda: s.verify_purchase(world.ids["a"], "201")).code == TEMPORARY


def test_reconciliation_never_moves_on_production_401(world):
    dual = world.dual
    dual.p.client.status_fail = APIException(401)
    assert _refused(lambda: dual.p.status("100", world.tokens["a"], "Production")).code == TEMPORARY
    assert dual.s.client.calls == []


def test_no_subject_reaches_the_logs(world, caplog):
    with caplog.at_level(logging.DEBUG):
        s.verify_purchase(world.ids["a"], "201")
    assert "trigger=prerelease_401" in caplog.text
    assert A not in caplog.text and world.tokens["a"] not in caplog.text


# ── Configuration and confinement ──────────────────────────────────────────

def test_flag_defaults_off_and_refuses_malformed_values():
    assert resolve_rollout_flags({})["APPLE_PRERELEASE_401_FALLBACK_ENABLED"] is False
    for raw in ("true", "yes", "1 ", "2"):
        with pytest.raises(FeatureFlagConfigurationError):
            resolve_rollout_flags({"APPLE_PRERELEASE_401_FALLBACK_ENABLED": raw})


def test_test_app_boots_with_the_exception_closed(app):
    with app.app_context():
        assert app.config["APPLE_PRERELEASE_401_FALLBACK_ENABLED"] is False
        assert app.config["APPLE_PRERELEASE_401_FALLBACK_EXPIRES_AT"] is None
        assert policy.prerelease_401_window_open() is False


def test_deadline_parser_accepts_one_exact_form():
    assert _utc_deadline("2026-11-20T12:30:00Z") == datetime(2026, 11, 20, 12, 30, tzinfo=timezone.utc)
    for raw in ("", " 2026-11-20T12:30:00Z", "2026-11-20T12:30:00Z ", "2026-11-20T12:30:00", "2026-11-20",
                "2026-11-20T12:30:00+00:00", "2026-11-20 12:30:00Z", "2026-13-01T00:00:00Z",
                "2026-02-30T00:00:00Z", "2026-1-1T00:00:00Z", "tomorrow", "٢٠٢٦-11-20T12:30:00Z"):
        assert _utc_deadline(raw) is None


def _source(path):
    return (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")


def test_exception_is_confined_to_named_modules():
    root = Path(__file__).resolve().parents[1]
    names = ("verify_prerelease_401_subject", "production_401", "_ProductionRejected",
             "prerelease_401_window_open", "APPLE_PRERELEASE_401")
    found = {path.relative_to(root).as_posix() for folder in ("app", "fitx_mcp", "scripts")
             for path in (root / folder).rglob("*.py")
             if any(name in path.read_text(encoding="utf-8") for name in names)}
    assert found == {"app/config.py", "app/feature_flags.py", "app/services/account_eligibility.py",
                     "app/services/apple_subscriptions.py", "app/services/subscriptions.py"}


def _function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


@pytest.mark.parametrize("name", ["verify", "status", "_status", "notification", "_notification", "_guard"])
def test_other_adapter_paths_cannot_reach_the_exception(name):
    function = _function(ast.parse(_source("app/services/apple_subscriptions.py")), name)
    assert "production_401" not in ast.dump(function) and "_ProductionRejected" not in ast.dump(function)


def test_only_verify_purchase_may_call_the_exception():
    tree = ast.parse(_source("app/services/subscriptions.py"))
    users = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
             and "verify_prerelease_401_subject" in ast.dump(node)]
    assert [node.name for node in users] == ["verify_purchase"]
    assert "prerelease" not in _source("app/services/apple_reconciliation.py")
