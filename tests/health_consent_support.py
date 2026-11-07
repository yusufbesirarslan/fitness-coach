"""Keep the first-party authority cost explicit in existing domain SQL budgets."""
def domain_statements(statements):
    authority = [s for s in statements if "from health_consent_state" in s.lower()]
    assert len(authority) == 1, authority
    return [s for s in statements if s not in authority]


def grant_synthetic_health(monkeypatch, owners):
    """Prospective fixture decisions through the real authority, not D1."""
    from app.services import health_consent, health_disclosure
    from tests.test_health_consent import command
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", health_disclosure.VERSION)
    for owner in owners:
        health_consent.mutate(owner, command())


def track_response_cleanup(app):
    """Model WSGI server close at test end without changing callback timing."""
    import weakref
    live = weakref.WeakSet()
    application = app.wsgi_app
    def track(environ, start_response):
        result = application(environ, start_response)
        if hasattr(result, "close"):
            live.add(result)
        return result
    app.wsgi_app = track
    def cleanup():
        for iterable in list(live):
            iterable.close()
    return cleanup
