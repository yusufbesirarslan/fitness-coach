"""Signed transport of the accepted exercise context from generate to save.

Plan generation derives one server-owned ``ExerciseContext`` from an
accepted preference set and builds the plan against exactly that equipment
truth. Saving happens later, over a separate call, and has to re-check the
same truth before it destroys the user's stored plan — but the equipment
context is not part of the plan document and must never be re-declared by
the caller, or "home workout" becomes a field the browser fills in.

This module is that carrier: a short, opaque, domain-separated HMAC token
the caller holds in memory between the two calls and hands back untouched.
It is an INTEGRITY device, not a capability grant — it proves the server
itself accepted this context for this user, and nothing more. Everything it
carries is still re-checked against the catalog on arrival, and the plan's
exercises are still resolved from scratch.

Deliberately narrow: standard-library crypto only (``hmac``/``hashlib``/
``base64``), no expiry or replay store, no transport concerns, no user-facing
copy, no diagnostics. The one failure type is ``ExerciseContextInvalid``; the
save layer is what turns it into an answer over the wire — so every rejection
path here, including a malformed charset, must raise that one type and never
an incidental ``UnicodeEncodeError``/``TypeError`` the save layer would not
catch. Nothing here ever emits the token or the decoded payload — a rejected
token is simply rejected, with no echo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

from app.services.exercise_catalog import (
    CARDIO_EQUIPMENT,
    CONTEXT_EQUIPMENT,
    ExerciseContext,
    load_exercise_catalog,
)
from app.services.training_generation.preference_contract import (
    STYLE_INPUT_ALIASES,
    STYLE_RULE_KEYS,
)


TOKEN_VERSION = 1
PAYLOAD_KEYS = frozenset({"v", "uid", "eq", "cardio", "style", "catalog"})
# A valid token is ~170 characters. The cap exists so a hostile body is
# rejected on sight instead of being base64-decoded and JSON-parsed first.
MAX_TOKEN_CHARS = 512

# Explicit domain separation: this key is also the session/CSRF secret, so
# the signed message is prefixed with what it is FOR. A signature minted for
# any other purpose can never be replayed as an exercise context.
_DOMAIN = b"axisai.training.exercise_context"
_SEPARATOR = b"\x00"
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")

# Closed vocabularies, owned elsewhere and only referenced here. Equipment and
# cardio come from the catalog (the compatibility authority); style comes from
# the preference contract, in both the UI-token form the generate route signs
# and the rule-key form the domain layer uses. A second copy of any of these
# would be a second, drifting authority.
EQUIPMENT_CONTEXTS = frozenset(CONTEXT_EQUIPMENT)
CARDIO_TYPES = frozenset(CARDIO_EQUIPMENT) | frozenset({"yok"})
STYLES = frozenset(STYLE_INPUT_ALIASES.values()) | frozenset(STYLE_RULE_KEYS.values())


class ExerciseContextInvalid(ValueError):
    """The exercise context could not be signed, or could not be trusted back.

    One type for every rejection reason on purpose: the caller must not be
    able to tell a bad signature from a wrong user from an unknown equipment
    token, because that difference is an oracle.
    """


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _checked_b64url(segment: str) -> str:
    """Reject anything that is not base64url before it is used at all.

    Charset-checking is the gate in front of BOTH decoding and signing: a
    non-ASCII character reaching ``_signature``'s strict ASCII encode would
    raise ``UnicodeEncodeError`` — a ``ValueError``, but not an
    ``ExerciseContextInvalid``, so it would escape the typed contract as a
    500 and put the raw token into the error store's frame locals.
    """
    if not isinstance(segment, str) or not _B64URL.fullmatch(segment):
        raise ExerciseContextInvalid("token segment is not base64url")
    return segment


def _b64decode(segment: str) -> bytes:
    # Character-checked before decoding: base64 is lenient about junk, and a
    # "successfully decoded" malformed segment is a parsing surface we do not
    # want behind the signature check.
    _checked_b64url(segment)
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ExerciseContextInvalid("token segment is not decodable") from exc


def _secret_bytes(secret_key) -> bytes:
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    if not isinstance(secret_key, bytes) or not secret_key:
        raise ExerciseContextInvalid("signing key is unusable")
    return secret_key


def _signature(secret_key, version_segment: str, payload_segment: str) -> str:
    message = _SEPARATOR.join((
        _DOMAIN,
        version_segment.encode("ascii", "strict"),
        payload_segment.encode("ascii", "strict"),
    ))
    digest = hmac.new(_secret_bytes(secret_key), message, hashlib.sha256).digest()
    return _b64encode(digest)


def _exact_int(value) -> int:
    # ``bool`` is an ``int`` subclass; True must never pass as user 1.
    if type(value) is not int:
        raise ExerciseContextInvalid("value is not an integer")
    return value


def _checked_context(context: ExerciseContext) -> ExerciseContext:
    """Reject any context outside the closed server vocabulary, fail-closed."""
    if not isinstance(context, ExerciseContext):
        raise ExerciseContextInvalid("context is not an ExerciseContext")
    if context.equipment_context not in EQUIPMENT_CONTEXTS:
        raise ExerciseContextInvalid("unknown equipment context")
    if context.cardio_type not in CARDIO_TYPES:
        raise ExerciseContextInvalid("unknown cardio type")
    if context.style not in STYLES:
        raise ExerciseContextInvalid("unknown program style")
    if _exact_int(context.catalog_version) != load_exercise_catalog().version:
        raise ExerciseContextInvalid("catalog version mismatch")
    return context


def sign_exercise_context(context: ExerciseContext, secret_key, user_id) -> str:
    """Mint the token that carries ``context`` back for exactly ``user_id``.

    Signing validates the context first: a token the server could never
    verify would turn a generation-time configuration slip into a save-time
    failure the user cannot act on.
    """
    checked = _checked_context(context)
    payload = {
        "v": TOKEN_VERSION,
        "uid": _exact_int(user_id),
        "eq": checked.equipment_context,
        "cardio": checked.cardio_type,
        "style": checked.style,
        "catalog": checked.catalog_version,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    version_segment = str(TOKEN_VERSION)
    payload_segment = _b64encode(body.encode("utf-8"))
    signature = _signature(secret_key, version_segment, payload_segment)
    return f"{version_segment}.{payload_segment}.{signature}"


def verify_exercise_context(token, secret_key, user_id) -> ExerciseContext:
    """Return the context this server signed for this user, or fail closed.

    Order matters: shape and version are settled before anything else, both
    remaining segments are charset-checked before the signature is computed
    over them, the signature is settled before any payload is decoded or
    interpreted, and the payload is re-checked against the closed vocabulary
    even after the signature passed — a token signed under an older
    vocabulary is still not authority.
    """
    if not isinstance(token, str):
        raise ExerciseContextInvalid("token is not a string")
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise ExerciseContextInvalid("token length is out of bounds")
    segments = token.split(".")
    if len(segments) != 3 or not all(segments):
        raise ExerciseContextInvalid("token shape is invalid")
    version_segment, payload_segment, signature_segment = segments

    # Version first: an unrecognized version is refused before this code
    # tries to interpret bytes written under rules it does not know.
    if version_segment != str(TOKEN_VERSION):
        raise ExerciseContextInvalid("unsupported token version")

    # Charset before signature: ``_signature`` encodes the payload segment as
    # strict ASCII, so a non-ASCII character has to be refused HERE or it
    # leaves this module as an untyped UnicodeEncodeError.
    _checked_b64url(payload_segment)
    _checked_b64url(signature_segment)

    expected = _b64decode(_signature(secret_key, version_segment, payload_segment))
    provided = _b64decode(signature_segment)
    if not hmac.compare_digest(expected, provided):
        raise ExerciseContextInvalid("token signature does not match")

    try:
        payload = json.loads(_b64decode(payload_segment).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExerciseContextInvalid("token payload is not readable") from exc
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise ExerciseContextInvalid("token payload keys are not canonical")
    if _exact_int(payload["v"]) != TOKEN_VERSION:
        raise ExerciseContextInvalid("unsupported payload version")
    if _exact_int(payload["uid"]) != _exact_int(user_id):
        raise ExerciseContextInvalid("token belongs to another user")

    return _checked_context(ExerciseContext(
        equipment_context=payload["eq"],
        cardio_type=payload["cardio"],
        style=payload["style"],
        catalog_version=payload["catalog"],
    ))
