"""Deterministic training-plan generation support.

Preference allow-lists and the capability matrix decide whether a request is
representable before any provider call. Provider text is untrusted: extraction,
structural validation, semantic validation, and at most one parse/truncation
repair decide whether a week becomes a canonical candidate. The provider
chooses each exercise as an ``exercise_id`` from one closed, request-scoped
choice set (exercise_choices.py); once accepted, every chosen ID is validated
against the server-owned exercise catalog and that same set
(exercise_resolution.py) — the sole exercise-identity authority — and the
catalog writes the display name. An unknown, malformed, retired or
incompatible ID fails the whole attempt closed. Injury
warnings are a warn-only overlay applied after that canonical identity
exists; they never repair, reject, or substitute an exercise. Save
re-validates before persistence.

See docs/TRAINING_GENERATOR.md.
"""

