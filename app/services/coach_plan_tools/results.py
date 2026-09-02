"""What the model is allowed to see about a plan mutation.

A tool result is not a return value — it is the next model call's input, it
gets billed, and whatever is in it can end up paraphrased to the user. So this
module builds a small, closed, deterministic object and nothing else.

Never crosses this boundary (brief §25): the before/after snapshots, the whole
plan document, ``lineage_id``, any database row id, the operation key, the
semantic fingerprint, SQL, or a raw exception message. ``mutation_id`` is
withheld too — PR2 made it opaque precisely so a transport could publish it,
but the model has no use for it and cannot act on it, and an identifier in the
context window is an identifier that can be echoed to the user.

Does cross it: the outcome, which command ran, the resulting plan version, the
targets the caller itself named, and one deterministic Turkish sentence
describing the change. That sentence is built from the typed command plus the
canonical service result (brief §26) — not from a second LLM call and not from
the pre-mutation context block, which is stale by the time this runs (§33).
"""
from app.services.plan_mutation import (
    AddExerciseCommand,
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseNotFound,
    IdempotencyConflict,
    InvalidMutation,
    InvalidPrescription,
    MoveTrainingDayCommand,
    PlanNotFound,
    PlanNotMutable,
    PlanStateConflict,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UndoConflict,
    UndoUnavailable,
    UpdateExercisePrescriptionCommand,
)
from app.services.plan_mutation.fingerprint import (
    UNDO_COMMAND_TYPE,
    command_type,
)
from app.services.plan_mutation.journal import OUTCOME_APPLIED, OUTCOME_NO_OP


#: Result statuses. ``replayed`` is distinct from ``applied`` on purpose: the
#: model must be able to say "that is already done" instead of narrating a
#: second change that never happened.
STATUS_APPLIED = "applied"
STATUS_NO_OP = "no_op"
STATUS_REPLAYED = "replayed"
STATUS_CONFIRMATION_REQUIRED = "confirmation_required"
STATUS_CANCELLED = "cancelled"
STATUS_NEEDS_INPUT = "needs_user_input"
STATUS_ERROR = "error"

#: Bounded error vocabulary. One entry per way this can honestly fail; the
#: model branches on the code, the user hears the message.
ERROR_PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
ERROR_PLAN_NOT_MUTABLE = "PLAN_NOT_MUTABLE"
ERROR_DAY_NOT_FOUND = "DAY_NOT_FOUND"
ERROR_TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
ERROR_AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
ERROR_INVALID_MUTATION = "INVALID_MUTATION"
ERROR_INVALID_PRESCRIPTION = "INVALID_PRESCRIPTION"
ERROR_INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
ERROR_IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
ERROR_PLAN_STATE_CONFLICT = "PLAN_STATE_CONFLICT"
ERROR_UNDO_UNAVAILABLE = "UNDO_UNAVAILABLE"
ERROR_UNDO_CONFLICT = "UNDO_CONFLICT"
ERROR_MUTATION_BUDGET_EXHAUSTED = "MUTATION_BUDGET_EXHAUSTED"
ERROR_CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
ERROR_TURN_IDENTITY_UNAVAILABLE = "TURN_IDENTITY_UNAVAILABLE"
ERROR_INTERNAL = "INTERNAL_ERROR"
ERROR_CONFIRMATION_NOT_AUTHORIZED = "CONFIRMATION_NOT_AUTHORIZED"
ERROR_NO_PENDING_CONFIRMATION = "NO_PENDING_CONFIRMATION"
ERROR_PENDING_CONFIRMATION_STALE = "PENDING_CONFIRMATION_STALE"
ERROR_PENDING_CONFIRMATION_EXISTS = "PENDING_CONFIRMATION_EXISTS"
ERROR_UNSUPPORTED_IMPACT = "UNSUPPORTED_IMPACT"

#: Domain exception → error code. Ordered most-specific first, because several
#: of these share a base class. Mapping the CLASS rather than the message is
#: what keeps a domain wording change from becoming a contract change.
_ERROR_BY_EXCEPTION = (
    (AmbiguousExerciseTarget, ERROR_AMBIGUOUS_TARGET),
    (ExerciseNotFound, ERROR_TARGET_NOT_FOUND),
    (DayNotFound, ERROR_DAY_NOT_FOUND),
    (InvalidPrescription, ERROR_INVALID_PRESCRIPTION),
    (InvalidMutation, ERROR_INVALID_MUTATION),
    (IdempotencyConflict, ERROR_IDEMPOTENCY_CONFLICT),
    (UndoUnavailable, ERROR_UNDO_UNAVAILABLE),
    (UndoConflict, ERROR_UNDO_CONFLICT),
    (PlanStateConflict, ERROR_PLAN_STATE_CONFLICT),
    (PlanNotMutable, ERROR_PLAN_NOT_MUTABLE),
    (PlanNotFound, ERROR_PLAN_NOT_FOUND),
)

#: Server-authored guidance per error code. Deterministic recovery policy
#: (brief §29) lives here rather than in the system prompt, so it travels with
#: the failure it describes instead of competing with it.
_ERROR_MESSAGES = {
    ERROR_PLAN_NOT_FOUND: (
        "Kullanıcının aktif bir antrenman planı yok. Plan uydurma; önce plan "
        "oluşturması gerektiğini söyle."),
    ERROR_PLAN_NOT_MUTABLE: (
        "Mevcut plan bu araçlarla düzenlenebilecek biçimde değil. Değişikliği "
        "yapamadığını söyle; planı yeniden kurmaya çalışma."),
    ERROR_DAY_NOT_FOUND: (
        "Belirtilen gün planda yok. Kullanıcıya hangi günü kastettiğini sor."),
    ERROR_TARGET_NOT_FOUND: (
        "Belirtilen egzersiz o günde yok. Kullanıcıya bunu söyle ve doğru "
        "egzersizi/günü sor. Tahmin ederek başka bir egzersizi değiştirme."),
    ERROR_AMBIGUOUS_TARGET: (
        "Bu ad o gün içinde birden fazla egzersizle eşleşiyor. Hangisini "
        "kastettiğini kullanıcıya SOR. Sıradaki ilkini seçme, konuma göre "
        "tahmin etme, aracı tekrar çağırma."),
    ERROR_INVALID_MUTATION: (
        "Bu değişiklik uygulanamaz. Değerleri kendiliğinden kırpma veya "
        "yeniden yorumlama; kullanıcıya neyin mümkün olmadığını açıkla."),
    ERROR_INVALID_PRESCRIPTION: (
        "Set/tekrar değeri kabul edilen sınırların dışında. Değeri kendin "
        "düzeltme; kullanıcıya geçerli bir değer sor."),
    ERROR_INVALID_ARGUMENTS: (
        "Araç argümanları geçersiz. Eksik bilgiyi kullanıcıdan iste; "
        "varsayılan uydurma."),
    ERROR_IDEMPOTENCY_CONFLICT: (
        "Bu işlem kimliği bu turda başka bir değişikliğe bağlanmış. Aracı "
        "tekrar çağırma; kullanıcıdan isteğini tek bir cümlede netleştirmesini "
        "iste."),
    ERROR_PLAN_STATE_CONFLICT: (
        "Plan bu istek işlenirken değişti ve değişiklik uygulanmadı. Zorla "
        "tekrar deneme; kullanıcıya planın değiştiğini söyle."),
    ERROR_UNDO_UNAVAILABLE: (
        "Geri alınacak bir plan değişikliği yok. Bir şeyi geri aldığını "
        "SÖYLEME."),
    ERROR_UNDO_CONFLICT: (
        "Plan o değişiklikten sonra başka bir şekilde değişmiş, bu yüzden "
        "geri alma güvenli değil. Zorla geri yükleme; kullanıcıya planın "
        "değiştiğini açıkla."),
    ERROR_MUTATION_BUDGET_EXHAUSTED: (
        "Bu turda izin verilen plan değişikliği sayısına ulaşıldı. Daha fazla "
        "değişiklik yapma; kullanıcıya kalan isteklerini yeni bir mesajda "
        "iletmesini söyle."),
    ERROR_CAPABILITY_DISABLED: (
        "Plan düzenleme şu anda kullanılamıyor. Değişiklik yapıldığını "
        "SÖYLEME; kullanıcıya planını uygulamadan düzenleyebileceğini söyle."),
    ERROR_TURN_IDENTITY_UNAVAILABLE: (
        "Plan düzenleme şu anda kullanılamıyor. Değişiklik yapıldığını "
        "SÖYLEME."),
    ERROR_INTERNAL: (
        "Plan değişikliği şu anda yapılamadı. Değişiklik yapıldığını SÖYLEME; "
        "kullanıcıdan daha sonra tekrar denemesini iste."),
    ERROR_CONFIRMATION_NOT_AUTHORIZED: (
        "Kullanıcı bu turda bekleyen plan değişikliğini onaylamadı. "
        "Onayladığını SÖYLEME; onay veya iptal iste."),
    ERROR_NO_PENDING_CONFIRMATION: (
        "Onaylanacak bekleyen bir plan değişikliği yok. Bir değişiklik "
        "yaptığını SÖYLEME."),
    ERROR_PENDING_CONFIRMATION_STALE: (
        "Bekleyen plan değişikliği artık geçerli değil çünkü plan o "
        "tekliften sonra değişti. Eski teklifi uygulama; kullanıcıdan "
        "isteğini yeniden söylemesini iste."),
    ERROR_PENDING_CONFIRMATION_EXISTS: (
        "Bekleyen bir plan değişikliği var. Yeni bir plan aracı çağırma; "
        "kullanıcıdan önce onay veya iptal iste."),
    ERROR_UNSUPPORTED_IMPACT: (
        "Bu değişiklik güvenle değerlendirilemedi. Uyguladığını SÖYLEME; "
        "kullanıcıya yapılamadığını söyle."),
}

#: Free-text length ceiling for any value echoed back. The targets came from
#: the model and are already bounded by the parser and the domain, but a tool
#: result feeds a billed model call and a hard cap is cheaper than trusting
#: every upstream bound to hold forever.
MAX_ECHO_CHARS = 120

#: Attached to every result that actually moved persisted state.
#:
#: The plan block in the prompt was built BEFORE this turn's tools ran, so the
#: moment a mutation commits that block describes a plan that no longer exists.
#: Nothing re-renders it mid-turn, and the honest fix is not to pretend it did:
#: the model is told which of its two sources is current (brief §33). Reading
#: the plan back here instead would put a second, competing description of the
#: plan in the context window — and it would still be stale after the next tool
#: call.
_STALE_CONTEXT_NOTE = (
    "Plan ŞİMDİ değişti. Konuşmanın başındaki plan bağlamı bu değişikliği "
    "İÇERMEZ; planın güncel hâli için o bloğu değil bu sonucu esas al.")


def error_code_for(exception):
    """The bounded code for a domain exception, or ``INTERNAL_ERROR``.

    An unrecognised exception maps to ``INTERNAL_ERROR`` rather than to
    something the user could act on: presenting a persistence defect as a
    user-correctable mistake sends them in circles (brief §73).
    """
    for exception_type, code in _ERROR_BY_EXCEPTION:
        if isinstance(exception, exception_type):
            return code
    return ERROR_INTERNAL


def error_result(code, detail=None):
    """A bounded failure result.

    ``detail`` is server-authored text only — an argument-shape complaint from
    the parser, never a domain exception's message and never a database error.
    """
    payload = {
        "status": STATUS_ERROR,
        "error": code,
        "message": _ERROR_MESSAGES.get(code, _ERROR_MESSAGES[ERROR_INTERNAL]),
    }
    if detail:
        payload["detail"] = _clip(detail)
    return payload


def _clip(value):
    text = str(value).strip()
    return text[:MAX_ECHO_CHARS]


def _status_of(result):
    if result.replayed:
        return STATUS_REPLAYED
    return STATUS_APPLIED if result.outcome == OUTCOME_APPLIED else STATUS_NO_OP


def _change_of(command):
    """The targets the command named, echoed back for a grounded answer.

    Only fields the caller supplied. Nothing is read back out of the plan here:
    a second read would be a second authority on what the plan now says, and
    the service result is already the authoritative one.
    """
    if isinstance(command, ReplaceExerciseCommand):
        return {"day": _clip(command.day), "exercise": _clip(command.exercise),
                "replacement": _clip(command.replacement)}
    if isinstance(command, AddExerciseCommand):
        return {"day": _clip(command.day), "exercise": _clip(command.exercise),
                "sets": command.sets, "reps": _clip(command.reps)}
    if isinstance(command, RemoveExerciseCommand):
        return {"day": _clip(command.day), "exercise": _clip(command.exercise)}
    if isinstance(command, UpdateExercisePrescriptionCommand):
        change = {"day": _clip(command.day),
                  "exercise": _clip(command.exercise)}
        if command.sets is not None:
            change["sets"] = command.sets
        if command.reps is not None:
            change["reps"] = _clip(command.reps)
        return change
    if isinstance(command, MoveTrainingDayCommand):
        return {"day": _clip(command.day),
                "target_day": _clip(command.target_day)}
    return {}


def _summary_of(command, applied):
    """One deterministic Turkish sentence stating what the plan now says.

    ``applied`` is False for an accepted no-op, and the wording changes rather
    than being softened: "already true" and "changed" are different facts and
    the user is entitled to the right one.
    """
    if isinstance(command, ReplaceExerciseCommand):
        if not applied:
            return (f"{command.day} gününde zaten {command.replacement} "
                    f"vardı; plan değişmedi.")
        return (f"{command.day} gününde {command.exercise} yerine "
                f"{command.replacement} kondu.")
    if isinstance(command, AddExerciseCommand):
        return (f"{command.day} gününe {command.exercise} eklendi "
                f"({command.sets}x{command.reps}).")
    if isinstance(command, RemoveExerciseCommand):
        return f"{command.day} gününden {command.exercise} çıkarıldı."
    if isinstance(command, UpdateExercisePrescriptionCommand):
        parts = []
        if command.sets is not None:
            parts.append(f"set {command.sets}")
        if command.reps is not None:
            parts.append(f"tekrar {command.reps}")
        detail = ", ".join(parts)
        if not applied:
            return (f"{command.day} gününde {command.exercise} zaten "
                    f"{detail} idi; plan değişmedi.")
        return (f"{command.day} gününde {command.exercise} artık {detail}.")
    if isinstance(command, MoveTrainingDayCommand):
        if not applied:
            return "Gün içeriği zaten istenen yerdeydi; plan değişmedi."
        return (f"{command.day} gününün içeriği {command.target_day} ile "
                f"yer değiştirdi.")
    return "Plan güncellendi."


def mutation_result(command, result):
    """The bounded result of one applied/no-op/replayed typed mutation."""
    status = _status_of(result)
    payload = {
        "status": status,
        "operation": command_type(command),
        "plan_version": result.plan_version,
        "change": _change_of(command),
        "summary": _summary_of(command, result.outcome == OUTCOME_APPLIED),
    }
    if status == STATUS_REPLAYED:
        payload["note"] = (
            "Bu değişiklik bu turda ZATEN uygulanmıştı; ikinci bir değişiklik "
            "yapılmadı. Kullanıcıya tek bir değişiklik olarak anlat.")
    elif result.outcome == OUTCOME_NO_OP:
        payload["note"] = (
            "İstenen durum planda zaten geçerliydi; hiçbir şey değişmedi. "
            "Değişiklik yaptığını SÖYLEME.")
    else:
        payload["note"] = _STALE_CONTEXT_NOTE
    return payload


def confirmation_required_result(command, reason_codes):
    """Bounded result when the server requires a structural confirmation.

    No proposal id, lineage, version, operation key or snapshot.
    """
    from app.services.coach_plan_policy import UNDO_LAST_CHANGE

    if command is UNDO_LAST_CHANGE:
        operation = UNDO_COMMAND_TYPE
        change = {}
        summary = "Plandaki son değişiklik geri alınacak. Kullanıcıdan onay iste."
    else:
        operation = command_type(command)
        change = _change_of(command)
        summary = _pending_summary_of(command)
    return {
        "status": STATUS_CONFIRMATION_REQUIRED,
        "operation": operation,
        "change": change,
        "summary": summary,
        "reason_codes": list(reason_codes),
        "note": (
            "Plan HENÜZ değişmedi. Kullanıcıya neyin onay beklediğini anlat; "
            "uygulandığını SÖYLEME. Onay bir SONRAKİ mesajdır; sen onaylama."),
    }


REASON_MISSING_PRESCRIPTION = "missing_prescription"
REASON_MISSING_SETS = "missing_sets"
REASON_MISSING_REPS = "missing_reps"
REASON_EXERCISE_UNKNOWN = "exercise_unknown"
REASON_EXERCISE_SUGGEST = "exercise_suggest"
REASON_AMBIGUOUS_WORKOUT = "ambiguous_workout"
REASON_WORKOUT_NOT_FOUND = "workout_not_found"

#: Model-facing recovery for a grounded clarification. Plan is unchanged.
_INPUT_SUMMARIES = {
    REASON_MISSING_PRESCRIPTION: (
        "Egzersiz ve antrenman günü bulundu ama set/tekrar kullanıcı "
        "tarafından verilmedi. Kullanıcıya sor; varsayılan uydurma, "
        "aracı set/tekrar uydurarak tekrar çağırma."),
    REASON_MISSING_SETS: (
        "Tekrar var, set sayısı yok. Yalnızca set sayısını sor; uydurma."),
    REASON_MISSING_REPS: (
        "Set var, tekrar yok. Yalnızca tekrarı sor; uydurma."),
    REASON_EXERCISE_UNKNOWN: (
        "Bu isimde bir egzersiz katalogda yok. Kullanıcıya bulamadığını "
        "söyle. Tahmin ederek başka bir isim yazma, aracı tekrar çağırma."),
    REASON_EXERCISE_SUGGEST: (
        "Kullanıcının yazdığı isim katalogda yok; olası bir eşleşme "
        "detail'de. 'Bunu mu demek istedin?' diye SOR. Onay gelmeden "
        "aracı çağırma, sessizce yerine koyma."),
    REASON_AMBIGUOUS_WORKOUT: (
        "İstenen antrenman birden fazla güne uyuyor. Hangisi olduğunu "
        "kullanıcıya SOR. İlkini seçme, tahmin etme, aracı tekrar çağırma."),
    REASON_WORKOUT_NOT_FOUND: (
        "İstenen antrenman aktif planda yok. Kullanıcıya hangi günü "
        "kastettiğini sor. Bir gün uydurma."),
}

_INPUT_NOTE = (
    "Plan değişmedi. Kullanıcıya sor; onaylamadan veya eksik bilgiyi "
    "almadan aracı tekrar çağırma. Değer uydurma.")


def needs_input_result(reason, command, suggestion=None, candidates=None,
                       label=None):
    """Non-applying clarification. Not a confirmation proposal."""
    change = _change_of(command) if command is not None else {}
    detail = reason
    if suggestion:
        detail = suggestion
    elif candidates:
        detail = ", ".join(str(item) for item in candidates)
    elif label:
        detail = label
    return {
        "status": STATUS_NEEDS_INPUT,
        "operation": command_type(command) if command is not None else "",
        "change": change,
        "summary": _INPUT_SUMMARIES.get(
            reason, _INPUT_SUMMARIES[REASON_WORKOUT_NOT_FOUND]),
        "detail": _clip(detail),
        "note": _INPUT_NOTE,
        "reason": reason,
    }


def cancelled_pending_result():
    return {
        "status": STATUS_CANCELLED,
        "operation": "cancel_pending",
        "change": {},
        "summary": "Bekleyen plan değişikliği iptal edildi; plan aynı kaldı.",
        "note": "Plan değişmedi. İptal edildiğini söyle; bir şey uyguladığını SÖYLEME.",
    }


def _pending_summary_of(command):
    if isinstance(command, ReplaceExerciseCommand):
        return (f"{command.day} gününde {command.exercise} yerine "
                f"{command.replacement} konacak.")
    if isinstance(command, AddExerciseCommand):
        return (f"{command.day} gününe {command.exercise} eklenecek "
                f"({command.sets}x{command.reps}).")
    if isinstance(command, RemoveExerciseCommand):
        return f"{command.day} gününden {command.exercise} çıkarılacak."
    if isinstance(command, UpdateExercisePrescriptionCommand):
        parts = []
        if command.sets is not None:
            parts.append(f"set {command.sets}")
        if command.reps is not None:
            parts.append(f"tekrar {command.reps}")
        detail = ", ".join(parts)
        return f"{command.day} gününde {command.exercise} {detail} olacak."
    if isinstance(command, MoveTrainingDayCommand):
        return (f"{command.day} gününün içeriği {command.target_day} ile "
                f"yer değiştirecek.")
    return "Plan değişikliği onay bekliyor."


def undo_result(result):
    """The bounded result of an undo."""
    payload = {
        "status": _status_of(result),
        "operation": UNDO_COMMAND_TYPE,
        "plan_version": result.plan_version,
        "change": {},
        "summary": "Plandaki son değişiklik geri alındı.",
    }
    if payload["status"] == STATUS_REPLAYED:
        payload["note"] = (
            "Geri alma bu turda ZATEN yapılmıştı; ikinci bir değişiklik geri "
            "alınmadı. Kullanıcıya tek bir geri alma olarak anlat.")
    else:
        payload["note"] = _STALE_CONTEXT_NOTE
    return payload
