"""Deterministic training-plan generation support.

Preference allow-lists and the capability matrix decide whether a request is
representable before any provider call. The LLM only formats a supported week.
Classification, safety caps, recovery scaling, style rules, and response
validation also live here so they can be tested without AI.

See docs/TRAINING_GENERATOR.md.
"""

