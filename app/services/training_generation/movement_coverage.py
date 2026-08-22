"""Movement-pattern coverage labels interpolated into the generation prompt.

These are **prompt-directive prose**, not catalog metadata. They are joined
into a human-readable sentence sent to the provider ("Movement coverage:
squat, hinge, ..."); they are never compared to a catalog value, never
resolved, and never persisted.

Do NOT import this where ``exercise_catalog.MOVEMENT_VOCABULARY`` is meant, or
vice versa. That vocabulary is the closed set of legal values for an
``ExerciseDefinition.movement`` field, enforced at catalog load time. The two
lists overlap on six strings and deliberately differ on two
(``core_anti_extension`` / ``core_anti_rotation`` here versus
``anti_extension`` / ``anti_rotation`` in the catalog). Renaming either side to
match the other would change the text of the prompt the provider receives —
a generation-behaviour change, and not what a vocabulary rename is for.

Split out of the deleted ``exercise_knowledge_base.py`` in Sprint 11 PR4. That
module also held an unwired ``EXERCISE_KB`` table of risk/difficulty/
progression opinions; it was a second, unreviewed exercise authority beside the
catalog and was removed rather than migrated.
"""

REQUIRED_MOVEMENT_COVERAGE = [
    "squat",
    "hinge",
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "core_anti_extension",
    "core_anti_rotation",
]
