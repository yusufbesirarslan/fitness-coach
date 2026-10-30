"""Conservative, durable resource reservations at the physical provider door.

No usage refund: an absent response, timeout, parse failure or lost commit ack
cannot prove that a provider did not bill. This is execution safety per logical
action, never an additional customer balance or a consent authority.
"""
import json
from types import MappingProxyType
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import AiUsageReservation as Hold
from app.services import ai_input_budget, ai_model_policy
from .errors import AllowanceError

VERSION = "aggregate-v1"
CATALOG = "standard-ephemeral-cache-2026-10-29-v1"
# Integers: USD nanodollars per token. No rounding or floating point admission.
PRICES = MappingProxyType({
    ("bedrock", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"): (3000, 15000),
    ("bedrock", "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"): (3300, 16500),
    ("openai", "gpt-4o-mini"): (150, 600),
    ("openai", "gpt-4o-mini-2024-07-18"): (150, 600),
    ("bedrock", "global.anthropic.claude-sonnet-5-5"): (2000, 10000),
    ("bedrock", "eu.anthropic.claude-sonnet-5-5"): (2200, 11000),
    ("bedrock", "global.anthropic.claude-haiku-5-5"): (100, 500),
    ("bedrock", "eu.anthropic.claude-haiku-5-5"): (110, 550),
})
# FINOPS-26 eu-central-1 price list (2026-10-29), 5-minute writes only.
# Nanodollars/token, rounded UP for the fractional Haiku EU write rate.
# Reads never receive an admission discount: the next request may be a miss.
CACHE_PRICES = MappingProxyType({
    ("bedrock", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"): (3750, 300),
    ("bedrock", "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"): (4125, 330),
    ("bedrock", "global.anthropic.claude-sonnet-5-5"): (2500, 100),
    ("bedrock", "eu.anthropic.claude-sonnet-5-5"): (2750, 110),
    ("bedrock", "global.anthropic.claude-haiku-5-5"): (125, 10),
    ("bedrock", "eu.anthropic.claude-haiku-5-5"): (138, 11),
})
# Engineering envelopes only. Customer weekly quantities are deliberately absent.
# All nested tools, fallback, repairs and transport retries share these vectors.
LIMITS = MappingProxyType({
    "coach": (256000, 7000, 8, 2, 873000000),
    "training_generation": (64000, 22000, 4, 0, 522000000),
    "nutrition_generation": (32000, 4000, 2, 0, 156000000),
    "pump_check": (24000, 2400, 2, 2, 108000000),
    "qr_menu": (208000, 34000, 16, 0, 1030800000),
})
COUNTERS = ("physical_input_reserved", "physical_output_reserved",
            "physical_images_reserved", "physical_cost_reserved")


def _unavailable():
    raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")


def snapshot(feature):
    """Freeze the active routing and exact price identities at dispatch commit."""
    cfg = ai_model_policy._cfg()
    ai_model_policy.validate_routing_config()
    routes = ([('bedrock', cfg.BEDROCK_SONNET55_MODEL), ('bedrock', cfg.BEDROCK_HAIKU55_MODEL)]
              if ai_model_policy.claude55() else
              [('bedrock', cfg.BEDROCK_MODEL), ('openai', cfg.OPENAI_MODEL)])
    if any(route not in PRICES for route in routes):
        _unavailable()
    inp, out, attempts, images, cost = LIMITS[feature]
    return json.dumps(dict(version=VERSION, catalog=CATALOG, feature=feature,
        routing=ai_model_policy.routing(), input=inp, output=out,
        attempts=attempts, images=images, cost=cost,
        routes=[[family, model, *PRICES[family, model]] for family, model in routes]),
        sort_keys=True, separators=(',', ':'))


def initialize(row):
    """Only on a fresh hold, inside its existing dispatch transaction."""
    if row.physical_budget is not None:
        _unavailable()
    row.physical_budget = snapshot(row.feature)
    row.physical_attempts = 0
    row.physical_attempt_limit = LIMITS[row.feature][2]
    for name in COUNTERS:
        setattr(row, name, 0)


def _nonstandard_pricing(value):
    # Cache controls are validated separately by location and exact shape.
    if isinstance(value, dict):
        return (any(k in value for k in ('service_tier', 'performanceConfig'))
                or str(value.get('type', '')).startswith(('web_search_', 'code_execution_'))
                or any(_nonstandard_pricing(v) for v in value.values()))
    return isinstance(value, (list, tuple)) and any(_nonstandard_pricing(v) for v in value)


def _cache_input_bound(payload, family):
    """Bound the union of cached prefixes, never sum overlapping checkpoints.

    Only the existing Coach tools/system surface is qualified. Messages,
    nested schema markers, explicit TTLs and automatic caching fail closed.
    The prefix includes the normal hidden-tool overhead; all later input is
    still charged normally. Short prefixes ignored by Bedrock are also safe.
    """
    prefix = {}
    allowed = set()
    tools = payload.get('tools', [])
    system = payload.get('system', [])
    for key, blocks in (('tools', tools), ('system', system)):
        if not isinstance(blocks, (list, tuple)):
            continue
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or 'cache_control' not in block:
                continue
            if (block['cache_control'] != {'type': 'ephemeral'}
                    or (key == 'system' and (block.get('type') != 'text'
                                             or not isinstance(block.get('text'), str)))
                    or (key == 'tools' and (block.get('type') not in (None, 'custom')
                                            or not isinstance(block.get('input_schema'), dict)))):
                _unavailable()
            allowed.add((key, index))
            prefix[key] = blocks[:index + 1]
            if key == 'system' and tools:
                prefix['tools'] = tools

    def validate(value, path=()):
        if isinstance(value, dict):
            if 'cache_control' in value and path not in allowed:
                _unavailable()
            for key, child in value.items():
                validate(child, (*path, key))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                validate(child, (*path, index))
    validate(payload)
    if not allowed:
        return 0
    if len(allowed) > 4 or (family, payload.get('model')) not in CACHE_PRICES:
        _unavailable()
    return ai_input_budget.input_upper_bound(prefix)[0]


def reserve(owner, reference, feature, provider, payload, *, operation_id=None, now=None):
    """Debit a frozen request before SDK entry in one short row transaction.

    D1 already owns User on its separate connection. Never acquire that lock
    here. PostgreSQL conditional UPDATE arbitrates workers and process restarts.
    Native marker and debit commit together; original operation stays authority.
    """
    now = now or datetime.now(timezone.utc)
    family = 'bedrock' if provider.startswith('bedrock') else provider
    try:
        if _nonstandard_pricing(payload):
            _unavailable()
        cached = _cache_input_bound(payload, family)
        inp, images = ai_input_budget.input_upper_bound(payload)
        if 'claude-haiku-5-5' in str(payload.get('model', '')) and inp > 100000:
            _unavailable()  # long-context tariff is outside this price authority
        out = payload.get('max_tokens')
        if type(out) is not int or out <= 0:
            _unavailable()
        raw = db.session.execute(sa.select(Hold.physical_budget).where(
            Hold.user_id == owner, Hold.public_id == reference)).scalar_one_or_none()
        budget = json.loads(raw) if raw else None
        if (not budget or budget['version'] != VERSION or budget['catalog'] != CATALOG
                or budget['routing'] != ai_model_policy.routing()):
            _unavailable()
        from .execution import PHYSICAL_FEATURES
        if (feature not in PHYSICAL_FEATURES.get(budget['feature'], ())
                or tuple(budget[n] for n in ('input','output','attempts','images','cost'))
                    != LIMITS.get(budget['feature'])):
            _unavailable()
        rates = next((r[2:] for r in budget['routes']
                      if r[:2] == [family, payload.get('model')]), None)
        if rates is None or tuple(rates) != PRICES.get((family, payload.get('model'))):
            _unavailable()
        if budget['feature'] == 'pump_check' and images != 1:
            _unavailable()  # comparisons have no qualified single-image budget
        if feature == 'menu_ocr':
            _unavailable()  # dormant PDF/image OCR is not enabled by a budget
        # Keep the original dollar ceiling. The write surcharge may reach it
        # before the token ceiling; a cache hit or malformed usage cannot refill.
        surcharge = 0
        if cached:
            write, read = CACHE_PRICES[family, payload.get('model')]
            surcharge = min(inp, cached) * (max(rates[0], write, read) - rates[0])
        amounts = (inp, out, images, inp * rates[0] + out * rates[1] + surcharge)
        limits = (budget['input'], budget['output'], budget['images'], budget['cost'])
        predicates = [Hold.user_id == owner, Hold.public_id == reference,
            Hold.feature == budget['feature'],
            Hold.state == 'RESERVED', Hold.expires_at > now,
            Hold.physical_budget == raw, Hold.physical_attempts >= 0,
            Hold.physical_attempts < budget['attempts'],
            Hold.physical_attempt_limit == budget['attempts']]
        if operation_id is None:
            predicates.append(Hold.request_fingerprint.is_not(None))
        else:
            from app.models import TrainingPlanGenerationOperation as Op
            from app.models import TrainingPlanGenerationAllowance as Bind
            # Same lock order as the original observer: operation -> hold.
            valid = db.session.execute(sa.update(Op).where(
                Op.id == operation_id, Op.user_id == owner, Op.status == 'IN_PROGRESS',
                Op.allowance_metered.is_(True), Op.provider_dispatch_possible.is_(True),
                Op.provider_attempt_possible.is_not(None),
                sa.exists().where(Bind.operation_id == Op.id, Bind.user_id == owner,
                    Bind.reservation_ref == reference, Bind.state == 'RESERVED'),
            ).values(provider_attempt_possible=True, updated_at=Op.updated_at)
              .execution_options(synchronize_session=False)).rowcount
            if valid != 1:
                _unavailable()
            predicates.extend([Hold.feature == 'training_generation',
                               Hold.request_fingerprint.is_(None)])
        updates = {Hold.physical_attempts: Hold.physical_attempts + 1}
        for name, amount, limit in zip(COUNTERS, amounts, limits):
            column = getattr(Hold, name)
            predicates.extend([column >= 0, column + amount <= limit])
            updates[column] = column + amount
        changed = db.session.execute(sa.update(Hold).where(*predicates).values(updates)
            .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            # Missing/corrupt/terminal evidence also refuses; never refills.
            raise AllowanceError('AI_ACTION_BUDGET_EXHAUSTED')
        db.session.commit()
    except (AllowanceError, SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError) as error:
        db.session.rollback()
        if isinstance(error, AllowanceError):
            raise
        _unavailable()
