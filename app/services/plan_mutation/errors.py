"""Domain outcomes for the canonical training-plan mutation boundary.

Deliberately *internal*. PR1 has no public endpoint and no AI tool, so these are
not API error codes and must not be published as one yet (brief §21). They exist
so a future transport/tool layer can map a failure onto a stable outcome without
re-deriving it from a message string.

Nothing here carries a SQL error, ORM internals, a stack trace, another user's
identifier, or a raw database primary key.
"""


class PlanMutationError(Exception):
    """Base class — one ``except`` clause covers the whole boundary."""


class PlanNotFound(PlanMutationError):
    """No canonical active plan exists for the authenticated user.

    Also raised when a plan exists but belongs to somebody else, so the two are
    indistinguishable to the caller and plan existence never leaks across users.
    """


class PlanNotMutable(PlanMutationError):
    """The active plan row exists but its ``plan_data`` is not a mutable plan.

    Unparseable JSON, or a shape that is not a program of days. PR1 refuses to
    repair or regenerate such a plan — that is the generator's job, not the
    mutation boundary's.
    """


class DayNotFound(PlanMutationError):
    """The command names a training day the plan does not contain."""


class ExerciseNotFound(PlanMutationError):
    """The command names an exercise the target day does not contain."""


class AmbiguousExerciseTarget(PlanMutationError):
    """The named exercise matches more than one entry in the target day.

    Exercise identity in the canonical plan is the name (``isim``) alone — there
    are no exercise IDs. Rather than silently picking an occurrence the caller
    did not identify, an ambiguous target is refused and the plan is unchanged.
    """


class InvalidMutation(PlanMutationError):
    """The command is malformed, empty, or structurally impossible to apply."""


class InvalidPrescription(PlanMutationError):
    """A prescription value falls outside canonical training bounds."""
