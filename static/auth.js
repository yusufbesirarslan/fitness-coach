(function () {
  "use strict";

  function qs(id) {
    return document.getElementById(id);
  }

  function tr(key, vars) {
    if (typeof window.t === "function") return window.t(key, vars);
    return key;
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
    localStorage.setItem("theme", document.documentElement.dataset.theme);
  }

  function initTheme() {
    setTheme(localStorage.getItem("theme") || "dark");
  }

  window.toggleTheme = function () {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  };

  function showNotice(type, message) {
    var error = qs("error-msg");
    var success = qs("success-msg");
    if (error) error.classList.remove("show");
    if (success) success.classList.remove("show");
    var target = type === "success" ? success : error;
    if (!target) return;
    target.textContent = message || tr("common.error");
    target.classList.add("show");
  }

  function setButtonLoading(button, loading, label) {
    if (!button) return;
    if (loading) {
      button.dataset.label = button.textContent;
      button.textContent = label || tr("common.saving");
      button.disabled = true;
      button.classList.add("is-loading");
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  }

  function normalizedValue(id) {
    var el = qs(id);
    return el ? el.value.trim() : "";
  }

  function submitOnEnter(event, fn) {
    if (event && event.key === "Enter") {
      event.preventDefault();
      fn();
    }
  }

  window.togglePassword = function (el) {
    var target = qs(el.getAttribute("data-target"));
    if (!target) return;
    var showing = target.type === "text";
    target.type = showing ? "password" : "text";
    el.setAttribute("aria-pressed", showing ? "false" : "true");
    el.textContent = showing ? "Show" : "Hide";
  };

  window.login = async function (el) {
    var username = normalizedValue("username");
    var passwordInput = qs("password");
    var password = passwordInput ? passwordInput.value : "";
    if (!username || !password) {
      showNotice("error", tr("auth.all_fields_required"));
      return;
    }

    setButtonLoading(el, true, tr("login.submit"));
    try {
      var response = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username, password: password })
      });
      var data = await response.json();
      if (response.ok) {
        showNotice("success", (data.message || tr("auth.welcome", { username: username })) + " " + tr("common.redirecting"));
        window.location.href = "/";
      } else if (data.needs_verification) {
        window.location.href = "/verify?u=" + encodeURIComponent(data.username || username);
      } else {
        showNotice("error", data.error);
      }
    } catch (err) {
      showNotice("error", tr("common.conn_error_retry"));
    } finally {
      setButtonLoading(el, false);
    }
  };

  window.loginOnEnter = function (el, event) {
    submitOnEnter(event, function () { window.login(qs("login-submit")); });
  };

  function passwordScore(value) {
    var score = 0;
    if (value.length >= 8) score += 1;
    if (/[A-Za-z]/.test(value) && /[0-9]/.test(value)) score += 1;
    if (value.length >= 12 || /[^A-Za-z0-9]/.test(value)) score += 1;
    return Math.min(score, 3);
  }

  window.updatePasswordStrength = function (el) {
    var meter = qs("password-strength");
    var text = qs("password-strength-text");
    if (!meter || !text) return;
    var score = passwordScore(el.value || "");
    meter.dataset.score = String(score);
    if (!el.value) text.textContent = tr("register.password_ph");
    else if (score < 2) text.textContent = tr("validate.password_min");
    else if (score < 3) text.textContent = tr("validate.password_digit");
    else text.textContent = "Strong password";
  };

  window.register = async function (el) {
    var username = normalizedValue("username");
    var email = normalizedValue("email");
    var passwordInput = qs("password");
    var password = passwordInput ? passwordInput.value : "";
    if (!username || !email || !password) {
      showNotice("error", tr("auth.all_fields_required"));
      return;
    }
    if (passwordScore(password) < 2) {
      showNotice("error", tr("register.password_ph"));
      return;
    }

    if (window.fxTrack) window.fxTrack("register_submit");
    setButtonLoading(el, true, tr("register.submit"));
    try {
      var response = await fetch("/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username,
          email: email,
          password: password,
          language: window.LOCALE
        })
      });
      var data = await response.json();
      if (response.ok) {
        if (window.fxTrack) window.fxTrack("register_success", { referred: !!data.referred });
        showNotice("success", data.message + " " + tr("common.redirecting"));
        var dest = data.needs_verification
          ? "/verify?u=" + encodeURIComponent(data.username || username)
          : "/login";
        window.setTimeout(function () { window.location.href = dest; }, 900);
      } else {
        showNotice("error", data.error);
      }
    } catch (err) {
      showNotice("error", tr("common.conn_error_retry"));
    } finally {
      setButtonLoading(el, false);
    }
  };

  window.registerOnEnter = function (el, event) {
    submitOnEnter(event, function () { window.register(qs("register-submit")); });
  };

  window.verifyCode = async function (el) {
    var username = normalizedValue("username");
    var code = normalizedValue("code");
    if (!username || !code) {
      showNotice("error", tr("verify.username_code_required"));
      return;
    }
    setButtonLoading(el, true, tr("verify.submit"));
    try {
      var response = await fetch("/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username, code: code })
      });
      var data = await response.json();
      if (response.ok) {
        showNotice("success", data.message + " " + tr("common.redirecting"));
        window.setTimeout(function () { window.location.href = "/login"; }, 900);
      } else {
        showNotice("error", data.error);
      }
    } catch (err) {
      showNotice("error", tr("common.conn_error_retry"));
    } finally {
      setButtonLoading(el, false);
    }
  };

  window.resendCode = async function (el) {
    var username = normalizedValue("username");
    if (!username) {
      showNotice("error", tr("verify.enter_username_first"));
      return;
    }
    setButtonLoading(el, true, tr("verify.resend"));
    try {
      var response = await fetch("/verify/resend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username })
      });
      var data = await response.json();
      showNotice(response.ok ? "success" : "error", response.ok ? data.message : data.error);
    } catch (err) {
      showNotice("error", tr("common.conn_error_retry"));
    } finally {
      setButtonLoading(el, false);
    }
  };

  window.verifyOnEnter = function (el, event) {
    submitOnEnter(event, function () { window.verifyCode(qs("verify-submit")); });
  };

  var currentStep = 0;
  var selections = { goal: "", level: "", activity: "" };

  function updateDots() {
    for (var i = 0; i < 4; i += 1) {
      var dot = qs("dot-" + i);
      if (!dot) continue;
      dot.classList.remove("active", "done");
      if (i === currentStep) dot.classList.add("active");
      else if (i < currentStep) dot.classList.add("done");
    }
  }

  function showStep(n) {
    document.querySelectorAll(".step-content").forEach(function (step) {
      step.classList.remove("active");
    });
    var target = qs("step-" + n);
    if (!target) return;
    target.classList.add("active");
    currentStep = n;
    updateDots();
    if (window.fxTrack) window.fxTrack("setup_step_" + n);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  window.selectOption = function (type, value, el) {
    selections[type] = value;
    var parent = el.closest(".option-cards");
    if (!parent) return;
    parent.querySelectorAll(".option-card").forEach(function (card) {
      card.classList.remove("selected");
      card.setAttribute("aria-checked", "false");
    });
    el.classList.add("selected");
    el.setAttribute("aria-checked", "true");
  };

  function validNumber(id, min, max) {
    var value = Number((qs(id) || {}).value);
    return Number.isFinite(value) && value >= min && value <= max;
  }

  window.nextStep = function (from) {
    if (from === 0) {
      if (!validNumber("s-weight", 30, 300) || !validNumber("s-height", 100, 240) || !validNumber("s-age", 13, 100)) {
        showNotice("error", tr("setup.fill_all"));
        return;
      }
    }
    if (from === 1 && !selections.goal) {
      showNotice("error", tr("setup.pick_goal"));
      return;
    }
    if (from === 2 && !selections.level) {
      showNotice("error", tr("setup.pick_level"));
      return;
    }
    showStep(from + 1);
  };

  window.prevStep = function (from) {
    showStep(from - 1);
  };

  window.finishSetup = async function (el) {
    if (!selections.activity) {
      showNotice("error", tr("setup.pick_activity"));
      return;
    }
    setButtonLoading(el, true, tr("setup.saving"));
    try {
      var response = await fetch("/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          weight: parseFloat(qs("s-weight").value),
          height: parseFloat(qs("s-height").value),
          age: parseInt(qs("s-age").value, 10),
          gender: qs("s-gender").value,
          goal: selections.goal,
          fitness_level: selections.level,
          current_activity: selections.activity,
          target_weight: normalizedValue("s-target-weight")
        })
      });
      var data = await response.json();
      if (!response.ok || data.error) {
        showNotice("error", data.error || tr("common.error"));
        return;
      }
      if (window.fxTrackOnce) window.fxTrackOnce("first_plan_generated");
      if (window.fxActivation) window.fxActivation("setup");
      qs("r-bmr").textContent = data.bmr;
      qs("r-tdee").textContent = data.tdee;
      qs("r-target").textContent = data.target_calories;
      qs("r-message").textContent = (selections.goal === "kas kazanma"
        ? tr("setup.goal_gain_msg", { cal: data.target_calories })
        : tr("setup.goal_loss_msg", { cal: data.target_calories })) + " " + tr("setup.coach_waiting");
      document.querySelectorAll(".step-content").forEach(function (step) {
        step.classList.remove("active");
      });
      qs("step-result").classList.add("active");
      for (var i = 0; i < 4; i += 1) {
        var dot = qs("dot-" + i);
        if (dot) {
          dot.classList.remove("active");
          dot.classList.add("done");
        }
      }
      showNotice("success", tr("route.profile_saved"));
    } catch (err) {
      showNotice("error", tr("common.conn_error_retry"));
    } finally {
      setButtonLoading(el, false);
    }
  };

  document.addEventListener("DOMContentLoaded", initTheme);
})();
