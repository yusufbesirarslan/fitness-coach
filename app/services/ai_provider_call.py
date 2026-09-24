"""The one door to a paid provider: final payload -> input check -> charge -> attempt.

Every paid provider call in the app goes through `admit()` (a static test in
tests/test_ai_provider_call.py fails if any module calls an SDK entry point
directly). The order is structural, not a convention at each call site:

    1. the caller assembles the FINAL request kwargs
    2. admit() deep-copies them: later mutation of the caller's lists (the
       tool loop appends to its conversation) cannot reach the checked copy
    3. hard input budget on that copy (`ai_input_budget`), with the caller's
       deterministic reducer applied and the result re-counted
    4. capacity permit (`ai_gate.model_concurrency_slot`), which charges the
       spend guard for the FIRST attempt (#337 semantics unchanged)
    5. the provider call, made with exactly the checked copy; the only extra
       argument a caller may add is the transport `timeout`

A payload refused at step 3 never takes a permit, never consumes spend-guard
budget and never reaches the provider, its fallback, or a retry.

Attempts. The SDK clients are built with max_retries=0 (app/extensions.py), so
one SDK call is one physical HTTP attempt. Transient failures (connection
refused/reset, 408/409/429/5xx — what the SDK used to retry by itself) are
retried HERE, up to the provider's retry count, and every retry is charged to
the spend guard like a new call before it is sent. So one spend-guard unit is
exactly one physical provider attempt, and a retry refused by the guard ends
the call. Timeouts are NOT retried here: a timed-out request may already be
generating (and billing) on the provider side, so repeating it is the costly
case; the higher-level recovery ladder (`ai_recovery`) decides that, and each
of its attempts is a fresh admission.
"""
import copy
import logging
import random
import time
from contextlib import contextmanager

from app.config import BEDROCK_MAX_RETRIES
from app.services import ai_input_budget, ai_spend_guard, ai_usage
from app.services.ai_gate import model_concurrency_slot
from app.services.ai_model_policy import policy_for_model_id

_log = logging.getLogger(__name__)

_RETRY_BASE_SECONDS = 0.5
_RETRY_MAX_SECONDS = 4.0
_RETRYABLE_STATUS = frozenset({408, 409, 429})
_TRANSPORT_KWARGS = frozenset({"timeout"})


class AIInputBudgetExceeded(ai_spend_guard.AISpendLimitExceeded):
    """The final payload is over its feature's hard input budget.

    A spend refusal like any other: the same existing handlers turn it into
    the localized capacity/soft-error response, and none of them falls back to
    the other provider or retries. The message never states the budget.
    """

    def __init__(self, feature, provider_class):
        super().__init__("input", provider_class)
        self.feature = feature


def _max_attempts(policy):
    # One transport. Extra attempts after the first are each charged.
    # The SDK client is built with max_retries=0; this is the only retry.
    if policy.transport != "anthropic_bedrock":
        raise RuntimeError("unsupported model transport")
    return 1 + max(0, int(BEDROCK_MAX_RETRIES))


def _is_timeout(exc):
    return "Timeout" in type(exc).__name__


def _is_retryable(exc):
    if _is_timeout(exc) or isinstance(exc, ai_spend_guard.AISpendLimitExceeded):
        return False
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS or status >= 500
    # Connection refused/reset. Both SDKs derive their timeout class from
    # APIConnectionError, which is why the timeout test above must come first.
    return any(cls.__name__ == "APIConnectionError" for cls in type(exc).__mro__)


def _outcome_of(exc):
    if _is_timeout(exc):
        return "timeout"
    if isinstance(exc, GeneratorExit):
        return "client_disconnect"
    return "provider_error"


def _record_rejection(feature, policy, bound, subject):
    _log.warning("[AI-BUDGET] input_budget_exceeded provider=%s model=%s feature=%s class=%s",
                 policy.billing_provider, policy.telemetry_model, feature, policy.spend_class)
    ai_usage.emit(feature=feature, provider=policy.billing_provider, model=policy.model_id,
                  outcome="input_budget_rejected", subject_id=subject,
                  input_bound=bound, spend_class=policy.spend_class,
                  billing_provider=policy.billing_provider)
    try:
        from app.services import runtime_metrics
        runtime_metrics.increment(
            "AiInputBudgetRejections",
            dimensions={"Class": policy.spend_class})
    except Exception:
        pass


def check_payload(feature, provider, payload, reduce=None):
    """Validate (and if needed deterministically reduce) a FROZEN payload.

    Returns ``(payload, bound, images)``; raises AIInputBudgetExceeded.
    ``provider`` is the gate label and is not used to classify spend.
    Separate from admit() only so the policy is testable on its own; admit()
    is the one production caller.
    """
    policy = policy_for_model_id(payload.get("model"))
    feature = ai_input_budget.normalize_feature(feature)
    budget = ai_input_budget.INPUT_BUDGETS[feature]
    subject = ai_spend_guard.current_subject()

    max_tokens = payload.get("max_tokens")
    if (not isinstance(max_tokens, int) or max_tokens <= 0
            or max_tokens > ai_input_budget.OUTPUT_BUDGETS[feature]):
        # Output is half the per-call cost; an uncapped or over-cap request is
        # a code defect, refused before anything is spent.
        _record_rejection(feature, policy, None, subject)
        raise AIInputBudgetExceeded(feature, policy.spend_class)

    try:
        bound, images = ai_input_budget.input_upper_bound(payload)
    except ai_input_budget.UnboundablePayload:
        _record_rejection(feature, policy, None, subject)
        raise AIInputBudgetExceeded(feature, policy.spend_class)

    while bound > budget and reduce is not None:
        reduced = reduce(payload)
        if reduced is None:
            break
        payload = reduced
        bound, images = ai_input_budget.input_upper_bound(payload)

    if bound > budget or images > ai_input_budget.IMAGE_LIMITS[feature]:
        _record_rejection(feature, policy, bound, subject)
        raise AIInputBudgetExceeded(feature, policy.spend_class)
    return payload, bound, images


class Admission:
    """One admitted logical provider call. Single use."""

    def __init__(self, *, feature, provider, payload, bound, images, charge,
                 deadline, tool_round, subject, policy):
        self.feature = feature
        self.provider = provider
        self.policy = policy
        self.family = policy.billing_provider
        self.spend_class = policy.spend_class
        self._payload = payload
        self.input_bound = bound
        self.images = images
        self._charge = charge
        self._deadline = deadline
        self.tool_round = tool_round
        self.subject = subject
        self._used = False

    @property
    def model(self):
        return self._payload.get("model")

    def _take(self, transport):
        if self._used:
            raise RuntimeError("an admission covers exactly one provider call")
        self._used = True
        extra = set(transport) - _TRANSPORT_KWARGS
        if extra:
            # Anything token-bearing must be in the payload admit() checked.
            raise TypeError(f"non-transport kwargs after admission: {sorted(extra)}")
        return {k: v for k, v in transport.items() if v is not None}

    def _emit(self, outcome, attempt, usage=None, source=None):
        if usage is None and outcome != "success":
            # Unknown provider-side cost: record the input upper bound, marked
            # as an estimate, rather than claiming zero.
            usage, source = {"input_tokens": self.input_bound}, "estimated"
        ai_usage.emit(
            feature=self.feature, provider=self.family, model=self.model,
            outcome=outcome, attempt=attempt, tool_round=self.tool_round,
            subject_id=self.subject, usage=usage, usage_source=source,
            input_bound=self.input_bound, image_units=self.images,
            output_cap=self._payload.get("max_tokens"),
            spend_class=self.spend_class, billing_provider=self.policy.billing_provider)

    def _emit_guard_rejection(self):
        ai_usage.emit(feature=self.feature, provider=self.family, model=self.model,
                      outcome="guard_rejected", tool_round=self.tool_round,
                      subject_id=self.subject, input_bound=self.input_bound,
                      image_units=self.images, spend_class=self.spend_class,
                      billing_provider=self.policy.billing_provider)

    def _remaining(self):
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    def create(self, method, **transport):
        """Blocking call with charged, bounded transient retries."""
        transport = self._take(transport)
        attempts = _max_attempts(self.policy)
        for attempt in range(1, attempts + 1):
            if attempt > 1 and self._charge:
                # A retry is a new paid attempt: admitted like one. A refusal
                # propagates — the guard's "stop" is never retried.
                try:
                    ai_spend_guard.charge(self.provider, spend_class=self.spend_class)
                except ai_spend_guard.AISpendLimitExceeded:
                    self._emit_guard_rejection()
                    raise
            kwargs = copy.deepcopy(self._payload)
            try:
                resp = method(**kwargs, **transport)
            except Exception as exc:
                self._emit(_outcome_of(exc), attempt)
                remaining = self._remaining()
                delay = min(_RETRY_MAX_SECONDS, _RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
                delay *= 0.5 + random.random() * 0.5
                if (attempt < attempts and _is_retryable(exc)
                        and (remaining is None or remaining > delay + 1.0)):
                    time.sleep(delay)
                    if remaining is not None and "timeout" in transport:
                        transport["timeout"] = min(transport["timeout"],
                                                   max(1.0, self._remaining()))
                    continue
                raise
            usage = ai_usage.usage_from_response(self.family, resp)
            self._emit("success", attempt, usage, "provider" if usage else None)
            return resp
        raise RuntimeError("unreachable")  # pragma: no cover

    @contextmanager
    def stream(self, method, **transport):
        """Streaming call: exactly one attempt (a stream is not replayable)."""
        transport = self._take(transport)
        holder = _StreamUsage()
        try:
            with method(**copy.deepcopy(self._payload), **transport) as stream:
                yield _StreamProxy(stream, holder)
        except BaseException as exc:
            self._emit(_outcome_of(exc), 1)
            raise
        if holder.final is None:
            self._emit("client_disconnect", 1)
        else:
            usage = ai_usage.usage_from_response(self.family, holder.final)
            self._emit("success", 1, usage, "provider" if usage else None)


class _StreamUsage:
    final = None


class _StreamProxy:
    """The SDK stream, with get_final_message() observed for usage."""

    def __init__(self, stream, holder):
        self._stream = stream
        self._holder = holder

    def get_final_message(self):
        final = self._stream.get_final_message()
        self._holder.final = final
        return final

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextmanager
def admit(*, feature, provider, payload, deadline=None, tool_round=None,
          reduce=None, gate=True, charge=True):
    """Admit one provider call; yields an `Admission` to make it with.

    ``provider`` is the gate label ("bedrock" or "bedrock-stream"). Spend
    class comes from the model policy on ``payload["model"]``, not from this
    label. ``gate=False`` skips the capacity permit but still charges (menu
    OCR). ``charge=False`` is reserved for the deep-health probe.
    """
    feature = ai_input_budget.normalize_feature(feature)
    frozen = copy.deepcopy(payload)
    policy = policy_for_model_id(frozen.get("model"))
    frozen, bound, images = check_payload(feature, provider, frozen, reduce=reduce)
    subject = ai_spend_guard.current_subject()
    admission = Admission(
        feature=feature, provider=provider, payload=frozen, bound=bound,
        images=images, charge=charge, deadline=deadline, tool_round=tool_round,
        subject=subject, policy=policy)
    if gate:
        try:
            with model_concurrency_slot(provider, deadline=deadline,
                                        spend_class=policy.spend_class):
                yield admission
        except ai_spend_guard.AISpendLimitExceeded as exc:
            if not admission._used and not isinstance(exc, AIInputBudgetExceeded):
                admission._emit_guard_rejection()
            raise
        return
    if charge:
        try:
            ai_spend_guard.charge(provider, spend_class=policy.spend_class)
        except ai_spend_guard.AISpendLimitExceeded:
            admission._emit_guard_rejection()
            raise
    yield admission

