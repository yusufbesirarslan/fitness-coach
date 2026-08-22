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

    What "matches" means depends on the plan. On a CANONICAL plan (one carrying
    the verified ``exercise_context`` the save boundary persists) identity is
    the catalog's ``exercise_id``: the named exercise is resolved to a catalog
    entry and the day is searched for that stable ID, so two differently-worded
    entries that resolve to the same entry are the same exercise twice. On a
    LEGACY name-only plan identity is still the name (``isim``) alone — those
    documents predate exercise IDs and are never silently upgraded.

    The behaviour itself is unchanged either way: rather than silently picking
    an occurrence the caller did not identify, an ambiguous target is refused
    and the plan is left exactly as it was.
    """


class InvalidMutation(PlanMutationError):
    """The command is malformed, empty, or structurally impossible to apply."""


class InvalidPrescription(PlanMutationError):
    """A prescription value falls outside canonical training bounds."""


class IdempotencyConflict(PlanMutationError):
    """The operation key is already bound to a *different* semantic operation.

    Deterministic and fail-closed: the caller's command is not applied, because
    letting a key silently change meaning is how a retry turns into a second,
    different mutation. Reusing a key for the same command replays instead
    (Sprint 1 PR2, brief §17).
    """


class UndoUnavailable(PlanMutationError):
    """There is no reversible mutation to undo on the current plan lineage.

    Nothing has been mutated yet, everything already reverted, or the only
    history belongs to a plan lineage that was replaced. Distinct from
    ``UndoConflict``: here the *selection* fails, not a precondition.
    """


class UndoConflict(PlanMutationError):
    """Current plan state is not the state the target mutation produced.

    Something changed the plan outside the reversal path — an out-of-band write,
    or a regeneration. Undo fails closed and overwrites nothing rather than
    restoring a snapshot on top of state it does not explain (brief §§27, 32).
    """


class PlanStateConflict(PlanMutationError):
    """A persisted plan-state precondition failed while committing.

    The authoritative row moved between the locked read and the write. The
    transaction is rolled back whole; no partial mutation survives.
    """
