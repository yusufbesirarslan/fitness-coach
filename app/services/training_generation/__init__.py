"""Deterministic training-plan generation support.

Preference allow-lists and the capability matrix decide whether a request is
representable before any provider call. Provider text is untrusted: extraction,
structural validation, semantic validation, and at most one parse/truncation
repair decide whether a week becomes a canonical candidate. Once accepted,
exercise references are canonicalized against the server-owned exercise
catalog (exercise_resolution.py) — the sole exercise-identity authority —
before generation returns them; an unresolved, ambiguous, or
equipment-incompatible reference fails the whole attempt closed. Injury
warnings are a warn-only overlay applied after that canonical identity
exists; they never repair, reject, or substitute an exercise. Save
re-validates before persistence.

See docs/TRAINING_GENERATOR.md.
"""

