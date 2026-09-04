"""One bounded, PII-free vocabulary for "why did a model provider call fail".

Two places need this answer and neither may improvise it: the Coach's Bedrock→
OpenAI fallback (which used to log *that* it fell back and never *why*) and the
deep-health Bedrock reachability probe.

The rule is the same as ``coach_plan_tools.executor._log``: what reaches a log
line comes from a closed server-owned vocabulary, never from provider prose.
A provider exception message is untrusted, unbounded text — for a Bedrock 403
it carries the caller's IAM ARN and the full resource ARN, and for other errors
it can carry echoed request content. Classifying by exception *class* and HTTP
*status* keeps the diagnostic ("the principal is not allowed to invoke this
model") while leaking neither.

The categories are chosen so that the failure that actually happened in
production — an ``AccessDenied`` on ``bedrock:InvokeModel`` — is distinguishable
at a glance from a timeout or a throttle, because those three demand completely
different operator responses and the old log line made all of them look alike.
"""

#: Closed set. Anything unrecognised is ``UNKNOWN`` — never the raw message.
ACCESS_DENIED = "access_denied"
UNAUTHENTICATED = "unauthenticated"
THROTTLED = "throttled"
TIMEOUT = "timeout"
CONNECTION = "connection_error"
MODEL_NOT_FOUND = "model_not_found"
BAD_REQUEST = "bad_request"
PROVIDER_ERROR = "provider_error"
EMPTY_RESPONSE = "empty_response"
UNKNOWN = "unknown"

CATEGORIES = frozenset({
    ACCESS_DENIED, UNAUTHENTICATED, THROTTLED, TIMEOUT, CONNECTION,
    MODEL_NOT_FOUND, BAD_REQUEST, PROVIDER_ERROR, EMPTY_RESPONSE, UNKNOWN,
})

#: Status code → category, for SDKs that expose one. Checked before the class
#: name because a status is the provider's own answer; a class name is the
#: SDK's rendering of it.
_BY_STATUS = {
    401: UNAUTHENTICATED,
    403: ACCESS_DENIED,
    404: MODEL_NOT_FOUND,
    408: TIMEOUT,
    422: BAD_REQUEST,
    429: THROTTLED,
}

#: Substring of the exception class name → category. Ordered: the first match
#: wins, so the more specific names are listed first.
_BY_CLASS_NAME = (
    ("permissiondenied", ACCESS_DENIED),
    ("accessdenied", ACCESS_DENIED),
    ("authentication", UNAUTHENTICATED),
    ("ratelimit", THROTTLED),
    ("throttl", THROTTLED),
    ("timeout", TIMEOUT),
    ("connection", CONNECTION),
    ("notfound", MODEL_NOT_FOUND),
    ("badrequest", BAD_REQUEST),
    ("unprocessable", BAD_REQUEST),
    ("internalserver", PROVIDER_ERROR),
    ("serviceunavailable", PROVIDER_ERROR),
    ("apistatus", PROVIDER_ERROR),
)


def exception_class(exc):
    """The exception's class name, or ``"none"`` when there is no exception.

    A class name is a code identifier, not user or provider content, so it is
    safe to log verbatim — and it is the one detail that lets an operator find
    the raising site without reproducing the failure.
    """
    return "none" if exc is None else type(exc).__name__


def classify(exc):
    """Return one member of ``CATEGORIES`` for ``exc``.

    Never raises and never returns anything derived from the exception's
    message. ``None`` classifies as ``UNKNOWN`` rather than as a success:
    this function answers "which failure", and the caller decides whether one
    occurred at all.
    """
    if exc is None:
        return UNKNOWN
    try:
        status = getattr(exc, "status_code", None)
        if not isinstance(status, bool) and isinstance(status, int):
            if status in _BY_STATUS:
                return _BY_STATUS[status]
            if status >= 500:
                return PROVIDER_ERROR
        name = type(exc).__name__.casefold()
        for needle, category in _BY_CLASS_NAME:
            if needle in name:
                return category
    except Exception:
        # A hostile or exotic exception object must not turn an already-failed
        # provider call into a second, unrelated failure at the log site.
        return UNKNOWN
    return UNKNOWN
