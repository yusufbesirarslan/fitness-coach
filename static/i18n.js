/* AxisAI i18n — istemci tarafı çeviri yardımcısı.
   window.I18N (anahtar→metin sözlüğü) ve window.LOCALE _head.html'de, bu
   script'ten ÖNCE, nonce'lı küçük bir blokla set edilir. Sunucu sözlüğü
   (locales/{tr,en}.json) ile AYNI anahtarları kullanır → tek kaynak.

   Kullanım:
     t('coach.greeting')                         → düz metin
     t('toast.added', {dish: 'Tavuk'})           → {dish} yerine değer koyar
   Eksik anahtar: anahtarın kendisini döndürür (kırılmaz, görünür kalır). */
(function () {
  window.I18N = window.I18N || {};
  window.LOCALE = window.LOCALE || 'tr';

  window.t = function (key, vars) {
    var s = (window.I18N && window.I18N[key] != null) ? window.I18N[key] : key;
    if (vars && typeof s === 'string') {
      Object.keys(vars).forEach(function (k) {
        s = s.split('{' + k + '}').join(String(vars[k]));
      });
    }
    return s;
  };

  // Dil değiştir: session'a yaz (girişliyse hesaba da) ve sayfayı yenile.
  // CSRF: static/csrf.js window.fetch'i sarıp X-CSRFToken'ı otomatik ekler.
  window.setLang = function (lang) {
    if (lang === window.LOCALE) return;
    fetch('/set-language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lang: lang })
    }).then(function () {
      try { localStorage.setItem('lang', lang); } catch (e) {}
      window.location.reload();
    }).catch(function () { window.location.reload(); });
  };
})();
