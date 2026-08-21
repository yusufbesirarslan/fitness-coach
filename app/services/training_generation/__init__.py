"""Deterministic training-plan generation support.

Preference allow-lists and the capability matrix decide whether a request is
representable before any provider call. Provider text is untrusted: extraction,
structural validation, semantic validation, and at most one parse/truncation
repair decide whether a week becomes a canonical candidate. Save re-validates
before persistence.

See docs/TRAINING_GENERATOR.md.
"""

