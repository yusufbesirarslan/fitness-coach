"""Hydration reliability: Water shows only server-confirmed truth.

WaterLog (`GET/POST /water`) is the only authority. `POST /water` sets an
absolute count and answers with the COMMITTED count, so its response is the
canonical confirmation. Contract under test:

  UNKNOWN != ZERO · READ FAILURE != EMPTY · LAST KNOWN != CURRENTLY VERIFIED ·
  WRITE SENT != WRITE CONFIRMED

Real rendered page and shipped script; `/water` is overridden per test to hold,
fail or forward requests to the authenticated Flask client.
"""
from collections import Counter
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_navigation_browser import ready

UNKNOWN = '—'


def forward(client, traffic, route, status=None, body=None):
    """Answer a held/intercepted /water request.

    status=None → run it against Flask and return the real response.
    status=N, body=None → fail WITHOUT reaching the server (no write happens).
    body given → answer with that body without reaching the server.
    """
    request = route.request
    path = urlsplit(request.url).path
    if status is None and body is None:
        response = client.open(path, method=request.method, data=request.post_data,
                               headers={k: v for k, v in request.headers.items()
                                        if k.lower() in {'content-type', 'origin', 'x-csrftoken'}})
        traffic.append((path, request.method, response.status_code))
        route.fulfill(status=response.status_code, body=response.get_data(),
                      content_type='application/json')
        return response
    traffic.append((path, request.method, status or 200))
    route.fulfill(status=status or 200, content_type='application/json', body=body or '{}')
    return None


def water_requests(traffic):
    return Counter(m for p, m, _ in traffic if p == '/water')


def wait_for(page, predicate, timeout_ms=5000):
    waited = 0
    while not predicate():
        assert waited < timeout_ms, 'request never arrived'
        page.wait_for_timeout(25)
        waited += 25


def seed(client, count):
    assert client.post('/water', json={'count': count}).get_json()['count'] == count


def stored(client):
    return client.get('/water').get_json()['count']


class WaterRoute:
    """Scripted /water handler. `script` is consumed per request; empty → forward."""

    def __init__(self, page, client):
        self.page, self.client = page, client
        self.traffic, self.held, self.script = [], [], []
        page.route('**/water', self._handle)

    def _handle(self, route):
        action = self.script.pop(0) if self.script else 'forward'
        if action == 'hold':
            self.held.append(route)
        elif action == 'forward':
            forward(self.client, self.traffic, route)
        elif action == 'commit-then-500':
            request = route.request
            real = self.client.open('/water', method='POST', data=request.post_data,
                                    headers={k: v for k, v in request.headers.items()
                                             if k.lower() in {'content-type', 'origin', 'x-csrftoken'}})
            assert real.status_code == 200
            self.traffic.append(('/water', request.method, 500))
            route.fulfill(status=500, content_type='application/json', body='{}')
        else:
            status, body = action
            forward(self.client, self.traffic, route, status=status, body=body)

    def release(self, index=0, **kwargs):
        route = self.held.pop(index)
        return forward(self.client, self.traffic, route, **kwargs)

    def wait_held(self, n=1):
        wait_for(self.page, lambda: len(self.held) >= n)


def open_water(page):
    # PR2 restores open disclosures across reloads; only open when closed.
    if not page.locator('#nutrition-tool-water').evaluate('e => e.open'):
        page.locator('#nutrition-tab-water').click()
    expect(page.locator('#panel-water')).to_be_visible()


def toasts(page, kind):
    return page.locator('#toast-wrap .toast-' + kind)


def assert_confirmed(page, n, language='en'):
    expect(page.locator('#water-num')).to_have_text(str(n))
    sub = f'Today {n} / 8 cups' if language == 'en' else f'Bugün {n} / 8 bardak'
    expect(page.locator('#qab-water-sub')).to_have_text(sub)
    expect(page.locator('.wg.filled')).to_have_count(n)
    expect(page.locator('#water-status')).to_have_text('')
    expect(page.locator('#water-retry')).to_be_hidden()


def assert_unknown(page):
    expect(page.locator('#water-num')).to_have_text(UNKNOWN)
    expect(page.locator('.wg.filled')).to_have_count(0)
    expect(page.locator('#water-btn')).to_be_disabled()
    expect(page.locator('#qab-water')).to_be_disabled()
    assert '/ 8 cups' not in page.locator('#qab-water-sub').inner_text()


# ── READ ────────────────────────────────────────────────────────────────


def test_canonical_read_renders_truth(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 3)
    page, _, _, _ = training_page
    page.goto('http://localhost/nutrition')
    open_water(page)
    assert_confirmed(page, 3)
    expect(page.locator('#water-btn')).to_be_enabled()


def test_failed_read_without_confirmed_state_is_not_zero(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 6)
    page, _, _, _ = training_page
    # A same-day browser cache must not stand in for the server either.
    page.add_init_script(
        "localStorage.setItem('fc_water', JSON.stringify({date: new Date().toDateString(), count: 5}))")
    water = WaterRoute(page, client)
    water.script = [(503, None)]
    page.goto('http://localhost/nutrition')
    open_water(page)
    expect(page.locator('#water-status')).to_have_text("Water couldn't be loaded right now.")
    assert_unknown(page)
    expect(page.locator('#water-last-known')).to_have_text('')
    # A locked control cannot write a guessed total.
    page.evaluate('() => { addWater(); }')
    page.wait_for_timeout(200)
    assert water_requests(water.traffic) == Counter({'GET': 1})
    assert stored(client) == 6


def test_failed_read_after_confirmed_state_is_last_known_not_current(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    assert_confirmed(page, 2)
    water.script = [(503, None), (503, None), (503, None)]
    page.locator('#water-btn').click()      # ambiguous write + failed reconciliation
    expect(page.locator('#water-status')).to_have_text("We couldn't confirm your last water change.")
    page.locator('#water-retry').click()    # re-read fails too
    expect(page.locator('#water-status')).to_have_text("Water couldn't be loaded right now.")
    assert_unknown(page)
    expect(page.locator('#water-last-known')).to_have_text('Last confirmed: 2 / 8 cups')


def test_older_read_cannot_overwrite_newer_read(app, auth_user, client, training_page):
    ready(app, auth_user)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    water.script = ['hold']
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.wait_held()
    seed(client, 4)
    page.evaluate('() => { initWaterButton(); }')      # newer read → 4
    assert_confirmed(page, 4)
    water.release(body='{"count": 0, "goal": 8}')   # older read arrives late
    page.wait_for_timeout(200)
    assert_confirmed(page, 4)


def test_water_is_usable_after_read_failure(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 1)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    water.script = [(503, None)]
    page.goto('http://localhost/nutrition')
    open_water(page)
    expect(page.locator('#water-retry')).to_be_visible()
    page.locator('#water-retry').click()
    assert_confirmed(page, 1)
    page.locator('#water-btn').click()
    assert_confirmed(page, 2)
    assert stored(client) == 2


# ── WRITE ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize('control', ['#water-btn', '#qab-water'])
def test_no_success_before_canonical_confirmation(app, auth_user, client, training_page, control):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    assert_confirmed(page, 2)
    water.script = ['hold']
    page.locator(control).click()
    water.wait_held()
    page.wait_for_timeout(150)
    # Sent, not confirmed: the last confirmed total stays, nothing is announced.
    expect(page.locator('#water-num')).to_have_text('2')
    expect(page.locator('#qab-water-sub')).to_have_text('Today 2 / 8 cups')
    expect(page.locator('.wg.filled')).to_have_count(2)
    expect(page.locator('#water-status')).to_have_text('Saving…')
    expect(page.locator('.water-card')).to_have_attribute('aria-busy', 'true')
    expect(page.locator('#water-btn')).to_be_disabled()
    expect(page.locator('#qab-water')).not_to_have_class('qab qab-done')
    expect(page.locator('#qab-water .qab-check')).to_be_hidden()
    assert toasts(page, 'info').count() == 0 and toasts(page, 'success').count() == 0
    water.release()
    assert_confirmed(page, 3)
    expect(toasts(page, 'info')).to_have_count(1)
    expect(page.locator('.water-card')).to_have_attribute('aria-busy', 'false')
    assert stored(client) == 3


def test_confirmation_updates_to_the_committed_count(app, auth_user, client, training_page):
    """The server clamps; the page shows what it committed, not what it asked for."""
    ready(app, auth_user)
    seed(client, 7)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.script = [(200, '{"count": 5, "goal": 8}')]
    page.locator('#water-btn').click()
    assert_confirmed(page, 5)


def test_rejected_write_leaves_confirmed_state_unchanged(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.traffic.clear()
    water.script = [(429, None)]
    page.locator('#water-btn').click()
    expect(toasts(page, 'error')).to_have_count(1)
    assert_confirmed(page, 2)
    expect(page.locator('#water-btn')).to_be_enabled()
    page.wait_for_timeout(300)
    assert water_requests(water.traffic) == Counter({'POST': 1})   # definite rejection: no re-read
    assert toasts(page, 'info').count() == 0
    assert stored(client) == 2


def test_ambiguous_write_that_did_not_land_is_reconciled(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.traffic.clear()
    water.script = [(503, None)]            # POST fails before the server; GET forwards
    page.locator('#water-btn').click()
    expect(toasts(page, 'error')).to_have_count(1)
    assert_confirmed(page, 2)
    assert water_requests(water.traffic) == Counter({'POST': 1, 'GET': 1})
    assert toasts(page, 'info').count() == 0


def test_ambiguous_write_that_landed_is_reconciled(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.traffic.clear()
    water.script = ['commit-then-500']      # committed, but the answer was lost
    page.locator('#water-btn').click()
    assert_confirmed(page, 3)
    expect(toasts(page, 'info')).to_have_count(1)
    assert toasts(page, 'error').count() == 0
    assert water_requests(water.traffic) == Counter({'POST': 1, 'GET': 1})


def test_confirmation_failure_does_not_fabricate_certainty(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.traffic.clear()
    water.script = ['commit-then-500', (503, None)]
    page.locator('#water-btn').click()
    expect(page.locator('#water-status')).to_have_text("We couldn't confirm your last water change.")
    # Neither the new total nor the old one is claimed as current.
    assert_unknown(page)
    expect(page.locator('#water-last-known')).to_have_text('Last confirmed: 2 / 8 cups')
    expect(page.locator('#water-retry')).to_be_visible()
    assert toasts(page, 'info').count() == 0 and toasts(page, 'success').count() == 0
    page.wait_for_timeout(1500)
    # Never re-sent automatically; no polling.
    assert water_requests(water.traffic) == Counter({'POST': 1, 'GET': 1})
    page.locator('#water-retry').click()
    assert_confirmed(page, 3)
    assert water_requests(water.traffic) == Counter({'POST': 1, 'GET': 2})


def test_one_mutation_per_action_and_rapid_writes_converge(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 1)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.traffic.clear()
    water.script = ['hold']
    page.evaluate('() => { addWater(); addWater(); quickAddWater(document.getElementById("qab-water")); }')
    water.wait_held()
    page.wait_for_timeout(200)
    assert len(water.held) == 1
    water.release()
    assert_confirmed(page, 2)
    page.locator('#water-btn').click()
    assert_confirmed(page, 3)
    assert water_requests(water.traffic) == Counter({'POST': 2})
    assert stored(client) == 3


def test_pre_write_read_cannot_overwrite_post_write_confirmation(app, auth_user, client, training_page):
    ready(app, auth_user)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    water.script = ['hold']
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.wait_held()
    page.evaluate('() => { initWaterButton(); }')
    assert_confirmed(page, 0)
    page.locator('#water-btn').click()
    assert_confirmed(page, 1)
    water.release(body='{"count": 0, "goal": 8}')   # pre-write read arrives last
    page.wait_for_timeout(200)
    assert_confirmed(page, 1)
    assert stored(client) == 1


def test_write_is_refused_while_a_read_is_in_flight(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    assert_confirmed(page, 2)
    water.traffic.clear()
    water.script = ['hold']
    page.evaluate('() => { initWaterButton(); }')
    water.wait_held()
    page.evaluate('() => { addWater(); }')             # no known base while re-reading
    page.wait_for_timeout(200)
    assert water_requests(water.traffic) == Counter()
    water.release()
    assert_confirmed(page, 2)
    assert stored(client) == 2


def test_write_failure_while_read_in_flight_keeps_newest_truth(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.script = ['hold']
    page.locator('#water-btn').click()
    water.wait_held()
    water.script = ['hold']
    page.evaluate('() => { initWaterButton(); }')      # a newer read starts before the write answers
    water.wait_held(2)
    water.release(0, status=429)            # the older write fails
    page.wait_for_timeout(150)
    assert toasts(page, 'error').count() == 0   # superseded: the read owns the answer
    water.release(0)
    assert_confirmed(page, 2)


def test_closing_water_during_confirmation(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.script = ['hold']
    page.locator('#water-btn').click()
    water.wait_held()
    page.locator('#nutrition-tab-water').click()
    expect(page.locator('#panel-water')).to_be_hidden()
    water.release()
    expect(page.locator('#qab-water-sub')).to_have_text('Today 3 / 8 cups')
    open_water(page)
    assert_confirmed(page, 3)


def test_today_plan_switch_during_confirmation(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.script = ['hold']
    page.locator('#qab-water').click()
    water.wait_held()
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')
    water.release()
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#panel-water')).to_be_visible()
    assert_confirmed(page, 3)


def test_reopen_after_failure_shows_failure_then_recovers(app, auth_user, client, training_page):
    ready(app, auth_user)
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    page.goto('http://localhost/nutrition')
    open_water(page)
    water.script = [(503, None), (503, None)]
    page.locator('#water-btn').click()
    expect(page.locator('#water-status')).to_have_text("We couldn't confirm your last water change.")
    page.locator('#nutrition-tab-water').click()
    open_water(page)
    expect(page.locator('#water-status')).to_have_text("We couldn't confirm your last water change.")
    assert_unknown(page)
    page.locator('#water-retry').click()
    assert_confirmed(page, 2)


# ── TOPOLOGY ────────────────────────────────────────────────────────────


def test_request_topology(app, auth_user, client, training_page):
    ready(app, auth_user)
    page, traffic, _, _ = training_page
    page.goto('http://localhost/nutrition')
    page.wait_for_timeout(300)
    # Initial: exactly the PR2 set (Water's one eager read is pre-existing).
    assert Counter(p for p, _, _ in traffic if not p.startswith('/static/')) == Counter({
        '/nutrition': 1, '/meal-log/today': 1, '/nutrition-plan/active': 1, '/water': 1,
        '/notifications/unread-count': 1, '/coach/history': 1,
        '/nutrition-day-view': 1})   # NUTR-PR6: one day-view read at load
    water = WaterRoute(page, client)
    open_water(page)
    page.wait_for_timeout(200)
    assert water_requests(water.traffic) == Counter()           # open: no request
    page.locator('#water-btn').click()
    assert_confirmed(page, 1)
    page.wait_for_timeout(300)
    assert water_requests(water.traffic) == Counter({'POST': 1})  # response is canonical
    water.traffic.clear()
    water.script = [(429, None)]
    page.locator('#water-btn').click()
    expect(toasts(page, 'error')).to_have_count(1)
    page.wait_for_timeout(300)
    assert water_requests(water.traffic) == Counter({'POST': 1})
    water.traffic.clear()
    water.script = [(503, None), (503, None)]
    page.locator('#water-btn').click()
    expect(page.locator('#water-retry')).to_be_visible()
    page.wait_for_timeout(1500)
    assert water_requests(water.traffic) == Counter({'POST': 1, 'GET': 1})
    # No write or failure touched any other surface (no provider/AI/meal re-read).
    assert Counter(p for p, _, _ in traffic if not p.startswith('/static/') and p != '/water') == Counter({
        '/nutrition': 1, '/meal-log/today': 1, '/nutrition-plan/active': 1,
        '/notifications/unread-count': 1, '/coach/history': 1,
        '/nutrition-day-view': 1})   # the load-time read only; water writes add none


# ── I18N / A11Y ─────────────────────────────────────────────────────────


def test_turkish_failure_and_saving_copy(app, auth_user, client, training_page):
    ready(app, auth_user, 'tr')
    seed(client, 2)
    page, _, _, _ = training_page
    water = WaterRoute(page, client)
    water.script = [(503, None)]
    page.goto('http://localhost/nutrition')
    open_water(page)
    expect(page.locator('#water-status')).to_have_text('Su takibi şu an yüklenemedi.')
    expect(page.locator('#water-retry')).to_have_text('Tekrar dene')
    page.locator('#water-retry').click()
    assert_confirmed(page, 2, 'tr')
    water.script = ['hold']
    page.locator('#water-btn').click()
    water.wait_held()
    expect(page.locator('#water-status')).to_have_text('Kaydediliyor…')
    water.release()
    assert_confirmed(page, 3, 'tr')


def test_status_is_a_polite_live_region(app, auth_user, training_page):
    ready(app, auth_user)
    page, _, _, _ = training_page
    page.goto('http://localhost/nutrition')
    status = page.locator('#water-status')
    expect(status).to_have_attribute('role', 'status')
    expect(status).to_have_attribute('aria-live', 'polite')
    expect(page.locator('#water-retry')).to_have_attribute('type', 'button')
