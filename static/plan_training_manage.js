/* AxisAI UX-3 PR3 — Plan's Training-management renderer.
 *
 * Supersedes `static/plan_create.js`, which could only create. Plan now owns ONE
 * Training-management workflow with two distinct operations over the same
 * canonical services:
 *
 *   create     (no active plan)     — additive; generate, review, save.
 *   regenerate (active/partial plan) — DESTRUCTIVE; generate, review, and then
 *                                      an explicit confirmation before the
 *                                      current plan is replaced.
 *
 * Everything that decides whether a write is safe lives in
 * `training_plan_management.js`, which legacy `/training` calls too. This file
 * is only the surface: form state, panel/dialog state, proposal presentation,
 * pending state, focus. It renders no active plan, holds no plan version,
 * derives no default the server does not own, and never treats a generated
 * proposal as persisted — after a successful write it reloads so the SERVER
 * re-renders the now-active plan.
 *
 * CSRF: same-origin state-changing fetch is auto-stamped by static/csrf.js
 * (X-CSRFToken). Copy comes from window.t (i18n.js) — no EN/TR literal here.
 */
(function () {
  "use strict";

  var management = window.FitXPlanManagement;
  var root = document.querySelector("[data-plan-manage]");
  if (!management || !root) return;

  // Canonical preference defaults — identical to legacy training.js `selections`,
  // so an unchanged field submits exactly what the endpoint already expects. They
  // are transport defaults for fields this surface does not expose, NOT plan
  // content: the server owns every prescription decision made from them.
  var DEFAULTS = {
    gun_sayisi: 3, ekipman: "spor_salonu", odak: "tum_vucut", sure: 45,
    kardiyo_tipi: "yok", kardiyo_gun: 0, kardiyo_sure: 20,
    kardiyo_yogunluk: "orta", antrenman_tarzi: "genel", odak_hedef: "genel",
    injuries: ""
  };

  var MANAGE_STATE = root.getAttribute("data-manage-state");
  var DESTRUCTIVE = MANAGE_STATE === "regenerate";

  var panel = root.querySelector("[data-plan-manage-panel]");
  var openBtn = root.querySelector("[data-plan-manage-open]");
  var generateBtn = root.querySelector("[data-plan-manage-generate]");
  var proposalBox = root.querySelector("[data-plan-manage-proposal]");
  var proposalDays = root.querySelector("[data-plan-manage-days]");
  var confirmBtn = root.querySelector("[data-plan-manage-confirm]");
  var msgNode = root.querySelector("[data-plan-manage-msg]");
  // The destructive confirmation lives OUTSIDE <main> (its `page-enter`
  // animation retains a transform, which would make a nested `position: fixed`
  // dialog a clipped block instead of a viewport overlay), so it is looked up
  // on the document rather than inside the management section.
  var dialog = document.querySelector("[data-plan-replace-confirm]");
  var restoreFocusTo = null;

  function tr(key) {
    return (typeof window.t === "function") ? window.t(key) : key;
  }

  function readBootstrap() {
    var node = root.querySelector("[data-plan-manage-bootstrap]");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (error) {
      return null;
    }
  }

  function collectSelections() {
    var selections = {};
    for (var key in DEFAULTS) {
      if (Object.prototype.hasOwnProperty.call(DEFAULTS, key)) {
        selections[key] = DEFAULTS[key];
      }
    }
    var fields = root.querySelectorAll("[data-plan-field]");
    Array.prototype.forEach.call(fields, function (el) {
      var name = el.getAttribute("data-plan-field");
      var value = el.value;
      if (el.getAttribute("data-plan-type") === "int") {
        var parsed = parseInt(value, 10);
        value = isNaN(parsed) ? DEFAULTS[name] : parsed;
      } else {
        value = (value == null ? "" : String(value)).trim();
      }
      selections[name] = value;
    });
    return selections;
  }

  function showMessage(key, isError, withSetupLink) {
    if (!msgNode) return;
    msgNode.textContent = "";
    msgNode.hidden = false;
    msgNode.classList.toggle("plan-manage-msg--error", !!isError);
    msgNode.appendChild(document.createTextNode(tr(key)));
    if (withSetupLink) {
      msgNode.appendChild(document.createTextNode(" "));
      var link = document.createElement("a");
      link.href = "/setup";
      link.textContent = tr("plan.create.go_setup");
      msgNode.appendChild(link);
    }
  }

  function clearMessage() {
    if (!msgNode) return;
    msgNode.textContent = "";
    msgNode.hidden = true;
    msgNode.classList.remove("plan-manage-msg--error");
  }

  function setBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    button.classList.toggle("loading", busy);
    button.setAttribute("aria-busy", busy ? "true" : "false");
  }

  /* Error copy is chosen from the CODE the shared contract returns, never from
   * a provider string, so a failure can never be narrated as a success. */
  var ERROR_COPY = {
    plan_changed: "plan.manage.error.plan_changed",
    freshness_unavailable: "plan.manage.error.freshness_unavailable",
    unknown_origin: "plan.manage.error.freshness_unavailable",
    refresh_failed: "plan.manage.error.refresh_failed",
    save_failed: "plan.manage.error.save_failed",
    save_unavailable: "plan.manage.error.save_failed",
    generation_unusable: "plan.manage.error.generation",
    generation_failed: "plan.manage.error.generation",
    generation_unavailable: "plan.manage.error.generation"
  };

  function reportFailure(result) {
    var code = result && result.code;
    if (code === management.CODE_NO_SESSION) {
      showMessage("plan.create.need_setup", true, true);
      return;
    }
    if (code === "plan_changed") {
      // The refusal itself is proof the page is out of date; surface the same
      // reload affordance the focus check would have.
      var notice = document.querySelector("[data-plan-stale-notice]");
      if (notice) notice.hidden = false;
    }
    showMessage(ERROR_COPY[code] || "plan.manage.error.generation", true, false);
  }

  /* Render the proposal as a PROPOSAL: a bounded, clearly-labelled preview of
   * what would replace the plan, built with textContent only. It is never
   * rendered into the active-plan region and never inherits its styling. */
  function renderProposal(program) {
    if (!proposalDays) return;
    proposalDays.replaceChildren();
    program.forEach(function (day) {
      var item = document.createElement("li");
      item.className = "plan-proposal-day";

      var label = document.createElement("span");
      label.className = "plan-proposal-day-name";
      label.textContent = day && day.gun ? String(day.gun) : tr("plan.day.untitled");
      item.appendChild(label);

      var meta = document.createElement("span");
      meta.className = "plan-proposal-day-meta";
      var exercises = (day && Array.isArray(day.egzersizler)) ? day.egzersizler : [];
      meta.textContent = (day && day.tip === "dinlenme")
        ? tr("plan.day.rest")
        : (day && day.odak ? String(day.odak) : tr("plan.day.workout"));
      item.appendChild(meta);

      if (!(day && day.tip === "dinlenme") && exercises.length) {
        var names = document.createElement("span");
        names.className = "plan-proposal-day-ex";
        names.textContent = exercises.map(function (exercise) {
          return exercise && exercise.isim ? String(exercise.isim) : tr("plan.ex.unnamed");
        }).join(" · ");
        item.appendChild(names);
      }
      proposalDays.appendChild(item);
    });
  }

  function showProposal(program) {
    renderProposal(program);
    if (proposalBox) {
      proposalBox.hidden = false;
      var heading = proposalBox.querySelector("[data-plan-manage-proposal-title]");
      if (heading && heading.focus) heading.focus();
    }
  }

  function hideProposal() {
    if (proposalBox) proposalBox.hidden = true;
    if (proposalDays) proposalDays.replaceChildren();
  }

  var manager = management.createPlanManagement({
    fetchImpl: window.fetch.bind(window),
    baseline: readBootstrap(),
    /* The canonical refresh after a successful write. Deliberately NOT a bare
     * `true`: that would make the module's "no success claim before a real
     * refresh" rule vacuous on this surface. It re-reads the canonical state and
     * confirms the server now holds a plan whose identity DIFFERS from the one
     * this page was rendered against — which is the only evidence the write took
     * effect that does not come from the proposal we just sent. The full
     * re-render is then the caller's reload; nothing is patched into the DOM
     * from the proposal. A read that fails returns false, and the workflow
     * reports the write as unconfirmed rather than as done. */
    refresh: async function () {
      try {
        var response = await window.fetch(
          management.CANONICAL_READ_URL, { method: "GET" });
        if (!response.ok) return false;
        var payload = await response.json();
        var plan = payload && payload.plan;
        if (!plan || typeof plan !== "object" || !plan.exists) return false;
        return management.compareBaselines(manager.getBaseline(), plan) === "changed";
      } catch (error) {
        return false;
      }
    }
  });

  /* ── Canonical staleness detection on return/focus (brief §16) ─────────────
   *
   * A Coach mutation (or another tab) can move the plan while this page sits
   * open showing a server render of the OLD one. PR2's refresh only exists on
   * pages that offer Start/Resume, and even there it refreshes execution state,
   * not the rendered program. So the management client asks the one canonical
   * question it is entitled to ask — "is this still the plan I was rendered
   * against?" — when the page comes back into use.
   *
   * Throttled to the same interval the workout-state client uses, so this is a
   * bounded read on return, never a poll. It reveals a notice and nothing else:
   * the page does not learn, guess, or render what the new plan is. */
  var FOCUS_MIN_MS = 5000;
  var lastFreshnessCheckAt = -Infinity;
  var staleNotice = document.querySelector("[data-plan-stale-notice]");

  async function checkCanonicalFreshness() {
    if (!staleNotice || !staleNotice.hidden) return;
    var now = Date.now();
    if (now - lastFreshnessCheckAt < FOCUS_MIN_MS) return;
    lastFreshnessCheckAt = now;
    var current;
    try {
      var response = await window.fetch(management.CANONICAL_READ_URL, { method: "GET" });
      if (!response.ok) return;                 // unknown is not "changed"
      var payload = await response.json();
      current = payload && payload.plan;
    } catch (error) {
      return;                                    // a failed read claims nothing
    }
    if (!current || typeof current !== "object") return;
    if (management.compareBaselines(manager.getBaseline(), current) !== "changed") return;
    staleNotice.hidden = false;
  }

  window.addEventListener("focus", checkCanonicalFreshness);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) checkCanonicalFreshness();
  });

  /* On an actionable Plan page `plan_workout.js` already refreshes the canonical
   * snapshot on focus/visibility/mutation. It hands the plan half of that
   * snapshot here so the same question is answered from the read that already
   * happened, instead of issuing a second one. This is a one-way notification of
   * a SERVER reading — it never lets the execution client decide anything about
   * the management workflow. */
  window.FitXPlanManageObserve = function (planPayload) {
    if (!planPayload || typeof planPayload !== "object") return;
    lastFreshnessCheckAt = Date.now();
    if (!staleNotice || !staleNotice.hidden) return;
    if (management.compareBaselines(manager.getBaseline(), planPayload) === "changed") {
      staleNotice.hidden = false;
    }
  };

  window.planManageOpen = function (element) {
    restoreFocusTo = element || document.activeElement;
    if (panel) panel.hidden = false;
    if (openBtn) openBtn.setAttribute("aria-expanded", "true");
    clearMessage();
    var first = panel && panel.querySelector("[data-plan-field]");
    if (first && first.focus) first.focus();
  };

  window.planManageClose = function () {
    manager.discardProposal();
    hideProposal();
    clearMessage();
    if (DESTRUCTIVE) {
      if (panel) panel.hidden = true;
      if (openBtn) {
        openBtn.setAttribute("aria-expanded", "false");
        if (openBtn.focus) openBtn.focus();
      }
    } else if (restoreFocusTo && restoreFocusTo.focus) {
      restoreFocusTo.focus();
    }
  };

  window.planManageGenerate = async function () {
    clearMessage();
    hideProposal();
    setBusy(generateBtn, true);
    showMessage("plan.manage.generating", false, false);
    var result = await manager.generate(collectSelections());
    setBusy(generateBtn, false);
    if (!result.ok) {
      reportFailure(result);
      return;
    }
    clearMessage();
    showProposal(result.proposal.program);
  };

  /* The confirm action. For CREATE it persists directly — nothing is destroyed.
   * For REGENERATE it must first pass an explicit destructive confirmation;
   * opening the generator or producing a proposal is never confirmation. */
  window.planManageConfirm = function (element) {
    if (!DESTRUCTIVE) return persist();
    restoreFocusTo = element || document.activeElement;
    if (!dialog) {
      // FAIL CLOSED. A destructive replacement without its confirmation is the
      // exact silent overwrite this workflow exists to prevent, so a missing
      // dialog refuses the operation rather than falling through to the write.
      showMessage("plan.manage.error.save_failed", true, false);
      return undefined;
    }
    dialog.hidden = false;
    dialog.setAttribute("aria-hidden", "false");
    var cancel = dialog.querySelector("[data-plan-replace-cancel]");
    if (cancel && cancel.focus) cancel.focus();
    return undefined;
  };

  window.planManageDismissConfirm = function () {
    if (!dialog) return;
    dialog.hidden = true;
    dialog.setAttribute("aria-hidden", "true");
    if (restoreFocusTo && restoreFocusTo.focus) restoreFocusTo.focus();
  };

  window.planManageReplace = function () {
    if (dialog) {
      dialog.hidden = true;
      dialog.setAttribute("aria-hidden", "true");
    }
    return persist();
  };

  async function persist() {
    clearMessage();
    setBusy(confirmBtn, true);
    showMessage("plan.manage.saving", false, false);
    var result = await manager.replace();
    if (!result.ok) {
      setBusy(confirmBtn, false);
      reportFailure(result);
      return;
    }
    // The write succeeded and the canonical refresh is this reload: the server,
    // not this file, decides what the active plan now is.
    window.location.reload();
  }

  // Escape closes the destructive confirmation only. It never confirms, and it
  // is deliberately NOT bound to the proposal panel, where a stray Escape during
  // review would discard a generation the user just paid for.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !dialog || dialog.hidden) return;
    event.preventDefault();
    window.planManageDismissConfirm();
  });

  // Keyboard-only completion: keep Tab inside the destructive dialog while open.
  if (dialog) {
    dialog.addEventListener("keydown", function (event) {
      if (event.key !== "Tab" || dialog.hidden) return;
      var focusable = Array.prototype.filter.call(
        dialog.querySelectorAll("button:not([disabled]), [href], [tabindex]:not([tabindex=\"-1\"])"),
        function (node) { return node.getClientRects().length > 0; }
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }
}());
