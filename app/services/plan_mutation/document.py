"""The PURE targeted-mutation engine (brief §10).

No ORM, no Flask, no database, no I/O — a plan document in, a plan document out.
That split is the same one ``workout_state/resolver.py`` and
``workout_session/models.py`` use, and it is what lets the whole mutation matrix
be exercised without a database.

How "targeted" is actually guaranteed
-------------------------------------
The canonical plan is one JSON text column, so *persisting* any change rewrites
the whole column. The invariant therefore has to hold one level up, in the
document: every operation here deep-copies the parsed document, reaches exactly
one node, and mutates that node in place. Untouched days and untouched exercises
are the *same objects* that were parsed, re-serialized in the same order with the
same keys — including keys this PR knows nothing about. Nothing is rebuilt from a
projection, which is what would silently drop an unknown field.

The plan is never regenerated, no field is recomputed, and derived plan-level
values (``haftalik_ozet`` and friends) are left exactly as the generator wrote
them: recomputing a weekly summary from a single exercise swap would be this
boundary inventing planning authority it does not have.

Two documents, one engine (Sprint 11 PR4)
-----------------------------------------
A plan saved through the Task 4 boundary carries the VERIFIED
``exercise_context`` the server accepted for it. When that block is present
this engine stops treating ``isim`` as identity: the command's target name is
resolved to a catalog entry and matched against the one slot holding that
stable ``exercise_id``, and an added or replacing exercise is written as
catalog identity (``exercise_id`` plus the canonical ``isim``) or not written
at all. Resolution is exact — canonical name or declared alias after
normalization — with no fuzzy matching and no automatic substitution, ever.

When the block is absent the document is a legacy, name-only plan and every
line below behaves exactly as it did before: casefold name matching, free-form
names written through, no ``exercise_id`` ever introduced. A legacy plan is
never silently upgraded, and — the other direction, which is the dangerous one
— a canonical plan whose context block is unreadable is never silently
downgraded: it is refused.

One identity, one slot, one day
-------------------------------
``add`` may not append an exercise the target day already holds. Whichever of
the two identity models is in force decides "already holds", which is why
there is exactly one predicate (``_identity_matches``) and both the target
lookup and the duplicate guard ask it — an alias must not be able to add what
a rename could not. The rule is DAY-local: the same exercise on two different
training days is an ordinary plan.
"""
import copy
from dataclasses import dataclass
import json

from app.services.exercise_catalog import (
    ExerciseCatalog,
    ExerciseContext,
    ExerciseDefinition,
    ExerciseResolutionError,
    is_exercise_compatible,
    load_exercise_catalog,
    resolve_exercise,
    with_owner_definitions,
)
from app.services.training_generation.exercise_resolution import (
    check_placement,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseIncompatibleError,
)

from app.services.training_generation.plan_schema import (
    CARDIO_SESSION_KEY,
    CARDIO_SESSION_PLACEMENT,
    NATIVE_MAX_EXERCISES_PER_DAY,
    SESSION_TIP,
)

from .commands import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)
from .errors import (
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseAlreadyPresent,
    ExerciseNotFound,
    InvalidMutation,
    InvalidPrescription,
    PlanNotMutable,
)
from .validation import (
    FIELD_EXERCISE_CONTEXT,
    REST_TIP,
    normalize_exercise_name,
    validate_exercise_context,
    validate_reps,
    validate_sets,
)


#: Canonical field names inside ``plan_data``. One definition, so a rename is a
#: single edit rather than a hunt through string literals.
FIELD_DAY = "gun"
FIELD_KIND = "tip"
FIELD_EXERCISES = "egzersizler"
FIELD_NAME = "isim"
FIELD_SETS = "set"
FIELD_REPS = "tekrar"
#: Catalog-owned stable identity, written by Task 3/4 and — from here on — by
#: this engine. Same literal as ``plan_schema.EXERCISE_ID_KEY``; named here
#: alongside the other document fields this module reads and writes.
FIELD_EXERCISE_ID = "exercise_id"


@dataclass(frozen=True)
class _ExerciseAuthority:
    """The catalog and the verified context, resolved once per command.

    Built at most once inside ``apply_command`` and threaded down, so a
    mutation performs exactly one catalog load no matter how many exercises
    it touches, and every resolution inside one command is answered by the
    same catalog object.
    """

    catalog: ExerciseCatalog
    context: ExerciseContext
    by_id: bool


def parse_plan_document(plan_text):
    """Parse stored ``plan_data`` into ``(document, program)``.

    Accepts both canonical shapes — a bare list of days, or an object with a
    ``program`` list — exactly as every existing reader does. Anything else
    raises ``PlanNotMutable``: PR1 refuses to guess at, repair, or regenerate a
    plan it cannot recognise.
    """
    try:
        document = json.loads(plan_text)
    except (TypeError, ValueError):
        raise PlanNotMutable("plan_data is not valid JSON")

    if isinstance(document, list):
        program = document
    elif isinstance(document, dict):
        program = document.get("program")
    else:
        raise PlanNotMutable("plan_data is not a plan document")

    if not isinstance(program, list) or not program:
        raise PlanNotMutable("plan_data has no program")
    if not all(isinstance(day, dict) for day in program):
        raise PlanNotMutable("plan_data has a malformed day")
    return document, program


def serialize_plan_document(document):
    """Serialize a mutated document with the repository's existing encoding.

    ``ensure_ascii=False`` matches the only pre-existing writer
    (``POST /training-plan/save``), so Turkish weekday and exercise names stay
    readable in storage instead of becoming escape sequences.
    """
    return json.dumps(document, ensure_ascii=False)


def _program_of(document):
    return document if isinstance(document, list) else document.get("program")


def _find_day(program, day_name):
    """The one day entry whose ``gun`` matches, or ``DayNotFound``."""
    if not isinstance(day_name, str) or not day_name.strip():
        raise InvalidMutation("day is required")
    wanted = day_name.strip()
    matches = [day for day in program if day.get(FIELD_DAY) == wanted]
    if not matches:
        raise DayNotFound("target day is not in the plan")
    if len(matches) > 1:
        # The generator guarantees unique weekdays; a duplicate means the stored
        # plan is not a plan this boundary can safely target.
        raise PlanNotMutable("plan has duplicate day identities")
    return matches[0]


def _exercises_of(day):
    """The day's exercise list, materializing an absent one.

    Materializing writes into ``day`` — which is safe because ``day`` always
    belongs to the deep copy, and because no path can both materialize the list
    and report "unchanged": the only operation that succeeds on an empty list is
    ``add`` (which then reports changed), while every other operation raises
    ``ExerciseNotFound`` and discards the copy.
    """
    exercises = day.get(FIELD_EXERCISES)
    if exercises is None:
        exercises = []
        day[FIELD_EXERCISES] = exercises
    if not isinstance(exercises, list):
        raise PlanNotMutable("day has a malformed exercise list")
    return exercises


def _document_carries_exercise_identity(document):
    """A pure structural walk: does any exercise anywhere carry an ``exercise_id``?

    No parsing assumptions beyond ``dict``/``list`` — a bare list, a dict
    without ``program``, a day that is not a dict, or a day without
    ``egzersizler`` must not raise here, they must simply contribute no IDs.
    This is a structural check only: it never loads the catalog and never
    resolves a name, so it costs nothing beyond the walk itself.
    """
    if isinstance(document, list):
        program = document
    elif isinstance(document, dict):
        program = document.get("program")
    else:
        return False
    if not isinstance(program, list):
        return False
    for day in program:
        if not isinstance(day, dict):
            continue
        exercises = day.get(FIELD_EXERCISES)
        if not isinstance(exercises, list):
            continue
        for entry in exercises:
            if isinstance(entry, dict) and FIELD_EXERCISE_ID in entry:
                return True
    return False


def _exercise_authority(document, *, by_id=False, private_definitions=None):
    """The catalog authority for this document, or ``None`` for a legacy plan.

    Present  — a dict document carrying an ``exercise_context`` block.
    Absent   — a bare-list legacy document, or a dict without that key —
               PROVIDED no exercise anywhere in it carries an ``exercise_id``.
               A document that bears catalog identity but no context to prove
               who authorized it is not legacy, it is unusable: see Unusable.
    Unusable — either the key is there but the block is not one the save
               boundary could have written, or the key is missing while some
               exercise still carries an ``exercise_id``. Both raise
               ``InvalidMutation``, never a quiet fall back to legacy name
               matching. Degrading silently would mean one corrupt byte in
               the context block — or one write that dropped the block
               entirely — is enough to take a canonical plan out from under
               the catalog's authority, reopening the exact defect (P1-4)
               this task closed: a stray ``exercise_id`` surviving a
               legacy-mode name rewrite it no longer matches.

    This is the only place ``load_exercise_catalog`` is called, and it runs
    once per command. The loader is ``lru_cache``d over a bundled data file,
    so it is not I/O in the sense this pure layer forbids — but calling it
    per exercise would still be wrong, and
    tests/test_plan_mutation_architecture.py pins the single call site.
    """
    if not isinstance(document, dict) or FIELD_EXERCISE_CONTEXT not in document:
        if by_id or _document_carries_exercise_identity(document):
            raise InvalidMutation("stored exercise context is not canonical")
        return None
    context = validate_exercise_context(document[FIELD_EXERCISE_CONTEXT])
    return _ExerciseAuthority(
        catalog=with_owner_definitions(load_exercise_catalog(), private_definitions),
        context=context, by_id=by_id)


def _resolve_catalog_exercise(authority, name) -> ExerciseDefinition:
    """Resolve one name to catalog identity, or refuse the whole mutation.

    Exact resolution only: an active entry's canonical name or one of its
    declared aliases, after normalization. No stemming, no token deletion, no
    nearest match — a name the catalog does not declare is simply not an
    exercise, and guessing which one was meant is exactly the substitution
    this PR forbids.

    Every ``ExerciseResolutionError`` becomes ``InvalidMutation`` here.
    ``errors.py`` is a published internal contract and gains no new class; a
    raw domain ``ValueError`` escaping this pure layer would reach the service
    with no handler and surface to the user as a 500.
    """
    try:
        identity = {"exercise_id": name} if authority.by_id else {"name": name}
        return resolve_exercise(catalog=authority.catalog, **identity)
    except ExerciseResolutionError as exc:
        raise InvalidMutation(
            "exercise is not canonical catalog identity") from exc


def _resolve_placeable_exercise(authority, name, day) -> ExerciseDefinition:
    """Resolve a name that is about to be WRITTEN into ``day``.

    Two gates, both the catalog's own and neither re-implemented here:

    * ``is_exercise_compatible`` against the plan's verified context, so the
      Coach cannot put a barbell into a bodyweight plan;
    * ``check_placement``, the generation path's cardio-vs-day rule, reused
      rather than copied. ``is_exercise_compatible`` deliberately gates cardio
      by ``cardio_type`` instead of by equipment (a home user who runs
      outdoors is a real case), which is only sound while a cardio movement
      can land on a ``kardiyo`` day and nowhere else. Task 4 closed that hole
      on the save door; leaving it open here would just move the same exploit
      to the Adaptive Coaching door and make PR4's central claim false.

    Importing the generation module's private helper is deliberate: the rule
    has to have exactly one definition, and Tasks 1-4 are accepted and not to
    be edited. A second copy of the predicate here is the failure mode worth
    avoiding — two doors drifting apart is how the first hole was opened.
    """
    exercise = _resolve_catalog_exercise(authority, name)
    if not is_exercise_compatible(exercise, authority.context):
        raise InvalidMutation(
            "exercise is not compatible with the plan's equipment context")
    try:
        check_placement(exercise, day, require_cardio_on_dedicated_day=authority.by_id)
    except GenerationExerciseIncompatibleError as exc:
        raise InvalidMutation(
            "exercise is incompatible with the day type") from exc
    return exercise


def _identity_matches(exercises, wanted, authority, resolved=None):
    """Every index in ``exercises`` holding the SAME exercise as ``wanted``.

    THE identity rule for this document, defined once. Both questions the
    engine asks — "which slot does this command target?" and "does this day
    already hold this exercise?" — are the same question about sameness, and a
    second implementation of it is how a guard and a lookup drift until an
    alias can add what a rename could not.

    On a LEGACY document, matching is case-insensitive and
    whitespace-insensitive because names are free text typed by humans and
    models, and the name is all the identity there is.

    On a CANONICAL document the name is resolved to a catalog entry first and
    slots are found by that entry's stable ``exercise_id``. Two entries worded
    differently that resolve to the same catalog entry are therefore the same
    exercise twice — casefold matching would have seen two unrelated names.
    There is no fall back to name matching when the ID does not appear: on a
    plan the catalog owns, an entry it does not own is not a match.

    ``resolved`` lets a caller that has ALREADY resolved this name pass the
    catalog entry in rather than resolve it a second time. It is only ever the
    resolution of ``wanted``; passing anything else would make the two
    arguments disagree about which exercise is meant.

    ``authority`` has no default here or on any ``_apply_*`` helper: a
    forgotten argument would silently pick LEGACY name matching on a
    canonical plan, which is the same downgrade ``_exercise_authority``
    refuses to perform on a broken context block.
    """
    if authority is None:
        folded = wanted.casefold()
        return [
            index
            for index, entry in enumerate(exercises)
            if isinstance(entry, dict)
            and str(entry.get(FIELD_NAME) or "").strip().casefold() == folded
        ]
    target = resolved if resolved is not None else _resolve_catalog_exercise(
        authority, wanted)
    return [
        index
        for index, entry in enumerate(exercises)
        if isinstance(entry, dict)
        and entry.get(FIELD_EXERCISE_ID) == target.exercise_id
    ]


def _find_exercise_index(exercises, exercise_name, authority,
                         match_sets=None, match_reps=None, *, position=None):
    """The single index matching ``exercise_name``.

    Native commands identify an occurrence by a revision-bound position and
    its expected ID. Name-based commands refuse two matches and a missing
    target raises ``ExerciseNotFound``. What counts as
    a match is ``_identity_matches``; the optional selectors only narrow those
    identity matches (``_select_by_prescription``) and never widen them.
    """
    wanted = normalize_exercise_name(exercise_name)
    if wanted is None:
        raise InvalidMutation("exercise name is required")
    if authority is not None and authority.by_id:
        # The service checked the exact plan bytes under lock before this
        # position could be used. Verify the ID at that position as well.
        if type(position) is not int or not 0 <= position < len(exercises):
            raise ExerciseNotFound("target position is not in the day")
        target = _resolve_catalog_exercise(authority, wanted)
        entry = exercises[position]
        if not isinstance(entry, dict) or entry.get(FIELD_EXERCISE_ID) != target.exercise_id:
            raise ExerciseNotFound("target exercise is not at that position")
        return position

    hits = _identity_matches(exercises, wanted, authority)
    hits = _select_by_prescription(exercises, hits, match_sets, match_reps)
    if not hits:
        raise ExerciseNotFound("target exercise is not in the day")
    if len(hits) > 1:
        raise AmbiguousExerciseTarget("target exercise matches more than once")
    return hits[0]


def _select_by_prescription(exercises, hits, match_sets, match_reps):
    """The identity matches whose stored prescription equals the selectors.

    Selectors are validated with the same bounds a written prescription gets,
    so a malformed one is refused rather than silently matching nothing — or,
    worse, being ignored and leaving the bare ambiguous target in force as if
    it had been honoured. Equality is the stored values compared as stored,
    the same ``==`` the duplicate-ADD guard uses: no rep parser, no "8-12"
    ≈ "8 to 12" equivalence. With no selector at all this is the identity.
    """
    if match_sets is None and match_reps is None:
        return hits
    sets = None if match_sets is None else validate_sets(match_sets)
    reps = None if match_reps is None else validate_reps(match_reps)
    return [
        index for index in hits
        if (sets is None or exercises[index].get(FIELD_SETS) == sets)
        and (reps is None or exercises[index].get(FIELD_REPS) == reps)
    ]


def remove_target_slots(document, day_name, exercise_name):
    """``(sets, reps)`` of every slot a remove of ``exercise_name`` could hit.

    Read-only, and answered by THE identity rule (``_identity_matches``) on
    the document's own authority, so the Coach can show the user the real
    candidates of an ambiguous remove without a second matcher that could
    disagree with the one the mutation will use. Returned in plan order,
    which is presentation only: a choice is always sent back as an exact
    selector, never as a position. Domain errors propagate unchanged.
    """
    program = _program_of(document)
    authority = _exercise_authority(document)
    day = _find_day(program, day_name)
    exercises = day.get(FIELD_EXERCISES)
    if not isinstance(exercises, list):
        return []
    wanted = normalize_exercise_name(exercise_name)
    if wanted is None:
        raise InvalidMutation("exercise name is required")
    return [
        (exercises[index].get(FIELD_SETS), exercises[index].get(FIELD_REPS))
        for index in _identity_matches(exercises, wanted, authority)
    ]


def _require_workout_day(day):
    """Refuse to put exercises on an explicit rest day."""
    if day.get(FIELD_KIND) == REST_TIP:
        raise InvalidMutation("cannot edit exercises on a rest day")


def _require_day_stays_valid(day):
    """A non-rest day must keep at least one exercise (canonical rule)."""
    if day.get(FIELD_KIND) == REST_TIP:
        return
    if not _exercises_of(day):
        raise InvalidMutation("a training day must keep at least one exercise")


def _misplaced_entries(day, authority, *, strict):
    """How many entries on ``day`` the canonical placement rule refuses.

    ``strict`` resolution refuses the mutation on any entry it cannot
    resolve. The non-strict count, taken before an edit, skips such entries,
    which can only lower the baseline and so only make the later check
    stricter.
    """
    misplaced = 0
    for entry in _exercises_of(day):
        if not isinstance(entry, dict):
            if strict:
                raise PlanNotMutable("day has a malformed exercise")
            continue
        try:
            exercise = _resolve_catalog_exercise(authority, entry.get(FIELD_EXERCISE_ID))
        except InvalidMutation:
            if strict:
                raise
            continue
        try:
            check_placement(exercise, day)
        except GenerationExerciseIncompatibleError:
            misplaced += 1
    return misplaced


def _native_placement_baseline(day, authority):
    """Misplaced entries on ``day`` as read, before a native edit touches it."""
    if authority is None or not authority.by_id:
        return 0
    return _misplaced_entries(day, authority, strict=False)


def _require_native_cardio_placement(day, authority, before):
    """A native edit may never ADD misplaced cardio.

    Cardio belongs on a ``kardiyo`` day or in a strength day's separate
    cardio session (G4), never in the strength list. Every written entry is
    already placement-checked (``_resolve_placeable_exercise``); this is the
    whole-day backstop: an edit that leaves more misplaced entries than the
    day held before is refused.

    G4 founder ruling: a legacy day that already holds misplaced cardio (a
    pre-P3A mixed list) is NOT locked. Its strength work stays editable and
    the misplaced entries stay where they are with the same identity — never
    moved to another list, re-identified or dropped by an unrelated edit. The
    owner may still edit their stored sets/reps (identity cannot change: a
    Replace is placement-checked) or correct them.
    """
    if authority is None or not authority.by_id:
        return
    after = _misplaced_entries(day, authority, strict=True)
    if after > before:
        raise InvalidMutation("cardio cannot be added to a strength list")
    if after and not _holds_strength_work(day, authority):
        # Stored cardio beside no strength work is a misleading day type: an
        # edit that would produce (or re-save) such a day is refused, and
        # only a correction of the cardio is accepted there.
        raise InvalidMutation("a strength day must keep its strength work")


#: Movements that are not the strength session a ``antrenman`` day names.
_NON_STRENGTH_MOVEMENTS = frozenset({"cardio", "mobility"})


def _holds_strength_work(day, authority):
    """Does ``day``'s own list hold at least one strength (or core) entry?"""
    for entry in _exercises_of(day):
        exercise = _resolve_catalog_exercise(authority, entry.get(FIELD_EXERCISE_ID))
        if exercise.movement not in _NON_STRENGTH_MOVEMENTS:
            return True
    return False


#: G4 session selectors on a native exercise command.
SESSION_STRENGTH = "strength"
SESSION_CARDIO = "cardio"


def _selected_session(command):
    """``None`` for the day's own list, ``SESSION_CARDIO`` for its session."""
    session = getattr(command, "session", None)
    if session is None or session == SESSION_STRENGTH:
        return None
    if session != SESSION_CARDIO:
        raise InvalidMutation("unknown session")
    return SESSION_CARDIO


def _target_list(day, command, authority, *, create=False):
    """``(exercises, placement)`` the command addresses on ``day``.

    The strength selector is exactly the pre-G4 behaviour. The cardio
    selector addresses the strength day's separate session: only on a
    canonical plan through the native editor, only on a strength day, with
    the dedicated-cardio placement. ``create`` (Add only) materializes an
    absent session on the working copy; any later refusal discards it.
    """
    if _selected_session(command) is None:
        return _exercises_of(day), day
    if authority is None or not authority.by_id:
        raise InvalidMutation("a cardio session needs a canonical native plan")
    if day.get(FIELD_KIND) != SESSION_TIP:
        raise InvalidMutation("a cardio session belongs to a strength day")
    session = day.get(CARDIO_SESSION_KEY)
    if session is None:
        if not create:
            raise ExerciseNotFound("the day has no cardio session")
        session = {FIELD_EXERCISES: []}
        day[CARDIO_SESSION_KEY] = session
    if not isinstance(session, dict) or not isinstance(session.get(FIELD_EXERCISES), list):
        raise PlanNotMutable("day has a malformed cardio session")
    return session[FIELD_EXERCISES], CARDIO_SESSION_PLACEMENT


def _finish_session(day, command):
    """Keep a cardio session canonical after a session edit.

    Every entry stays one round of ASCII ``"<n> dk"`` and the total stays
    bounded — exactly the structural rule the reader applies — and removing
    the last entry removes the block: an empty session is not a planned
    session.
    """
    if _selected_session(command) is None:
        return
    exercises = day[CARDIO_SESSION_KEY][FIELD_EXERCISES]
    if not exercises:
        del day[CARDIO_SESSION_KEY]
        return
    # The plan READER's own session validator is the one authority: an edit
    # the reader would refuse can never be saved (a refused stored session
    # would make the whole plan unreadable on every client).
    from app.services.training_generation.output_errors import SchemaInvalidError
    from app.services.training_generation.response_validator import validate_cardio_session
    try:
        validate_cardio_session(day[CARDIO_SESSION_KEY])
    except SchemaInvalidError as exc:
        raise InvalidPrescription("a cardio session entry is one round of minutes") from exc


def apply_command(document, command, *, native_editor=False, private_definitions=None, coach=False):
    """Apply ``command`` to a copy of ``document``.

    Returns ``(new_document, changed)``. ``changed`` is False when the requested
    desired state already held — a deterministic no-op, with the *original*
    document returned so the caller can skip the write entirely (brief §14).

    The copy is taken up-front and every validation happens against it, so a
    command that fails half-way leaves the caller's document untouched and there
    is no partially-mutated state to roll back.
    """
    if not native_editor and getattr(command, "position", None) is not None:
        raise InvalidMutation("exercise position requires a bound editor revision")
    working = copy.deepcopy(document)
    program = _program_of(working)
    # Resolved once, before any branch: the context decides how EVERY command
    # below reads identity, and a command that touches no exercise at all
    # (a day move) must still be refused on a plan whose context is broken.
    authority = _exercise_authority(working, by_id=native_editor,
                                    private_definitions=private_definitions if native_editor else None)

    if coach:
        day = _find_day(program, command.day)
        if isinstance(command, MoveTrainingDayCommand):
            other = _find_day(program, command.target_day)
            for slot in (day, other):
                if CARDIO_SESSION_KEY in slot and slot.get(FIELD_KIND) != SESSION_TIP:
                    raise InvalidMutation("a cardio session belongs to a strength day")
                if slot.get(FIELD_KIND) == REST_TIP:
                    if slot.get(FIELD_EXERCISES) not in (None, []):
                        raise InvalidMutation("a rest day cannot contain exercises")
                else:
                    _require_day_stays_valid(slot)
        else:
            _require_workout_day(day)

    if isinstance(command, ReplaceExerciseCommand):
        changed = _apply_replace(program, command, authority, coach=coach)
    elif isinstance(command, AddExerciseCommand):
        changed = _apply_add(program, command, authority, coach=coach)
    elif isinstance(command, RemoveExerciseCommand):
        changed = _apply_remove(program, command, authority)
    elif isinstance(command, UpdateExercisePrescriptionCommand):
        changed = _apply_update(program, command, authority)
    elif isinstance(command, MoveTrainingDayCommand):
        changed = _apply_move(program, command)
    else:  # pragma: no cover - the service refuses unknown types earlier
        raise InvalidMutation("unsupported command")

    return (working, True) if changed else (document, False)


def _apply_replace(program, command, authority, *, coach):
    replacement = normalize_exercise_name(command.replacement)
    if replacement is None:
        raise InvalidMutation("replacement name is required")

    day = _find_day(program, command.day)
    exercises, placement = _target_list(day, command, authority)
    index = _find_exercise_index(
        exercises, command.exercise, authority, position=command.position)

    # Mutate the existing slot in place: position, rest, notes and any field
    # this PR does not model are inherited, not rebuilt (brief §9A).
    entry = exercises[index]
    before = _native_placement_baseline(day, authority)
    updates = {FIELD_NAME: replacement}
    if authority is not None:
        # Name and identity are written as ONE update, which is what closes
        # P1-4: the pre-PR4 code rewrote ``isim`` alone and left
        # ``exercise_id`` pointing at the exercise that used to be here.
        # Because both land in ``updates``, the no-op short-circuit below
        # also still holds — replacing an exercise with an alias of itself
        # resolves back to the same pair and stays a deterministic no-op.
        resolved = _resolve_placeable_exercise(authority, replacement, placement)
        if ((authority.by_id or coach) and resolved.exercise_id != entry.get(FIELD_EXERCISE_ID)
                and _identity_matches(exercises, replacement, authority, resolved)):
            raise ExerciseAlreadyPresent("replacement is already in the target day")
        updates[FIELD_NAME] = resolved.canonical_name
        updates[FIELD_EXERCISE_ID] = resolved.exercise_id
    if command.sets is not None:
        updates[FIELD_SETS] = validate_sets(command.sets)
    if command.reps is not None:
        updates[FIELD_REPS] = validate_reps(command.reps)

    if all(entry.get(key) == value for key, value in updates.items()):
        return False
    entry.update(updates)
    _require_native_cardio_placement(day, authority, before)
    _finish_session(day, command)
    return True


def _apply_add(program, command, authority, *, coach):
    name = normalize_exercise_name(command.exercise)
    if name is None:
        raise InvalidMutation("exercise name is required")
    if command.sets is None or command.reps is None:
        raise InvalidMutation("sets and reps are required when adding")
    sets = validate_sets(command.sets)
    reps = validate_reps(command.reps)

    day = _find_day(program, command.day)
    _require_workout_day(day)
    exercises, placement = _target_list(day, command, authority, create=True)
    entry = {FIELD_NAME: name, FIELD_SETS: sets, FIELD_REPS: reps}
    resolved = None
    if authority is not None:
        resolved = _resolve_placeable_exercise(authority, name, placement)
        entry[FIELD_NAME] = resolved.canonical_name
        entry["dinlenme"] = "—" if coach else "0"
        entry["not"] = ""
        # Appended last, matching the key order canonicalization produces on
        # the generate/save path, so a mutated canonical plan and a freshly
        # saved one serialize the same way.
        entry[FIELD_EXERCISE_ID] = resolved.exercise_id

    # One exercise identity, at most one slot, per day. Checked here — after
    # the catalog/compatibility/placement gates above, which therefore still
    # decide an ADD exactly as they did, and before the append, which is the
    # only statement that could create the second occurrence.
    #
    # Deliberately DAY-local. The same exercise on two different training days
    # is an ordinary plan (a squat on leg day and on a full-body day), so a
    # weekly-global rule would refuse plans the generator itself writes.
    existing = _identity_matches(exercises, name, authority, resolved)
    if len(existing) > 1:
        # Historical state this boundary does not repair: some day already
        # holds this exercise twice. Picking one, merging them or deleting one
        # would all be a write nobody asked for, so it refuses and appends
        # nothing — a third copy is the one outcome that is certainly wrong.
        raise AmbiguousExerciseTarget("target exercise matches more than once")
    if existing:
        current = exercises[existing[0]]
        if (current.get(FIELD_SETS) == sets
                and current.get(FIELD_REPS) == reps):
            # Already exactly what was asked for. A deterministic no-op on the
            # engine's existing terms: the ORIGINAL document goes back to the
            # caller, so no version moves and no bytes are rewritten.
            #
            # Equality is the canonical validated values compared as they are
            # stored — the same ``==`` every other operation here uses. No
            # equivalence is invented for this guard: if the repository does
            # not already consider "8-12" and "8 to 12" the same prescription,
            # they are two different prescriptions, and the branch below is
            # the honest answer rather than a rep parser this task has no
            # mandate to write.
            return False
        # Present, but not as requested. NOT converted into an update: the
        # caller asked to add an exercise, and rewriting the prescription of
        # the one already there is a different change than the one requested.
        raise ExerciseAlreadyPresent("exercise is already in the target day")

    if ((coach or (authority is not None and authority.by_id))
            and len(exercises) >= NATIVE_MAX_EXERCISES_PER_DAY):
        raise InvalidMutation("workout has reached the exercise limit")
    before = _native_placement_baseline(day, authority)
    exercises.append(entry)
    _require_native_cardio_placement(day, authority, before)
    _finish_session(day, command)
    return True


def _apply_remove(program, command, authority):
    day = _find_day(program, command.day)
    exercises, _placement = _target_list(day, command, authority)
    index = _find_exercise_index(
        exercises, command.exercise, authority,
        match_sets=command.match_sets, match_reps=command.match_reps,
        position=command.position)
    before = _native_placement_baseline(day, authority)
    exercises.pop(index)
    _require_day_stays_valid(day)
    _require_native_cardio_placement(day, authority, before)
    _finish_session(day, command)
    return True


def _apply_update(program, command, authority):
    if command.sets is None and command.reps is None:
        raise InvalidMutation("no prescription field supplied")

    updates = {}
    if command.sets is not None:
        updates[FIELD_SETS] = validate_sets(command.sets)
    if command.reps is not None:
        updates[FIELD_REPS] = validate_reps(command.reps)

    day = _find_day(program, command.day)
    exercises, _placement = _target_list(day, command, authority)
    # Identity is only READ here. A prescription change is not an identity
    # change, so ``exercise_id`` and ``isim`` are both left exactly alone.
    entry = exercises[
        _find_exercise_index(
            exercises, command.exercise, authority, position=command.position)]
    before = _native_placement_baseline(day, authority)

    if all(entry.get(key) == value for key, value in updates.items()):
        return False
    entry.update(updates)
    _require_native_cardio_placement(day, authority, before)
    _finish_session(day, command)
    return True


def _apply_move(program, command):
    source = _find_day(program, command.day)
    target = _find_day(program, command.target_day)
    if source is target:
        return False

    # Exchange content, not identity: each entry keeps its own ``gun`` so the
    # weekday calendar is never renamed and never reordered.
    source_label = source.get(FIELD_DAY)
    target_label = target.get(FIELD_DAY)
    source_content = {k: v for k, v in source.items() if k != FIELD_DAY}
    target_content = {k: v for k, v in target.items() if k != FIELD_DAY}

    # Swapping two days that already hold the same content changes nothing, so it
    # is reported as nothing (two rest days, say): claiming ``changed`` would burn
    # a mutation_version and rewrite identical plan bytes -- the same
    # deterministic no-op the other ``_apply_*`` helpers honour (triage
    # 2026-09-30 #6). Nothing has been touched yet, so the document stays as read.
    if source_content == target_content:
        return False

    source.clear()
    source[FIELD_DAY] = source_label
    source.update(target_content)

    target.clear()
    target[FIELD_DAY] = target_label
    target.update(source_content)
    return True
