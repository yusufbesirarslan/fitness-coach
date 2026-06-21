/* CSRF synchronizer token enjeksiyonu.
 *
 * Durum-değiştiren (POST/PUT/PATCH/DELETE) aynı-origin fetch'lere X-CSRFToken
 * başlığını otomatik ekler. Token <meta name="csrf-token"> etiketinden okunur;
 * sunucu oturumdaki değerle karşılaştırır (bkz. app/hooks.py).
 *
 * _head.html'den (yani <head> içinde, gövde script'lerinden ÖNCE) senkron
 * yüklenir → herhangi bir sayfa/JS fetch çağrısı yapmadan önce window.fetch
 * sarmalanmış olur. GET ve çapraz-origin istekler değiştirilmez. */
(function () {
  "use strict";
  if (!window.fetch || window.__fitxCsrfWrapped) return;
  window.__fitxCsrfWrapped = true;
  var STATE_CHANGING = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function sameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return true;  // göreli/parse edilemeyen URL → aynı origin say
    }
  }

  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    init = init || {};
    var method = (init.method ||
      (input && typeof input === "object" && input.method) || "GET").toUpperCase();
    var url = (typeof input === "string") ? input : (input && input.url) || "";
    if (STATE_CHANGING[method] && sameOrigin(url)) {
      var token = csrfToken();
      if (token) {
        var headers = new Headers(init.headers ||
          (input && typeof input === "object" && input.headers) || {});
        if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
        init = Object.assign({}, init, { headers: headers });
      }
    }
    return nativeFetch(input, init);
  };
})();
