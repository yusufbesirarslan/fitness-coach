/* AxisAI UIUX Sprint 1 PR3 — Plan v2 creation control (no_active_plan only).
 *
 * A CREATION-ONLY submitter, deliberately NOT a second training-plan authority:
 * it renders no plan, selects no "today", infers no rest day, reads no browser
 * storage. It reuses the exact canonical endpoints the legacy in-page generator
 * uses (POST /training-plan → POST /training-plan/save), then reloads so the
 * SERVER (Plan v2) renders the now-active plan authoritatively. Loaded ONLY on the
 * no_active_plan state; the populated/partial/error states ship none of this.
 *
 * CSRF: same-origin state-changing fetch is auto-stamped by static/csrf.js
 * (X-CSRFToken) — no manual header here. Copy comes from window.t (i18n.js). */
(function () {
  "use strict";

  // Canonical preference defaults — identical to legacy training.js `selections`,
  // so an unchanged field submits exactly what the endpoint already expects.
  var DEFAULTS = {
    gun_sayisi: 3, ekipman: "spor_salonu", odak: "tum_vucut", sure: 45,
    kardiyo_tipi: "yok", kardiyo_gun: 0, kardiyo_sure: 20,
    kardiyo_yogunluk: "orta", antrenman_tarzi: "genel", odak_hedef: "genel",
    injuries: ""
  };

  function tr(key, fallback) {
    var out = (typeof window.t === "function") ? window.t(key) : key;
    return (out === key && fallback !== undefined) ? fallback : out;
  }

  function collectSelections(root) {
    var selections = {};
    for (var k in DEFAULTS) { if (Object.prototype.hasOwnProperty.call(DEFAULTS, k)) selections[k] = DEFAULTS[k]; }
    var fields = root.querySelectorAll("[data-plan-field]");
    Array.prototype.forEach.call(fields, function (el) {
      var key = el.getAttribute("data-plan-field");
      var value = el.value;
      if (el.getAttribute("data-plan-type") === "int") {
        var n = parseInt(value, 10);
        value = isNaN(n) ? DEFAULTS[key] : n;
      } else {
        value = (value == null ? "" : String(value)).trim();
      }
      selections[key] = value;
    });
    // Mirror the canonical rule: no cardio type → zero cardio days.
    if (selections.kardiyo_tipi === "yok") selections.kardiyo_gun = 0;
    return selections;
  }

  function showMessage(root, text, isError, setupLink) {
    var msg = root.querySelector("[data-plan-create-msg]");
    if (!msg) return;
    msg.textContent = "";
    msg.hidden = false;
    msg.classList.toggle("plan-create-msg--error", !!isError);
    msg.appendChild(document.createTextNode(text));
    if (setupLink) {
      msg.appendChild(document.createTextNode(" "));
      var a = document.createElement("a");
      a.href = "/setup";
      a.textContent = tr("plan.create.go_setup", "Set up your profile");
      msg.appendChild(a);
    }
  }

  function setBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = busy;
    btn.classList.toggle("loading", busy);
    btn.setAttribute("aria-busy", busy ? "true" : "false");
  }

  var inFlight = false;

  window.createPlan = function (el) {
    var root = (el && el.closest) ? el.closest("[data-plan-create]") : document.querySelector("[data-plan-create]");
    if (!root || inFlight) return;
    var btn = root.querySelector("[data-plan-create-submit]");
    inFlight = true;
    setBusy(btn, true);
    showMessage(root, tr("plan.create.generating", "Creating your plan…"), false, false);

    var selections = collectSelections(root);

    fetch("/training-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selections)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        // A 400 from this endpoint uniquely means "no profile session yet" — the
        // one honest recovery is the canonical /setup wizard (a route, not a
        // self-link to /training).
        if (res.status === 400 && (!data.code || data.code === "TRAINING_PLAN_NO_SESSION")) {
          showMessage(root, tr("plan.create.need_setup", "Complete your profile first."), true, true);
          throw { handled: true };
        }
        if (!res.ok || data.error) {
          throw { message: data.error || tr("plan.create.error", "Couldn't create the plan. Please try again.") };
        }
        return data;
      });
    }).then(function (data) {
      return fetch("/training-plan/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: data.program, score: data.overall_score })
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (saved) {
          if (!res.ok || saved.error) throw { message: saved.error };
          return saved;
        });
      });
    }).then(function () {
      // Server re-renders the now-active plan on reload — the sole plan authority.
      window.location.reload();
    }).catch(function (err) {
      if (!(err && err.handled)) {
        var text = (err && err.message) ? err.message : tr("plan.create.error", "Couldn't create the plan. Please try again.");
        showMessage(root, text, true, false);
      }
      inFlight = false;
      setBusy(btn, false);
    });
  };
})();
