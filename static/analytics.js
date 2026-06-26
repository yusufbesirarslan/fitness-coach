/* ═══════════════════════════════════════════════════════
   AxisAI — Funnel / aktivasyon olayları (Google Analytics)
   gtag() _head.html'de tanımlanır; burada anlamlı olayları
   tek noktadan gönderir ve data-attribute'larla bağlarız.
   ═══════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // gtag henüz yoksa olayları yutmak yerine güvenle no-op'a düş.
  function track(name, params) {
    try {
      if (typeof window.gtag === "function") {
        window.gtag("event", name, params || {});
      }
    } catch (e) { /* analytics asla akışı bozmamalı */ }
  }

  // Bir aktivasyon olayını kullanıcı/cihaz başına yalnızca bir kez gönder
  // (ör. first_meal_logged). localStorage bayrağıyla tekrarları bastır.
  function trackOnce(name, params) {
    var key = "fx_evt_" + name;
    try {
      if (localStorage.getItem(key)) return;
      localStorage.setItem(key, "1");
    } catch (e) { /* gizli mod vb. — yine de gönder */ }
    track(name, params);
  }

  // Aktivasyon = ilk anlamlı eylem (ilk öğün VEYA ilk plan). Genel "activation"
  // olayını da bir kez tetikle ki funnel'da tek metrik olarak izlenebilsin.
  function activation(source) {
    trackOnce("activation", { source: source });
  }

  window.fxTrack = track;
  window.fxTrackOnce = trackOnce;
  window.fxActivation = activation;

  // Bildirimsel bağlama: data-ga-event="ad" [data-ga-once] olan ögelere tıklama.
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-ga-event]");
    if (!el) return;
    var name = el.getAttribute("data-ga-event");
    var params = {};
    var p = el.getAttribute("data-ga-params");
    if (p) { try { params = JSON.parse(p); } catch (_) {} }
    if (el.hasAttribute("data-ga-once")) trackOnce(name, params);
    else track(name, params);
  });

  // Sayfa, funnel adımını body[data-ga-step] ile bildirebilir (ör. setup_step_2).
  document.addEventListener("DOMContentLoaded", function () {
    var step = document.body && document.body.getAttribute("data-ga-step");
    if (step) track(step);
  });
})();
