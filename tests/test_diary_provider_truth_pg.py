"""Opt-in PostgreSQL races for the canonical diary provider-truth write path.

SQLite cannot prove any of this. It has no `SELECT ... FOR UPDATE`, so
`with_for_update()` is silently a no-op there and every diary snapshot/claim
test in the SQLite suite passes whether or not the row locks exist. These
tests run the REAL routes against a disposable PostgreSQL database, with the
provider network boundary (`mobile_food_discovery.servings`) held open on a
barrier so a competing writer commits at exactly the moment the reviewed code
is doing provider I/O.

That interleave is also what proves the transaction boundary: the competing
request can only commit while the resolving request holds NO read transaction.
If a future change lets that transaction span the provider call, these tests
do not fail with a wrong value - they TIME OUT, which the barrier timeouts
below turn into a failure rather than a hang.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \\
        python -m pytest tests/test_diary_provider_truth_pg.py -m pg_concurrency
"""
import os
import threading

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )

BARRIER_TIMEOUT = 20
THREAD_TIMEOUT = 45

_SERVINGS = {
    "s1": ("1 adet", 60, 90, 7, 0.6, 6.3),
    "s9": ("1 tabak", 150, 195, 4, 42, 0.5),
}


@pytest.fixture(scope="module", autouse=True)
def _postgres_database_url():
    """Point the standard app fixture at PostgreSQL for this module only.

    Reusing the suite's own `app`/`client`/`login` stack is deliberate: a
    bespoke app would be a second wiring, and a race proven against a second
    wiring proves nothing about the one that ships.
    """
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip(
            "PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        os.environ["DATABASE_URL"] = previous


@pytest.fixture
def gated_provider(monkeypatch):
    """The provider boundary, parkable for exactly ONE resolution.

    The hold is one-shot and must be armed: staging the fixture data resolves
    the provider too, and so does the competing writer's own PATCH. Only the
    request under test may be parked, or the test would deadlock against its
    own setup instead of measuring the code.
    """
    from app.services import mobile_food_discovery

    gate = threading.Event()
    entered = threading.Event()
    armed = threading.Event()
    lock = threading.Lock()
    calls = []

    def servings(food_id):
        calls.append(food_id)
        with lock:
            hold = armed.is_set()
            armed.clear()
        if hold:
            entered.set()
            if not gate.wait(timeout=BARRIER_TIMEOUT):
                raise AssertionError(
                    "the provider gate was never released - the resolving "
                    "request is holding a transaction across provider I/O, or "
                    "the competing writer never committed")
        return {
            "provider": "fatsecret", "food_id": food_id,
            "name": "Provider food", "brand": "",
            "servings": [{
                "serving_id": sid, "description": values[0],
                "metric_mass": {"amount": values[1], "unit": "g"},
                "nutrition": dict(zip(
                    ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g"),
                    values[2:])),
                "nutrition_per_100g": dict(zip(
                    ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g"),
                    (value * 100 / values[1] for value in values[2:]))),
            } for sid, values in _SERVINGS.items()],
        }

    def arm():
        entered.clear()
        gate.clear()
        armed.set()

    monkeypatch.setattr(mobile_food_discovery, "servings", servings)
    return type("Gate", (), {
        "gate": gate, "entered": entered, "calls": calls,
        "arm": staticmethod(arm), "release": staticmethod(gate.set),
    })()


@pytest.fixture
def rival(app, login):
    """A SECOND authenticated client, so the two racers hold separate sessions."""
    other = app.test_client()
    response = other.post(
        "/login", json={"username": "testuser", "password": "Sifre123"})
    assert response.status_code in (200, 302), response.status_code
    return other


def _meal(client):
    return client.post(
        "/api/diary/meal", json={"meal_name": "Öğle"}).get_json()["meal_id"]


def _provider_item(client, meal_id, quantity=1):
    return client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "caller", "fatsecret_food_id": "f1",
        "serving_id": "s1", "serving_quantity": quantity,
    }).get_json()["item_id"]


def _manual_item(client, meal_id):
    return client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "yumurta", "grams": 100,
        "per_100g": {"calories": 150, "protein": 12, "carbs": 1, "fat": 10},
    }).get_json()["item_id"]


def _in_thread(fn):
    outcome = {}

    def run():
        try:
            outcome["result"] = fn()
        except BaseException as error:  # pragma: no cover - surfaced below
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _join(thread, outcome):
    thread.join(timeout=THREAD_TIMEOUT)
    assert not thread.is_alive(), "the racing request never completed"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _interleave(client, meal_id, provider, competing_write):
    """Run `POST /log` and commit `competing_write` DURING provider resolution."""
    provider.arm()
    thread, outcome = _in_thread(
        lambda: _status_and_body(client.post(f"/api/diary/meal/{meal_id}/log")))
    assert provider.entered.wait(timeout=BARRIER_TIMEOUT), (
        "the commit never reached provider resolution")
    competing = competing_write()
    provider.release()
    return _join(thread, outcome), competing


def _status_and_body(response):
    return response.status_code, response.get_json()


# ---------------------------------------------------------------------------
# 1. Concurrent double diary log - the claim is the replay authority
# ---------------------------------------------------------------------------

def test_concurrent_diary_log_writes_exactly_one_ledger_row(
        app, client, auth_user, rival, gated_provider):
    from app.extensions import db
    from app.models import CustomMeal, MealLog

    meal_id = _meal(client)
    _provider_item(client, meal_id)

    start = threading.Barrier(2, timeout=BARRIER_TIMEOUT)

    def log(with_client):
        def run():
            start.wait()
            return _status_and_body(
                with_client.post(f"/api/diary/meal/{meal_id}/log"))
        return run

    first = _in_thread(log(client))
    second = _in_thread(log(rival))
    results = [_join(*first), _join(*second)]

    statuses = sorted(status for status, _ in results)
    assert statuses == [200, 400], results
    assert MealLog.query.filter_by(
        user_id=auth_user.id, source="diary").count() == 1
    assert db.session.get(CustomMeal, meal_id).is_logged is True


def test_claim_is_won_exactly_once_under_row_locks(app, auth_user, client):
    """The atomic `is_logged` flip, isolated from transport."""
    from app.blueprints.nutrition.diary import _claim_diary_meal
    from app.extensions import db

    meal_id = _meal(client)
    start = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    claims = []

    def claim():
        with app.app_context():
            start.wait()
            try:
                claims.append(_claim_diary_meal(meal_id, auth_user.id))
                db.session.commit()
            finally:
                db.session.remove()

    threads = [_in_thread(claim) for _ in range(2)]
    for thread, outcome in threads:
        _join(thread, outcome)

    assert sorted(claims) == [0, 1], claims


# ---------------------------------------------------------------------------
# 2-6. The item set and every semantic item field are compared under locks
# ---------------------------------------------------------------------------

def test_item_added_during_provider_resolution_is_detected(
        client, auth_user, rival, gated_provider):
    from app.models import MealLog

    meal_id = _meal(client)
    _provider_item(client, meal_id)

    (status, body), _ = _interleave(
        client, meal_id, gated_provider,
        lambda: _manual_item(rival, meal_id))

    assert (status, body["error"]) == (409, "diary_meal_changed")
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_item_deleted_during_provider_resolution_is_detected(
        client, auth_user, rival, gated_provider):
    from app.models import MealLog

    meal_id = _meal(client)
    _provider_item(client, meal_id)
    doomed = _manual_item(client, meal_id)

    (status, body), deleted = _interleave(
        client, meal_id, gated_provider,
        lambda: rival.delete(f"/api/diary/item/{doomed}").status_code)

    assert deleted == 200
    assert (status, body["error"]) == (409, "diary_meal_changed")
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


@pytest.mark.parametrize("mutation", [
    {"serving_id": "s9", "serving_quantity": 1},
    {"serving_quantity": 3},
])
def test_provider_item_mutation_during_resolution_is_detected(
        client, auth_user, rival, gated_provider, mutation):
    """A serving change and a quantity change are each in the compared state."""
    from app.models import MealLog

    meal_id = _meal(client)
    item_id = _provider_item(client, meal_id)

    def mutate():
        # The rival's own PATCH resolves the provider too. The hold is
        # one-shot and already spent by the parked commit, so this passes
        # straight through instead of deadlocking behind it.
        return rival.patch(
            f"/api/diary/item/{item_id}", json=mutation).status_code

    (status, body), mutated = _interleave(
        client, meal_id, gated_provider, mutate)

    assert mutated == 200
    assert (status, body["error"]) == (409, "diary_meal_changed")
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_manual_item_mutation_during_resolution_is_detected(
        client, auth_user, rival, gated_provider):
    """Manual grams and macros are part of the compared state too."""
    from app.models import MealLog

    meal_id = _meal(client)
    _provider_item(client, meal_id)
    manual = _manual_item(client, meal_id)

    (status, body), mutated = _interleave(
        client, meal_id, gated_provider,
        lambda: rival.patch(
            f"/api/diary/item/{manual}", json={"grams": 250}).status_code)

    assert mutated == 200
    assert (status, body["error"]) == (409, "diary_meal_changed")
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_uncontended_commit_still_succeeds_through_the_same_gate(
        client, auth_user, gated_provider):
    """The 409s above must come from the CONFLICT, not from the gate itself."""
    from app.models import MealLog

    meal_id = _meal(client)
    _provider_item(client, meal_id)

    (status, body), _ = _interleave(
        client, meal_id, gated_provider, lambda: None)

    assert status == 200, body
    assert MealLog.query.filter_by(
        user_id=auth_user.id, source="diary").count() == 1


# ---------------------------------------------------------------------------
# 7. Provider failure before the claim leaves no partial durable effect
# ---------------------------------------------------------------------------

def test_provider_failure_leaves_meal_unlogged_and_retryable(
        app, client, auth_user, monkeypatch):
    from app.extensions import db
    from app.models import CustomMeal, MealLog
    from app.services import mobile_food_discovery

    working = mobile_food_discovery.servings
    meal_id = _meal(client)

    def resolve(food_id):
        return {
            "provider": "fatsecret", "food_id": food_id,
            "name": "Provider food", "brand": "",
            "servings": [{
                "serving_id": sid, "description": values[0],
                "metric_mass": {"amount": values[1], "unit": "g"},
                "nutrition": dict(zip(
                    ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g"),
                    values[2:])),
                "nutrition_per_100g": dict(zip(
                    ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g"),
                    (value * 100 / values[1] for value in values[2:]))),
            } for sid, values in _SERVINGS.items()],
        }

    monkeypatch.setattr(mobile_food_discovery, "servings", resolve)
    _provider_item(client, meal_id)

    def explode(food_id):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(mobile_food_discovery, "servings", explode)
    response = client.post(f"/api/diary/meal/{meal_id}/log")

    assert response.status_code == 503
    assert response.get_json()["retryable"] is True
    db.session.expire_all()
    assert db.session.get(CustomMeal, meal_id).is_logged is False
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0

    # Retryable means retryable: the same meal commits once the provider heals.
    monkeypatch.setattr(mobile_food_discovery, "servings", resolve)
    assert client.post(f"/api/diary/meal/{meal_id}/log").status_code == 200
    assert MealLog.query.filter_by(
        user_id=auth_user.id, source="diary").count() == 1
    monkeypatch.setattr(mobile_food_discovery, "servings", working)


# ---------------------------------------------------------------------------
# 8. Lock ORDER - concurrent edits across two items of one meal never deadlock
# ---------------------------------------------------------------------------

def test_concurrent_item_patches_on_one_meal_do_not_deadlock(
        client, auth_user, rival, gated_provider):
    from app.extensions import db
    from app.models import CustomMealItem

    meal_id = _meal(client)
    first = _provider_item(client, meal_id)
    second = _provider_item(client, meal_id)
    start = threading.Barrier(2, timeout=BARRIER_TIMEOUT)

    def patch(with_client, item_id):
        def run():
            start.wait()
            return with_client.patch(
                f"/api/diary/item/{item_id}",
                json={"serving_id": "s9", "serving_quantity": 2}).status_code
        return run

    racers = [_in_thread(patch(client, first)),
              _in_thread(patch(rival, second))]
    statuses = [_join(*racer) for racer in racers]

    assert statuses == [200, 200], statuses
    db.session.expire_all()
    for item_id in (first, second):
        assert db.session.get(CustomMealItem, item_id).serving_id == "s9"
