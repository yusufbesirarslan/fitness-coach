"""Opaque, owner-bound API identity for canonical ledger entries.

A later mobile PR has to address a persisted meal in order to edit or delete it,
and `MealLog.id` is not an API identity: it is sequential, so it leaks how many
rows exist and in what order they were written, it is the same integer for every
account, and it invites a client to do arithmetic on something the server may
reorganise. The mobile contract therefore publishes a token instead.

The token is DERIVED, not stored. `MealLog.id` already is a stable internal
identity, so persisting a second one would be schema churn for what is really a
naming problem — the rule the repository follows for exactly this case (see the
migration rule in docs/MOBILE_NUTRITION.md). The token is a keyed digest over
(user, entry): stable while `SECRET_KEY` is, opaque, and bound to its owner, so
user A's token for row N is not user B's token for row N and a token that leaks
cannot be replayed against another account's ledger.

Resolution (the future mutation PR) recomputes the digest over the authenticated
user's own candidate rows and compares with `hmac.compare_digest`. That is the
whole point of binding the owner in: the scan is user-scoped by construction, so
an unknown token cannot reveal whether some other account's entry exists.

Pure: stdlib only, no Flask, no ORM. The caller supplies the key.
"""
import base64
import hashlib
import hmac


# Domain separation. `SECRET_KEY` also signs browser cookies and CSRF tokens; a
# fixed label keeps this digest from ever colliding with those, and the trailing
# version lets a future format change be a new label rather than a silent
# reinterpretation of old tokens.
_SUBKEY_INFO = b"axisai/mobile-nutrition/diary-entry-id/v1"

# 144 bits — far past guessing range, and a multiple of 3 so base64url needs no
# padding and the token stays URL-safe for a later /entries/<id> route.
_TOKEN_BYTES = 18


def _subkey(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()


def diary_entry_id(secret, user_id, entry_id):
    """Return the opaque API identity of one ledger row, owner included."""
    message = f"{int(user_id)}\x00{int(entry_id)}".encode("ascii")
    digest = hmac.new(_subkey(secret), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_TOKEN_BYTES]).decode("ascii")


def matches_diary_entry_id(secret, user_id, entry_id, token):
    """Constant-time check that ``token`` addresses this user's ledger row.

    Not used by the read contract, and deliberately shipped with it: the read
    publishes identities that only mean something if the same rule can recognise
    them again, and pinning that rule in a test now is what makes the identity a
    contract rather than a string.
    """
    if not isinstance(token, str) or not token:
        return False
    return hmac.compare_digest(diary_entry_id(secret, user_id, entry_id), token)
