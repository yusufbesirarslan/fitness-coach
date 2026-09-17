"""The one place a client-supplied plan ``score`` becomes a persistable float.

Both plan-save routes write ``score`` into a ``db.Float`` column. Before this
boundary existed they wrote whatever arrived on the wire, so ``"abc"`` survived
the route untouched and failed at the flush — an unhandled ``DataError`` that
Flask rendered as an HTML 500 to a caller that only ever parses JSON.

The accepted range is ``[0, 10]`` because that is what the application itself
produces, on both sides and nowhere else:

* nutrition — ``avg_score`` averages three 0-10 food sub-scores and returns 0
  when nothing resolves (``app/blueprints/nutrition/plan.py``);
* training — the generator averages ``yogunluk``/``denge``/``uygunluk``, each
  bounded to ``SCORE_MIN..SCORE_MAX`` = 1..10 by the plan schema
  (``app/services/training_generation/plan_schema.py``).

A score is a rating, never a measurement, so there is no coercion here beyond
``int`` → ``float``: strings are refused rather than parsed. ``None`` stays
``None`` — the column is nullable and an omitted score always meant "unrated".
"""
from __future__ import annotations

import math

SCORE_MIN = 0.0
SCORE_MAX = 10.0

CODE_SCORE_INVALID = "plan_score_invalid"
I18N_SCORE_INVALID = "route.plan_score_invalid"


class PlanScoreInvalid(ValueError):
    """A client sent something that is not a score. Deterministic 400 JSON.

    One code for every way to fail on purpose: which way it failed is the
    caller's own payload, and spelling it back adds nothing but a probe.
    """

    public_code = CODE_SCORE_INVALID
    i18n_key = I18N_SCORE_INVALID
    http_status = 400
    retryable = False

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def to_body(self, translate) -> dict:
        return {
            "error": translate(self.i18n_key),
            "code": self.public_code,
            "retryable": self.retryable,
        }


def parse_plan_score(value):
    """``value`` as a finite float in ``[0, 10]``, or ``None``.

    Raises ``PlanScoreInvalid`` for anything else — strings (numeric or not),
    booleans, NaN, ±Infinity, out-of-range numbers and non-scalars alike.
    """
    if value is None:
        return None
    # bool is an int subclass; True is not a 1-out-of-10 rating.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanScoreInvalid("not a number")
    numeric = float(value)
    # json.loads accepts the bare NaN/Infinity literals, so a request really can
    # deliver a non-finite float here.
    if not math.isfinite(numeric):
        raise PlanScoreInvalid("not finite")
    if not SCORE_MIN <= numeric <= SCORE_MAX:
        raise PlanScoreInvalid("out of range")
    return numeric
