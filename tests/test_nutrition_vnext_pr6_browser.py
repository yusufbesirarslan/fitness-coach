"""NUTR-PR6 — Next step + "Review with AxisAI" in a real browser.

The real page and shipped scripts run against the authenticated Flask client
(`gate`). Server failures are injected in the real service; HTTP failures and
forged bodies per request. Every request count is read from the recorded
traffic, never assumed.

  next_action is server-owned and allowlisted · one day-view read at load,
  none on redraw, none on the chooser, no polling · failure is never empty ·
  older answers never overwrite newer ones · Review opens Coach with a
  constant marker only, sends nothing, and the first explicit Send carries it
"""
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from playwright.sync_api import expect

from ux4_gate_support import gate, ready  # noqa: F401

from app.blueprints import coach as coach_bp
from app.extensions import db
from app.models import UserSession
from app.services import nutrition_day_view as dv
from test_nutrition_vnext_pr6_coach_handoff import seed

CATALOG = {lang: json.loads((Path(__file__).resolve().parent.parent / 'locales' / f'{lang}.json')
                            .read_text(encoding='utf-8')) for lang in ('en', 'tr')}
INITIAL = Counter({'/nutrition': 1, '/meal-log/today': 1, '/nutrition-plan/active': 1,
                   '/water': 1, '/notifications/unread-count': 1, '/coach/history': 1,
                   '/nutrition-day-view': 1})
WIDTHS = (320, 390, 430, 768, 1024, 1366)


@pytest.fixture(autouse=True)
def close_adapter_responses(client, monkeypatch):
    """The browser adapter buffers responses; close them to release SSE slots."""
    original = client.open

    def buffered_open(*args, **kwargs):
        response = original(*args, **kwargs)
        response.get_data()
        response.close()
        return response

    monkeypatch.setattr(client, 'open', buffered_open)


def start(app, make_user, login, name, language='en', **seed_kw):
    user = make_user(name, profile_complete=True)
    ready(app, user.id, language)
    seed(user.id, **seed_kw)
    login(name)
    return user


def open_nutrition(gate, width=390):  # noqa: F811
    gate.visit('/nutrition', width=width)
    expect(gate.page.locator('#nut-next')).not_to_have_attribute('data-next-state', 'loading')
    return gate.page


def reads(gate, path):  # noqa: F811
    return [p for p, _m, _s in gate.traffic if p == path]


def t(lang, key):
    return CATALOG[lang][key]


# ── NEXT STEP: ALLOWLISTED SERVER ACTIONS ───────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_log_food_next_step_opens_the_existing_chooser(app, gate, make_user, login, language):  # noqa: F811
    start(app, make_user, login, 'pr6b_log_' + language, language)
    page = open_nutrition(gate)
    box = page.locator('#nut-next')
    expect(box).to_have_attribute('data-next-state', 'available')
    expect(box).to_have_attribute('data-next-kind', 'log_food')
    expect(page.locator('#nut-next-title')).to_have_text(t(language, 'nutrition.next.title'))
    expect(page.locator('#nut-next-lead')).to_have_text(t(language, 'nutrition.next.log_food_lead'))
    button = page.locator('#nut-next-action')
    expect(button).to_have_text(t(language, 'nutrition.next.log_food'))
    expect(page.locator('#nut-next-link')).to_be_hidden()
    assert len(reads(gate, '/nutrition-day-view')) == 1
    gate.traffic.clear()
    button.focus()
    page.keyboard.press('Enter')                                   # keyboard activation
    expect(page.locator('#log-sheet')).to_have_class(re.compile(r'\bopen\b'))
    assert gate.app_reads() == []                                  # opening writes/reads nothing
    page.keyboard.press('Escape')
    expect(page.locator('#log-sheet')).not_to_have_class(re.compile(r'\bopen\b'))
    expect(button).to_be_focused()
    assert gate.app_reads() == []
    assert gate.errors == []


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_missing_target_maps_to_the_existing_onboarding_form(app, gate, client, make_user, login,
                                                            language):  # noqa: F811
    user = start(app, make_user, login, 'pr6b_tgt_' + language, language, target=None)
    page = open_nutrition(gate)
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'set_target')
    expect(page.locator('#nut-next-lead')).to_have_text(t(language, 'nutrition.next.set_target_lead'))
    link = page.locator('#nut-next-link')
    expect(link).to_be_visible()
    expect(link).to_have_text(t(language, 'nutrition.next.set_target'))
    assert link.get_attribute('href') == '/setup?yeniden=1'
    expect(page.locator('#nut-next-action')).to_be_hidden()
    # "Target not set yet" (Today) and Set target agree; the target is not invented.
    expect(page.locator('#nut-day')).to_have_attribute('data-target-state', 'absent')
    before = UserSession.query.filter_by(user_id=user.id).count()
    res = client.get('/setup?yeniden=1')                           # the canonical form, GET only
    assert res.status_code == 200 and 'setup' in res.get_data(as_text=True).lower()
    assert UserSession.query.filter_by(user_id=user.id).count() == before


def test_intake_failure_asks_for_retry_and_recovers_with_focus(app, gate, make_user, login,
                                                              monkeypatch):  # noqa: F811
    start(app, make_user, login, 'pr6b_retry')
    failing = {'on': True}
    real = dv._read_intake

    def intake(*args):
        if failing['on']:
            raise RuntimeError('ledger down')
        return real(*args)
    monkeypatch.setattr(dv, '_read_intake', intake)
    page = open_nutrition(gate)
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'retry')
    expect(page.locator('#nut-next-lead')).to_have_text(t('en', 'nutrition.next.retry_lead'))
    button = page.locator('#nut-next-action')
    expect(button).to_have_text(t('en', 'nutrition.next.retry'))
    # Healthy siblings stay usable: Today's own ledger read is fine.
    expect(page.locator('#nut-day')).to_have_attribute('data-intake-state', 'confirmed')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(2)
    failing['on'] = False
    gate.traffic.clear()
    button.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'log_food')
    page.wait_for_load_state('networkidle')
    assert Counter(gate.app_reads()) == Counter({'/nutrition-day-view': 1, '/meal-log/today': 1})
    expect(button).to_be_focused()                                 # focus preserved after retry
    expect(button).to_have_text(t('en', 'nutrition.next.log_food'))


def test_day_view_http_failure_is_unavailable_never_empty(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_http')
    gate.overrides['/nutrition-day-view'] = (503, '{"error":"nutrition_day_view_unavailable"}')
    page = open_nutrition(gate)
    box = page.locator('#nut-next')
    expect(box).to_have_attribute('data-next-state', 'unavailable')
    expect(page.locator('#nut-next-lead')).to_have_text(t('en', 'nutrition.next.unavailable'))
    expect(page.locator('#nut-next-action')).to_have_text(t('en', 'nutrition.next.retry'))
    # Siblings are not blanked by the failed day view.
    expect(page.locator('#nut-day')).to_have_attribute('data-intake-state', 'confirmed')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(2)
    expect(page.locator('#qab-water')).to_be_enabled()
    del gate.overrides['/nutrition-day-view']
    page.locator('#nut-next-action').click()
    expect(box).to_have_attribute('data-next-kind', 'log_food')


FORGED = [
    {'state': 'available', 'kind': 'eat_more', 'label_key': 'nutrition.next.eat_more'},
    {'state': 'available', 'kind': 'log_food', 'label_key': 'nutrition.next.set_target'},
    {'state': 'available', 'kind': 'toString', 'label_key': 'nutrition.next.log_food'},
    {'state': 'available', 'kind': '__proto__', 'label_key': 'nutrition.next.log_food'},
    {'state': 'available', 'kind': 'constructor', 'label_key': 'nutrition.next.log_food'},
    {'state': 'available', 'kind': 'set_target', 'label_key': 'nutrition.next.set_target',
     'href': 'javascript:alert(1)', 'url': 'https://evil.example/'},
    {'state': 'empty', 'kind': 'log_food', 'label_key': 'nutrition.next.log_food'},
    {'state': 'available', 'kind': ['log_food'], 'label_key': 'nutrition.next.log_food'},
    None,
]


@pytest.mark.parametrize('forged', FORGED)
def test_forged_next_action_is_never_run_or_linked(app, gate, make_user, login, forged):  # noqa: F811
    start(app, make_user, login, 'pr6b_forge')
    body = {'contract_version': 1, 'next_action': forged}
    gate.overrides['/nutrition-day-view'] = (200, json.dumps(body))
    page = open_nutrition(gate)
    if forged and forged.get('href'):
        # an allowlisted kind stays itself; server URLs are never used
        expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'set_target')
        assert page.locator('#nut-next-link').get_attribute('href') == '/setup?yeniden=1'
        assert 'evil' not in page.content() and 'javascript:alert' not in page.content()
        return
    expect(page.locator('#nut-next')).to_have_attribute('data-next-state', 'empty')
    expect(page.locator('#nut-next-action')).to_be_hidden()
    expect(page.locator('#nut-next-link')).to_be_hidden()
    expect(page.locator('#nut-next-lead')).to_have_text(t('en', 'nutrition.next.none'))
    assert 'eat_more' not in page.locator('#nut-next').inner_text()
    page.evaluate('() => runNextAction()')                          # a direct call is inert
    expect(page.locator('#log-sheet')).not_to_have_class(re.compile(r'\bopen\b'))


# ── REQUEST TOPOLOGY ────────────────────────────────────────────────────


def test_request_topology(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_topo')
    page = open_nutrition(gate)
    page.wait_for_load_state('networkidle')
    assert Counter(gate.app_reads()) == INITIAL                     # PR5 baseline + ONE day view
    gate.traffic.clear()
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#panel-plan')).to_be_visible()
    page.wait_for_load_state('networkidle')
    assert gate.app_reads() == []                                  # Plan: no read, no AI
    page.locator('#nutrition-tab-today').click()
    page.wait_for_load_state('networkidle')
    assert Counter(gate.app_reads()) == Counter({'/meal-log/today': 1})   # pre-existing redraw only
    gate.traffic.clear()
    page.locator('#log-food-btn').click()                          # chooser: 0 requests
    page.keyboard.press('Escape')
    page.wait_for_timeout(1500)                                    # idle window: no polling
    assert gate.app_reads() == []


# ── RACES ───────────────────────────────────────────────────────────────


def hold_day_view(page):
    held = []
    page.route('**/nutrition-day-view*', lambda route: held.append(route))
    return held


def test_pending_read_survives_tab_switches_without_a_second_request(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_race_b')
    page = gate.page
    held = hold_day_view(page)
    page.goto('http://localhost/nutrition', wait_until='domcontentloaded')
    page.wait_for_function('() => document.getElementById("nut-next").dataset.nextState === "loading"')
    page.locator('#nutrition-tab-plan').click()
    page.locator('#nutrition-tab-today').click()
    page.locator('#nutrition-tab-plan').click()
    page.locator('#nutrition-tab-today').click()
    page.wait_for_function('() => true')
    assert len(held) == 1
    held.pop().fallback()
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'log_food')
    assert len(held) == 0


def test_double_retry_is_one_request(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_race_c')
    gate.overrides['/nutrition-day-view'] = (503, '{}')
    page = open_nutrition(gate)
    expect(page.locator('#nut-next')).to_have_attribute('data-next-state', 'unavailable')
    del gate.overrides['/nutrition-day-view']
    held = hold_day_view(page)
    page.locator('#nut-next-action').dblclick()
    page.evaluate('() => { runNextAction(); retryDayView(); loadDayView(); }')
    page.wait_for_function('() => true')
    assert len(held) == 1
    held.pop().fallback()
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'log_food')


def test_older_answer_never_overwrites_a_newer_one(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_race_h')
    page = gate.page
    held = hold_day_view(page)
    page.goto('http://localhost/nutrition', wait_until='domcontentloaded')
    page.wait_for_function('() => document.getElementById("nut-next").dataset.nextState === "loading"')
    # A newer read supersedes the pending one (as a retry after a lost answer would).
    page.evaluate('() => { _dayViewInFlight = null; loadDayView(); }')
    page.wait_for_function('() => true')
    assert len(held) == 2
    older, newer = held
    newer.fallback()                                                 # real: log_food
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'log_food')
    older.fulfill(status=200, content_type='application/json', body=json.dumps({
        'next_action': {'state': 'available', 'kind': 'retry', 'label_key': 'nutrition.next.retry'}}))
    page.wait_for_timeout(300)
    expect(page.locator('#nut-next')).to_have_attribute('data-next-kind', 'log_food')
    expect(page.locator('#nut-next-action')).to_have_text(t('en', 'nutrition.next.log_food'))


# ── REVIEW WITH AXISAI ──────────────────────────────────────────────────


def test_review_link_carries_only_the_constant(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_link')
    page = open_nutrition(gate)
    link = page.locator('#nut-review-coach')
    assert link.get_attribute('href') == '/coach?review=nutrition-day'
    expect(link).to_have_text(t('en', 'nutrition.review_with_axisai'))
    hrefs = page.evaluate("() => [...document.querySelectorAll('a[href*=\"/coach\"]')].map(a => a.getAttribute('href'))")
    assert all(not re.search(r'\d', h.split('?')[1] if '?' in h else '') for h in hrefs), hrefs
    assert not any(k in json.dumps(hrefs) for k in ('kcal', 'calories', 'water', 'meal', 'plan'))


def test_review_opens_coach_without_sending_and_send_uses_the_marker(
        app, gate, make_user, login, monkeypatch):  # noqa: F811
    user = start(app, make_user, login, 'pr6b_review')
    app.config['AI_CHAT_QUOTA_ENABLED'] = False
    seen, bodies = [], []

    def fake_stream(uid, question, history, language='tr', **kw):
        seen.append((uid, question, kw.get('handoff')))
        yield {'type': 'meta', 'conversation_id': None}
        yield {'type': 'done', 'text': 'A grounded answer.', 'is_error_fallback': False,
               'usage': None}
    monkeypatch.setattr(coach_bp, 'stream_answer', fake_stream)
    monkeypatch.setattr(coach_bp, 'generate_answer', lambda *a, **k: pytest.fail('blocking call'))
    page = open_nutrition(gate)
    page.on('request', lambda r: bodies.append(r.post_data) if r.url.endswith('/ask/stream') else None)
    gate.traffic.clear()
    page.locator('#nut-review-coach').dblclick()                   # double-click: still no send
    # The second click supersedes the first click's navigation (net::ERR_ABORTED
    # on a request the server answered 200). `wait_for_url` latches onto that
    # first navigation event and raises; polling the committed URL does not.
    expect(page).to_have_url(re.compile(r'/coach\?review=nutrition-day$'))
    page.wait_for_load_state('networkidle')
    context = page.locator('#coach-nutrition-context')
    expect(context).to_be_visible()
    expect(context.locator('p')).to_have_count(2)
    expect(context.locator('p').first).to_have_text('Meals logged today: 2 · 1235 kcal')
    expect(page.locator('#cw-input')).to_have_value("Review today's nutrition with me.")
    assert page.evaluate('() => window.CW.handoff') == 'nutrition-day'
    assert not any(p.startswith('/ask') for p in gate.app_reads())  # 0 sends, 0 model calls
    assert seen == []
    page.locator('#cw-send').click()
    page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('A grounded answer.')")
    assert seen == [(user.id, "Review today's nutrition with me.", 'nutrition-day')]
    sent = json.loads(bodies[0])
    assert set(sent) == {'question', 'history', 'handoff'} and sent['handoff'] == 'nutrition-day'
    assert '1235' not in bodies[0] and '2100' not in bodies[0]
    # One-shot: consumed after the first successful reply.
    expect(context).to_have_count(0)
    assert page.evaluate('() => location.pathname + location.search') == '/coach'
    assert page.evaluate('() => window.CW.handoff') is None
    page.locator('#cw-input').fill('Another question.')
    page.locator('#cw-send').click()
    page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('Another question.')")
    assert seen[-1] == (user.id, 'Another question.', None)


def test_dismiss_clears_the_one_shot_context(app, gate, make_user, login, monkeypatch):  # noqa: F811
    user = start(app, make_user, login, 'pr6b_dismiss')
    app.config['AI_CHAT_QUOTA_ENABLED'] = False
    seen = []

    def fake_stream(uid, question, history, language='tr', **kw):
        seen.append(kw.get('handoff'))
        yield {'type': 'done', 'text': 'ok answer', 'is_error_fallback': False, 'usage': None}
    monkeypatch.setattr(coach_bp, 'stream_answer', fake_stream)
    gate.visit('/coach?review=nutrition-day', width=390)
    dismiss = gate.page.locator('#coach-nutrition-dismiss')
    expect(dismiss).to_have_text('Dismiss Nutrition context')
    dismiss.focus()
    gate.page.keyboard.press('Enter')
    expect(gate.page.locator('#coach-nutrition-context')).to_have_count(0)
    expect(gate.page.locator('#cw-input')).to_be_focused()
    assert gate.page.evaluate('() => window.CW.handoff') is None
    assert gate.page.evaluate('() => location.pathname + location.search') == '/coach'
    expect(gate.page.locator('#cw-input')).to_have_value("Review today's nutrition with me.")  # still editable
    gate.page.locator('#cw-send').click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('ok answer')")
    assert seen == [None]


def test_send_failure_keeps_marker_for_explicit_retry(app, gate, make_user, login, monkeypatch):  # noqa: F811
    start(app, make_user, login, 'pr6b_sendfail')
    app.config['AI_CHAT_QUOTA_ENABLED'] = False
    seen = []

    def answer(uid, question, history, language='tr', **kw):
        seen.append(kw.get('handoff'))
        return {'answer': 'Failed reply' if len(seen) == 1 else 'Successful reply',
                'is_error_fallback': len(seen) == 1, 'conversation_id': None}
    monkeypatch.setattr(coach_bp, 'generate_answer', answer)
    gate.visit('/coach?review=nutrition-day', width=390)
    gate.page.evaluate("""() => {
      window.CW._ask = function(q, h) {
        this._lastQ = q; this._lastHandoff = h; this._activeHandoff = h;
        this._setLoading(true); return this._plainAsk(q);
      };
    }""")
    gate.page.locator('#cw-send').click()
    gate.page.wait_for_function('() => !window.CW.busy')
    expect(gate.page.locator('#coach-nutrition-context')).to_have_count(1)
    gate.page.locator('.cw-regen').click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('Successful reply')")
    expect(gate.page.locator('#coach-nutrition-context')).to_have_count(0)
    assert seen == ['nutrition-day', 'nutrition-day']


def test_failed_derivation_opens_normal_coach_without_claiming_context(
        app, gate, make_user, login, monkeypatch):  # noqa: F811
    start(app, make_user, login, 'pr6b_noctx')
    monkeypatch.setattr(dv, '_read_intake', lambda *a: 1 / 0)
    gate.visit('/coach?review=nutrition-day', width=390)
    expect(gate.page.locator('#coach-nutrition-context')).to_have_count(0)
    assert gate.page.evaluate('() => window.CW.handoff') is None
    expect(gate.page.locator('#cw-input')).to_be_visible()


# ── ACCESSIBILITY / RESPONSIVE ──────────────────────────────────────────


A11Y = """() => {
  const box = el => el && !el.hidden && getComputedStyle(el).display !== 'none'
    ? el.getBoundingClientRect() : null;
  const act = document.getElementById('nut-next-action');
  const link = document.getElementById('nut-next-link');
  const review = document.getElementById('nut-review-coach');
  const control = box(act) ? act : link;
  const clipped = el => el.scrollWidth > el.clientWidth + 1;
  const inView = el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; };
  return {
    h1: document.querySelectorAll('h1').length,
    tabs: [...document.querySelectorAll('[role="tab"]')].map(t => t.dataset.tabName),
    nextHeading: document.getElementById('nut-next-title').tagName,
    controlH: box(control) ? box(control).height : 0,
    reviewH: review.getBoundingClientRect().height,
    overflow: document.scrollingElement.scrollWidth - innerWidth,
    controlClipped: clipped(control), reviewClipped: clipped(review),
    controlInView: inView(control), reviewInView: inView(review),
    leadClipped: clipped(document.getElementById('nut-next-lead')),
    leadRole: document.getElementById('nut-next-lead').getAttribute('role'),
  };
}"""


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_accessible_and_responsive_across_widths(app, gate, make_user, login, language):  # noqa: F811
    start(app, make_user, login, 'pr6b_resp_' + language, language, target=None)
    for width in WIDTHS:
        page = open_nutrition(gate, width)
        facts = page.evaluate(A11Y)
        assert facts['h1'] == 1 and facts['tabs'] == ['today', 'plan'], (width, facts)
        assert facts['nextHeading'] == 'H2' and facts['leadRole'] == 'status'
        assert facts['controlH'] >= 44 and facts['reviewH'] >= 44, (width, facts)
        assert facts['overflow'] <= 0, (width, facts)
        assert not facts['controlClipped'] and not facts['reviewClipped'], (width, facts)
        assert facts['controlInView'] and facts['reviewInView'], (width, facts)
        assert not facts['leadClipped'], (width, facts)


def test_log_food_control_size_and_visible_focus(app, gate, make_user, login):  # noqa: F811
    start(app, make_user, login, 'pr6b_focus')
    page = open_nutrition(gate, 320)
    assert page.evaluate(A11Y)['controlH'] >= 44
    page.locator('#nut-next-action').focus()
    page.keyboard.press('Shift+Tab')
    page.keyboard.press('Tab')                                     # keyboard focus → :focus-visible
    outline = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
    assert page.evaluate('() => document.activeElement.id') == 'nut-next-action'
    assert outline != 'none'


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_large_text_and_reduced_motion_at_320(app, gate, make_user, login, language):  # noqa: F811
    start(app, make_user, login, 'pr6b_large_' + language, language, target=None)
    gate.page.emulate_media(reduced_motion='reduce')
    page = open_nutrition(gate, 320)
    page.evaluate("() => { document.documentElement.style.fontSize = '150%'; }")
    facts = page.evaluate(A11Y)
    assert facts['overflow'] <= 0, facts
    assert facts['controlH'] >= 44 and not facts['controlClipped'] and facts['controlInView'], facts
    assert not facts['reviewClipped'] and facts['reviewInView'], facts
    expect(page.locator('#nut-next-lead')).to_be_visible()


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_coach_preview_is_compact_across_widths(app, gate, make_user, login, language):  # noqa: F811
    start(app, make_user, login, 'pr6b_cprev_' + language, language)
    for width in WIDTHS:
        gate.visit('/coach?review=nutrition-day', width=width)
        facts = gate.page.evaluate("""() => {
          const c = document.getElementById('coach-nutrition-context');
          const r = c.getBoundingClientRect();
          return {h: r.height, right: r.right, overflow: document.scrollingElement.scrollWidth - innerWidth,
                  label: c.getAttribute('aria-label'), dismiss: !!document.getElementById('coach-nutrition-dismiss')};
        }""")
        assert facts['h'] < 150 and facts['right'] <= width + 1, (width, facts)
        assert facts['overflow'] <= 0, (width, facts)
        assert facts['label'] == t(language, 'coach.handoff_from_nutrition') and facts['dismiss']
