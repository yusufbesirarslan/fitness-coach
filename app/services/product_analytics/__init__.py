"""First-public privacy-first product analytics (v2), default OFF.

Architecture (see docs/product-analytics-v2/README.md):

* ``consent``  - the ONE independent optional analytics authority.
* ``emit``     - server-authoritative success events, emitted only after the
                 canonical writer committed (client projections never count).
* ``ingress``  - strict validation of client observation events.
* ``wire``     - the exact PostHog EU batch wire (allowlist only).
* ``dispatch`` - consent-fenced outbox dispatch and vendor erasure jobs.
* ``transport``- synthetic transport ONLY. There is deliberately no network
                 transport in this source: vendor activation is a separate,
                 reviewed change.

Nothing here runs while ``FITX_PRODUCT_ANALYTICS_ENABLED`` is OFF.
"""
FLAG = "FITX_PRODUCT_ANALYTICS_ENABLED"


def enabled():
    try:
        from flask import current_app
        return current_app.config.get(FLAG) is True
    except Exception:
        return False
