"""Sprint 13 PR1 - characterization tests for the Nutrition closure discovery.

These are DISCOVERY tests. They do not assert what AxisAI's Nutrition domain
should become; they pin down what it *is* on `origin/main` a44f31e, so the
architecture report's claims are executable rather than prose, and so a later PR
that changes one of these facts has to change the discovery decision out loud
instead of letting the document rot.

Every assertion below corresponds to a numbered finding, decision or closure
criterion in
``docs/superpowers/specs/2026-08-30-sprint13-pr1-nutrition-closure-discovery.md``.

Nothing here snapshots copy, layout or an incidental source string where an AST,
import, URL-map or runtime property could prove the same thing.

    python -m pytest tests/test_sprint13_nutrition_closure_discovery.py -v
"""
import ast
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog, UserSession


REPO_ROOT = Path(__file__).resolve().parent.parent

FIXED_DAY = date(2026, 8, 9)

# `MealLog.tarih` is the canonical Istanbul day key. Every live writer produces
# this shape. Migration df0d08c0cd24 briefly did not, and its direct successor
# 9be792c80008 normalized every row it wrote (report 5.1).
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _module_source(relative):
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _python_files(*relative_roots):
    """Every .py file under the given roots, excluding the test suite itself."""
    for root in relative_roots:
        base = REPO_ROOT / root
        if base.is_file():
            yield base
            continue
        for path in base.rglob("*.py"):
            if "tests" in path.relative_to(REPO_ROOT).parts:
                continue
            yield path


def _relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def _literal(tree, name):
    """The literal value assigned to a module-level name, or None."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            return ast.literal_eval(node.value)
    return None


def _revision_graph():
    """``{revision: down_revision}`` for every Alembic revision file."""
    graph = {}
    for path in (REPO_ROOT / "migrations" / "versions").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        graph[_literal(tree, "revision")] = _literal(tree, "down_revision")
    return graph


def _parents(down_revision):
    if isinstance(down_revision, (tuple, list)):
        return [item for item in down_revision if item]
    return [down_revision] if down_revision else []


def _ancestors_of_head(graph):
    """Every revision reachable by walking down from the single head."""
    parents = set()
    for down in graph.values():
        parents.update(_parents(down))
    heads = sorted(set(graph) - parents)
    assert len(heads) == 1, f"Expected exactly one Alembic head, found {heads}."
    seen, stack = set(), [heads[0]]
    while stack:
        revision = stack.pop()
        if revision in seen:
            continue
        seen.add(revision)
        stack.extend(_parents(graph.get(revision)))
    return seen


def _constructs(path, class_name):
    """True when the module calls ``class_name(...)`` anywhere."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == class_name):
            return True
    return False


# ---------------------------------------------------------------------------
# N1 / C1 - exactly one canonical consumed-food ledger
# ---------------------------------------------------------------------------
# The whole sprint rests on this. A "consumed-food ledger" is a model that owns
# both a persisted day key and all four macro columns: the day key is what makes
# a row belong to a calendar day of eating, and the macros are what make it a
# measurement rather than a plan or a builder line. `CustomMeal` has the day but
# no macros; `CustomMealItem` has macros but no day. Only `MealLog` has both.

_DAY_KEY_COLUMNS = {"tarih", "date_key"}
_MACRO_COLUMN_SETS = (
    {"kalori", "protein", "karb", "yag"},
    {"calories", "protein", "carbs", "fat"},
)


def _declared_columns(class_node):
    names = set()
    for statement in class_node.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        value = statement.value
        if not (isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "Column"):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def test_meal_log_is_the_only_model_that_owns_a_day_and_four_macros():
    """F-none / N1: one ledger, proven from the model definitions themselves."""
    tree = ast.parse(_module_source("app/models.py"), filename="app/models.py")
    ledgers = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        columns = _declared_columns(node)
        has_day = bool(columns & _DAY_KEY_COLUMNS)
        has_macros = any(macros <= columns for macros in _MACRO_COLUMN_SETS)
        if has_day and has_macros:
            ledgers.add(node.name)
    assert ledgers == {"MealLog"}, (
        "A second model now owns both a day key and a full macro set. That is a "
        "second definition of what the user ate; decision C1 forbids it.")


def test_the_builder_holds_the_provenance_the_ledger_does_not():
    """C4 / F-provenance: the ledger cannot prove provider, serving or quantity.

    This is the fact that makes advanced diary editing unprovable and therefore
    out of scope. It is asserted from the mapped columns, not from prose.
    """
    ledger_columns = set(MealLog.__table__.columns.keys())
    builder_columns = set(CustomMealItem.__table__.columns.keys())

    provenance = {
        "fatsecret_food_id", "serving_id", "serving_description",
        "serving_quantity", "grams", "per_100g_calories", "per_100g_protein",
        "per_100g_carbs", "per_100g_fat",
    }
    assert not (provenance & ledger_columns), (
        "MealLog gained provider/serving provenance. Decision C4 said the "
        "product does not require it; adding it is an architecture change.")
    assert provenance <= builder_columns, (
        "The builder lost provenance the discovery relied on.")

    assert ledger_columns == {
        "id", "user_id", "ogun", "yemekler", "kalori", "protein", "karb",
        "yag", "tarih", "source", "idempotency_key",
        "idempotency_fingerprint", "photo_key", "created_at",
    }


# ---------------------------------------------------------------------------
# N2 / W1-W11 - the writer inventory is closed
# ---------------------------------------------------------------------------

def test_canonical_ledger_writer_inventory_is_closed():
    """Section 6: every module that can create a MealLog row, pinned.

    A new writer is the single most likely way a future PR reintroduces a
    protection gap (client-trusted macros, a missing clamp, a fabricated day).
    This test forces that PR to name itself here.
    """
    writers = {
        _relative(path)
        for path in _python_files("app", "fitx_mcp", "scripts", "worker.py")
        if _relative(path) != "app/models.py" and _constructs(path, "MealLog")
    }
    assert writers == {
        "app/blueprints/nutrition/diary.py",      # W2 quick-add, W3 builder commit
        "app/blueprints/nutrition/meallog.py",    # W1 manual / AI / override
        "app/blueprints/social.py",               # W6 shared meal suggestion
        "app/services/ai_coach.py",               # W5 coach confirmation
        "app/services/barcode.py",                # W4 barcode add
        "app/services/mobile_log_food/service.py",  # W7 mobile LogFood
        "scripts/frontend_audit/seed.py",         # W10 audit seeder, not production
    }


def test_raw_sql_ledger_inserts_are_limited_to_the_two_known_places():
    """W9 / W11: an ORM-bypassing writer skips every model-level guard."""
    raw_writers = {
        _relative(path)
        for path in _python_files("app", "fitx_mcp", "scripts", "migrations")
        if "insert into meal_log" in path.read_text(encoding="utf-8").lower()
    }
    assert raw_writers == {
        "fitx_mcp/server.py",  # W9 - developer tool, not in the deployed image
        "migrations/versions/"
        # W11 - the yearless backfill; repaired by 9be792c80008 (report 5.1)
        "df0d08c0cd24_backfill_user_daily_nutrition_to_meal_.py",
    }


def test_the_mcp_ledger_writer_is_not_part_of_the_deployed_image():
    """W9: recorded as out-of-band precisely because nothing deploys it."""
    for relative in ("docker-compose.yml", "Dockerfile"):
        assert "fitx_mcp" not in _module_source(relative), (
            "fitx_mcp is now deployed. It writes MealLog with `user_id` as a "
            "tool argument and no idempotency (F11) - that changes its severity.")


# ---------------------------------------------------------------------------
# N5 / report 5.1 - the day key
# ---------------------------------------------------------------------------
# F13 was withdrawn and C14 retired by independent review: the yearless backfill
# was normalized by its own direct successor. These tests therefore pin the whole
# chain - defect *and* repair - rather than only the half that made the original
# finding look live.

def test_every_live_writer_uses_the_canonical_iso_day_key(app, make_user):
    """N5: the day is the server's, and it is ISO.

    Asserted through the model default rather than through eight call sites: a
    row written with no explicit `tarih` still lands on a valid ISO Istanbul day.
    """
    user = make_user("s13-day-key")
    entry = MealLog(user_id=user.id, ogun="Kahvaltı", yemekler="Yulaf",
                    kalori=100.0, protein=1.0, karb=2.0, yag=3.0)
    db.session.add(entry)
    db.session.commit()
    assert ISO_DAY.match(entry.tarih)
    assert date.fromisoformat(entry.tarih)


def test_the_yearless_backfill_was_repaired_by_its_direct_successor():
    """Report 5.1 / N5: the complete historical invariant, not half of it.

    PR1 as first written pinned only the transient defect - migration
    df0d08c0cd24 formatting a yearless `%d.%m` day key - and concluded from it
    that the ledger still holds unreachable rows. It does not: the very next
    revision rewrote every row to the ISO Istanbul day. That made F13 a false
    P1, and nothing in this module would have caught the mistake, because
    deleting the repair migration failed no test.

    So this asserts the chain end to end: the defect existed, the repair exists,
    it is wired directly to the migration it repairs, it derives the day key from
    canonical created-at semantics, and it sits before the Alembic head. Break
    any link and the withdrawn finding becomes live again - which is exactly when
    someone should have to look at report 5.1.
    """
    versions = REPO_ROOT / "migrations" / "versions"
    backfill = versions / "df0d08c0cd24_backfill_user_daily_nutrition_to_meal_.py"
    repair = versions / "9be792c80008_tarih_to_iso_date_istanbul.py"

    # 1. the older migration really did write a yearless day key.
    produced = None
    for node in ast.walk(ast.parse(backfill.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "strftime"
                and node.args
                and isinstance(node.args[0], ast.Constant)):
            produced = datetime(2026, 6, 15, 20, 0).strftime(node.args[0].value)
    assert produced is not None and not ISO_DAY.match(produced), (
        "The backfill no longer formats a non-ISO day key. The chain this test "
        "pins has changed; update report 5.1 deliberately.")

    # 2. + 3. the repair exists, and it is the backfill's direct successor.
    assert repair.exists(), (
        "9be792c80008 is gone. That migration is the entire reason F13 is a "
        "withdrawn finding rather than a live P1; removing it re-opens the "
        "defect and would require a repair migration after all.")
    repair_tree = ast.parse(repair.read_text(encoding="utf-8"))
    assert _literal(repair_tree, "revision") == "9be792c80008"
    assert _literal(repair_tree, "down_revision") == "df0d08c0cd24", (
        "The repair was disconnected from the migration it repairs. Its "
        "guarantee is that no database can run one without the other.")

    # 4. it normalizes with canonical created-at -> Istanbul day semantics.
    source = repair.read_text(encoding="utf-8")
    for fragment in ("UPDATE meal_log SET tarih", "created_at",
                     "Europe/Istanbul", "astimezone", "date().isoformat()"):
        assert fragment in source, (
            f"The repair no longer derives tarih via {fragment!r}. Its rule is "
            "identical to the repair PR2A would have performed, which is why "
            "PR2A does not exist.")

    # 5. the repair is an ancestor of the current head, so it cannot be skipped.
    assert "9be792c80008" in _ancestors_of_head(_revision_graph()), (
        "The repair is no longer an ancestor of the Alembic head, so a database "
        "at head could have run the backfill without it.")


def test_a_malformed_day_key_is_invisible_to_the_canonical_readers(
        app, make_user):
    """Report 5.1: why a non-ISO key is invisible rather than miscounted.

    Retained after F13's withdrawal because the reader behaviour is still the
    load-bearing fact: it is what made the transient backfill state harmless
    between the two June 2026 migrations, and it is why a *live* writer emitting
    a non-ISO key would be a real defect rather than a cosmetic one.
    """
    user = make_user("s13-orphan-row")
    db.session.add(MealLog(
        user_id=user.id, ogun="AI Koç", yemekler="Eski koç öğünü",
        kalori=500.0, protein=30.0, karb=50.0, yag=20.0,
        tarih="15.06", source="coach",
        created_at=datetime(2026, 6, 15, 17, 0)))
    db.session.commit()

    same_day = MealLog.query.filter_by(
        user_id=user.id, tarih=FIXED_DAY.isoformat()).all()
    assert same_day == []

    in_range = MealLog.query.filter(
        MealLog.user_id == user.id,
        MealLog.tarih >= "2026-06-01").all()
    assert in_range == [], (
        "A DD.MM row became reachable by a range filter. That is worse than "
        "invisible - it would be counted into the wrong year.")

    assert MealLog.query.filter_by(user_id=user.id).count() == 1, (
        "The row exists; only the day-keyed queries cannot see it.")


def test_the_ledger_schema_pins_macro_bounds_and_not_the_day_key_format():
    """C14 (RETIRED): the day key needs no database constraint.

    The original report proposed `ck_meal_log_tarih_iso`. Review retired it on
    its own merits, independently of the repair: `tarih` is `String(10)`, so a
    CHECK can pin a *pattern* but never a calendar date - `'2026-13-45'`
    satisfies every portable form - and PostgreSQL's regex operator is a syntax
    error on SQLite, so no single portable constraint expresses the intent.

    This test therefore no longer justifies a future migration. It characterizes
    the constraint set as it is, so that adding one becomes a visible decision
    rather than a drive-by.
    """
    constraint_names = {
        constraint.name for constraint in MealLog.__table__.constraints
        if constraint.name
    }
    assert "ck_meal_log_macro_bounds" in constraint_names, (
        "The macro bounds are the ledger's one real value constraint (N7).")
    assert "uq_meal_log_user_idempotency" in constraint_names
    assert "ck_meal_log_tarih_iso" not in constraint_names, (
        "A day-key CHECK landed. C14 was retired on the evidence in report 5.1; "
        "reinstating it is a new architecture decision, not a rebased migration.")
    assert MealLog.__table__.c.tarih.type.length == 10, (
        "tarih is a fixed-width string, which is precisely why a CHECK could "
        "only ever pin a shape and not a real date.")


# ---------------------------------------------------------------------------
# Mobile contract - route inventory and intentional absences
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def api_v1_rules():
    """The `/api/v1` URL map. Built once; every test here only reads it.

    `tests/conftest.py` already sets MOBILE_AUTH_ENABLED=1 for the suite, so the
    blueprint is registered. In production the flag defaults to False, which is
    why this is a contract inventory and not a reachability claim.
    """
    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return {
        (rule.rule, frozenset(rule.methods) - {"HEAD", "OPTIONS"})
        for rule in flask_app.url_map.iter_rules()
        if rule.rule.startswith("/api/v1")
    }


def test_mobile_nutrition_route_inventory(api_v1_rules):
    """Section 8: exactly seven mobile nutrition routes, and no more."""
    nutrition = {
        rule for rule in api_v1_rules if rule[0].startswith("/api/v1/nutrition")
    }
    assert nutrition == {
        ("/api/v1/nutrition/diary/today", frozenset({"GET"})),
        ("/api/v1/nutrition/foods/search", frozenset({"GET"})),
        ("/api/v1/nutrition/foods/fatsecret/<food_id>/servings",
         frozenset({"GET"})),
        ("/api/v1/nutrition/foods/barcode", frozenset({"GET"})),
        ("/api/v1/nutrition/logs", frozenset({"POST"})),
        ("/api/v1/nutrition/logs/<entry_token>", frozenset({"PATCH"})),
        ("/api/v1/nutrition/logs/<entry_token>", frozenset({"DELETE"})),
    }


def test_the_mobile_surface_publishes_no_history_menu_plan_or_water(
        api_v1_rules):
    """C6, C7, C9, C10: four deliberate absences, each an architecture decision.

    They are asserted together because they share one reason: each would be a
    new capability, and none is required for consumption correctness.
    """
    paths = {rule[0] for rule in api_v1_rules}
    for absent in ("history", "menu", "nutrition-plan", "water", "draft",
                   "diary/builder"):
        assert not any(absent in path for path in paths), (
            f"/api/v1 gained a '{absent}' surface. Sprint 13 decided it was "
            "out of scope; adding it is a scope decision, not a detail.")


# ---------------------------------------------------------------------------
# Entry identity and revision ownership
# ---------------------------------------------------------------------------

def test_entry_identity_is_owner_bound_and_never_the_database_id():
    from app.services.mobile_nutrition import identity

    secret = "s13-secret"
    token = identity.diary_entry_id(secret, 7, 42)

    assert token != "42" and "42" not in token
    assert identity.matches_diary_entry_id(secret, 7, 42, token)
    assert not identity.matches_diary_entry_id(secret, 8, 42, token), (
        "A token minted for one owner resolved against another.")
    assert not identity.matches_diary_entry_id(secret, 7, 43, token)
    assert identity.diary_entry_id("other-secret", 7, 42) != token


def test_revision_covers_the_authoritative_state_and_only_that():
    """C5 depends on this: the revision is what makes a mutation stale-safe."""
    from app.services.mobile_nutrition import revision as rev

    base = dict(
        user_id=7, entry_id=42, meal_label="Kahvaltı", description="Yulaf",
        energy_kcal=320.0, protein_g=12.0, carbohydrate_g=40.0, fat_g=9.0,
        diary_date=FIXED_DAY.isoformat(), source="manual",
        idempotency_key=None, idempotency_fingerprint=None, photo_key=None,
        created_at=datetime(2026, 8, 9, 5, 24))
    secret = "s13-secret"
    token = rev.diary_entry_revision(
        secret, rev.DiaryEntryRevisionState(**base))

    assert rev.matches_diary_entry_revision(
        secret, rev.DiaryEntryRevisionState(**base), token)

    for field, changed in (
            ("meal_label", "Öğle"),
            ("description", "Yulaf ezmesi"),
            ("energy_kcal", 321.0),
            ("diary_date", "2026-08-10"),
            ("source", "coach"),
            ("photo_key", "meals/1.jpg")):
        mutated = dict(base, **{field: changed})
        assert not rev.matches_diary_entry_revision(
            secret, rev.DiaryEntryRevisionState(**mutated), token), (
            f"Changing {field} left the revision unchanged; a stale write "
            "would be accepted.")

    # A null macro is not a measured zero, and the revision must tell them apart.
    assert not rev.matches_diary_entry_revision(
        secret, rev.DiaryEntryRevisionState(**dict(base, fat_g=None)), token)


def test_the_mobile_mutation_vocabulary_is_exactly_set_slot():
    """Section 9: what "supported" means today, in executable form."""
    from app.services import mobile_diary_mutation as mutation

    command = mutation.parse_mutation_command(
        {"operation": "set_slot", "slot": "ogle"})
    assert command == mutation.SetSlotCommand(slot="ogle")

    for rejected in (
            {"operation": "set_slot", "slot": "brunch"},
            {"operation": "set_description", "slot": "ogle"},
            {"operation": "set_slot", "slot": "ogle", "description": "x"},
            {"operation": "set_slot"},
            {"operation": "delete"},
            {"operation": "set_nutrition",
             "nutrition": {"energy_kcal": 1}},
            None, [], "set_slot"):
        with pytest.raises(mutation.InvalidDiaryMutation):
            mutation.parse_mutation_command(rejected)


# ---------------------------------------------------------------------------
# N3 - server-owned totals, and the builder that must never be added to them
# ---------------------------------------------------------------------------

def test_the_server_owns_the_daily_totals_and_nulls_are_not_measurements():
    from app.services.mobile_nutrition import serialization

    entries = (
        SimpleNamespace(energy_kcal=320.0, protein_g=12.0,
                        carbohydrate_g=40.0, fat_g=None),
        SimpleNamespace(energy_kcal=None, protein_g=8.0,
                        carbohydrate_g=0.0, fat_g=5.0),
    )
    totals = serialization.day_totals(entries)
    assert totals == {"energy_kcal": 320.0, "protein_g": 20.0,
                      "carbohydrate_g": 40.0, "fat_g": 5.0}

    # ... while the per-entry projection keeps the same values missing.
    facts = serialization.nutrient_facts(None, 8.0, 0.0, 5.0)
    assert facts["energy_kcal"] is None
    assert facts["carbohydrate_g"] == 0.0, (
        "A measured zero must survive as a measurement, not become null.")

    assert serialization.nutrition_goal(None) is None
    assert serialization.nutrition_goal(0) is None, (
        "A zero calorie target is not a target anyone configured.")
    assert serialization.nutrition_goal(2200) == {"target_energy_kcal": 2200.0}


def test_committing_a_builder_meal_writes_the_ledger_and_keeps_the_builder(
        app, client, auth_user):
    """C1 / N1: the double-count the docstrings forbid, demonstrated.

    Both surfaces report the same calories after one commit. Their sum is not a
    day's intake, which is exactly why no reader may add them.
    """
    created = client.post("/api/diary/meal", json={"meal_name": "Öğle"})
    meal_id = created.get_json()["meal_id"]

    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "Tavuk göğsü", "grams": 200,
        "per_100g": {"calories": 165, "protein": 31, "carbs": 0, "fat": 3.6},
    })
    assert client.post(f"/api/diary/meal/{meal_id}/log").status_code == 200

    builder_total = client.get("/api/diary/today").get_json()["totals"]
    ledger_total = client.get("/meal-log/today").get_json()["totals"]

    assert builder_total["calories"] == pytest.approx(330.0)
    assert ledger_total["kalori"] == pytest.approx(330.0)
    assert CustomMeal.query.filter_by(user_id=auth_user.id).count() == 1
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


# ---------------------------------------------------------------------------
# F1 / N9 - the web has no correction path for a committed entry
# ---------------------------------------------------------------------------

def test_the_web_nutrition_blueprint_publishes_no_ledger_mutation_route(app):
    """F1: pinned as a URL-map fact so PR4 has to add itself here."""
    nutrition_rules = {
        (rule.rule, frozenset(rule.methods) - {"HEAD", "OPTIONS"})
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("nutrition.")
    }
    assert nutrition_rules == {
        ("/api/quick-add-meal", frozenset({"POST"})),
        ("/api/diary/meal", frozenset({"POST"})),
        ("/api/diary/meal/<int:meal_id>/item", frozenset({"POST"})),
        ("/api/diary/meal/<int:meal_id>/log", frozenset({"POST"})),
        ("/api/diary/item/<int:item_id>", frozenset({"PATCH"})),
        ("/api/diary/item/<int:item_id>", frozenset({"DELETE"})),
        ("/api/diary/today", frozenset({"GET"})),
        ("/meal-log", frozenset({"POST"})),
        ("/meal-log/today", frozenset({"GET"})),
        ("/meal-log/history", frozenset({"GET"})),
        ("/meal-log/review", frozenset({"POST"})),
        ("/nutrition", frozenset({"GET"})),
        ("/nutrition-plan", frozenset({"POST"})),
        ("/nutrition-plan/save", frozenset({"POST"})),
        ("/nutrition-plan/active", frozenset({"GET"})),
    }
    mutating = {
        rule for rule in nutrition_rules
        if rule[1] & {"PATCH", "DELETE"}
    }
    assert all(rule[0].startswith("/api/diary/item/") for rule in mutating), (
        "The only web PATCH/DELETE routes address the builder, not the ledger. "
        "If PR4 added a ledger route, update F1 and this inventory together.")


def test_the_builder_delete_route_cannot_reach_a_committed_ledger_row(
        app, client, auth_user):
    """F1: the closest thing the web has to a delete does not touch the ledger."""
    entry = MealLog(
        user_id=auth_user.id, ogun="Kahvaltı", yemekler="Yanlış öğün",
        kalori=900.0, protein=10.0, karb=100.0, yag=40.0,
        tarih=date.today().isoformat(), source="manual")
    db.session.add(entry)
    db.session.commit()
    entry_id = entry.id

    response = client.delete(f"/api/diary/item/{entry_id}")
    assert response.status_code == 404
    assert db.session.get(MealLog, entry_id) is not None, (
        "A web route deleted a canonical ledger row. That is the capability F1 "
        "says does not exist; adding it is decision C5, not a bug fix.")


# ---------------------------------------------------------------------------
# F2 / F3 / N4 - the derived figures have no single owner
# ---------------------------------------------------------------------------

def test_barcode_and_coach_now_agree_on_the_carbohydrate_target(
        app, make_user):
    """F2 CLOSED by Sprint 13 PR2 (C2).

    This test used to pin the *disagreement* — barcode published 225 g of
    carbohydrate for a non-muscle-gain user while the coach published 250 g —
    and it said, in as many words, that PR2's convergence should replace it.
    PR2 landed, so the assertion is inverted: the surviving definition is the
    coach/menu formula, and the divergent barcode value was the defect, not a
    behaviour anyone relied on.

    Still run through the real functions rather than source constants, so this
    is a behavioural fact. The wider convergence proof (menu included, plus the
    architecture guard) lives in `tests/test_nutrition_targets.py`.
    """
    from app.services import ai_coach, barcode

    user = make_user("s13-targets")
    session = UserSession(user_id=user.id, goal="kilo verme",
                          target_calories=2000.0)
    db.session.add(session)
    db.session.commit()

    barcode_targets = barcode._target_macros(session)
    coach_remaining = ai_coach._remaining_macros_for_user(user.id)

    # No meals logged, so the coach's "remaining" is its target.
    assert coach_remaining is not None
    assert coach_remaining["carbs"] == pytest.approx(250.0)   # 2000 * 0.50 / 4
    assert barcode_targets.carbs == pytest.approx(
        coach_remaining["carbs"]), (
        "Barcode and the coach diverged again. There is exactly one canonical "
        "daily macro-target derivation (C2) — see app/services/nutrition_targets.py.")
    assert barcode_targets.protein == pytest.approx(
        coach_remaining["protein"])


def test_the_barcode_target_no_longer_fabricates_a_goal_the_user_never_set():
    """F3a CLOSED by Sprint 13 PR2 (C2).

    This used to assert `targets["calories"] == 2000` for a user who had
    configured nothing, with a note saying PR2 would remove that default. It
    did: absence is now published as absence, matching the mobile boundary that
    was already the reference behaviour for this decision.

    The second half of the finding is unchanged and still checked: the payload
    that carries the target is rendered by no first-party surface, which is why
    F3a was P2 rather than P1. If that ever changes, PR2's correction becomes
    user-visible and this test says so out loud.
    """
    from app.services import barcode

    unconfigured = SimpleNamespace(target_calories=None, goal=None)
    assert barcode._target_macros(unconfigured) is None, (
        "A fabricated configured target came back. F3a is closed by C2 — the "
        "server must never substitute a number for an unset target.")

    from app.services.mobile_nutrition import serialization
    assert serialization.nutrition_goal(None) is None, (
        "The mobile boundary is the reference behaviour for F3a.")

    frontend = list((REPO_ROOT / "static").rglob("*.js"))
    frontend += list((REPO_ROOT / "templates").rglob("*.html"))
    for key in ("daily_context", "portion_recommendation", "axisai_food_score"):
        renderers = [
            _relative(path) for path in frontend
            if key in path.read_text(encoding="utf-8", errors="ignore")
        ]
        assert renderers == [], (
            f"{key} gained a first-party consumer in {renderers}, so the "
            "fabricated target is now user-visible. F3a becomes a P1.")


def test_progress_and_mobile_today_consume_no_nutrition_authority():
    """C8: the canonical services deliberately carry no nutrition at all."""
    canonical = [
        "app/services/progress_summary",
        "app/services/progress_insights",
        "app/services/progress_history",
        "app/services/progress_physique",
        "app/services/mobile_today.py",
        "app/today_presenter.py",
    ]
    for path in _python_files(*canonical):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "MealLog" not in imported, (
            f"{_relative(path)} now imports the nutrition ledger. Progress "
            "deliberately owns no nutrition intelligence (C8); wiring one in "
            "is an architecture decision.")


def test_the_orphaned_nutrition_read_surfaces_still_exist_and_have_no_consumer(
        app):
    """F9: both halves of the finding - the routes exist, nothing calls them."""
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/progress/nutrition" in rules
    assert "/api/progress/insights" in rules

    frontend = list((REPO_ROOT / "static").rglob("*.js"))
    frontend += list((REPO_ROOT / "templates").rglob("*.html"))
    for endpoint in ("progress/nutrition", "progress/insights"):
        callers = [
            _relative(path) for path in frontend
            if endpoint in path.read_text(encoding="utf-8", errors="ignore")
        ]
        assert callers == [], (
            f"{endpoint} gained a consumer. F9 recommended retiring it (C8); "
            "wiring it up instead is a decision that needs recording.")


# ---------------------------------------------------------------------------
# F14 / C4 / N9 - the correction primitive's resource lifecycle
# ---------------------------------------------------------------------------

def test_deleting_a_ledger_row_releases_no_stored_meal_photo():
    """F14: the primitive Sprint 13 closes on has no object lifecycle.

    Two halves, both load-bearing. The repository owns no S3 deletion primitive
    at all, so there is nothing a caller could reach for; and the single code
    path that deletes a canonical row does not reach for one. Together they are
    why "delete + re-log is exact" (C4, as first written) is false for a
    photo-bearing row, and why PR4 must add the lifecycle rather than assume it.
    """
    import s3_helper

    deleters = sorted(
        name for name in dir(s3_helper)
        if not name.startswith("__")
        and ("delete" in name.lower() or "remove" in name.lower()))
    assert deleters == [], (
        f"s3_helper grew {deleters}. If PR4 added the deletion primitive F14 "
        "asks for, replace this characterization with the lifecycle's own "
        "test - including its partial-failure path - rather than deleting it.")

    tree = ast.parse(
        _module_source("app/services/mobile_diary_mutation/service.py"))
    delete_entry = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "delete_entry")
    body = ast.dump(delete_entry).lower()
    assert "photo" not in body and "s3" not in body, (
        "delete_entry now touches the stored object. That is F14's fix landing; "
        "update the finding, C4 and this test together.")


# ---------------------------------------------------------------------------
# F5 / F7 / N2 - the web ledger writer's trust model
# ---------------------------------------------------------------------------

def test_the_web_meal_log_persists_client_supplied_macros_unchanged(
        app, client, auth_user):
    """F5: the web trusts the caller's arithmetic; the mobile path does not.

    The values below are physically plausible, so `clamp_serving_macros` is a
    no-op and what reaches the ledger is exactly what the browser computed.
    """
    response = client.post("/meal-log", json={
        "ogun": "Öğle",
        "yemekler": "Tavuk ve pilav",
        "override_macros": {
            "kalori": 642.0, "protein": 44.0, "karb": 61.0, "yag": 21.0},
    })
    assert response.status_code == 200

    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert (entry.kalori, entry.protein, entry.karb, entry.yag) == (
        642.0, 44.0, 61.0, 21.0), (
        "The web write path started recomputing. That is decision C3 landing; "
        "update F5 and this characterization together.")
    assert entry.source == "manual", (
        "The override branch sets no source, so the column default stamps "
        "'manual' - a browser-computed provider serving is recorded as a "
        "hand-entered meal, and no reader can tell the difference.")


def test_the_web_meal_log_does_not_validate_the_meal_slot(
        app, client, auth_user):
    """F7 / C12: free text reaches the canonical slot column."""
    from app.services.mobile_nutrition import serialization

    assert client.post("/meal-log", json={
        "ogun": "brunch, sort of",
        "yemekler": "Menemen",
        "override_macros": {
            "kalori": 400.0, "protein": 20.0, "karb": 12.0, "yag": 28.0},
    }).status_code == 200

    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert entry.ogun == "brunch, sort of"
    assert serialization.slot_token(entry.ogun) == serialization.SLOT_UNKNOWN, (
        "An unrecognised label must publish as 'unknown' rather than be forced "
        "into a bucket the user did not choose.")


def test_the_web_history_contract_publishes_a_day_with_no_year(
        app, client, auth_user):
    """C6: why no mobile history contract exists over this surface."""
    db.session.add(MealLog(
        user_id=auth_user.id, ogun="Akşam", yemekler="Mercimek çorbası",
        kalori=210.0, protein=11.0, karb=30.0, yag=4.0,
        tarih=date.today().isoformat(), source="manual"))
    db.session.commit()

    days = client.get("/meal-log/history").get_json()
    assert days, "The history surface returned nothing to characterize."
    for day in days:
        assert re.fullmatch(r"\d{2}\.\d{2}", day["tarih"]), (
            "History gained a year. If a native history contract is being "
            "built, decision C6 has changed.")
