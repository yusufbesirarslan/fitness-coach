"""Explicit synthetic authority for pre-existing layer-isolation tests.

These suites qualify payload minimization, budget/retry behavior and domain
transactions under an ALLOW decision; they do not qualify D1 consent. D1's
separate tests exercise the real authority, default deny and PostgreSQL fence.
Never imported by application code; no runtime allow-on-error or test flag.
"""
from contextlib import nullcontext


def allow_synthetic_authority(monkeypatch, app=None, *, memory=True):
    from app.services import ai_consent as consent
    from app.blueprints import ai_consent as routes
    monkeypatch.setattr(consent, 'BINDINGS', {**consent.BINDINGS, **{
        f: ('synthetic.layer-test', ()) for f in
        ('coach', 'summary', 'nutrition', 'menu_extract', 'menu_ocr', 'vision', 'health_probe', 'other')
    }})
    monkeypatch.setattr(consent, 'generation', lambda owner: 0)
    monkeypatch.setattr(consent, 'dispatch', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(consent, 'stream_dispatch', lambda *a, **kw: nullcontext())
    if memory:
        monkeypatch.setattr(consent, 'memory_available', lambda owner: True)
    monkeypatch.setattr(routes, '_CLOSED_ENDPOINTS', frozenset())
    monkeypatch.setattr(routes, 'require_legacy_ai', lambda *a, **kw: None)
    if app is not None and memory:
        app.config['AI_MEMORY_ENABLED'] = True
