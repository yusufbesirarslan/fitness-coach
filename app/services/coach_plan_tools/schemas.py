"""The LLM-facing tool surface — six narrow tools, one per typed command.

There is deliberately no ``mutate_training_plan(operation, payload)``, no JSON
Patch and no ``set_plan_field``: handing a model an arbitrary mutation language
is exactly what the PR1 boundary exists to prevent, and a generic tool would
smuggle one back in above it (brief §11).

Two rules govern what a property may be.

**Only user intent.** A property exists here only if the typed PR1 command has a
field for it that carries the user's actual meaning. ``user_id``, ``plan_id``,
``lineage_id``, ``mutation_version``, ``actor``, the operation key and both
snapshots are server-owned and appear nowhere in this module (brief §12), which
``tests/test_coach_plan_tools_architecture.py`` enforces structurally rather
than by reading these docstrings.

**Required means required OF THE MODEL.** A property the server resolves for
itself is never published as ``required``, however essential it is to the
command. ``SERVER_GROUNDABLE`` below is the single declaration of which ones
those are, and both the published ``required`` list and the parser's
``GROUNDABLE`` table are derived from it. Two hand-written copies is how a
tool ends up mandatory to the model and optional to the server: the model then
obeys the schema, asks the user instead of calling, and the partial request the
user already made is never stored — which is exactly how "add Walking Lunges
with 4 sets" came back asking for sets again (§37).

**Bounds come from the domain.** Every limit below is imported from
``plan_mutation.validation`` — the same constants the mutation boundary rejects
against, which in turn reuse the generator's ``response_validator``. A second
hardcoded copy would drift, and the drift would show up as a model being told
"1-100 sets" while the domain accepted something else. The provider schema is
UX: it steers the model. The domain remains the authority (brief §37).

Descriptions are Turkish to match every other Coach tool, and each one names
the single situation it is allowed in — advice, hypotheticals, full-program
redesign and inferred adaptation are called out as NOT that situation (§63).
"""
from app.services.plan_mutation.validation import (
    MAX_EXERCISE_NAME_CHARS,
    MAX_REPS_CHARS,
    MAX_SETS,
    MIN_SETS,
    WEEKDAYS,
)


#: Every tool name this package owns. The Coach router uses it to decide
#: whether a name belongs to the plan-mutation surface at all.
REPLACE_EXERCISE_TOOL = "replace_training_plan_exercise"
ADD_EXERCISE_TOOL = "add_training_plan_exercise"
REMOVE_EXERCISE_TOOL = "remove_training_plan_exercise"
UPDATE_PRESCRIPTION_TOOL = "update_training_plan_prescription"
MOVE_DAY_TOOL = "move_training_plan_day"
UNDO_TOOL = "undo_last_training_plan_change"
CONFIRM_TOOL = "confirm_pending_training_plan_change"
CANCEL_PENDING_TOOL = "cancel_pending_training_plan_change"


_ONLY_ON_EXPLICIT_REQUEST = (
    "YALNIZCA kullanıcı bu değişikliği bu turda AÇIKÇA istediğinde çağır. "
    "Tavsiye, öneri, varsayımsal tartışma, senin kendi önerin ya da "
    "gözlemlerinden (yorgunluk, plato, kaçırılan antrenman) çıkardığın bir "
    "sonuç için ASLA çağırma. Tüm programı yeniden kurmak için de kullanma."
)

#: Weekday enum, straight from the generator's canonical vocabulary. Sent to
#: the provider so the model picks a real day instead of inventing "Monday".
_DAY_PROPERTY = {
    "type": "string",
    "enum": list(WEEKDAYS),
    "description": "Planın kanonik gün adı (Türkçe hafta günü).",
}

_TARGET_EXERCISE_PROPERTY = {
    "type": "string",
    "maxLength": MAX_EXERCISE_NAME_CHARS,
    "description": (
        "O gündeki hedef egzersizin adı. Plandaki adla eşleşmeli; aynı gün "
        "içinde birden çok kez geçiyorsa araç değişikliği REDDEDER ve "
        "kullanıcıya sorman gerekir."),
}

_SETS_PROPERTY = {
    "type": "integer",
    "minimum": MIN_SETS,
    "maximum": MAX_SETS,
    "description": "Set sayısı.",
}

_REPS_PROPERTY = {
    "type": "string",
    "maxLength": MAX_REPS_CHARS,
    "description": "Tekrar reçetesi, serbest metin. Ör. '8-12', '5', '30 dk'.",
}


#: ``{tool name: properties the SERVER resolves for itself}``.
#:
#: The single source of truth for groundability. ``parser.GROUNDABLE`` imports
#: this, and the published ``required`` list below is this set subtracted from
#: the command's real requirements — so a property cannot be mandatory to the
#: model and optional to the server. Kept deliberately narrow:
#:
#: * ``day`` on add/replace/update, because ``workout_targets`` resolves a
#:   weekday, a nickname ("my leg workout") or a unique exercise slot against
#:   the persisted plan, and ``grounding`` asks the user when it cannot;
#: * ``sets``/``reps`` on add, because grounding stores the half the user
#:   already gave and asks for the other half.
#:
#: ``remove`` and ``move`` ground nothing: neither has a continuation path
#: (``grounding._OPERATION_TOOLS`` cannot re-issue them), so a stored
#: clarification for one could never be completed. ``exercise``,
#: ``replacement`` and ``target_day`` are never groundable — a target nobody
#: named is the one thing this boundary exists to refuse.
SERVER_GROUNDABLE = {
    REPLACE_EXERCISE_TOOL: frozenset({"day"}),
    ADD_EXERCISE_TOOL: frozenset({"day", "sets", "reps"}),
    UPDATE_PRESCRIPTION_TOOL: frozenset({"day"}),
    REMOVE_EXERCISE_TOOL: frozenset(),
    MOVE_DAY_TOOL: frozenset(),
}


def _model_required(tool_name, *fields):
    """The subset of ``fields`` the MODEL must send.

    Everything the server grounds is dropped: publishing it as required tells
    the model to withhold the call and interrogate the user itself, and a call
    the model never makes is a request the server never gets to store.
    """
    groundable = SERVER_GROUNDABLE[tool_name]
    return [field for field in fields if field not in groundable]


PLAN_MUTATION_TOOL_DEFS = (
    {
        "name": REPLACE_EXERCISE_TOOL,
        "description": (
            "Kullanıcının aktif antrenman planında BİR gündeki BİR egzersizi "
            "başka bir egzersizle değiştirir. Set/tekrar verilmezse eski "
            "egzersizin reçetesi aynen korunur. " + _ONLY_ON_EXPLICIT_REQUEST),
        "parameters": {
            "type": "object",
            "properties": {
                "day": _DAY_PROPERTY,
                "exercise": _TARGET_EXERCISE_PROPERTY,
                "replacement": {
                    "type": "string",
                    "maxLength": MAX_EXERCISE_NAME_CHARS,
                    "description": "Yerine konacak egzersizin adı.",
                },
                "sets": _SETS_PROPERTY,
                "reps": _REPS_PROPERTY,
            },
            "required": _model_required(
                REPLACE_EXERCISE_TOOL, "day", "exercise", "replacement"),
            "additionalProperties": False,
        },
    },
    {
        "name": ADD_EXERCISE_TOOL,
        "description": (
            "Kullanıcının aktif antrenman planında BİR güne BİR egzersiz "
            "ekler. Kullanıcının SÖYLEDİĞİ gün/set/tekrar neyse onu gönder; "
            "söylemediği alanı HİÇ GÖNDERME ve aracı YİNE DE çağır — eksiği "
            "sunucu kullanıcıya sorar, verdiğini saklar. Değer UYDURMA ve "
            "aracı çağırmak yerine kendin sorma: sorduğun turda sunucu "
            "kullanıcının verdiği kısmı kaydedemez. Dinlenme gününe egzersiz "
            "eklenemez. "
            + _ONLY_ON_EXPLICIT_REQUEST),
        "parameters": {
            "type": "object",
            "properties": {
                "day": _DAY_PROPERTY,
                "exercise": {
                    "type": "string",
                    "maxLength": MAX_EXERCISE_NAME_CHARS,
                    "description": "Eklenecek egzersizin adı.",
                },
                "sets": _SETS_PROPERTY,
                "reps": _REPS_PROPERTY,
            },
            "required": _model_required(
                ADD_EXERCISE_TOOL, "day", "exercise", "sets", "reps"),
            "additionalProperties": False,
        },
    },
    {
        "name": REMOVE_EXERCISE_TOOL,
        "description": (
            "Kullanıcının aktif antrenman planında BİR gündeki BİR egzersizi "
            "kaldırır. Antrenman günü en az bir egzersizle kalmalıdır. "
            + _ONLY_ON_EXPLICIT_REQUEST),
        "parameters": {
            "type": "object",
            "properties": {
                "day": _DAY_PROPERTY,
                "exercise": _TARGET_EXERCISE_PROPERTY,
            },
            "required": _model_required(
                REMOVE_EXERCISE_TOOL, "day", "exercise"),
            "additionalProperties": False,
        },
    },
    {
        "name": UPDATE_PRESCRIPTION_TOOL,
        "description": (
            "Kullanıcının aktif antrenman planında BİR egzersizin set ve/veya "
            "tekrar reçetesini günceller. En az biri verilmelidir; egzersizin "
            "kendisi değişmez. " + _ONLY_ON_EXPLICIT_REQUEST),
        "parameters": {
            "type": "object",
            "properties": {
                "day": _DAY_PROPERTY,
                "exercise": _TARGET_EXERCISE_PROPERTY,
                "sets": _SETS_PROPERTY,
                "reps": _REPS_PROPERTY,
            },
            "required": _model_required(
                UPDATE_PRESCRIPTION_TOOL, "day", "exercise"),
            "additionalProperties": False,
        },
    },
    {
        "name": MOVE_DAY_TOOL,
        "description": (
            "İki gün SLOTUNUN içeriğini takas eder (ör. Cuma antrenmanını "
            "Cumartesi'ye taşımak). Gün adları değişmez, egzersizler yer "
            "değiştirir. " + _ONLY_ON_EXPLICIT_REQUEST),
        "parameters": {
            "type": "object",
            "properties": {
                "day": dict(_DAY_PROPERTY,
                            description="Taşınacak içeriğin bulunduğu gün."),
                "target_day": dict(_DAY_PROPERTY,
                                   description="İçeriğin taşınacağı gün."),
            },
            "required": _model_required(
                MOVE_DAY_TOOL, "day", "target_day"),
            "additionalProperties": False,
        },
    },
    {
        "name": UNDO_TOOL,
        "description": (
            "Antrenman planında yapılan EN SON değişikliği geri alır. "
            "YALNIZCA kullanıcı son plan değişikliğini geri almayı açıkça "
            "istediğinde çağır ('geri al', 'eski hâline döndür'). Sağlayıcı "
            "hatası, kullanıcının kararsız görünmesi veya sonraki bir aracın "
            "hata vermesi geri alma SEBEBİ DEĞİLDİR. Belirli bir eski "
            "değişiklik ya da sürüm seçilemez."),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
)

PLAN_MUTATION_TOOL_NAMES = frozenset(
    definition["name"] for definition in PLAN_MUTATION_TOOL_DEFS)

CONFIRMATION_TOOL_DEFS = (
    {
        "name": CONFIRM_TOOL,
        "description": (
            "Bekleyen antrenman-planı değişikliğini uygular. YALNIZCA "
            "kullanıcının bu turdaki mesajı sunucunun onay olarak tanıdığı "
            "kısa bir onay ise çağır. Argüman alma. Teklif kimliği, plan "
            "sürümü veya kullanıcı kimliği gönderme — sunucu kendi bekleyen "
            "teklifini çözer. Onay yoksa ÇAĞIRMA."),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": CANCEL_PENDING_TOOL,
        "description": (
            "Bekleyen antrenman-planı değişikliğini iptal eder. YALNIZCA "
            "kullanıcı bu turda iptal ettiğinde çağır. Planı DEĞİŞTİRMEZ. "
            "Argüman alma."),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
)

CONFIRMATION_TOOL_NAMES = frozenset(
    definition["name"] for definition in CONFIRMATION_TOOL_DEFS)
assert CONFIRMATION_TOOL_DEFS[0]["name"] == CONFIRM_TOOL
assert CONFIRMATION_TOOL_DEFS[1]["name"] == CANCEL_PENDING_TOOL

PLAN_WRITE_TOOL_NAMES = PLAN_MUTATION_TOOL_NAMES | CONFIRMATION_TOOL_NAMES
