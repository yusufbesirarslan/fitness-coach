"""Native Supplements contract over the ONE cabinet (``Supplement``).

Persistence and side effects are ``supplement_cabinet``'s (shared with the
browser routes). This module adds only the native contract:

* identity  — owner-bound digest over (owner, row id), resolved by scanning the
              owner's OWN ids, so a token of user A never addresses user B and
              unknown/forged/cross-user tokens are one private not-found;
* revision  — owner-bound digest over every mutable column + identity;
              ``If-Match`` on update/delete, compared under the row lock;
* cabinet revision — owner-bound digest over the owner's id set. It is the
              CREATE precondition (``If-Match`` on the collection): every
              committed create (and delete) changes the id set, so a duplicated
              tap, a blind resend after a lost response, or a second device that
              read the same cabinet is refused (412) instead of creating a second
              row. No schema is needed: ids are never reused on PostgreSQL.

Wire vocabularies are stable tokens; the stored English display strings never
cross the boundary. A stored value outside the canonical list is ``unknown``,
never coerced.
"""
import math
import re
from datetime import timezone

from app.models import Supplement
from app.services import supplement_cabinet as cabinet

from . import errors, tokens


CONTRACT_VERSION = 1
CABINET_MAX = 200
NAME_MAX = 150
BRAND_MAX = 100
REVIEW_MAX = 2000
PRICE_MAX = 100_000
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NAME_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

CATEGORY_TOKENS = {
    "Protein": "protein", "Amino Acid": "amino_acid", "Pre-Workout": "pre_workout",
    "Vitamin/Health": "vitamin_health", "Creatine": "creatine", "Other": "other",
}
STATUS_TOKENS = {"Active": "active", "Low Stock": "low_stock", "Finished": "finished"}
CATEGORY_BY_TOKEN = {token: label for label, token in CATEGORY_TOKENS.items()}
STATUS_BY_TOKEN = {token: label for label, token in STATUS_TOKENS.items()}
RATING_FIELDS = (("effect", "rating_effect"), ("taste", "rating_taste"),
                 ("digestion", "rating_digestion"), ("price", "rating_price"))
WIRE_FIELDS = frozenset({"product_name", "brand", "category", "status",
                         "ratings", "review_text", "price_paid", "is_public"})
CREATE_REQUIRED = frozenset({"product_name", "brand", "category", "is_public"})


# ── identities ─────────────────────────────────────────────────────────────


def supplement_id(secret, user_id, row_id):
    return tokens.digest_token(secret, tokens.SUPPLEMENT_ID,
                               tokens.integer(user_id), tokens.integer(row_id))


def supplement_revision(secret, row):
    parts = [tokens.integer(row.user_id), tokens.integer(row.id)]
    for field in cabinet.MUTABLE_FIELDS:
        value = getattr(row, field)
        if field == "price_paid":
            parts.append(tokens.number(value))
        elif field == "is_public":
            parts.append(tokens.text(None if value is None else bool(value)))
        else:
            parts.append(tokens.text(value))
    parts.append(tokens.timestamp(row.created_at))
    return tokens.digest_token(secret, tokens.SUPPLEMENT_REVISION, *parts)


def cabinet_revision(secret, user_id, ids):
    return tokens.digest_token(
        secret, tokens.CABINET_REVISION, tokens.integer(user_id),
        tokens.integer(len(ids)), *(tokens.integer(i) for i in sorted(ids)))


# ── projection ─────────────────────────────────────────────────────────────


def _iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def project(row, secret):
    price = row.price_paid
    return {
        "id": supplement_id(secret, row.user_id, row.id),
        "revision": supplement_revision(secret, row),
        "product_name": row.product_name,
        "brand": row.brand,
        "category": CATEGORY_TOKENS.get(row.category, "unknown"),
        "status": STATUS_TOKENS.get(row.status, "unknown"),
        "ratings": {wire: getattr(row, column) for wire, column in RATING_FIELDS},
        "review_text": row.review_text,
        "price_paid": (float(price) if isinstance(price, (int, float))
                       and not isinstance(price, bool) and math.isfinite(price)
                       else None),
        "is_public": bool(row.is_public) if row.is_public is not None else None,
        "created_at": _iso(row.created_at),
    }


def read_cabinet(user_id, secret):
    """The owner's cabinet: one bounded SELECT. Raises on failure (→ 503)."""
    rows = (Supplement.query.filter_by(user_id=user_id)
            .order_by(Supplement.created_at.desc(), Supplement.id.desc())
            .limit(CABINET_MAX + 1).all())
    truncated = len(rows) > CABINET_MAX
    rows = rows[:CABINET_MAX]
    ids = [row.id for row in rows]
    if truncated:
        ids = cabinet.owner_ids(user_id)
    return {"contract_version": CONTRACT_VERSION, "cabinet": {
        "state": "available" if rows else "empty",
        "revision": cabinet_revision(secret, user_id, ids),
        "truncated": truncated,
        "supplements": [project(row, secret) for row in rows],
    }}


# ── parsing ────────────────────────────────────────────────────────────────


def _text(value, maximum, required, control=_NAME_CONTROL):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise errors.InvalidSupplement
    value = value.strip()
    if not value:
        if required:
            raise errors.InvalidSupplement
        return None
    if len(value) > maximum or control.search(value):
        raise errors.InvalidSupplement
    return value


def _ratings(value):
    if not isinstance(value, dict) or not set(value) <= {w for w, _ in RATING_FIELDS}:
        raise errors.InvalidSupplement
    parsed = {}
    for wire, column in RATING_FIELDS:
        if wire not in value:
            continue
        rating = value[wire]
        if rating is not None and (isinstance(rating, bool)
                                   or not isinstance(rating, int)
                                   or not 1 <= rating <= 5):
            raise errors.InvalidSupplement
        parsed[column] = rating
    return parsed


def _price(value):
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= PRICE_MAX):
        raise errors.InvalidSupplement
    return float(value)


def parse_fields(data, creating):
    """Closed key set → validated column values. Unknown keys are refused."""
    if not isinstance(data, dict) or not data or set(data) - WIRE_FIELDS:
        raise errors.InvalidSupplement
    if creating and not CREATE_REQUIRED <= set(data):
        raise errors.InvalidSupplement
    fields = {}
    if "product_name" in data:
        fields["product_name"] = _text(data["product_name"], NAME_MAX, True)
    if "brand" in data:
        fields["brand"] = _text(data["brand"], BRAND_MAX, True)
    if "category" in data:
        if data["category"] not in CATEGORY_BY_TOKEN:
            raise errors.InvalidSupplement
        fields["category"] = CATEGORY_BY_TOKEN[data["category"]]
    if "status" in data:
        if data["status"] not in STATUS_BY_TOKEN:
            raise errors.InvalidSupplement
        fields["status"] = STATUS_BY_TOKEN[data["status"]]
    if "ratings" in data:
        fields.update(_ratings(data["ratings"]))
    if "review_text" in data:
        fields["review_text"] = _text(
            data["review_text"], REVIEW_MAX, False, control=_CONTROL)
    if "price_paid" in data:
        fields["price_paid"] = _price(data["price_paid"])
    if "is_public" in data:
        if not isinstance(data["is_public"], bool):
            raise errors.InvalidSupplement
        fields["is_public"] = data["is_public"]
    if creating:
        fields.setdefault("status", "Active")
        for _wire, column in RATING_FIELDS:
            fields.setdefault(column, None)
        fields.setdefault("review_text", None)
        fields.setdefault("price_paid", None)
    return fields


# ── writes ─────────────────────────────────────────────────────────────────


def _resolve(user_id, secret, token):
    """Owner-scoped token → row id, or ``SupplementNotFound`` (private)."""
    if not isinstance(token, str) or not 16 <= len(token) <= 64:
        raise errors.SupplementNotFound
    for row_id in cabinet.owner_ids(user_id):
        if tokens.matches(supplement_id(secret, user_id, row_id), token):
            return row_id
    raise errors.SupplementNotFound


def create(user_id, secret, expected_cabinet_revision, data):
    fields = parse_fields(data, creating=True)

    def check(ids):
        if not tokens.matches(cabinet_revision(secret, user_id, ids),
                              expected_cabinet_revision):
            raise errors.StaleCabinet
        if len(ids) >= CABINET_MAX:
            raise errors.CabinetFull

    row, _quest = cabinet.create_supplement(user_id, fields, check)
    ids = cabinet.owner_ids(user_id)
    return {"contract_version": CONTRACT_VERSION,
            "supplement": project(row, secret),
            "cabinet_revision": cabinet_revision(secret, user_id, ids)}


def update(user_id, secret, token, revision, data):
    changes = parse_fields(data, creating=False)
    row_id = _resolve(user_id, secret, token)

    def check(row):
        if not tokens.matches(supplement_revision(secret, row), revision):
            raise errors.StaleSupplement

    row = cabinet.update_supplement(user_id, row_id, changes, check)
    if row is None:
        raise errors.SupplementNotFound
    return {"contract_version": CONTRACT_VERSION,
            "supplement": project(row, secret)}


def delete(user_id, secret, token, revision):
    row_id = _resolve(user_id, secret, token)

    def check(row):
        if not tokens.matches(supplement_revision(secret, row), revision):
            raise errors.StaleSupplement

    if not cabinet.delete_supplement(user_id, row_id, check):
        raise errors.SupplementNotFound
