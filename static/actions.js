/* FitX olay-delegasyonu — satır-içi on* (onclick/oninput/...) işleyicilerinin
 * yerini alır. Böylece CSP'den script-src-attr 'unsafe-inline' kaldırılabilir
 * (bkz. app/hooks.py). Öğeler davranışlarını HTML nitelikleriyle bildirir:
 *
 *   data-action="fn"                 → tıklamada window.fn(...) çağır
 *   data-action-self="fn"            → yalnızca tıklanan öğe niteliğin sahibiyse
 *                                       (modal arka-planı kapatma) çağır
 *   data-action-input/-change/-keydown="fn"  → ilgili olayda çağır
 *   data-args='["a", 1]'             → fn'e iletilecek sabit argümanlar (JSON)
 *
 * Çağrı biçimi: fn.apply(el, args.concat([el, event])). Yani fn İÇİNDE
 * `this === öğe`, ardından gelen iki parametre (öğe, olay). Eski satır-içi
 * `fn('x', this)` çağrıları `data-action="fn" data-args='["x"]'` olur ve
 * argüman sırası KORUNUR (this artık son sıradaki öğe parametresine düşer).
 *
 * Dinleyiciler document seviyesinde olduğu için JS ile sonradan eklenen DOM
 * (innerHTML şablonları) da otomatik kapsanır — ayrı bağlama gerekmez. */
(function () {
  "use strict";

  function parseArgs(el) {
    var raw = el.getAttribute("data-args");
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch (err) {
      console.warn("[actions] geçersiz data-args:", raw, err);
      return [];
    }
  }

  function dispatch(name, el, event) {
    if (!name) return;
    // Noktalı isimleri çöz (ör. "CW.send"): son segmentin sahibi `this` olur,
    // böylece nesne metotları doğru bağlamla çalışır. Tek segmentlilerde `this`
    // tıklanan öğedir (eski satır-içi `fn(this)` davranışıyla uyumlu).
    var parts = name.split(".");
    var owner = window;
    for (var i = 0; i < parts.length - 1 && owner; i++) owner = owner[parts[i]];
    var fn = owner && owner[parts[parts.length - 1]];
    if (typeof fn !== "function") {
      console.warn("[actions] '" + name + "' için fonksiyon yok.");
      return;
    }
    var thisArg = parts.length > 1 ? owner : el;
    fn.apply(thisArg, parseArgs(el).concat([el, event]));
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-action]");
    if (el) dispatch(el.getAttribute("data-action"), el, e);
    // Modal arka-planı: yalnızca arka-planın kendisine (çocuklarına değil) tıklanınca.
    var selfEl = e.target.closest("[data-action-self]");
    if (selfEl && e.target === selfEl) {
      dispatch(selfEl.getAttribute("data-action-self"), selfEl, e);
    }
  });

  ["input", "change", "keydown"].forEach(function (type) {
    var attr = "data-action-" + type;
    document.addEventListener(type, function (e) {
      var el = e.target.closest("[" + attr + "]");
      if (el) dispatch(el.getAttribute(attr), el, e);
    });
  });

  /* ── Genel yardımcılar: isimli fonksiyonu olmayan saf-DOM satır-içi
     işleyicilerin (önceden onclick/oninput içinde yazılıydı) yerini alır. ── */

  // <el data-action="fxToggleClass" data-class="on"> → el.classList.toggle('on')
  window.fxToggleClass = function (el) {
    var cls = el.getAttribute("data-class");
    if (cls) el.classList.toggle(cls);
  };

  // <input data-action-input="fxSetText" data-target="id"> → #id.textContent = el.value
  window.fxSetText = function (el) {
    var target = document.getElementById(el.getAttribute("data-target"));
    if (target) target.textContent = el.value;
  };

  // <select data-action-change="fxValue" data-fn="setTimeframe"> → setTimeframe(el.value)
  window.fxValue = function (el) {
    var fn = window[el.getAttribute("data-fn")];
    if (typeof fn === "function") fn(el.value);
  };

  // <div data-action="fxClickTarget" data-target="avatar-file-input"> → #id.click()
  window.fxClickTarget = function (el) {
    var target = document.getElementById(el.getAttribute("data-target"));
    if (target) target.click();
  };

  // <button data-action="fxHref" data-href="/"> → window.location.href = "/"
  window.fxHref = function (el) {
    var href = el.getAttribute("data-href");
    if (href) window.location.href = href;
  };
})();
