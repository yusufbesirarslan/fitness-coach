"""NUTR-PR7 — the native (Bearer) Nutrition contract over EXISTING authorities.

Not a second Nutrition backend. Every module here is an adapter:

    tokens.py         owner-bound, domain-separated opaque tokens (pure)
    preconditions.py  If-Match / If-None-Match parsing (diary dialect)
    errors.py         the closed native error vocabulary
    plan.py           NutritionPlan read / generation / save / planned-meal log
    hydration.py      WaterLog absolute desired-state contract
    history.py        bounded MealLog history pages
    supplements.py    Supplement cabinet list / create / update / delete

The authorities they adapt — ``nutrition_day_view``, ``nutrition_plan_store``,
``nutrition_plan_generation``, ``nutrition_plan_schema``, ``plan_score``,
``hydration``, ``supplement_cabinet``, ``meal_idempotency`` and the
``mobile_nutrition`` ledger projection — are shared with the browser routes.

Contract: docs/NUTRITION_VNEXT_PR7.md
"""
