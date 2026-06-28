/* AI Coach Floating Chat Widget — self-injecting, no template include needed.
   Add <script src="/static/coach_widget.js"></script> before </body>. */
(function () {
  'use strict';

  /* ── 1. Inject CSS ──
     Harici stylesheet olarak yüklenir: <link rel="stylesheet" href="/static/coach_widget.css">.
     CSP style-src-elem 'self' same-origin <link>'lere izin verir; nonce gerekmez.
     (Eskiden CSS JS ile <style> bloğu olarak enjekte ediliyordu; style-src-elem
     nonce zorunlu olunca nonce'suz inline <style> bloklanıp widget stilsiz kalıyordu —
     bkz. app/hooks.py set_csp_header.) */
  if (!document.getElementById('cw-style')) {
    var cwLink = document.createElement('link');
    cwLink.id   = 'cw-style';
    cwLink.rel  = 'stylesheet';
    cwLink.href = '/static/coach_widget.css';
    document.head.appendChild(cwLink);
  }

  /* ── 2. Inject HTML ── */
  var html = '<div id="cw-root">' +

    '<div id="cw-window" role="dialog" aria-label="AI Fitness Coach">' +
      '<div id="cw-header">' +
        '<div id="cw-hleft">' +
          '<div id="cw-avatar">🏋️</div>' +
          '<div>' +
            '<div id="cw-htitle">AI Fitness Coach</div>' +
          '</div>' +
        '</div>' +
        '<button id="cw-close" aria-label="Kapat">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
          '</svg>' +
        '</button>' +
      '</div>' +
      '<div id="cw-msgs" role="log" aria-live="polite"></div>' +

      '<div id="cw-qr-menu" role="menu">' +
        '<button class="cw-qr-opt" id="cw-qr-scan" role="menuitem">' +
          '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3"/><path d="M20 14v3h-3"/><path d="M14 20h3"/></svg>' +
          t('coach.qr_scan') +
        '</button>' +
        '<button class="cw-qr-opt" id="cw-qr-url" role="menuitem">' +
          '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>' +
          t('coach.url_enter') +
        '</button>' +
      '</div>' +

      '<div id="cw-urlbox">' +
        '<div id="cw-urlbox-label">Menü URL\'si</div>' +
        '<div id="cw-urlbox-row">' +
          '<input type="url" id="cw-url-input" placeholder="https://menu.example.com" autocomplete="off">' +
          '<button id="cw-url-go">ANALİZ ET</button>' +
        '</div>' +
      '</div>' +

      '<div id="cw-irow">' +
        '<button id="cw-qr" aria-label="Menü tara">' +
          '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3"/><path d="M20 14v3h-3"/><path d="M14 20h3"/></svg>' +
        '</button>' +
        '<input type="text" id="cw-input" placeholder="' + t('coach.placeholder') + '" autocomplete="off">' +
        '<button id="cw-send" aria-label="' + t('coach.send') + '">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' +
            '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>' +
          '</svg>' +
        '</button>' +
      '</div>' +

      '<div id="cw-scan">' +
        '<div id="cw-scan-head">' +
          '<div id="cw-scan-title">MENÜ TARA</div>' +
          '<button id="cw-scan-close" aria-label="Kapat">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">' +
              '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
            '</svg>' +
          '</button>' +
        '</div>' +
        '<div id="cw-scan-reader"></div>' +
        '<div id="cw-scan-hint">QR kodu kameraya gösterin</div>' +
        '<div id="cw-scan-status"></div>' +
      '</div>' +

    '</div>' +

    '<button id="cw-fab" aria-label="' + t('coach.fab') + '">' +
      '<span id="cw-badge" aria-hidden="true"></span>' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
      '</svg>' +
    '</button>' +

  '</div>' +
  '<div id="cw-notify" aria-live="assertive"></div>';

  var wrap = document.createElement('div');
  wrap.innerHTML = html;
  while (wrap.firstChild) document.body.appendChild(wrap.firstChild);

  /* ── 3. Wire events ── */
  document.getElementById('cw-close').addEventListener('click', function () { CW.toggle(); });
  document.getElementById('cw-fab').addEventListener('click', function () { CW.toggle(); });
  document.getElementById('cw-send').addEventListener('click', function () { CW.send(); });
  document.getElementById('cw-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); CW.send(); }
  });

  document.getElementById('cw-qr').addEventListener('click', function (e) {
    e.stopPropagation();
    CW.toggleQrMenu();
  });
  document.getElementById('cw-qr-scan').addEventListener('click', function () { CW.startScan(); });
  document.getElementById('cw-qr-url').addEventListener('click', function () { CW.promptUrl(); });
  document.getElementById('cw-scan-close').addEventListener('click', function () { CW.stopScan(); });
  document.getElementById('cw-url-go').addEventListener('click', function () { CW.submitUrl(); });
  document.getElementById('cw-url-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); CW.submitUrl(); }
  });
  /* Close the popover when clicking elsewhere */
  document.addEventListener('click', function (e) {
    var m = document.getElementById('cw-qr-menu');
    if (m && m.classList.contains('cw-open') && !m.contains(e.target) && e.target.id !== 'cw-qr') {
      CW.hideQrMenu();
    }
  });

  /* ── 4. CW object ── */
  var STORAGE_KEY  = 'fc_coach_messages';
  var MAX_MESSAGES = 60;
  // jsdelivr: CSP script-src bu TAM dosyaya sabitlenmiştir (SEC1 — geniş host
  // joker'i kaldırıldı; hooks.py CSP'si aynı sabit URL'i listeler). SRI hash'i +
  // crossorigin ile yüklenir; sürüm/hash değişirse hooks.py CSP'si de güncellenmeli.
  var QR_LIB_SRC   = 'https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js';
  var QR_LIB_SRI   = 'sha384-c9d8RFSL+u3exBOJ4Yp3HUJXS4znl9f+z66d1y54ig+ea249SpqR+w1wyvXz/lk+';

  var CW = window.CW = {
    open:     false,
    busy:     false,
    unread:   false,
    messages: [],
    _scanner: null,

    init: function () {
      try {
        var s = sessionStorage.getItem(STORAGE_KEY);
        this.messages = s ? JSON.parse(s) : [];
      } catch (_) { this.messages = []; }

      if (this.messages.length === 0) {
        this._push('bot', t('coach.greeting'));
      } else {
        this._render();
      }
    },

    toggle: function () {
      this.open = !this.open;
      var win = document.getElementById('cw-window');
      var fab = document.getElementById('cw-fab');
      if (this.open) {
        win.classList.add('cw-open');
        fab.classList.add('cw-hidden');
        this.unread = false;
        document.getElementById('cw-badge').style.display = 'none';
        this._scrollBottom();
        setTimeout(function () {
          var inp = document.getElementById('cw-input');
          if (inp) inp.focus();
        }, 240);
      } else {
        win.classList.remove('cw-open');
        fab.classList.remove('cw-hidden');
        this.hideQrMenu();
        this.hideUrlBox();
        this.stopScan();
      }
    },

    send: function () {
      var input    = document.getElementById('cw-input');
      var question = input.value.trim();
      if (!question || this.busy) return;
      input.value = '';
      this._push('user', question);
      this._setLoading(true);
      var self = this;
      var history = this.messages.slice(-8)
        .filter(function (m) { return m.type !== 'menu'; })
        .map(function (m) {
          return { role: m.role === 'user' ? 'user' : 'bot', text: m.text || '' };
        });
      fetch('/ask', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ question: question, history: history })
      })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        self._setLoading(false);
        self._push('bot', d.answer || d.error || t('coach.no_reply'));
      })
      .catch(function () {
        self._setLoading(false);
        self._push('bot', t('coach.conn_error'));
      });
    },

    receiveCheckinFeedback: function (text) {
      this._push('bot', t('coach.checkin_feedback') + '\n\n' + text);
      if (!this.open) {
        this.unread = true;
        document.getElementById('cw-badge').style.display = 'block';
        this._showNotify();
      }
    },

    /* ── QR / menu flow ── */
    _normUrl: function (raw) {
      var url = (raw || '').trim();
      if (!url) return '';
      if (!/^https?:\/\//i.test(url)) {
        if (/^[a-zA-Z0-9]/.test(url)) url = 'https://' + url;
        else return '';
      }
      return url;
    },

    toggleQrMenu: function () {
      var m = document.getElementById('cw-qr-menu');
      if (m) m.classList.toggle('cw-open');
      this.hideUrlBox();
    },

    hideQrMenu: function () {
      var m = document.getElementById('cw-qr-menu');
      if (m) m.classList.remove('cw-open');
    },

    promptUrl: function () {
      this.hideQrMenu();
      var box = document.getElementById('cw-urlbox');
      if (!box) return;
      box.classList.add('cw-open');
      var inp = document.getElementById('cw-url-input');
      if (inp) { inp.value = ''; setTimeout(function () { inp.focus(); }, 50); }
    },

    hideUrlBox: function () {
      var box = document.getElementById('cw-urlbox');
      if (box) box.classList.remove('cw-open');
    },

    submitUrl: function () {
      var inp = document.getElementById('cw-url-input');
      var url = this._normUrl(inp ? inp.value : '');
      if (!url) { this._toast('Geçerli bir URL girin.', 'error'); return; }
      this.hideUrlBox();
      this.processMenuUrl(url);
    },

    startScan: function () {
      this.hideQrMenu();
      var self = this;
      var overlay = document.getElementById('cw-scan');
      if (!overlay) return;
      overlay.classList.add('cw-open');
      this._scanStatus('');

      var begin = function () {
        if (typeof Html5Qrcode === 'undefined') { self._scanStatus('Tarayıcı yüklenemedi.'); return; }
        if (!window.isSecureContext) {
          self._scanStatus('Kamera yalnızca güvenli (HTTPS) bağlantıda açılır. "URL Gir" seçeneğini kullanın.');
          return;
        }
        var reader = document.getElementById('cw-scan-reader');
        if (reader) reader.innerHTML = '';
        var qrCfg = { fps: 10, qrbox: { width: 220, height: 220 } };
        var onDecode = function (decoded) {
          var url = self._normUrl(decoded);
          self.stopScan();
          if (!url) { self._toast('QR kod geçerli bir URL içermiyor.', 'error'); return; }
          self.processMenuUrl(url);
        };
        var fail = function (err) {
          var info = ((err && (err.name || err.type)) || '') + ' ' + ((err && err.message) || err || '');
          if (/NotAllowed|Permission|denied/i.test(info)) {
            self._scanStatus('Kamera izni reddedildi. Tarayıcı ayarlarından kamera iznini açıp tekrar deneyin.');
          } else if (/NotReadable|TrackStart|in use/i.test(info)) {
            self._scanStatus('Kamera başka bir uygulama tarafından kullanılıyor.');
          } else if (/NotFound|Overconstrained|no camera|devices/i.test(info)) {
            self._scanStatus('Uygun kamera bulunamadı. "URL Gir" seçeneğini kullanabilirsiniz.');
          } else {
            self._scanStatus('Kamera açılamadı. "URL Gir" seçeneğini kullanabilirsiniz.');
          }
        };
        // Arka kamerayı dene; başarısız olursa (örn. masaüstü) mevcut kameraya düş.
        var fallbackToAnyCamera = function () {
          Html5Qrcode.getCameras().then(function (cams) {
            if (!cams || !cams.length) { fail({ name: 'NotFoundError' }); return; }
            var back = cams.filter(function (c) { return /back|rear|arka|environment/i.test(c.label || ''); })[0];
            var cam  = back || cams[cams.length - 1];
            self._scanner.start(cam.id, qrCfg, onDecode, function () {})
              .then(function () { self._scanStatus(''); })
              .catch(fail);
          }).catch(fail);
        };
        try {
          self._scanner = new Html5Qrcode('cw-scan-reader');
          self._scanStatus('Kamera başlatılıyor...');
          self._scanner.start({ facingMode: 'environment' }, qrCfg, onDecode, function () {})
            .then(function () { self._scanStatus(''); })
            .catch(fallbackToAnyCamera);
        } catch (e) { fail(e); }
      };

      if (typeof Html5Qrcode !== 'undefined') {
        begin();
      } else {
        self._scanStatus('Tarayıcı yükleniyor...');
        var s = document.createElement('script');
        s.src = QR_LIB_SRC;
        s.integrity = QR_LIB_SRI;       // SEC1: tedarik-zinciri bütünlük doğrulaması
        s.crossOrigin = 'anonymous';    // SRI'nin cross-origin script'te çalışması için şart
        s.onload = function () { self._scanStatus(''); begin(); };
        s.onerror = function () { self._scanStatus('Tarayıcı yüklenemedi.'); };
        document.head.appendChild(s);
      }
    },

    stopScan: function () {
      var overlay = document.getElementById('cw-scan');
      if (overlay) overlay.classList.remove('cw-open');
      if (this._scanner) {
        try { this._scanner.stop().catch(function () {}); } catch (e) {}
        this._scanner = null;
      }
    },

    _scanStatus: function (msg) {
      var el = document.getElementById('cw-scan-status');
      if (el) el.textContent = msg || '';
    },

    processMenuUrl: function (url) {
      var self = this;
      if (!this.open) this.toggle();
      this._push('user', '🔗 Menü: ' + url);
      this._setLoading(true);

      fetch('/api/proxy/scan-menu', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ url: url })
      })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          throw { msg: (res.d && res.d.error) || 'Menü içeriği şu anda korumalı veya okunamıyor. Lütfen linki kontrol edip tekrar deneyin.' };
        }
        var pd = res.d;
        var menuText = [pd.title || ''].concat(pd.headings || [], [pd.body_text || '']).join('\n');
        if (!menuText || menuText.trim().length < 20) {
          throw { msg: 'Menü içeriği şu anda korumalı veya okunamıyor. Lütfen linki kontrol edip tekrar deneyin.' };
        }
        var body = { menu_text: menuText };
        if (pd.menu_source) body.menu_source = pd.menu_source;
        if (pd.framework_state) body.framework_state = pd.framework_state;
        if (pd.headings && pd.headings.length) body.headings = pd.headings;
        return fetch('/api/menu/analyze', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(body)
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
      })
      .then(function (res) {
        self._setLoading(false);
        var result = res.d;
        if (!res.ok || result.success === false) {
          self._push('bot', result.message || result.error || 'Menü metni işlenirken bir hata oluştu. Lütfen tekrar deneyin.');
          return;
        }
        var cats = result.categories || {};
        var totalItems = Object.keys(cats).reduce(function (s, k) { return s + (cats[k] || []).length; }, 0);
        if (result.error && totalItems === 0) {
          self._push('bot', result.message || result.error);
          return;
        }
        if (!totalItems && !(result.coach_picks || []).length) {
          self._push('bot', 'Menüde analiz edilebilir yemek bulunamadı. Lütfen başka bir menü deneyin.');
          return;
        }
        self._pushMenu(result);
      })
      .catch(function (err) {
        self._setLoading(false);
        self._push('bot', (err && err.msg) || 'Bağlantı hatası. Lütfen internet bağlantınızı kontrol edip tekrar deneyin.');
      });
    },

    // data-action köprüsü: data-dd niteliğindeki (escape'li) JSON'u addToLog'a aktarır.
    addDishFromEl: function (el) {
      this.addToLog(el, el.dataset.dd);
    },

    addToLog: function (btn, dishJson) {
      var d;
      try { d = JSON.parse(dishJson); } catch (e) { return; }
      var self = this;
      var orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '...';
      var h    = new Date().getHours();
      var ogun = h < 11 ? 'kahvalti' : h < 15 ? 'ogle' : h < 20 ? 'aksam' : 'ara';
      fetch('/meal-log', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          ogun: ogun,
          yemekler: d.name,
          override_macros: {
            kalori:  d.macros.calories,
            protein: d.macros.protein,
            karb:    d.macros.carbs,
            yag:     d.macros.fat
          }
        })
      })
      .then(function (r) {
        if (!r.ok) throw new Error('fail');
        btn.textContent = 'Eklendi ✓';
        btn.style.color = '#00C48C';
        btn.style.borderColor = 'rgba(0,196,140,0.3)';
        self._toast(d.name + ' günlük kayda eklendi', 'success');
      })
      .catch(function () {
        btn.textContent = 'Hata';
        btn.disabled = false;
        setTimeout(function () { btn.textContent = orig || 'Günlük Kayda Ekle'; }, 2000);
        self._toast('Kayıt başarısız.', 'error');
      });
    },

    _push: function (role, text) {
      var now  = new Date();
      var time = now.toLocaleTimeString(window.LOCALE === 'en' ? 'en-US' : 'tr-TR', { hour: '2-digit', minute: '2-digit' });
      this.messages.push({ role: role, text: text, time: time });
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.messages.slice(-MAX_MESSAGES))); } catch (_) {}
      this._render();
      this._scrollBottom();
    },

    _pushMenu: function (result) {
      var now  = new Date();
      var time = now.toLocaleTimeString(window.LOCALE === 'en' ? 'en-US' : 'tr-TR', { hour: '2-digit', minute: '2-digit' });
      var picks = (result.coach_picks || []).length;
      this.messages.push({ role: 'bot', type: 'menu', data: result, text: 'Menü analizi (' + picks + ' öneri)', time: time });
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.messages.slice(-MAX_MESSAGES))); } catch (_) {}
      this._render();
      this._scrollBottom();
    },

    _esc: function (s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    _dishCard: function (dd, isTop) {
      var self = this;
      var scoreClass = dd.score >= 50 ? 'high' : dd.score >= 20 ? 'mid' : 'low';
      var warns = (dd.warnings || []).map(function (w) {
        return '<span class="cw-dish-warn">' + self._esc(w) + '</span>';
      }).join('');
      var m = dd.macros || {};
      var ddJson = this._esc(JSON.stringify(dd));
      return '<div class="cw-dish' + (isTop ? ' top' : '') + '">' +
          '<div class="cw-dish-h">' +
            '<div class="cw-dish-name">' + this._esc(dd.name) + '</div>' +
            '<div class="cw-dish-score ' + scoreClass + '">' + (dd.score > 0 ? '+' : '') + dd.score + '</div>' +
          '</div>' +
          '<div class="cw-dish-reason">' + this._esc(dd.reason) + '</div>' +
          '<div class="cw-dish-macros">' +
            '<div class="cw-dish-macro"><span>' + m.calories + '</span> kcal</div>' +
            '<div class="cw-dish-macro"><span>' + m.protein + 'g</span> protein</div>' +
            '<div class="cw-dish-macro"><span>' + m.carbs + 'g</span> karb</div>' +
            '<div class="cw-dish-macro"><span>' + m.fat + 'g</span> yağ</div>' +
          '</div>' +
          (warns ? '<div class="cw-dish-warns">' + warns + '</div>' : '') +
          '<button class="cw-dish-add" data-action="CW.addDishFromEl" data-dd="' + ddJson + '">Günlük Kayda Ekle</button>' +
        '</div>';
    },

    _menuHtml: function (d) {
      var self = this;
      var out = '<div class="cw-menu">';
      if (d.remaining) {
        out += '<div class="cw-menu-rem">Kalan: ' +
          '<b>' + Math.round(d.remaining.calories) + '</b> kcal · ' +
          '<b>' + Math.round(d.remaining.protein) + 'g</b> protein · ' +
          '<b>' + Math.round(d.remaining.carbs) + 'g</b> karb · ' +
          '<b>' + Math.round(d.remaining.fat) + 'g</b> yağ</div>';
      }
      var picks = d.coach_picks || [];
      if (picks.length) {
        out += '<div class="cw-menu-coach">' +
          '<div class="cw-menu-coach-title">KOÇUN SEÇİMİ <span class="cw-menu-badge">Top 3</span></div>' +
          picks.map(function (x) { return self._dishCard(x, true); }).join('') +
          '</div>';
      }
      var cats = d.categories || {};
      Object.keys(cats).forEach(function (catName) {
        var items = cats[catName] || [];
        if (!items.length) return;
        out += '<div class="cw-cat">' +
          '<div class="cw-cat-head">' + self._esc(catName) + ' <span class="cw-cat-count">' + items.length + '</span></div>' +
          items.map(function (x) { return self._dishCard(x, false); }).join('') +
          '</div>';
      });
      out += '</div>';
      return out;
    },

    _render: function () {
      var container = document.getElementById('cw-msgs');
      if (!container) return;
      if (!this.messages.length && !this.busy) {
        container.innerHTML = '<div class="cw-empty">' + t('coach.empty') + '</div>';
        return;
      }
      var self = this;
      var html = this.messages.map(function (m) {
        if (m.type === 'menu' && m.data) {
          return '<div class="cw-row cw-bot cw-menu-row">' +
                   self._menuHtml(m.data) +
                   '<div class="cw-ts">' + m.time + '</div>' +
                 '</div>';
        }
        var cls = m.role === 'user' ? 'cw-user' : 'cw-bot';
        var esc = (m.text || '')
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
          .replace(/\n/g, '<br>');
        return '<div class="cw-row ' + cls + '">' +
                 '<div class="cw-bubble">' + esc + '</div>' +
                 '<div class="cw-ts">' + m.time + '</div>' +
               '</div>';
      }).join('');
      if (this.busy) {
        html += '<div class="cw-row cw-bot"><div class="cw-bubble cw-typing"><span></span><span></span><span></span></div></div>';
      }
      container.innerHTML = html;
    },

    _scrollBottom: function () {
      var el = document.getElementById('cw-msgs');
      if (el) requestAnimationFrame(function () { el.scrollTop = el.scrollHeight; });
    },

    _setLoading: function (state) {
      this.busy = state;
      var btn = document.getElementById('cw-send');
      if (btn) btn.classList.toggle('cw-busy', state);
      this._render();
      this._scrollBottom();
    },

    _toast: function (msg, type) {
      var n = document.getElementById('cw-notify');
      if (!n) return;
      var icon = type === 'success' ? '✅' : type === 'error' ? '⚠️' : '💬';
      n.innerHTML = '<span style="font-size:15px">' + icon + '</span>' + this._esc(msg);
      n.classList.add('cw-show');
      clearTimeout(this._toastT);
      this._toastT = setTimeout(function () { n.classList.remove('cw-show'); }, 3500);
    },

    _showNotify: function () {
      var n = document.getElementById('cw-notify');
      if (!n) return;
      n.innerHTML = '<span style="font-size:15px">💬</span>' + t('coach.new_message');
      n.classList.add('cw-show');
      clearTimeout(this._toastT);
      this._toastT = setTimeout(function () { n.classList.remove('cw-show'); }, 5000);
    }
  };

  /* Boot after DOM is ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { CW.init(); });
  } else {
    CW.init();
  }
})();
