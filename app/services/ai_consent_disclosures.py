"""Source-owned disclosure text projection for D1, never a policy authority.

POLICIES in ai_consent remains the only authority. This registry deliberately
ships empty until exact EN/TR copy and recipient facts are approved in source.
Draft copy belongs in docs, not here. Environment and request values cannot
publish text or qualify a policy.
"""
from hashlib import sha256
from types import MappingProxyType

from app.services import ai_consent


# (version, locale) -> exact, reviewed UTF-8 text, without newline normalization.
PUBLISHED_TEXTS = MappingProxyType({})
LOCALES = frozenset({"en", "tr"})


def localized_texts(policy):
    """Return an EN/TR pair only when both match the immutable D1 contract.

    No fallback to another language, draft version, remote page or runtime
    setting. A partial/misconfigured release offers no affirmative action.
    """
    if ai_consent.POLICIES.get(policy.version) != policy:
        return None
    expected = dict(policy.texts)
    if set(expected) != LOCALES or len(policy.texts) != len(LOCALES):
        return None
    texts = {}
    for locale in sorted(LOCALES):
        body = PUBLISHED_TEXTS.get((policy.version, locale))
        if (not isinstance(body, str) or not body.strip()
                or sha256(body.encode("utf-8")).hexdigest() != expected[locale]):
            return None
        texts[locale] = body
    return texts


def available_disclosures():
    """D1's public metadata plus the exact reviewed text a client must display.

    Integration seam for ai_consent._public, pending that owner's handoff.
    This helper cannot grant, authorize dispatch, or bypass the endpoint gate.
    """
    disclosures = []
    for policy in ai_consent.POLICIES.values():
        texts = localized_texts(policy)
        if texts is None:
            continue
        disclosures.append({
            "version": policy.version,
            "purpose": policy.purpose,
            "categories": list(policy.categories),
            "recipients": [list(recipient) for recipient in policy.recipients],
            "contract_digest": ai_consent.contract_digest(policy),
            "texts": dict(policy.texts),
            "localized_texts": texts,
        })
    return disclosures
