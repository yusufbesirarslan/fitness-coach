"""F8 — toast messages are text, never markup.

Three toast helpers used to interpolate ``msg`` into ``innerHTML``:
``static/nutrition.js`` and ``static/progress.js`` ``showToast`` and the
``templates/quests.html`` ``showToast``. Their callers pass server ``error``
strings and browser exception text (``res.json()`` on a non-JSON body quotes
the body), so the message must never cross an HTML parser boundary.

Hermetic: the real rendered pages and scripts, every request served by the
authenticated Flask test client. The server-derived proofs drive the real
callers (``submitCheckin`` / ``logMeal``) through the real Flask routes.
"""
import pytest

from app.blueprints import tracking
from app.blueprints.nutrition import meallog
from app.extensions import db
from app.models import User
from test_training_execution_boundary import training_page  # noqa: F401

PAGES = ("/nutrition", "/progress-page", "/quests")

IMG = '<img src=x onerror="window.__f8Pwned = true">'
BOLD = "<b>hello</b>"
LINK = '<a href="https://example.com">click</a>'
TURKISH = "İşlem başarıyla tamamlandı."

# The toast's message span and every element the toast contains, read the
# instant the toast exists (they auto-dismiss after ~3s).
READ_LAST_TOAST = r"""
() => {
  const toasts = document.querySelectorAll('#toast-wrap .toast');
  const t = toasts[toasts.length - 1];
  if (!t) return null;
  const spans = t.querySelectorAll(':scope > span');
  return {
    cls: t.className,
    children: [...t.children].map(c => c.tagName.toLowerCase() + (c.className ? '.' + c.className : '')),
    descendants: [...t.querySelectorAll('*')].map(e => e.tagName.toLowerCase()),
    icon: spans[0] ? spans[0].textContent : null,
    message: spans[1] ? spans[1].textContent : null,
    text: t.textContent,
    pwned: window.__f8Pwned === true,
  };
}
"""


def _ready(user_id):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = "en"
    db.session.commit()


def _open(page, path):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto("http://localhost" + path)
    page.wait_for_function("typeof showToast === 'function'")
    page.evaluate("document.getElementById('toast-wrap').replaceChildren()")
    return errors


def _toast(page, msg, kind):
    page.evaluate("([m, k]) => showToast(m, k)", [msg, kind])
    # A dropped onerror would fire asynchronously; give it the chance.
    page.wait_for_timeout(150)
    return page.evaluate(READ_LAST_TOAST)


@pytest.mark.parametrize("path", PAGES)
def test_markup_payloads_render_literally_and_create_no_elements(
    app, auth_user, training_page, path,
):
    page, _, _, _ = training_page
    with app.app_context():
        _ready(auth_user.id)
    errors = _open(page, path)

    for payload, forbidden in ((BOLD, "b"), (IMG, "img"), (LINK, "a")):
        got = _toast(page, payload, "error")
        assert got["message"] == payload
        assert payload in got["text"]
        # Only the two static chrome spans exist — nothing parsed from msg.
        assert got["descendants"] == ["span", "span"]
        assert forbidden not in got["descendants"]
        assert got["pwned"] is False
    assert page.evaluate("window.__f8Pwned") is None
    assert errors == []


@pytest.mark.parametrize("path,icons", (
    ("/nutrition", {"success": "✓", "error": "✕", "info": "ℹ"}),
    ("/progress-page", {"success": "✓", "error": "✗", "info": "ℹ"}),
    ("/quests", {"success": "✓", "error": "✗"}),
))
def test_ordinary_unicode_messages_keep_their_icon_chrome_and_visible_text(
    app, auth_user, training_page, path, icons,
):
    page, _, _, _ = training_page
    with app.app_context():
        _ready(auth_user.id)
    errors = _open(page, path)
    icon_class = "" if path == "/quests" else ".toast-icon"

    for kind, icon in icons.items():
        got = _toast(page, TURKISH, kind)
        assert got["cls"] == "toast toast-" + kind
        assert got["children"] == ["span" + icon_class, "span"]
        assert got["icon"] == icon
        assert got["message"] == TURKISH
        assert got["text"] == icon + TURKISH
    assert errors == []


@pytest.mark.parametrize("path", PAGES)
def test_missing_message_does_not_throw_or_print_undefined(
    app, auth_user, training_page, path,
):
    page, _, _, _ = training_page
    with app.app_context():
        _ready(auth_user.id)
    errors = _open(page, path)

    for missing in ("undefined", "null"):
        page.evaluate(f"showToast({missing}, 'error')")
        got = page.evaluate(READ_LAST_TOAST)
        assert got["message"] == ""
        assert "undefined" not in got["text"] and "null" not in got["text"]
    assert errors == []


def test_progress_checkin_server_error_travels_the_real_route_as_text(
    app, auth_user, training_page, monkeypatch,
):
    """Real caller (submitCheckin) -> real POST /checkin -> real `error` JSON."""
    page, traffic, _, _ = training_page
    with app.app_context():
        _ready(auth_user.id)
    real_t = tracking.t
    monkeypatch.setattr(
        tracking, "t",
        lambda key, **kw: IMG if key == "route.weight_range" else real_t(key, **kw))
    errors = _open(page, "/progress-page")

    # Out of range -> route.weight_range. The form sits in a collapsed section.
    page.evaluate("document.getElementById('ci-weight').value = '5'; submitCheckin()")
    page.wait_for_selector("#toast-wrap .toast-error")
    got = page.evaluate(READ_LAST_TOAST)
    page.wait_for_timeout(150)

    assert ("/checkin", 400) in [(p, s) for p, _, s in traffic]
    assert got["message"] == IMG
    assert "img" not in got["descendants"]
    assert page.evaluate("window.__f8Pwned") is None
    assert errors == []


def test_nutrition_meal_log_server_error_travels_the_real_route_as_text(
    app, auth_user, training_page, monkeypatch,
):
    """Real caller (logMeal) -> real POST /meal-log -> real `error` JSON."""
    page, traffic, _, _ = training_page
    with app.app_context():
        _ready(auth_user.id)
    real_t = meallog.t
    monkeypatch.setattr(
        meallog, "t",
        lambda key, **kw: LINK if key == "route.invalid_meal_slot" else real_t(key, **kw))
    errors = _open(page, "/nutrition")

    page.evaluate("""() => {
      selectedMealType = 'not-a-slot';
      document.getElementById('meal-input').value = 'yulaf';
      return logMeal();
    }""")
    got = page.evaluate(READ_LAST_TOAST)

    assert ("/meal-log", 400) in [(p, s) for p, _, s in traffic]
    assert got["cls"] == "toast toast-error"
    assert got["message"] == LINK
    assert "a" not in got["descendants"]
    assert errors == []


def test_nutrition_non_json_error_page_quoted_by_the_parser_stays_text(
    app, auth_user, training_page,
):
    """A proxy/edge HTML error body makes res.json() throw; V8 quotes the body
    in e.message, which logMeal prefixes and hands to showToast."""
    page, _, _, _ = training_page
    with app.app_context():
        _ready(auth_user.id)
    errors = _open(page, "/nutrition")
    page.route("**/meal-log", lambda route: route.fulfill(
        status=502, content_type="text/html", body="<img src=x><b>Bad gateway</b>"))

    page.evaluate("""() => {
      document.getElementById('meal-input').value = 'yulaf';
      return logMeal();
    }""")
    got = page.evaluate(READ_LAST_TOAST)

    assert got["cls"] == "toast toast-error"
    # V8 quotes a prefix of the body; the tag start arrives verbatim, as text.
    assert '"<img src=x' in got["message"]
    assert got["descendants"] == ["span", "span"]
    assert errors == []


def test_every_toast_helper_is_text_only_or_audited_safe():
    """Tripwire for the F8 audit: no toast helper writes its message via HTML.

    Exact set of helpers defined in production code at the time of F8; a new
    helper must be added here (and be text-only) deliberately.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    helper = re.compile(
        r"function\s+(showToast|toast)\s*\([^)]*\)\s*\{(?P<body>.*?)\n\s*\}\s*\n",
        re.S)
    found = {}
    for path in [*root.glob("static/*.js"), *root.glob("templates/*.html")]:
        for m in helper.finditer(path.read_text(encoding="utf-8")):
            found[f"{path.parent.name}/{path.name}:{m.group(1)}"] = m.group("body")
    assert set(found) == {
        "static/nutrition.js:showToast", "static/progress.js:showToast",
        "static/profile.js:toast", "templates/quests.html:showToast",
        "templates/challenges.html:toast", "templates/chat.html:toast",
        "templates/feed.html:toast", "templates/friends.html:toast",
        "templates/leaderboard.html:toast", "templates/manage_stack.html:toast",
        "templates/notifications.html:toast",
    }
    for name, body in found.items():
        assert "innerHTML" not in body and "insertAdjacentHTML" not in body, name
        assert "textContent" in body, name
