"""Hard pre-provider input budget: the per-call half of the AI cost boundary.

The spend guard (`ai_spend_guard`) bounds how many provider calls happen. On
its own that bounds nothing in dollars: a single call could carry the model's
whole context window (200K tokens on Sonnet 4.5), so the per-call worst case
was the provider's limit, not ours. This module makes the per-call worst case
an application constant.

Every paid provider call is checked here against the FINAL payload it will be
sent with (`ai_provider_call.admit` is the only caller), before a capacity
permit is taken and before the spend guard is charged. A payload over its
feature's budget is first reduced by the caller's deterministic reducer (if it
has one) and re-checked; if it still does not fit, the call is refused and the
provider is never contacted.

Counting — why this never undercounts
-------------------------------------
There is no offline tokenizer for Claude, and a network count per call would
put a second dependency in front of every AI request. So the count here is a
deterministic UPPER BOUND, not an estimate:

    bound = UTF-8 bytes of the JSON-serialized payload (image data removed)
            + images x per-image ceiling
            + BASE_OVERHEAD_TOKENS

Both providers use byte-level BPE, so a token always covers at least one byte
of text: the text alone can never produce more tokens than it has bytes. What
the provider adds on top — role/turn markers and the hidden tool-use system
prompt — is covered by BASE_OVERHEAD_TOKENS. Calibrated 2026-09-23 against
Bedrock CountTokens for the production Sonnet 4.5 model: digit-and-space text
is ~1.00 token per byte, and a tool call with a large tool_result is ~300
tokens ABOVE its serialized byte count (the tool preamble) — hence a fixed
overhead that is several times that, not zero. Images: Sonnet 4.5 downscales
anything over 1568 px on the long edge, measured at <= 1,568 tokens for a
5000x5000 image; the ceiling below is 1,600. gpt-4o-mini bills an image at
2,833 + 5,667 x tiles with at most 8 tiles after its own resize (high/auto
detail), 2,833 at low detail.

The bound over-estimates ordinary text (Turkish prose ~2x, a coach turn 2.09x)
— that is the price of never undercounting, and the budgets below are set in
these bound units against the production payloads they were measured on.
Provider-reported usage is logged next to the bound for every call
(`ai_usage`), so an undercount would be visible, not silent.
"""
import json
import os

# ── Taxonomy (bounded: these are also log fields and metric dimensions) ──────

FEATURES = (
    "coach",           # Coach tool loop (blocking + streaming), coach feedback
    "training_plan",   # training-plan generation + its one repair call
    "nutrition_plan",  # nutrition-plan generation
    "nutrition",       # food/macro/serving estimates, meal review
    "menu_extract",    # menu text -> categorized items
    "menu_ocr",        # menu image / scanned-PDF page OCR
    "vision",          # image validation, pump-check analysis/compare
    "summary",         # conversation summaries
    "health_probe",    # deep-health Bedrock reachability probe
    "other",
)
PROVIDERS = ("bedrock", "openai")

# ── Counting constants (see module docstring for their evidence) ─────────────

BASE_OVERHEAD_TOKENS = 1024
ANTHROPIC_IMAGE_TOKENS = 1600
OPENAI_IMAGE_TOKENS_LOW = 2833
OPENAI_IMAGE_TOKENS_HIGH = 2833 + 8 * 5667  # 48,169


class BudgetConfigError(ValueError):
    """An AI_INPUT_BUDGET_* / AI_OUTPUT_BUDGET_* value is not a positive int."""


def _env_positive(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise BudgetConfigError(f"{name} must be a positive integer") from None
    if value <= 0:
        # No "0 = unlimited": an unbounded provider input is exactly the hole
        # this module closes. Removing a budget is a code change, not an env flip.
        raise BudgetConfigError(f"{name} must be a positive integer")
    return value


# Input budget per call, in upper-bound tokens. Derived from production
# (AWS/Bedrock InputTokenCount, 2026-07-25..09-23, ~150 non-probe calls — a
# small sample): the largest call was a coach turn at 19,795 real tokens, which
# is ~41K in bound units at the measured 2.09x coach ratio. Budgets leave
# headroom above the largest legitimate payload each feature can build from
# its own existing caps (history/context/menu-text truncation), not above the
# provider's context window.
_INPUT_DEFAULTS = {
    "coach": 64000,
    "training_plan": 32000,
    "nutrition_plan": 32000,
    "nutrition": 16000,
    "menu_extract": 64000,
    "menu_ocr": 56000,     # one high-detail image (48,169) + prompt
    "vision": 12000,       # up to two images (3,200) + prompt
    "summary": 16000,
    "health_probe": 2048,
    "other": 16000,
}

# Output (max_tokens) ceiling per call, the largest value any call site of the
# feature requests today. A call asking for more is refused, not clamped: that
# is a code change that must also move the documented dollar bound.
_OUTPUT_DEFAULTS = {
    "coach": 700,
    "training_plan": 7000,
    "nutrition_plan": 2000,
    "nutrition": 4000,
    "menu_extract": 5000,
    "menu_ocr": 4000,
    "vision": 1200,
    "summary": 500,
    "health_probe": 1,
    "other": 2000,
}

# Images per provider call. Anything else carrying an image is refused.
_IMAGE_LIMITS = {"vision": 2, "menu_ocr": 1}

INPUT_BUDGETS = {
    f: _env_positive(f"AI_INPUT_BUDGET_{f.upper()}", d) for f, d in _INPUT_DEFAULTS.items()
}
OUTPUT_BUDGETS = {
    f: _env_positive(f"AI_OUTPUT_BUDGET_{f.upper()}", d) for f, d in _OUTPUT_DEFAULTS.items()
}
IMAGE_LIMITS = {f: _IMAGE_LIMITS.get(f, 0) for f in FEATURES}
# Scanned-PDF pages sent to OCR per menu upload (each page is one menu_ocr call).
MENU_OCR_MAX_PAGES = _env_positive("AI_MENU_OCR_MAX_PAGES", 5)


def normalize_feature(feature):
    return feature if feature in FEATURES else "other"


# ── Bound ────────────────────────────────────────────────────────────────────

class UnboundablePayload(ValueError):
    """The payload has a part whose billable size this module cannot bound."""


def _plain(obj):
    """SDK response objects (tool_use blocks re-sent in the loop) -> plain data."""
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    return obj


def _strip_images(obj, counts):
    obj = _plain(obj)
    if isinstance(obj, dict):
        kind = obj.get("type")
        if kind == "image":
            source = obj.get("source") or {}
            if source.get("type") != "base64":
                # A URL/file image is fetched and sized by the provider; we
                # cannot see it, so we cannot bound it.
                raise UnboundablePayload("non-inline image source")
            counts["anthropic"] += 1
            return {"type": "image"}
        if kind == "image_url":
            detail = ((obj.get("image_url") or {}).get("detail") or "auto")
            counts["openai_low" if detail == "low" else "openai_high"] += 1
            return {"type": "image_url"}
        if kind in ("document", "file", "input_file", "input_audio"):
            raise UnboundablePayload(f"unsupported content block {kind!r}")
        return {k: _strip_images(v, counts) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_strip_images(v, counts) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    # Anything else is serialized by its str(): still counted, never dropped.
    return str(obj)


def input_upper_bound(payload):
    """Return (upper-bound input tokens, image count) for a provider payload."""
    counts = {"anthropic": 0, "openai_low": 0, "openai_high": 0}
    stripped = _strip_images(payload, counts)
    text_bytes = len(json.dumps(stripped, ensure_ascii=False).encode("utf-8"))
    images = counts["anthropic"] + counts["openai_low"] + counts["openai_high"]
    bound = (text_bytes + BASE_OVERHEAD_TOKENS
             + counts["anthropic"] * ANTHROPIC_IMAGE_TOKENS
             + counts["openai_low"] * OPENAI_IMAGE_TOKENS_LOW
             + counts["openai_high"] * OPENAI_IMAGE_TOKENS_HIGH)
    return bound, images


# ── Deterministic reduction ──────────────────────────────────────────────────

def history_reducer(first_index, history_len):
    """Reducer that drops the OLDEST conversation history, two messages a step.

    `first_index` is where history starts in `messages` (after any leading
    system messages); `history_len` is how many messages are history. Nothing
    else — system prompt, tool schemas, the current request, this turn's tool
    calls and results — is ever removed. The remaining history must still
    start with a user message, so an assistant message left at the front is
    dropped too. Returns None when no history is left to drop.
    """
    state = {"left": history_len}

    def reduce(payload):
        messages = payload.get("messages") or []
        if state["left"] <= 0:
            return None
        drop = min(2, state["left"])
        state["left"] -= drop
        del messages[first_index:first_index + drop]
        while (state["left"] > 0 and len(messages) > first_index
               and messages[first_index].get("role") == "assistant"):
            del messages[first_index]
            state["left"] -= 1
        payload["messages"] = messages
        return payload

    return reduce
