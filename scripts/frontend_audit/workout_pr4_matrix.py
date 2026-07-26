"""Sprint 7 PR4 workout-state convergence browser audit.

Runs only in the repository's hermetic WSL/Linux Playwright environment.  The
audit application configures inert integrations and an isolated SQLite database
before importing ``app.*``; all non-loopback browser requests are fulfilled by
the harness and therefore never reach the network.

Run from the repository root in WSL::

    PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/axisai-sprint0-playwright" \
      "$HOME/axisai-sprint0-audit-venv/bin/python" \
      -m scripts.frontend_audit.workout_pr4_matrix
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from .app import ROOT, create_audit_app
from .runner import AuditServer, browser_clock_script
from .seed import seed_all


VIEWPORTS = {
    "mobile-320": {"width": 320, "height": 720},
    "mobile-360": {"width": 360, "height": 800},
    "mobile-375": {"width": 375, "height": 812},
    "mobile-390": {"width": 390, "height": 844},
    "mobile-430": {"width": 430, "height": 932},
    "tablet-768": {"width": 768, "height": 1024},
    "wide-1024": {"width": 1024, "height": 900},
    "desktop-1440": {"width": 1440, "height": 900},
}
STATE_SCENARIOS = {
    "no_plan": "social-empty",
    "active": "active-workout",
    "completed": "completed-workout",
}
LOCALES = ("en", "tr")


TIMER_PROBE = r"""
(() => {
  const nativeSet = window.setInterval.bind(window);
  const nativeClear = window.clearInterval.bind(window);
  const live = new Map();
  window.setInterval = (fn, ms, ...args) => {
    const id = nativeSet(fn, ms, ...args);
    live.set(id, {ms, fn});
    return id;
  };
  window.clearInterval = (id) => { live.delete(id); nativeClear(id); };
  window.__pr4TimerProbe = {
    count: (ms) => [...live.values()].filter(x => ms == null || x.ms === ms).length,
    periods: () => [...live.values()].map(x => x.ms).sort((a,b) => a-b),
    fire: async (ms) => {
      const items = [...live.values()].filter(x => x.ms === ms);
      for (const item of items) await item.fn();
      await Promise.resolve();
      return items.length;
    }
  };
})();
"""


LIFECYCLE_PROBE = r"""
(() => {
  const events = [];
  const record = (type, event) => events.push({
    type,
    persisted: !!event.persisted,
    is_trusted: !!event.isTrusted
  });
  window.addEventListener('pagehide', event => record('pagehide', event));
  window.addEventListener('pageshow', event => record('pageshow', event));
  window.__pr4LifecycleProbe = {events};
})();
"""

MEASURE_TRAINING = r"""
() => {
  const display = (id) => {
    const el = document.getElementById(id);
    return el ? getComputedStyle(el).display : null;
  };
  const root = document.documentElement;
  const hero = document.getElementById('workout-hero');
  const text = document.body ? document.body.innerText : '';
  return {
    path: location.pathname,
    locale: window.LOCALE,
    active_plan_display: display('active-plan-view'),
    setup_display: display('setup-form'),
    hero_done: !!(hero && hero.classList.contains('is-done')),
    blocked_copy: /Workout state unavailable|Antrenman durumu al/.test(
      (document.getElementById('wh-cta') || {}).textContent || ''),
    raw_i18n_keys: [...new Set(text.match(/\btraining\.[a-z0-9_.]+/g) || [])],
    horizontal_overflow: root.scrollWidth > root.clientWidth + 1,
    local_storage: Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])),
    timer_periods: window.__pr4TimerProbe ? window.__pr4TimerProbe.periods() : [],
  };
}
"""


def _set_language(app, scenario: str, locale: str) -> None:
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = User.query.filter_by(username=f"audit-{scenario}").first()
        user.language = locale
        db.session.commit()


def _normalize_seed_plans(app) -> None:
    """Adapt the older Sprint-0 fixture to the canonical plan vocabulary."""
    from app.extensions import db
    from app.models import TrainingPlan

    with app.app_context():
        for plan in TrainingPlan.query.all():
            payload = json.loads(plan.plan_data)
            program = payload if isinstance(payload, list) else payload.get("program", [])
            for day in program:
                if day.get("tip") == "kuvvet":
                    day["tip"] = "antrenman"
                for exercise in day.get("egzersizler", []):
                    exercise.setdefault("dinlenme", "")
                    exercise.setdefault("not", "")
            plan.plan_data = json.dumps(payload, ensure_ascii=False)
        db.session.commit()


def _install_consumer_probe(app) -> None:
    """Audit-only authenticated route; never registered by the production app."""
    from flask import jsonify
    from flask_login import current_user

    from app.auth_middleware import require_auth
    from app.services import barcode, context_builder
    from app.services.workout_state import resolve_workout_state
    from app.services.workout_state.serialization import workout_state_payload

    @app.get("/__audit__/pr4/consumers")
    @require_auth
    def _pr4_consumers():
        state = resolve_workout_state(current_user.id)
        payload = workout_state_payload(state)
        canonical_json = json.dumps(
            state.to_dict(), ensure_ascii=False, separators=(",", ":"))
        coach = context_builder.fetch_coach_context(
            current_user.id, language=getattr(current_user, "language", "tr"))
        barcode_context = barcode.get_user_barcode_context(current_user.id)
        return jsonify({
            "canonical": payload,
            "barcode_completed": barcode_context["workout_completed_today"],
            "coach_contains_canonical": canonical_json in coach,
        })


def _block_external(route, request, base: str, blocked: list[dict]) -> None:
    if request.url.startswith(base):
        route.continue_()
        return
    blocked.append({"method": request.method, "url": request.url})
    route.fulfill(status=204, body="", content_type="text/plain")


def _open_context(browser, clocks, scenario, viewport):
    context = browser.new_context(viewport=dict(viewport))
    context.add_init_script(browser_clock_script(
        clocks[scenario]["fixed_current_datetime"]))
    context.add_init_script(TIMER_PROBE)
    context.add_init_script(LIFECYCLE_PROBE)
    return context


def _network_page(context, base: str):
    page = context.new_page()
    record = {
        "requests": [], "responses_4xx_5xx": [], "failed_requests": [],
        "console_errors": [], "page_errors": [], "external_blocked": [],
    }
    context.route("**/*", lambda route, req: _block_external(
        route, req, base, record["external_blocked"]))
    page.on("request", lambda req: record["requests"].append({
        "method": req.method, "url": req.url,
    }) if req.url.startswith(base) else None)
    page.on("response", lambda res: record["responses_4xx_5xx"].append({
        "status": res.status, "url": res.url,
    }) if res.url.startswith(base) and res.status >= 400 else None)
    page.on("requestfailed", lambda req: record["failed_requests"].append({
        "url": req.url, "error": str(req.failure),
    }) if req.url.startswith(base) else None)
    page.on("console", lambda msg: record["console_errors"].append(msg.text)
            if msg.type == "error" else None)
    page.on("pageerror", lambda err: record["page_errors"].append(str(err)))
    return page, record


def _login(page, base: str, scenario: str) -> None:
    response = page.context.request.get(
        f"{base}/__audit__/login/{scenario}", max_redirects=0)
    if response.status != 302:
        raise RuntimeError(f"audit login status={response.status}")


def _unexpected_console_errors(messages: list[str]) -> list[str]:
    """Exclude deterministic errors caused only by hermetically blocked CDNs."""
    return [message for message in messages
            if "Failed to find a valid digest" not in message]


def _path_counts(requests) -> dict[str, int]:
    return dict(Counter(urlparse(item["url"]).path for item in requests))


def _matrix_cell(browser, server, clocks, app, state, scenario, locale,
                 viewport_name, viewport):
    _set_language(app, scenario, locale)
    context = _open_context(browser, clocks, scenario, viewport)
    page, network = _network_page(context, server.base_url)
    cell_id = f"training__{state}__{viewport_name}__{locale}"
    try:
        _login(page, server.base_url, scenario)
        for values in network.values():
            if isinstance(values, list):
                values.clear()
        page.add_init_script("""
          localStorage.setItem('fitx_workout_completed_2026-07-20', 'true');
          localStorage.setItem('fitx_workout_state', '{\"completed\":true}');
        """)
        with page.expect_response(
                lambda res: urlparse(res.url).path == "/training/bootstrap",
                timeout=20_000):
            response = page.goto(f"{server.base_url}/training",
                                 wait_until="domcontentloaded", timeout=20_000)
        if state in ("active", "completed"):
            page.wait_for_function(
                "() => getComputedStyle(document.getElementById('active-plan-view')).display !== 'none'",
                timeout=10_000)
        page.wait_for_timeout(100)
        measurement = page.evaluate(MEASURE_TRAINING)
        identity = page.evaluate("""async () => {
          const [bootstrap, status] = await Promise.all([
            fetch('/training/bootstrap').then(r => r.json()),
            fetch('/workout/status').then(r => r.json())
          ]);
          return {bootstrap: bootstrap.workout, status};
        }""")
        counts = _path_counts(network["requests"])
        analytics_loaders = counts.get("/static/analytics.js", 0)
        analytics_event_requests = [item for item in network["external_blocked"]
                                    if "/g/collect" in item["url"] or
                                    "google-analytics.com/collect" in item["url"]]
        same_origin_posts = [item for item in network["requests"]
                             if item["method"] == "POST"]
        reasons = []
        if response is None or response.status != 200:
            reasons.append(f"document status={None if response is None else response.status}")
        if counts.get("/training/bootstrap") != 2:  # page bootstrap + identity probe
            reasons.append(f"bootstrap count={counts.get('/training/bootstrap', 0)} (want 2 including evidence probe)")
        if analytics_loaders != 1:
            reasons.append(f"analytics loader count={analytics_loaders}")
        if len(analytics_event_requests) > 1:
            reasons.append(f"duplicate analytics events={analytics_event_requests}")
        if same_origin_posts:
            reasons.append(f"unexpected mutation(s) on load={same_origin_posts}")
        if measurement["timer_periods"].count(60_000) > 1:
            reasons.append("duplicate heartbeat timers")
        unexpected_5xx = [x for x in network["responses_4xx_5xx"] if x["status"] >= 500]
        if unexpected_5xx:
            reasons.append(f"unexpected same-origin 5xx={unexpected_5xx}")
        pr4_console_errors = _unexpected_console_errors(network["console_errors"])
        if network["page_errors"] or pr4_console_errors:
            reasons.append("PR4 page/console error")
        if measurement["raw_i18n_keys"]:
            reasons.append(f"raw i18n={measurement['raw_i18n_keys']}")
        if identity["bootstrap"] != identity["status"]:
            reasons.append("bootstrap/status canonical identity mismatch")
        completed = identity["status"]["completed"]
        if state == "no_plan":
            if measurement["setup_display"] == "none" or identity["status"]["state"]["schedule_state"] != "no_plan":
                reasons.append("no-plan state rendered dishonestly")
        elif state == "active":
            if measurement["active_plan_display"] == "none" or measurement["hero_done"] or completed:
                reasons.append("active state rendered dishonestly")
        elif state == "completed":
            if not measurement["hero_done"] or not completed:
                reasons.append("completed state rendered dishonestly")
        return {
            "id": cell_id, "state": state, "scenario": scenario,
            "locale": locale, "viewport": viewport_name, "viewport_px": viewport,
            "verdict": "pass" if not reasons else "fail", "reasons": reasons,
            "request_counts": counts, "canonical_identity": identity,
            "pr4_console_errors": pr4_console_errors,
            "duplicate_guards": {"bootstrap_requests": counts.get("/training/bootstrap", 0),
                                 "analytics_loaders": analytics_loaders,
                                 "analytics_event_request_count": len(analytics_event_requests),
                                 "analytics_event_requests": analytics_event_requests,
                                 "same_origin_posts": same_origin_posts,
                                 "heartbeat_timers": measurement["timer_periods"].count(60_000)},
            "layout_observations": {"horizontal_overflow": measurement["horizontal_overflow"]},
            "measurement": measurement, **network,
        }
    except Exception as exc:  # noqa: BLE001
        return {"id": cell_id, "state": state, "locale": locale,
                "viewport": viewport_name, "verdict": "blocked",
                "reasons": [f"{type(exc).__name__}: {exc}"], **network}
    finally:
        context.close()


def _fail_closed(browser, server, clocks, app):
    scenario = "active-workout"
    _set_language(app, scenario, "en")
    context = _open_context(browser, clocks, scenario, VIEWPORTS["mobile-390"])
    page, network = _network_page(context, server.base_url)
    try:
        _login(page, server.base_url, scenario)
        for values in network.values():
            if isinstance(values, list):
                values.clear()
        page.route("**/training/bootstrap", lambda route: route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"error": "Try again", "code": "bootstrap_unavailable"})))
        page.goto(f"{server.base_url}/training", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        measurement = page.evaluate(MEASURE_TRAINING)
        responses = network["responses_4xx_5xx"]
        expected_5xx = [x for x in responses if urlparse(x["url"]).path == "/training/bootstrap" and x["status"] == 500]
        unexpected_5xx = [x for x in responses if x not in expected_5xx and x["status"] >= 500]
        reasons = []
        if not measurement["blocked_copy"]:
            reasons.append("fail-closed UI not rendered")
        if len(expected_5xx) != 1:
            reasons.append(f"expected bootstrap 500 count={len(expected_5xx)}")
        if unexpected_5xx:
            reasons.append(f"unexpected 5xx={unexpected_5xx}")
        return {"id": "bootstrap-fail-closed", "verdict": "pass" if not reasons else "fail",
                "reasons": reasons, "expected_same_origin_5xx": expected_5xx,
                "unexpected_same_origin_5xx": unexpected_5xx,
                "measurement": measurement, "request_counts": _path_counts(network["requests"]), **network}
    except Exception as exc:  # noqa: BLE001
        return {"id": "bootstrap-fail-closed", "verdict": "blocked",
                "reasons": [f"{type(exc).__name__}: {exc}"], **network}
    finally:
        context.close()


def _navigation_and_consumers(browser, server, clocks, app):
    scenario = "active-workout"
    previous_sessions_flag = app.config.get(
        "FITX_WORKOUT_SESSIONS_ENABLED", False)
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    context = None
    network = {
        "requests": [], "responses_4xx_5xx": [], "failed_requests": [],
        "console_errors": [], "page_errors": [], "external_blocked": [],
    }
    try:
        _set_language(app, scenario, "tr")
        context = _open_context(
            browser, clocks, scenario, VIEWPORTS["mobile-390"])
        page, network = _network_page(context, server.base_url)
        _login(page, server.base_url, scenario)
        page.goto(f"{server.base_url}/training", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        session_start = page.evaluate("""async () => {
          const response = await fetch(
            '/workout/session/start', {method: 'POST'});
          return {status: response.status, body: await response.json()};
        }""")
        if session_start["status"] not in (200, 201):
            raise RuntimeError(
                f"active v2 session start status={session_start['status']}")

        for values in network.values():
            if isinstance(values, list):
                values.clear()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("""() =>
          window.__pr4TimerProbe && window.__pr4TimerProbe.count(60000) === 1 &&
          getComputedStyle(document.getElementById('active-plan-view')).display !== 'none'
        """, timeout=10_000)
        direct = page.evaluate(MEASURE_TRAINING)
        before_reload = _path_counts(network["requests"]).get(
            "/training/bootstrap", 0)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.__pr4TimerProbe.count(60000) === 1",
            timeout=10_000)
        after_reload = _path_counts(network["requests"]).get(
            "/training/bootstrap", 0)
        for width in (320, 768, 1023, 1024, 1440, 390, 1440, 320):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(50)
        after_breakpoints = _path_counts(network["requests"]).get(
            "/training/bootstrap", 0)

        def history_measurement():
            return page.evaluate("""() => {
              const nav = performance.getEntriesByType('navigation')[0] || null;
              const reasons = nav && nav.notRestoredReasons;
              return {
                path: location.pathname,
                navigation_type: nav ? nav.type : null,
                not_restored_reasons: reasons == null ? null :
                  JSON.parse(JSON.stringify(reasons)),
                lifecycle: window.__pr4LifecycleProbe.events.slice(),
                timer_periods: window.__pr4TimerProbe.periods(),
                active_plan_visible:
                  getComputedStyle(document.getElementById('active-plan-view')).display !== 'none'
              };
            }""")

        history_before = history_measurement()
        page.goto(
            f"{server.base_url}/progress-page", wait_until="domcontentloaded")
        with page.expect_response(
                lambda res: urlparse(res.url).path == "/training/bootstrap",
                timeout=20_000) as first_history_info:
            page.go_back(wait_until="domcontentloaded")
        first_history_response = first_history_info.value
        first_history_response.finished()
        page.wait_for_function("""() =>
          window.__pr4TimerProbe.count(60000) === 1 &&
          getComputedStyle(document.getElementById('active-plan-view')).display !== 'none'
        """, timeout=10_000)
        first_history = history_measurement()
        first_history.update({
            "bootstrap_status": first_history_response.status,
            "bootstrap_count": _path_counts(network["requests"]).get(
                "/training/bootstrap", 0),
            "restoration_mode": "trusted_bfcache"
            if any(event["type"] == "pageshow" and event["persisted"] and
                   event["is_trusted"]
                   for event in first_history["lifecycle"])
            else "history_reload",
        })

        page.go_forward(wait_until="domcontentloaded")
        with page.expect_response(
                lambda res: urlparse(res.url).path == "/training/bootstrap",
                timeout=20_000) as second_history_info:
            page.go_back(wait_until="domcontentloaded")
        second_history_response = second_history_info.value
        second_history_response.finished()
        page.wait_for_function("""() =>
          window.__pr4TimerProbe.count(60000) === 1 &&
          getComputedStyle(document.getElementById('active-plan-view')).display !== 'none'
        """, timeout=10_000)
        second_history = history_measurement()
        second_history.update({
            "bootstrap_status": second_history_response.status,
            "bootstrap_count": _path_counts(network["requests"]).get(
                "/training/bootstrap", 0),
            "restoration_mode": "trusted_bfcache"
            if any(event["type"] == "pageshow" and event["persisted"] and
                   event["is_trusted"]
                   for event in second_history["lifecycle"])
            else "history_reload",
        })

        synthetic_before = _path_counts(network["requests"]).get(
            "/training/bootstrap", 0)
        synthetic_teardown_timers = page.evaluate("""() => {
          window.dispatchEvent(new PageTransitionEvent(
            'pagehide', {persisted: true}));
          return window.__pr4TimerProbe.count(60000);
        }""")
        with page.expect_response(
                lambda res: urlparse(res.url).path == "/training/bootstrap",
                timeout=20_000) as synthetic_info:
            page.evaluate("""() => window.dispatchEvent(
              new PageTransitionEvent('pageshow', {persisted: true}))""")
        synthetic_response = synthetic_info.value
        synthetic_response.finished()
        page.wait_for_function("""() =>
          window.__pr4TimerProbe.count(60000) === 1 &&
          getComputedStyle(document.getElementById('active-plan-view')).display !== 'none'
        """, timeout=10_000)
        synthetic_after = _path_counts(network["requests"]).get(
            "/training/bootstrap", 0)
        synthetic_lifecycle = page.evaluate(
            "() => window.__pr4LifecycleProbe.events.slice(-2)")
        synthetic = {
            "dispatch": "browser_dispatched_PageTransitionEvent",
            "bootstrap_status": synthetic_response.status,
            "bootstrap_before": synthetic_before,
            "bootstrap_after": synthetic_after,
            "teardown_heartbeat_timers": synthetic_teardown_timers,
            "restored_heartbeat_timers": page.evaluate(
                "() => window.__pr4TimerProbe.count(60000)"),
            "lifecycle": synthetic_lifecycle,
            "events_are_synthetic": all(
                not event["is_trusted"] for event in synthetic_lifecycle),
        }

        checkpoint_before = sum(
            urlparse(item["url"]).path.startswith("/workout/session/") and
            urlparse(item["url"]).path.endswith("/checkpoint")
            for item in network["requests"])
        synthetic["heartbeat_callbacks_fired"] = page.evaluate(
            "() => window.__pr4TimerProbe.fire(60000)")
        checkpoint_after = sum(
            urlparse(item["url"]).path.startswith("/workout/session/") and
            urlparse(item["url"]).path.endswith("/checkpoint")
            for item in network["requests"])
        synthetic["heartbeat_checkpoint_delta"] = (
            checkpoint_after - checkpoint_before)
        preview = page.locator(
            '.week-chip[data-action="previewDay"]:not(.is-rest)').first
        preview.click()
        synthetic["preview_opened"] = page.locator(
            "#day-preview").evaluate("el => el.classList.contains('open')")
        page.keyboard.press("Escape")
        synthetic["preview_closed"] = page.locator(
            "#day-preview").evaluate("el => !el.classList.contains('open')")

        canonical = page.evaluate("""async () => {
          const [status, progress, consumers] = await Promise.all([
            fetch('/workout/status').then(r => r.json()),
            fetch('/api/progress/workout?range=week').then(r => r.json()),
            fetch('/__audit__/pr4/consumers').then(r => r.json())
          ]);
          return {status, progress: progress.current, consumers};
        }""")
        counts = _path_counts(network["requests"])
        unexpected_5xx = [
            x for x in network["responses_4xx_5xx"] if x["status"] >= 500]
        reasons = []
        if direct["path"] != "/training":
            reasons.append("direct route did not load")
        if direct["timer_periods"].count(60_000) != 1:
            reasons.append("initial active v2 heartbeat owner was not exactly one")
        if after_reload - before_reload != 1:
            reasons.append("refresh did not issue exactly one bootstrap")
        if after_breakpoints != after_reload:
            reasons.append("breakpoint transition issued bootstrap")
        if first_history["path"] != "/training" or second_history["path"] != "/training":
            reasons.append("back/forward did not restore or reload Training")
        if first_history["bootstrap_status"] != 200:
            reasons.append("first history bootstrap did not complete successfully")
        if second_history["bootstrap_status"] != 200:
            reasons.append("second history bootstrap did not complete successfully")
        if first_history["bootstrap_count"] != after_breakpoints + 1:
            reasons.append("first history restoration issued wrong bootstrap count")
        if second_history["bootstrap_count"] != first_history["bootstrap_count"] + 1:
            reasons.append("second history restoration issued wrong bootstrap count")
        if (not first_history["active_plan_visible"] or
                not second_history["active_plan_visible"]):
            reasons.append("history-restored Training was not canonical")
        if (first_history["timer_periods"].count(60_000) != 1 or
                second_history["timer_periods"].count(60_000) != 1):
            reasons.append("history-restored heartbeat ownership was not exactly one")
        if synthetic["bootstrap_status"] != 200:
            reasons.append("synthetic persisted bootstrap did not complete")
        if synthetic_after != synthetic_before + 1:
            reasons.append("synthetic persisted restore did not issue one bootstrap")
        if synthetic["teardown_heartbeat_timers"] != 0:
            reasons.append("synthetic persisted pagehide did not tear down heartbeat")
        if synthetic["restored_heartbeat_timers"] != 1:
            reasons.append("synthetic persisted pageshow did not restore one heartbeat")
        if (len(synthetic_lifecycle) != 2 or
                [event["type"] for event in synthetic_lifecycle] !=
                ["pagehide", "pageshow"] or
                not all(event["persisted"] for event in synthetic_lifecycle) or
                not synthetic["events_are_synthetic"]):
            reasons.append("synthetic persisted lifecycle evidence is inaccurate")
        if (synthetic["heartbeat_callbacks_fired"] != 1 or
                synthetic["heartbeat_checkpoint_delta"] != 1):
            reasons.append("restored heartbeat was not live and single-owner")
        if not synthetic["preview_opened"] or not synthetic["preview_closed"]:
            reasons.append("restored Training interaction was not live")
        if (canonical["status"] != canonical["progress"] or
                canonical["status"] != canonical["consumers"]["canonical"]):
            reasons.append("Training/Progress consumer canonical identity mismatch")
        if (canonical["consumers"]["barcode_completed"] !=
                canonical["status"]["completed"]):
            reasons.append("barcode completion mismatch")
        if not canonical["consumers"]["coach_contains_canonical"]:
            reasons.append("Coach canonical identity absent")
        if unexpected_5xx:
            reasons.append(f"unexpected same-origin 5xx={unexpected_5xx}")
        pr4_console_errors = _unexpected_console_errors(network["console_errors"])
        if network["page_errors"] or pr4_console_errors:
            reasons.append("page/console errors")
        return {
            "id": "navigation-breakpoints-consumers",
            "verdict": "pass" if not reasons else "fail",
            "reasons": reasons, "request_counts": counts, "direct": direct,
            "session_start": session_start,
            "canonical_identity": canonical,
            "pr4_console_errors": pr4_console_errors,
            "bootstrap_counts": {
                "initial": before_reload, "after_reload": after_reload,
                "after_breakpoints": after_breakpoints,
                "first_history": first_history["bootstrap_count"],
                "second_history": second_history["bootstrap_count"],
                "after_synthetic_persisted": synthetic_after,
            },
            "history_navigation": {
                "before": history_before,
                "first": first_history, "second": second_history,
            },
            "synthetic_persisted_lifecycle": synthetic,
            **network,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": "navigation-breakpoints-consumers", "verdict": "blocked",
            "reasons": [f"{type(exc).__name__}: {exc}"], **network,
        }
    finally:
        app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = previous_sessions_flag
        if context is not None:
            context.close()

CONTROLLER_LAB = r"""
async () => {
  const api = window.FitXWorkoutStateClient;
  const response = (body, ok=true, status=200) => ({ok, status, json: async () => body});
  const deferred = () => { let resolve; const promise = new Promise(r => resolve=r); return {promise, resolve}; };
  const snap = (id, active=false) => ({id, workout:{state:{contract_version:2,
    session_state:active?'active_resumable':'none', session:active?{status:'active', public_id:'session-1'}:null}}});

  const staleApplied=[]; const a=deferred(), b=deferred(); let reads=0;
  const staleClient=api.createWorkoutStateClient({fetchImpl:()=> (++reads===1?a.promise:b.promise),
    onSnapshot:s=>staleApplied.push(s.id), documentRef:document});
  const p1=staleClient.refresh('old'), p2=staleClient.refresh('new');
  b.resolve(response(snap('new'))); await p2; a.resolve(response(snap('old'))); await p1;
  staleClient.destroy();

  const order=[]; const applied=[]; let boot=0, posts=0;
  const mutationClient=api.createWorkoutStateClient({fetchImpl:async(url, init={})=>{
    order.push((init.method||'GET')+' '+url);
    if ((init.method||'GET')==='POST') { posts++; return response({outcome:'ok'}); }
    boot++; return response(snap('canonical-'+boot));
  }, onSnapshot:s=>applied.push(s.id), documentRef:document});
  const mutationResult=await mutationClient.mutate('/workout/test',{method:'POST'});
  const concurrent=await Promise.all([
    mutationClient.mutate('/workout/test',{method:'POST'}),
    mutationClient.mutate('/workout/test',{method:'POST'})
  ]);
  mutationClient.destroy();

  const listeners={}; const intervals=new Map(); let nextId=1, heartPosts=0, heartBoot=0;
  const fakeDoc={hidden:false, addEventListener:(n,f)=>listeners[n]=f,
    removeEventListener:(n,f)=>{if(listeners[n]===f) delete listeners[n];}};
  const scheduler={now:()=>10000, setInterval:(fn,ms)=>{const id=nextId++; intervals.set(id,{fn,ms});return id;},
    clearInterval:id=>intervals.delete(id)};
  const heartClient=api.createWorkoutStateClient({fetchImpl:async(url, init={})=>{
    if (url==='/training/bootstrap') {heartBoot++; return response(snap('heart-'+heartBoot, heartBoot<3));}
    heartPosts++; return response({outcome:'checkpointed'});
  }, scheduler, documentRef:fakeDoc, addEventListener:(n,f)=>listeners['window:'+n]=f,
     removeEventListener:(n,f)=>{if(listeners['window:'+n]===f) delete listeners['window:'+n];}});
  await heartClient.refresh('load'); const timersAfterActive=intervals.size;
  await heartClient.refresh('repeat'); const timersAfterRepeat=intervals.size;
  await [...intervals.values()][0].fn(); const checkpointPosts=heartPosts;
  await heartClient.refresh('inactive'); const timersAfterInactive=intervals.size;
  heartClient.destroy(); heartClient.destroy();

  return {
    stale:{applied:staleApplied, reads},
    mutation:{order, applied, mutation_result:mutationResult, concurrent_results:concurrent,
      post_count:posts, bootstrap_count:boot},
    heartbeat:{timers_after_active:timersAfterActive, timers_after_repeat:timersAfterRepeat,
      checkpoint_posts:checkpointPosts, timers_after_inactive:timersAfterInactive,
      remaining_listeners:Object.keys(listeners), periods:[...intervals.values()].map(x=>x.ms)}
  };
}
"""


def _controller_lab(browser):
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto("about:blank")
        page.add_script_tag(path=str(ROOT / "static" / "workout_state_client.js"))
        result = page.evaluate(CONTROLLER_LAB)
        reasons = []
        if result["stale"]["applied"] != ["new"]:
            reasons.append(f"stale overwrite={result['stale']['applied']}")
        mutation = result["mutation"]
        if not mutation["mutation_result"]["ok"] or "GET /training/bootstrap" not in mutation["order"]:
            reasons.append("mutation did not authoritatively refresh")
        # One initial mutation plus exactly one shared write for two repeated actions.
        if mutation["post_count"] != 2:
            reasons.append(f"overlapping/repeated mutation POST count={mutation['post_count']} (want 2 total)")
        heart = result["heartbeat"]
        if heart["timers_after_active"] != 1 or heart["timers_after_repeat"] != 1:
            reasons.append("heartbeat timer was not exactly one")
        if heart["checkpoint_posts"] != 1 or heart["timers_after_inactive"] != 0:
            reasons.append("heartbeat checkpoint/cleanup mismatch")
        if heart["remaining_listeners"]:
            reasons.append(f"destroy left listeners={heart['remaining_listeners']}")
        return {"id": "chromium-controller-ordering-heartbeat", "verdict": "pass" if not reasons else "fail",
                "reasons": reasons, "results": result}
    except Exception as exc:  # noqa: BLE001
        return {"id": "chromium-controller-ordering-heartbeat", "verdict": "blocked",
                "reasons": [f"{type(exc).__name__}: {exc}"]}
    finally:
        context.close()


def _real_heartbeat(browser, server, clocks, app):
    scenario = "active-workout"
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    _set_language(app, scenario, "en")
    context = _open_context(browser, clocks, scenario, VIEWPORTS["mobile-390"])
    page, network = _network_page(context, server.base_url)
    try:
        _login(page, server.base_url, scenario)
        page.goto(f"{server.base_url}/training", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        start = page.evaluate("""async () => {
          const r = await fetch('/workout/session/start', {method:'POST'});
          return {status:r.status, body:await r.json()};
        }""")
        for values in network.values():
            if isinstance(values, list): values.clear()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        timer = page.evaluate("() => ({count:__pr4TimerProbe.count(60000), periods:__pr4TimerProbe.periods()})")
        fired = page.evaluate("async () => __pr4TimerProbe.fire(60000)")
        page.wait_for_timeout(100)
        checkpoints = _path_counts(network["requests"]).get(
            f"/workout/session/{start['body'].get('session', {}).get('public_id', '')}/checkpoint", 0)
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        after_teardown = page.evaluate("() => __pr4TimerProbe.count(60000)")
        reasons = []
        if start["status"] not in (200, 201): reasons.append(f"session start status={start['status']}")
        if timer["count"] != 1: reasons.append(f"active heartbeat timers={timer['count']}")
        if fired != 1 or checkpoints != 1: reasons.append(f"heartbeat fire/checkpoint={fired}/{checkpoints}")
        if after_teardown != 0: reasons.append(f"teardown timers={after_teardown}")
        unexpected_5xx = [x for x in network["responses_4xx_5xx"] if x["status"] >= 500]
        if unexpected_5xx: reasons.append(f"unexpected same-origin 5xx={unexpected_5xx}")
        return {"id": "real-session-heartbeat", "verdict": "pass" if not reasons else "fail",
                "reasons": reasons, "session_start": start, "timer_counts": {
                    "active": timer, "callbacks_fired": fired, "checkpoint_requests": checkpoints,
                    "after_teardown": after_teardown}, "request_counts": _path_counts(network["requests"]), **network}
    except Exception as exc:  # noqa: BLE001
        return {"id": "real-session-heartbeat", "verdict": "blocked",
                "reasons": [f"{type(exc).__name__}: {exc}"], **network}
    finally:
        app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
        context.close()


def run(output_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    started = time.perf_counter()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = ROOT / "artifacts" / "ui-audit" / "workout_pr4_matrix.db"
    app = create_audit_app(db_path)
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    seed_summary = seed_all(app)
    _normalize_seed_plans(app)
    _install_consumer_probe(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]

    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for state, scenario in STATE_SCENARIOS.items():
                for locale in LOCALES:
                    for viewport_name, viewport in VIEWPORTS.items():
                        results.append(_matrix_cell(
                            browser, server, clocks, app, state, scenario, locale,
                            viewport_name, viewport))
            results.append(_fail_closed(browser, server, clocks, app))
            results.append(_navigation_and_consumers(browser, server, clocks, app))
            results.append(_controller_lab(browser))
            results.append(_real_heartbeat(browser, server, clocks, app))
        finally:
            browser.close()

    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    source = (ROOT / "static" / "training.js").read_text(encoding="utf-8") + \
        (ROOT / "static" / "workout_state_client.js").read_text(encoding="utf-8")
    authority_scan = {
        "local_storage_workout_authority_absent": "localStorage" not in source,
        "client_local_date_authority_absent": all(token not in source for token in (
            "toLocaleDateString", "getTodayTurkish", "fitx_workout_completed_")),
    }
    if not all(authority_scan.values()):
        failed.append("source-authority-scan")
    duration = round(time.perf_counter() - started, 3)
    manifest = {
        "schema_version": "1.0.0", "pr": "sprint7-pr4-workout-state-convergence",
        "engine": "playwright-chromium-linux", "hermetic": True,
        "external_network_allowed": False,
        "command": "PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/axisai-sprint0-playwright $HOME/axisai-sprint0-audit-venv/bin/python -m scripts.frontend_audit.workout_pr4_matrix",
        "duration_seconds": duration, "seed_summary": seed_summary,
        "viewports": VIEWPORTS, "locales": list(LOCALES),
        "authority_scan": authority_scan,
        "totals": {"cells": len(results),
                   "passed": sum(r["verdict"] == "pass" for r in results),
                   "failed": len([r for r in results if r["verdict"] == "fail"]),
                   "blocked": len(blocked), "matrix_cells": 48},
        "failed_ids": failed, "blocked_ids": blocked, "results": results,
    }
    evidence = output_dir / "browser-validation.json"
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2)
    if failed or blocked:
        (output_dir / "browser-validation.failed.json").write_text(
            rendered, encoding="utf-8")
    else:
        candidate = output_dir / "browser-validation.candidate.json"
        candidate.write_text(rendered, encoding="utf-8")
        candidate.replace(evidence)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.frontend_audit.workout_pr4_matrix")
    parser.add_argument("--output", type=Path, default=(
        ROOT / "docs" / "frontend-readiness" / "sprint-7-pr4"))
    args = parser.parse_args()
    result = run(args.output)
    totals = result["totals"]
    print(f"cells={totals['cells']} passed={totals['passed']} "
          f"failed={totals['failed']} blocked={totals['blocked']} "
          f"duration={result['duration_seconds']}s")
    for item in result["results"]:
        if item["verdict"] != "pass":
            print(" !", item["id"], item.get("reasons"))
    if result["failed_ids"] or result["blocked_ids"]:
        raise SystemExit(1)
    print("evidence:", args.output / "browser-validation.json")


if __name__ == "__main__":
    main()
