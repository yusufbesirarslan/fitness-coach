"""Self-declared adult boundary at the browser wizard, without provider calls."""
import pytest
from ux4_gate_support import gate, ready  # noqa: F401


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_underage_cannot_advance_but_eighteen_can(app, auth_user, gate, language):
    ready(app, auth_user.id, language)
    gate.visit('/setup?yeniden=1', 390)
    page = gate.page
    page.locator('#s-weight').fill('80')
    page.locator('#s-height').fill('180')
    assert page.locator('#s-age').get_attribute('min') == '18'
    page.locator('#s-age').fill('17')
    page.evaluate('window.nextStep(0)')
    assert page.locator('#step-0').is_visible()
    error = page.locator('#error-msg')
    assert error.is_visible() and '18' in error.inner_text()
    assert ('adults' if language == 'en' else 'yetişkinler') in error.inner_text()
    page.locator('#s-age').fill('18')
    page.evaluate('window.nextStep(0)')
    assert page.locator('#step-1').is_visible()
    assert not gate.errors
