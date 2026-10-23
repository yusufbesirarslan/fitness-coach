"""Real EN/TR registration bundle, served by the hermetic browser harness."""
import json

import pytest
from ux4_gate_support import gate  # noqa: F401


@pytest.mark.parametrize("language", ["en", "tr"])
def test_contacts_appear_only_after_screen_and_are_removed_on_age_change(app, client, gate, language):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True
    with client.session_transaction() as session:
        session["lang"] = language
    gate.visit("/register", 320)
    page = gate.page
    assert page.locator("#email").count() == 0
    assert page.locator("#password").count() == 0
    page.locator("#eligibility-age").fill("12")
    page.locator("#eligibility-country").select_option("GB")
    page.locator('[data-action="checkEligibility"]').click()
    page.wait_for_function("document.querySelector('#error-msg').classList.contains('show')")
    assert page.locator("#email").count() == 0
    page.locator("#eligibility-age").fill("13")
    page.locator('[data-action="checkEligibility"]').click()
    page.locator("#email").wait_for()
    page.locator("#email").fill("fixture@example.invalid")
    page.locator("#eligibility-age").fill("12")
    assert page.locator("#email").count() == 0
    page.evaluate("window.register(null)")
    assert not any(path == "/register" and method == "POST" for path, method, _ in gate.traffic)
    assert not gate.errors


def test_expired_ticket_returns_to_precontact_without_retransmitting(app, gate):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True
    gate.overrides["/register"] = (422, json.dumps({"error": "Review age and country.",
                                                   "code": "AUTH_ELIGIBILITY_REQUIRED"}))
    # Visit real HTML first; override only subsequent POST, as the shared
    # harness override map deliberately does not infer request methods.
    gate.overrides.pop("/register")
    gate.visit("/register")
    page = gate.page
    page.locator("#eligibility-age").fill("18")
    page.locator("#eligibility-country").select_option("GB")
    page.locator('[data-action="checkEligibility"]').click()
    page.locator("#email").wait_for()
    page.locator("#username").fill("fixture")
    page.locator("#email").fill("fixture@example.invalid")
    page.locator("#password").fill("Sifre123")
    gate.overrides["/register"] = (422, json.dumps({"error": "Review age and country.",
                                                   "code": "AUTH_ELIGIBILITY_REQUIRED"}))
    page.locator("#register-submit").click()
    page.wait_for_function("!document.querySelector('#email')")
    page.evaluate("window.register(null)")
    assert sum(path == "/register" and method == "POST" for path, method, _ in gate.traffic) == 1
    assert not gate.errors


def test_late_preview_for_previous_inputs_cannot_expose_contact_form(app, gate):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True
    gate.visit("/register")
    page = gate.page
    page.evaluate("""() => {
      const original = window.fetch;
      window.resolveEligibility = null;
      window.fetch = (url, init) => url === '/eligibility'
        ? new Promise(resolve => { window.resolveEligibility = resolve; })
        : original(url, init);
    }""")
    page.locator("#eligibility-age").fill("18")
    page.locator("#eligibility-country").select_option("GB")
    page.locator('[data-action="checkEligibility"]').click()
    page.wait_for_function("window.resolveEligibility !== null")
    page.locator("#eligibility-age").fill("12")
    page.evaluate("""() => window.resolveEligibility(new Response(JSON.stringify({
      eligibility: {status:'screen_passed',age_band:'18_plus',country:'GB',reason:null,
      capabilities:{body_photos:true,automated_weight_targets:true,social:false},
      registration_token:'stale-fixture'}
    }), {status:200,headers:{'Content-Type':'application/json'}}))""")
    page.wait_for_function("!document.querySelector('[data-action=checkEligibility]').disabled")
    assert page.locator("#email").count() == 0
    assert not gate.errors
