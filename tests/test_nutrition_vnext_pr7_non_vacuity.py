"""NUTR-PR7 — non-vacuity: every contract guard KILLS a real defect.

    python -m pytest tests/test_nutrition_vnext_pr7_non_vacuity.py -q

Each N7-xx test plants one defect into SHIPPED code (monkeypatching the real
function the routes call), then runs the real contract test against the real
routes and asserts it now FAILS. A guard that still passed under its defect
would be decoration. Documentation greps do not count anywhere here.
"""
from types import SimpleNamespace

import pytest

import test_mobile_auth_feature_gate as gate
import test_nutrition_vnext_pr5_plan_contract as pr5
import test_nutrition_vnext_pr7_native_coach_handoff as coach
import test_nutrition_vnext_pr7_native_contract as contract
import test_nutrition_vnext_pr7_native_history as hist
import test_nutrition_vnext_pr7_native_hydration as water
import test_nutrition_vnext_pr7_native_plan as plan_tests
import test_nutrition_vnext_pr7_native_planned_meal as planned
import test_nutrition_vnext_pr7_native_supplements as supp
import test_sprint13_nutrition_closure_discovery as sprint13
from app.blueprints import mobile_coach
from app.blueprints import mobile_nutrition_closure as closure
from app.extensions import db
from app.models import NutritionPlan, Supplement
from app.services import mobile_diary_mutation, nutrition_day_view as dv
from app.services import supplement_cabinet as cabinet
from app.services.mobile_log_food import service as log_food_service
from app.services.nutrition_native import history, hydration, plan, supplements, tokens

from nutrition_pr7_support import bearer, native, no_provider  # noqa: F401
from test_mobile_coach_api import (  # noqa: F401
    alice, bob, mobile, no_real_provider, provider,
)


def killed(check, *args, **kwargs):
    with pytest.raises(AssertionError):
        check(*args, **kwargs)


@pytest.fixture
def generator(monkeypatch, no_provider):
    from app.services import ai
    fake = plan_tests.Generator()
    monkeypatch.setattr(ai, "_heavy_chat", fake)
    return fake


@pytest.fixture
def owner(app, make_user, bearer):
    user = make_user("nv-planned")
    from nutrition_pr7_support import save_plan_row
    save_plan_row(user.id)
    return user, bearer(user)


# ── day view ───────────────────────────────────────────────────────────────


def test_n7_01_native_day_view_reimplementing_pr6_truth_is_detected(
        app, client, native, bearer, make_user, login, no_provider, monkeypatch):
    real = closure.nutrition_day_view_payload

    def reimplemented(view):
        body = real(view)
        body["hydration"] = dict(body["hydration"], unit="ml")
        return body
    monkeypatch.setattr(closure, "nutrition_day_view_payload", reimplemented)
    killed(contract.test_web_and_native_day_view_are_semantically_identical,
           app, client, native, bearer, make_user, login, None)


def test_n7_02_missing_target_as_zero_is_detected(
        app, native, bearer, make_user, no_provider, monkeypatch):
    real = dv._read_target

    def zero(user_id):
        section = real(user_id)
        return dv.TargetSection(dv.AVAILABLE, value=0.0) if section.state == dv.EMPTY else section
    monkeypatch.setattr(dv, "_read_target", zero)
    killed(contract.test_native_day_view_states_unknown_is_not_zero,
           app, native, bearer, make_user, None)


# ── hydration ──────────────────────────────────────────────────────────────


def test_n7_03_hydration_failure_as_zero_is_detected(
        app, native, bearer, make_user, monkeypatch):
    real = hydration.read_today

    def swallow(user_id, secret, day=None):
        try:
            return real(user_id, secret, day)
        except Exception:
            day_iso = __import__("app.timeutil", fromlist=["app_today"]).app_today().isoformat()
            return hydration._payload(secret, user_id, day_iso, 0)
    monkeypatch.setattr(hydration, "read_today", swallow)
    killed(water.test_read_failure_is_unavailable_not_zero,
           app, native, bearer, make_user, monkeypatch)


def test_n7_25_ambiguous_write_reported_as_success_is_detected(
        app, native, bearer, make_user, monkeypatch):
    real = hydration.set_today

    def claim_success(user_id, secret, revision, amount, day=None):
        try:
            return real(user_id, secret, revision, amount, day)
        except Exception:
            day_iso = __import__("app.timeutil", fromlist=["app_today"]).app_today().isoformat()
            return hydration._payload(secret, user_id, day_iso, amount)
    monkeypatch.setattr(hydration, "set_today", claim_success)
    killed(water.test_write_failure_is_never_reported_as_confirmed,
           app, native, bearer, make_user, monkeypatch, None)


# ── nutrition plan ─────────────────────────────────────────────────────────


def test_n7_04_plan_failure_as_absent_is_detected(app, native, bearer, make_user, monkeypatch):
    real = plan.read_plan

    def absent_on_failure(user_id, secret, language):
        try:
            return real(user_id, secret, language)
        except Exception:
            return {"contract_version": 1, "plan": plan._absent_payload(),
                    "generation_options": plan.generation_options(language)}
    monkeypatch.setattr(plan, "read_plan", absent_on_failure)
    killed(plan_tests.test_plan_storage_failure_is_a_typed_503,
           app, native, bearer, make_user, monkeypatch)


def test_n7_05_replace_ignoring_stale_revision_is_detected(
        app, native, bearer, make_user, generator, monkeypatch):
    monkeypatch.setattr(plan, "_replacement_check", lambda *_a: (lambda _current: None))
    killed(plan_tests.test_two_devices_from_one_revision_cannot_both_succeed,
           app, native, bearer, make_user, generator)


def test_n7_06_generation_auto_saving_is_detected(
        app, native, bearer, make_user, generator, monkeypatch):
    real = plan.generate_proposals

    def autosave(user, secret, request, chat_fn, quota_enabled, logger=None):
        body = real(user, secret, request, chat_fn, quota_enabled, logger=logger)
        document = plan.canonical_from_native(body["proposals"][0]["plan"])
        plan.replace_nutrition_plan(user.id, document, body["food_rating"])
        return body
    monkeypatch.setattr(plan, "generate_proposals", autosave)
    killed(plan_tests.test_generation_returns_proposals_and_saves_nothing,
           app, native, bearer, make_user, generator)


def test_n7_07_delete_before_validation_is_detected(
        app, native, bearer, make_user, generator, monkeypatch):
    real = plan.verify_proposal_token

    def delete_first(secret, user_id, token, now=None):
        NutritionPlan.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        return real(secret, user_id, token, now=now)
    monkeypatch.setattr(plan, "verify_proposal_token", delete_first)
    mutate = lambda p: p["plan"].update(name="Renamed")  # noqa: E731
    killed(plan_tests.test_save_revalidates_and_preserves_the_old_plan,
           app, native, bearer, make_user, generator, mutate, "INVALID_PLAN_PROPOSAL")


# ── supplements ────────────────────────────────────────────────────────────


def test_n7_08_supplement_token_not_owner_bound_is_detected(monkeypatch):
    real = supplements.supplement_id
    monkeypatch.setattr(supplements, "supplement_id",
                        lambda secret, _user_id, row_id: real(secret, 0, row_id))
    killed(supp.test_ids_are_opaque_owner_bound_tokens, None)


def test_n7_09_update_ignoring_stale_state_is_detected(
        app, native, bearer, make_user, monkeypatch):
    real = cabinet.update_supplement
    monkeypatch.setattr(cabinet, "update_supplement",
                        lambda user_id, sid, changes, check=None: real(user_id, sid, changes))
    killed(supp.test_update_with_current_revision_then_stale,
           app, native, bearer, make_user, None)


def test_n7_24_cross_user_token_resolution_is_detected(
        app, native, bearer, make_user, monkeypatch):
    real_id = supplements.supplement_id
    monkeypatch.setattr(supplements, "supplement_id",
                        lambda secret, _user_id, row_id: real_id(secret, 0, row_id))
    monkeypatch.setattr(cabinet, "owner_ids", lambda _user_id: [
        r[0] for r in db.session.query(Supplement.id).order_by(Supplement.id).all()])
    monkeypatch.setattr(cabinet, "locked_supplement", lambda _user_id, sid: (
        Supplement.query.filter_by(id=sid).populate_existing().with_for_update().one_or_none()))
    killed(supp.test_owner_isolation_and_no_existence_oracle,
           app, native, bearer, make_user, None)


# ── history ────────────────────────────────────────────────────────────────


def test_n7_10_unbounded_history_is_detected(app, native, bearer, make_user, monkeypatch):
    monkeypatch.setattr(history, "parse_limit", lambda _raw: 100_000)
    killed(hist.test_default_page_is_seven_whole_days_newest_first,
           app, native, bearer, make_user, None)


def test_n7_11_history_leaking_raw_ids_is_detected(app, native, bearer, make_user, monkeypatch):
    monkeypatch.setattr(history, "diary_entry_id",
                        lambda _secret, _user_id, entry_id: str(entry_id))
    killed(hist.test_no_raw_ids_and_no_n_plus_one, app, native, bearer, make_user, None)


# ── planned meal ───────────────────────────────────────────────────────────


def test_n7_12_name_based_planned_meal_identity_is_detected(app, native, owner, monkeypatch):
    monkeypatch.setattr(plan, "planned_meal_id", lambda secret, _u, _row, slot: (
        tokens.digest_token(secret, tokens.PLANNED_MEAL_ID, tokens.text(slot))))
    killed(planned.test_identity_is_not_the_display_name, app, native, owner, None)


def test_n7_13_key_accepting_a_different_command_is_detected(app, native, owner, monkeypatch):
    monkeypatch.setattr(plan, "planned_meal_fingerprint", lambda *_a: "0" * 64)
    killed(planned.test_same_key_different_command_is_a_conflict, app, native, owner, None)


def test_n7_14_stale_plan_still_logging_is_detected(app, native, owner, monkeypatch):
    real = plan.log_planned_meal

    def ignore_revision(user_id, secret, _revision, meal_token, key):
        current = plan.plan_revision(secret, plan.newest_plan(user_id))
        return real(user_id, secret, current, meal_token, key)
    monkeypatch.setattr(plan, "log_planned_meal", ignore_revision)
    killed(planned.test_stale_plan_revision_writes_nothing, app, native, owner, None)


# ── menu (deferred: guarded by absence) ────────────────────────────────────


@pytest.mark.parametrize("path", ["/api/v1/nutrition/menu/analyze",
                                  "/api/v1/nutrition/menu/scan"])
def test_n7_15_16_any_native_menu_adapter_is_detected(app, path):
    """Menu analysis is DEFERRED in PR7. A native menu route (which could write
    MealLog or bypass the browser SSRF stack) cannot ship unreviewed: the exact
    route inventory refuses it."""
    from app import create_app
    flask_app = create_app()
    rules = {(r.rule, frozenset(r.methods) - {"HEAD", "OPTIONS"})
             for r in flask_app.url_map.iter_rules() if r.rule.startswith("/api/v1")}
    rules.add((path, frozenset({"POST"})))
    killed(sprint13.test_mobile_nutrition_route_inventory, rules)
    killed(sprint13.test_the_mobile_surface_publishes_no_history_menu_plan_or_water, rules)


# ── coach handoff ──────────────────────────────────────────────────────────


def test_n7_17_client_nutrition_facts_reaching_coach_is_detected(
        mobile, bearer, provider, alice, monkeypatch):
    monkeypatch.setattr(mobile_coach, "_OPTIONAL_KEYS",
                        frozenset({"handoff", "calories", "context", "intake", "user_id"}))
    killed(coach.test_unknown_markers_and_client_facts_are_refused_and_spend_nothing,
           mobile, bearer, provider, alice,
           {"message": "x", "handoff": "nutrition-day", "calories": 9999})


def test_n7_18_unknown_handoff_reaching_the_model_is_detected(
        mobile, bearer, provider, alice, monkeypatch):
    monkeypatch.setattr(mobile_coach, "NATIVE_HANDOFF_KINDS",
                        frozenset({"nutrition-day", "progress-insight"}))
    killed(coach.test_unknown_markers_and_client_facts_are_refused_and_spend_nothing,
           mobile, bearer, provider, alice,
           {"message": "x", "handoff": "progress-insight"})


# ── auth / route gates ─────────────────────────────────────────────────────


def test_n7_19_cookie_auth_on_native_nutrition_is_detected(
        app, client, make_user, login, monkeypatch):
    from flask import g
    from flask_login import current_user
    from app import mobile_auth_middleware as middleware
    real = middleware._load_mobile_principal

    def cookie_fallback():
        if current_user.is_authenticated:
            g.mobile_user = current_user._get_current_object()
            return SimpleNamespace(user=g.mobile_user), None
        return real()
    monkeypatch.setattr(middleware, "_load_mobile_principal", cookie_fallback)
    killed(contract.test_a_browser_cookie_session_is_not_native_auth,
           app, client, make_user, login, "get", "/api/v1/nutrition/hydration")


def test_n7_20_wildcard_route_growth_is_detected(monkeypatch):
    real = gate._create_app

    def grown():
        flask_app = real()
        flask_app.add_url_rule("/api/v1/nutrition/anything", "mobile_api.unreviewed",
                               lambda: "", methods=["GET"])
        return flask_app
    monkeypatch.setattr(gate, "_create_app", grown)
    killed(gate.test_enabled_startup_exposes_only_approved_mobile_routes, monkeypatch)


# ── existing native + web semantics ────────────────────────────────────────


def test_n7_21_logfood_key_regression_is_detected(
        app, native, bearer, make_user, monkeypatch):
    monkeypatch.setattr(log_food_service, "_existing_or_conflict", lambda u, k, f: (
        log_food_service.meal_idempotency.find_existing(u, k)))
    killed(contract.test_existing_logfood_key_semantics_are_preserved,
           app, native, bearer, make_user, None)


def test_n7_22_diary_if_match_regression_is_detected(
        app, native, bearer, make_user, monkeypatch):
    from app.services.mobile_diary_mutation import service
    monkeypatch.setattr(service, "matches_diary_entry_revision", lambda *_a: True)
    assert mobile_diary_mutation.set_slot is service.set_slot
    killed(contract.test_existing_diary_if_match_semantics_are_preserved,
           app, native, bearer, make_user, None)


def test_n7_23_web_nutrition_semantic_change_is_detected(
        app, client, auth_user, monkeypatch):
    """The browser save must stay unconditional; inheriting the native
    precondition would silently change the web contract."""
    from app.services import nutrition_plan_store as store
    from app.blueprints.nutrition import plan as web_plan
    real = store.replace_nutrition_plan

    def refuse_existing(user_id, document, score, check=store.UNCONDITIONAL):
        def native_like(current):
            if current is not None:
                raise RuntimeError("precondition")
        return real(user_id, document, score, native_like)
    monkeypatch.setattr(web_plan, "replace_nutrition_plan", refuse_existing)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    killed(pr5.test_invalid_save_never_destroys_the_current_plan, app, client, auth_user)
