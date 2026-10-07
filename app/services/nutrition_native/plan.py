"""Native Nutrition Plan: read, generation, save/replacement, planned-meal logging.

Authorities (none of them new):

* saved plan        ``NutritionPlan`` via ``nutrition_plan_store`` — the same
                    newest-row selector and the same replacement boundary as
                    the browser's ``/nutrition-plan/active`` and ``/save``;
* document schema   ``nutrition_plan_schema.validate_nutrition_plan_for_save``;
* score             ``plan_score.parse_plan_score``;
* generation        ``nutrition_plan_generation`` (catalogue, rating, prompt,
                    parse) — the browser route runs the same code; the native
                    picker labels it per account language via
                    ``generation_labels`` and maps labels back to the
                    canonical names before generation;
* consumed food     ``MealLog`` via ``meal_idempotency.commit_once`` and the
                    ``mobile_log_food.response_meal`` projection.

What this module adds is the native CONTRACT: an owner-bound opaque plan
revision (``If-Match`` / ``If-None-Match: *`` on replacement), an owner-bound
planned-meal identity derived from (plan row, slot) — never from display text —
and an integrity-protected, owner-bound proposal token so a saved plan is
provably one the server generated for this owner, unchanged.

Transport-neutral: no Flask request/response. The caller passes the owner id
from the verified Bearer principal and ``SECRET_KEY``.
"""
import hashlib
import json
import math
import re
import time
from datetime import datetime

from app.extensions import db
from app.models import MealLog, UserSession
from app.services import meal_idempotency
from app.services import nutrition_plan_generation as generation
from app.services.mobile_log_food import response_meal
from app.services.nutrition_pipeline import clamp_serving_macros
from app.services.nutrition_plan_schema import (
    ITEMS_KEY,
    MEAL_KEYS,
    MEAL_MACRO_KEYS,
    NAME_KEY,
    NutritionPlanInvalid,
    validate_nutrition_plan_for_save,
)
from app.services.nutrition_plan_store import (
    newest_plan,
    newest_plan_query,
    replace_nutrition_plan,
)
from app.services.plan_owner_lock import lock_plan_owner
from app.services.plan_score import PlanScoreInvalid, parse_plan_score
from app.services.premium import (
    FREE_WEEKLY_AI_PLANS,
    refund_ai_quota,
    reserve_ai_quota,
)
from app.timeutil import day_key, to_app_tz

from . import errors, tokens
from . import generation_labels as food_labels
from .preconditions import Precondition


CONTRACT_VERSION = 1

ACTIVE = "active"
ABSENT = "absent"
INVALID = "invalid"
PLAN_STATES = (ACTIVE, ABSENT, INVALID)

# (wire, canonical) — the canonical keys stay Turkish in every locale.
NUTRIENTS = (("energy_kcal", "kalori"), ("protein_g", "protein"),
             ("carbohydrate_g", "karb"), ("fat_g", "yag"))
TOTALS = (("energy_kcal", "toplam_kalori"), ("protein_g", "toplam_protein"),
          ("carbohydrate_g", "toplam_karb"), ("fat_g", "toplam_yag"))

PROPOSAL_TTL_SECONDS = 24 * 60 * 60
PROPOSAL_CLOCK_SKEW_SECONDS = 300

MAX_FOODS_PER_GROUP = 10
MAX_CUSTOM_FOODS = 10
CUSTOM_FOOD_MAX = 60
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DIGEST = re.compile(r"[0-9a-f]{64}")


# ── identities ─────────────────────────────────────────────────────────────


def plan_revision(secret, row):
    """Opaque, owner-bound revision of ONE persisted plan row's exact state.

    Over (owner, row id, stored document text, score, saved instant). Every
    replacement inserts a new row, so a replacement always changes it — even
    one that saves byte-identical content.
    """
    return tokens.digest_token(
        secret, tokens.PLAN_REVISION,
        tokens.integer(row.user_id), tokens.integer(row.id),
        tokens.text(row.plan_data), tokens.number(row.score),
        tokens.timestamp(row.created_at))


def planned_meal_id(secret, user_id, row_id, slot):
    """Identity of one meal INSIDE one persisted plan row — never display text."""
    return tokens.digest_token(
        secret, tokens.PLANNED_MEAL_ID,
        tokens.integer(user_id), tokens.integer(row_id), tokens.text(slot))


# ── document projection ────────────────────────────────────────────────────


def _parsed_document(row):
    """The persisted document as the strict schema sees it, or ``None``."""
    try:
        document = json.loads(row.plan_data)
        return validate_nutrition_plan_for_save(document)
    except (TypeError, ValueError, NutritionPlanInvalid):
        return None


def _meal_payload(slot, meal):
    items = meal.get(ITEMS_KEY)
    return {
        "slot": slot,
        "items": list(items) if items is not None else None,
        "nutrition": {wire: meal.get(src) for wire, src in NUTRIENTS},
    }


def native_document(document):
    """Canonical (Turkish-keyed) plan document → the native wire document."""
    return {
        "name": document.get(NAME_KEY),
        "meals": [_meal_payload(slot, document[slot])
                  for slot in MEAL_KEYS if slot in document],
        "totals": {wire: document.get(src) for wire, src in TOTALS},
    }


def is_loggable(meal):
    """A planned meal can become consumed food only with every value present.

    Logging a meal whose macros are missing would write invented zeroes into
    the ledger (unknown != 0), so such a meal is shown but not loggable.
    """
    items = meal.get(ITEMS_KEY)
    return (isinstance(items, list) and len(items) > 0
            and all(meal.get(key) is not None for key in MEAL_MACRO_KEYS))


def _absent_payload():
    return {"state": ABSENT, "revision": None, "saved_at": None,
            "food_rating": None, "name": None, "meals": None, "totals": None}


def project_plan_row(row, secret):
    """The native plan section for one persisted row (active or invalid)."""
    saved = to_app_tz(row.created_at)
    base = {"revision": plan_revision(secret, row),
            "saved_at": saved.isoformat() if saved is not None else None}
    document = _parsed_document(row)
    if document is None:
        return dict(base, state=INVALID, food_rating=None, name=None,
                    meals=None, totals=None)
    projected = native_document(document)
    for meal in projected["meals"]:
        canonical = document[meal["slot"]]
        meal["id"] = planned_meal_id(secret, row.user_id, row.id, meal["slot"])
        meal["loggable"] = is_loggable(canonical)
    score = row.score
    food_rating = (float(score) if isinstance(score, (int, float))
                   and not isinstance(score, bool) and math.isfinite(score)
                   else None)
    return dict(base, state=ACTIVE, food_rating=food_rating, **projected)


def _catalogue_groups():
    """Canonical (Turkish) food names per picker group, in catalogue order."""
    db_ = generation.FOOD_DATABASE
    return {
        "proteins": [f["isim"] for group in db_["protein"].values() for f in group],
        "carbs": [f["isim"] for f in db_["karbonhidrat"]],
        "fats": [f["isim"] for f in db_["yag"]],
    }


def generation_options(language):
    """The fixed generation catalogue, labelled in the account's ``language``.

    ``language`` is the authenticated owner's stored ``User.language`` — the
    value the generator's prompt is built from — never a request field. The
    labels are display text only; ``parse_generation_request`` maps them back
    to the canonical names before anything else sees them.
    """
    options = {group: [food_labels.display_label(name, language) for name in names]
               for group, names in _catalogue_groups().items()}
    options["max_per_group"] = MAX_FOODS_PER_GROUP
    options["max_custom_foods"] = MAX_CUSTOM_FOODS
    return options


def read_plan(user_id, secret, language):
    """The owner's canonical saved plan. Raises on storage failure (→ 503)."""
    row = newest_plan(user_id)
    plan = _absent_payload() if row is None else project_plan_row(row, secret)
    return {"contract_version": CONTRACT_VERSION, "plan": plan,
            "generation_options": generation_options(language)}


# ── native document parsing (save) ─────────────────────────────────────────


def _native_number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise errors.InvalidPlan
    return value


def _nutrition_block(raw):
    if not isinstance(raw, dict) or set(raw) != {wire for wire, _ in NUTRIENTS}:
        raise errors.InvalidPlan
    return raw


def canonical_from_native(data):
    """Strict native plan document → the canonical document, VALIDATED.

    Closed key sets everywhere; ``null`` means "absent" and maps to an absent
    canonical key, so the projection round-trips exactly. The canonical schema
    validator then runs exactly as it does for the browser save.
    """
    if not isinstance(data, dict) or set(data) != {"name", "meals", "totals"}:
        raise errors.InvalidPlan
    document = {}
    name = data["name"]
    if name is not None:
        if not isinstance(name, str):
            raise errors.InvalidPlan
        document[NAME_KEY] = name
    meals = data["meals"]
    if not isinstance(meals, list) or not 1 <= len(meals) <= len(MEAL_KEYS):
        raise errors.InvalidPlan
    for meal in meals:
        if not isinstance(meal, dict) or set(meal) != {"slot", "items", "nutrition"}:
            raise errors.InvalidPlan
        slot = meal["slot"]
        if slot not in MEAL_KEYS or slot in document:
            raise errors.InvalidPlan
        canonical = {}
        if meal["items"] is not None:
            canonical[ITEMS_KEY] = meal["items"]
        nutrition = _nutrition_block(meal["nutrition"])
        for wire, src in NUTRIENTS:
            value = _native_number(nutrition[wire])
            if value is not None:
                canonical[src] = value
        document[slot] = canonical
    totals = data["totals"]
    if not isinstance(totals, dict) or set(totals) != {wire for wire, _ in TOTALS}:
        raise errors.InvalidPlan
    for wire, src in TOTALS:
        value = _native_number(totals[wire])
        if value is not None:
            document[src] = value
    try:
        return validate_nutrition_plan_for_save(document)
    except NutritionPlanInvalid:
        raise errors.InvalidPlan from None


# ── proposal integrity ─────────────────────────────────────────────────────


def _digest_form(value):
    if isinstance(value, dict):
        return {key: _digest_form(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_digest_form(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def document_digest(document):
    """Representation-independent digest of a canonical document.

    ``420`` and ``420.0`` are the same number to every JSON client, so they
    must be the same proposal too.
    """
    encoded = json.dumps(_digest_form(document), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def issue_proposal_token(secret, user_id, document, score, now=None):
    issued = int(time.time() if now is None else now)
    return tokens.sign_payload(secret, tokens.PLAN_PROPOSAL, user_id, {
        "d": document_digest(document), "s": score, "t": issued})


def verify_proposal_token(secret, user_id, token, now=None):
    """``(digest, score)`` of a genuine, unexpired proposal of THIS owner."""
    try:
        payload = tokens.verify_payload(
            secret, tokens.PLAN_PROPOSAL, user_id, token)
    except tokens.InvalidSignedToken:
        raise errors.InvalidProposal from None
    if set(payload) != {"d", "s", "t"}:
        raise errors.InvalidProposal
    digest, score, issued = payload["d"], payload["s"], payload["t"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise errors.InvalidProposal
    if isinstance(issued, bool) or not isinstance(issued, int):
        raise errors.InvalidProposal
    current = time.time() if now is None else now
    if issued > current + PROPOSAL_CLOCK_SKEW_SECONDS:
        raise errors.InvalidProposal
    if current - issued > PROPOSAL_TTL_SECONDS:
        raise errors.ProposalExpired
    return digest, score


# ── generation ─────────────────────────────────────────────────────────────


def _food_group(value, allowed):
    """Known catalogue labels of ONE group → their canonical names, in order.

    A label of ANY supported locale is accepted (a picker loaded before the
    account language changed still submits known foods); an unknown string, a
    food of another group, or two labels naming the same food are refused.
    """
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_FOODS_PER_GROUP:
        raise errors.InvalidGenerationRequest
    names = []
    for item in value:
        name = food_labels.canonical_food(item)
        if name is None or name not in allowed:
            raise errors.InvalidGenerationRequest
        names.append(name)
    if len(set(names)) != len(names):
        raise errors.InvalidGenerationRequest
    return names


def parse_generation_request(data):
    """Closed body: catalogue labels only, plus a bounded custom-food list.

    The returned ``proteins``/``carbs``/``fats`` are CANONICAL food names —
    rating, prompt and every later rule never see a display label.
    """
    if not isinstance(data, dict):
        raise errors.InvalidGenerationRequest
    allowed_keys = {"proteins", "carbs", "fats", "custom_foods"}
    if set(data) - allowed_keys or not {"proteins", "carbs", "fats"} <= set(data):
        raise errors.InvalidGenerationRequest
    groups = _catalogue_groups()
    request = {group: _food_group(data[group], set(groups[group]))
               for group in ("proteins", "carbs", "fats")}
    custom = data.get("custom_foods", [])
    if not isinstance(custom, list) or len(custom) > MAX_CUSTOM_FOODS:
        raise errors.InvalidGenerationRequest
    cleaned = []
    for item in custom:
        if not isinstance(item, str):
            raise errors.InvalidGenerationRequest
        item = item.strip()
        if not 1 <= len(item) <= CUSTOM_FOOD_MAX or _CONTROL.search(item):
            raise errors.InvalidGenerationRequest
        cleaned.append(item)
    request["custom_foods"] = cleaned
    return request


def _generation_target(user_id):
    """The newest ``UserSession`` target — the browser generator's selector."""
    row = (UserSession.query.filter_by(user_id=user_id)
           .order_by(UserSession.created_at.desc(), UserSession.id.desc())
           .with_entities(UserSession.target_calories, UserSession.goal).first())
    if row is None:
        raise errors.TargetRequired
    target, goal = row
    if (target is None or isinstance(target, bool)
            or not isinstance(target, (int, float))
            or not math.isfinite(target) or target <= 0):
        raise errors.TargetRequired
    return target, goal


def generate_proposals(user, secret, request, chat_fn, quota_enabled, logger=None):
    """One explicit generation → validated PROPOSALS. Never writes a plan.

    Gates in the browser route's order: target, weekly ``nutrition`` allowance
    (the SAME premium bucket), provider. Any provider/parse failure refunds the
    allowance and raises ``GenerationFailed`` — a proposal is never invented.
    """
    target, goal = _generation_target(user.id)
    # Close the read transaction before the quota write and the provider call.
    db.session.rollback()
    if quota_enabled and not reserve_ai_quota(user, "nutrition", FREE_WEEKLY_AI_PLANS):
        raise errors.GenerationQuotaExceeded
    try:
        options = generation.generate_plan_options(
            chat_fn, user.language or "tr", target, goal, request["proteins"],
            request["carbs"], request["fats"], request["custom_foods"])
        documents = []
        if isinstance(options, list):
            for option in options:
                try:
                    documents.append(validate_nutrition_plan_for_save(option))
                except NutritionPlanInvalid:
                    continue
        if not documents:
            raise errors.GenerationFailed
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        if quota_enabled:
            try:
                refund_ai_quota(user, "nutrition")
            except Exception:
                db.session.rollback()
        if logger is not None:
            logger.error(
                "nutrition_native event=plan_generation_failed error_type=%s",
                type(error).__name__)
        raise errors.GenerationFailed from None
    rating = generation.food_rating(
        request["proteins"] + request["carbs"] + request["fats"])
    issued = time.time()
    return {
        "contract_version": CONTRACT_VERSION,
        "target": {"value": round(target), "unit": "kcal"},
        "food_rating": rating,
        "proposals": [
            {"plan": native_document(document),
             "proposal_token": issue_proposal_token(
                 secret, user.id, document, rating, now=issued)}
            for document in documents
        ],
    }


# ── save / replacement ─────────────────────────────────────────────────────


def _replacement_check(secret, precondition):
    def check(current):
        if precondition.is_create:
            if current is not None:
                raise errors.StalePlan
            return
        if current is None or not tokens.matches(
                plan_revision(secret, current), precondition.revision):
            raise errors.StalePlan
    return check


def save_plan(user_id, secret, precondition, body, now=None):
    """validate proposal+score → validate schema → precondition → replace.

    Nothing is deleted before every validation has passed; the precondition is
    compared under the owner + plan locks inside the shared boundary.
    """
    if not isinstance(precondition, Precondition):
        raise TypeError("precondition must be a Precondition")
    if not isinstance(body, dict) or set(body) != {"plan", "proposal_token"}:
        raise errors.InvalidPlan
    digest, score = verify_proposal_token(
        secret, user_id, body["proposal_token"], now=now)
    try:
        score = parse_plan_score(score)
    except PlanScoreInvalid:
        raise errors.InvalidProposal from None
    document = canonical_from_native(body["plan"])
    if document_digest(document) != digest:
        raise errors.InvalidProposal
    row = replace_nutrition_plan(
        user_id, document, score, _replacement_check(secret, precondition))
    return {"contract_version": CONTRACT_VERSION,
            "plan": project_plan_row(row, secret)}


# ── planned meal → consumed food ───────────────────────────────────────────


def planned_meal_fingerprint(plan_revision_token, meal_token):
    canonical = json.dumps({
        "domain": tokens.PLANNED_MEAL_COMMAND,
        "plan_revision": plan_revision_token,
        "planned_meal": meal_token,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def planned_meal_values(meal):
    """(description, kcal, protein, carbs, fat) for a LOGGABLE planned meal.

    The plan's own values, rounded to 0.1 and passed through the canonical
    serving clamp every ledger writer uses (``clamp_serving_macros``).
    """
    description = ", ".join(meal[ITEMS_KEY])
    rounded = [round(float(meal[key]), 1) for key in MEAL_MACRO_KEYS]
    return (description,) + tuple(clamp_serving_macros(*rounded))


_SLOT_LABELS = {"kahvalti": "Kahvaltı", "ogle": "Öğle", "aksam": "Akşam",
                "ara_ogun": "Ara Öğün"}


def log_planned_meal(user_id, secret, revision, meal_token, key):
    """Log ONE planned meal of ONE plan revision as consumed food.

    Returns ``(meal_payload, created)``. Replay is decided by the durable
    (owner, Idempotency-Key) row and its semantic fingerprint: the same key and
    the same (plan revision, planned meal) → the confirmed original; the same
    key and anything else → ``IdempotencyConflict``. A different key is a new,
    intentional serving.
    """
    fingerprint = planned_meal_fingerprint(revision, meal_token)
    existing = meal_idempotency.find_existing(user_id, key)
    if existing is not None:
        if existing.idempotency_fingerprint != fingerprint:
            raise errors.IdempotencyConflict
        return response_meal(existing, secret, user_id), False
    try:
        lock_plan_owner(user_id)
        row = (newest_plan_query(user_id)
               .populate_existing().with_for_update().first())
        if row is None or not tokens.matches(plan_revision(secret, row), revision):
            raise errors.StalePlan
        document = _parsed_document(row)
        slot = None
        if document is not None:
            for candidate in MEAL_KEYS:
                if candidate in document and tokens.matches(
                        planned_meal_id(secret, user_id, row.id, candidate),
                        meal_token):
                    slot = candidate
        if slot is None:
            raise errors.PlannedMealNotFound
        meal = document[slot]
        if not is_loggable(meal):
            raise errors.PlannedMealNotLoggable
        description, kcal, protein, carbs, fat = planned_meal_values(meal)
    except Exception:
        db.session.rollback()
        raise
    entry = MealLog(
        user_id=user_id, ogun=_SLOT_LABELS[slot], yemekler=description,
        kalori=kcal, protein=protein, karb=carbs, yag=fat, tarih=day_key(),
        source="ai_plan", idempotency_fingerprint=fingerprint,
        created_at=datetime.utcnow())
    winner, created = meal_idempotency.commit_once(entry, key)
    if winner.idempotency_fingerprint != fingerprint:
        raise errors.IdempotencyConflict
    return response_meal(winner, secret, user_id), created
