"""Goal pace: the arithmetic behind the server-owned daily calorie target (PD1).

Pure. No database, Flask, request, clock, provider or logging. The one caller
is `account_profile`, which owns the profile, the goal vocabulary, the legacy
formula and persistence; this module only answers "what daily target does
this pace give this body, and is the pace suitable for it?".

Vocabulary
----------
Stable, locale-independent tokens. Presentation (the "A"/"B" letters, any
gram figure, any language) is the client's; nothing here is copy.

    lose_weight   lose_slower  target pace about 200 g/week  (A)
                  lose_faster  target pace about 500 g/week  (B)
    build_muscle  gain_lean    small, individual energy surplus   (A)
                  gain_steady  moderate, individual energy surplus (B)

A pace is a TARGET used to size an energy deficit or surplus, never a
promised outcome. No muscle-gain rate is modelled or published: the gain
options size a surplus relative to the person's own maintenance estimate.

Rules (all constants below, nothing implicit)
---------------------------------------------
* Weight loss: nominal deficit = pace kg/week x 7700 kcal/kg / 7 days. It is
  capped at `MAX_DEFICIT_FRACTION` of maintenance and the result never goes
  below the general self-guided minimum for the stated sex
  (`CALORIE_FLOOR_KCAL`; unknown sex takes the higher one). Either limit marks
  the result `bounded`.
* A loss pace is unsuitable — the target falls back to maintenance, with a
  closed reason — when the body-mass index is below `MIN_LOSS_BMI`, when a
  stated target weight is not below the current weight, or when the limits
  above leave less than `MIN_MEANINGFUL_DEFICIT_KCAL` of deficit.
* Muscle gain: surplus = a fraction of maintenance, clamped to a fixed band
  per option, so the two options stay distinct and individual.
* Every pace-derived target is rounded to `ROUND_TO_KCAL`, so every reader —
  Python or Dart, half-to-even or half-up — shows the same whole number.

These are conservative general-population bounds for a consumer fitness app,
not medical advice. They are deliberately not configurable at runtime.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

LOSE_SLOWER = "lose_slower"
LOSE_FASTER = "lose_faster"
GAIN_LEAN = "gain_lean"
GAIN_STEADY = "gain_steady"

LOSS_PACES = (LOSE_SLOWER, LOSE_FASTER)
GAIN_PACES = (GAIN_LEAN, GAIN_STEADY)
PACES = LOSS_PACES + GAIN_PACES

# The nominal weekly body-weight change a loss pace aims at, in grams. Only
# loss paces have one; it is a target, published so a client never invents it.
NOMINAL_WEEKLY_CHANGE_G = {LOSE_SLOWER: -200, LOSE_FASTER: -500}

KCAL_PER_KG = 7700.0
MAX_DEFICIT_FRACTION = 0.25
CALORIE_FLOOR_KCAL = {"male": 1500.0, "female": 1200.0}
MIN_LOSS_BMI = 18.5
MIN_MEANINGFUL_DEFICIT_KCAL = 100.0

# (fraction of maintenance, minimum kcal, maximum kcal)
GAIN_SURPLUS = {
    GAIN_LEAN: (0.05, 100.0, 250.0),
    GAIN_STEADY: (0.10, 200.0, 450.0),
}

ROUND_TO_KCAL = 10

BASIS_PACE = "pace"
BASIS_MAINTENANCE = "maintenance"

REASON_LOW_BMI = "low_bmi"
REASON_TARGET_WEIGHT_REACHED = "target_weight_reached"
REASON_CALORIE_FLOOR = "calorie_floor"


@dataclass(frozen=True)
class PaceOutcome:
    """The daily target one pace gives one body.

    `basis` is `pace` when the pace sized the target, `maintenance` when it
    was unsuitable and the target is the maintenance estimate (`reason`
    says why). `bounded` is true when a safety limit moved a pace target.
    """

    target_calories: float
    basis: str
    bounded: bool
    reason: Optional[str]


def round_kcal(value):
    """Half-up to the nearest `ROUND_TO_KCAL`, as a float."""
    return float(math.floor(value / ROUND_TO_KCAL + 0.5) * ROUND_TO_KCAL)


def _maintenance(tdee, reason):
    return PaceOutcome(round_kcal(tdee), BASIS_MAINTENANCE, False, reason)


def _bmi(weight, height):
    metres = height / 100.0
    return weight / (metres * metres)


def _floor(gender):
    return CALORIE_FLOOR_KCAL.get(gender, max(CALORIE_FLOOR_KCAL.values()))


def pace_target(pace, *, tdee, weight, height, gender, target_weight=None):
    """The `PaceOutcome` of `pace` for this body. `pace` must be in `PACES`.

    `tdee`, `weight` and `height` must be finite and positive — the caller
    only calls this for a complete, validated profile.
    """
    if pace not in PACES:
        raise ValueError("unknown goal pace")
    for value in (tdee, weight, height):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError("pace_target needs positive finite inputs")

    if pace in GAIN_PACES:
        fraction, low, high = GAIN_SURPLUS[pace]
        surplus = min(max(tdee * fraction, low), high)
        return PaceOutcome(round_kcal(tdee + surplus), BASIS_PACE, False, None)

    if _bmi(weight, height) < MIN_LOSS_BMI:
        return _maintenance(tdee, REASON_LOW_BMI)
    if target_weight is not None and target_weight >= weight:
        return _maintenance(tdee, REASON_TARGET_WEIGHT_REACHED)

    nominal = -NOMINAL_WEEKLY_CHANGE_G[pace] / 1000.0 * KCAL_PER_KG / 7.0
    deficit = min(nominal, tdee * MAX_DEFICIT_FRACTION)
    bounded = deficit < nominal
    target = tdee - deficit
    floor = _floor(gender)
    if target < floor:
        target, bounded = floor, True
    if tdee - target < MIN_MEANINGFUL_DEFICIT_KCAL:
        return _maintenance(tdee, REASON_CALORIE_FLOOR)
    return PaceOutcome(round_kcal(target), BASIS_PACE, bounded, None)
