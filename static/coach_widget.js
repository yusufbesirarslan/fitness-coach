/* AI Coach Floating Chat Widget — self-injecting, no template include needed.
   Add <script src="/static/coach_widget.js"></script> before </body>. */
(function () {
  'use strict';

  /* ── 0. Lifecycle ownership (AxisAI UIUX Sprint 1 PR3) ──
     The widget self-injects its host, wires its events, defines window.CW and
     boots exactly ONCE per document. Guarding on an explicit module-level flag —
     not on window.CW existence alone (a page may legitimately have set it) and not
     on #cw-root existence alone (a host may be server-rendered) — makes a second
     script evaluation a clean no-op: no duplicate #cw-root (no second
     accessibility-exposed Coach instance), no duplicate event/bootstrap/stream
     wiring, no duplicate /coach/history fetch. This is a shared correctness
     invariant: on the first (and, on a normal single-include page, only)
     evaluation the flag is unset, so every step below runs exactly as before —
     single-init behavior is unchanged. Route-mode behaviors (auto-open,
     page-shell) are NOT added here; they live in the Coach route template so the
     floating widget is untouched (answer.txt §4, §5). */
  if (window.__cwWidgetInit) return;
  window.__cwWidgetInit = true;

  /* ── 0b. Launcher ownership (AxisAI UX-1 PR3) ──
     The floating action button is the ONLY part of this widget that was ever
     global chrome, and Coach is now a primary navigation destination (/coach),
     so an app-wide launcher is duplicate navigation. It is injected ONLY where
     the host page opts in with <body data-coach-launcher> — the canonical Coach
     destination, nothing else. Everything else the widget owns (window, composer,
     stream, /coach/history hydration, menu scanner) is untouched and still
     available to its in-domain consumers, e.g. Nutrition's menu scan.
     Contextual Coach entry points are plain links to /coach; they do NOT
     resurrect an in-page launcher. */
  var CW_LAUNCHER = !!(document.body &&
                       document.body.hasAttribute('data-coach-launcher'));

  function mealWriteHeaders() {
    var key = (window.crypto && window.crypto.randomUUID)
      ? window.crypto.randomUUID()
      : ('meal-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
    return { 'Content-Type': 'application/json', 'Idempotency-Key': key };
  }

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

  /* Notification glyphs. Emoji (✅ / ⚠️ / 💬) rendered here in a
     different colour, weight and optical size than every other icon in the
     widget and could not follow the theme; these are the same stroke family. */
  var CW_ICONS = {
    success: '<svg class="cw-notify-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    error:   '<svg class="cw-notify-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info:    '<svg class="cw-notify-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>'
  };

  /* One writer for the notification bubble: the glyph is a trusted constant
     (innerHTML), the message is untrusted (textContent). hasOwnProperty guards
     the lookup so an unexpected `type` cannot reach Object.prototype. */
  function cwSetNotify(node, type, message) {
    var key = Object.prototype.hasOwnProperty.call(CW_ICONS, type) ? type : 'info';
    node.innerHTML = CW_ICONS[key];
    var label = document.createElement('span');
    label.textContent = message == null ? '' : String(message);
    node.appendChild(label);
  }

  /* ── 2. Inject HTML ── */
  var html = '<div id="cw-root">' +

    '<div id="cw-window" role="dialog" aria-label="AI Fitness Coach">' +
      '<div id="cw-header">' +
        '<div id="cw-hleft">' +
          '<div id="cw-avatar" aria-hidden="true">' + '<svg viewBox="0 0 24 24"><path d="M6.5 6.5h11M6.5 17.5h11M4 9v6M20 9v6M8 8v8M16 8v8"/></svg>' + '</div>' +
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
        '<button id="cw-stop" class="cw-hidden" aria-label="' + t('coach.stop') + '" title="' + t('coach.stop') + '">' +
          '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>' +
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

    /* Launcher: opt-in only (see §0b). The unread badge went with it — its
       single writer was the cross-page check-in push, which no longer exists. */
    (CW_LAUNCHER
      ? '<button id="cw-fab" aria-label="' + t('coach.fab') + '">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
          '</svg>' +
        '</button>'
      : '') +

  '</div>' +
  '<div id="cw-notify" aria-live="assertive"></div>';

  /* Adopt a pre-existing host instead of injecting a second one: with the
     module-level guard above, #cw-root can only pre-exist if it was
     server-rendered (a legitimately-present-but-uninitialized host) — never from a
     prior evaluation. Injecting is otherwise identical to before. */
  if (!document.getElementById('cw-root')) {
    var wrap = document.createElement('div');
    wrap.innerHTML = html;
    while (wrap.firstChild) document.body.appendChild(wrap.firstChild);
  }

  /* A closed window is invisible (opacity:0) but its composer and buttons were
     still in the tab order. That was survivable while a launcher stood next to
     it; on a launcher-less host it is focusable UI the user cannot see and could
     not have opened. Cleared on every open (CW.toggle). */
  var cwWinEl = document.getElementById('cw-window');
  if (cwWinEl) cwWinEl.inert = true;

  /* ── 3. Wire events ── */
  document.getElementById('cw-close').addEventListener('click', function () { CW.toggle(); });
  var cwFab = document.getElementById('cw-fab');   // absent unless the page opts in
  if (cwFab) cwFab.addEventListener('click', function () { CW.toggle(); });
  document.getElementById('cw-send').addEventListener('click', function () { CW.send(); });
  document.getElementById('cw-stop').addEventListener('click', function () { CW.stop(); });
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
  // jsdelivr: CSP script-src bu TAM dosyalara sabitlenmiştir (SEC1 — geniş host
  // joker'i kaldırıldı; hooks.py CSP'si aynı sabit URL'leri listeler). SRI hash'i +
  // crossorigin ile yüklenir; sürüm/hash değişirse hooks.py CSP'si de güncellenmeli.
  var QR_LIB_SRC   = 'https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js';
  var QR_LIB_SRI   = 'sha384-c9d8RFSL+u3exBOJ4Yp3HUJXS4znl9f+z66d1y54ig+ea249SpqR+w1wyvXz/lk+';
  var MD_SRC       = 'https://cdn.jsdelivr.net/npm/marked@15.0.7/marked.min.js';
  var MD_SRI       = 'sha384-H+hy9ULve6xfxRkWIh/YOtvDdpXgV2fmAGQkIDTxIgZwNoaoBal14Di2YTMR6MzR';
  var DP_SRC       = 'https://cdn.jsdelivr.net/npm/dompurify@3.2.4/dist/purify.min.js';
  var DP_SRI       = 'sha384-eEu5CTj3qGvu9PdJuS+YlkNi7d2XxQROAFYOr59zgObtlcux1ae1Il3u7jvdCSWu';

  function loadScript(src, sri) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.integrity = sri;            // tedarik-zinciri bütünlük doğrulaması
      s.crossOrigin = 'anonymous';  // SRI'nin cross-origin script'te çalışması için şart
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  var CW = window.CW = {
    open:     false,
    busy:     false,
    messages: [],
    _scanner: null,
    _abort:   null,   // akan isteği iptal eden AbortController (Durdur)
    _stream:  '',     // akış sırasında biriken ham metin
    _raf:     0,
    _lastQ:   '',     // Yeniden üret için son kullanıcı sorusu

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

      // Markdown yığını arka planda yüklenir; gelene kadar _md() düz-metin
      // yedeğine düşer (sohbet asla boş/bozuk görünmez).
      var self = this;
      Promise.all([loadScript(MD_SRC, MD_SRI), loadScript(DP_SRC, DP_SRI)])
        .then(function () {
          if (window.marked && window.marked.setOptions) {
            window.marked.setOptions({ breaks: true, gfm: true });
          }
          self._render();  // yüklenmiş kütüphaneyle bir kez yeniden boya
        })
        .catch(function () { /* CDN yoksa düz-metin yedeği zaten çalışıyor */ });

      // WS1: sunucu hafızası KAYNAK-DOĞRU olandır — sessionStorage yalnızca
      // ilk boyama içindi. Tarayıcı yenilense/başka cihazdan girilse de sohbet
      // aynı yerden devam etsin diye aktif konuşmayı sunucudan hidratla.
      this._hydrate();
    },

    _hydrate: function () {
      var self = this;
      fetch('/coach/history', { headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.messages || !d.messages.length) return;
          self.messages = d.messages.map(function (m) {
            return {
              role: m.role === 'user' ? 'user' : 'bot',
              text: m.text || '',
              time: self._time(m.created_at)
            };
          });
          self._save();
          self._render();
          if (self.open) self._scrollBottom();
        })
        .catch(function () { /* çevrimdışı: sessionStorage kopyası kalır */ });
    },

    toggle: function () {
      this.open = !this.open;
      var win = document.getElementById('cw-window');
      var fab = document.getElementById('cw-fab');   // null on a launcher-less host
      win.inert = !this.open;
      if (this.open) {
        win.classList.add('cw-open');
        if (fab) fab.classList.add('cw-hidden');
        this._scrollBottom();
        setTimeout(function () {
          var inp  = document.getElementById('cw-input');
          var scan = document.getElementById('cw-scan');
          // The composer sits UNDER the scanner overlay, so focusing it there
          // only summons a keyboard behind the camera view. startScan opens the
          // window itself now, which is what makes this reachable.
          if (inp && !(scan && scan.classList.contains('cw-open'))) inp.focus();
        }, 240);
      } else {
        win.classList.remove('cw-open');
        if (fab) fab.classList.remove('cw-hidden');
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
      this._ask(question);
    },

    /* Durdur: akışı iptal et. Sunucu bağlantı kopmasını görür ve o ana dek
       üretilen kısmi yanıtı `interrupted` işaretiyle hafızaya yazar — ekranda
       gördüğün metin ile modelin hatırladığı metin AYNI kalır. */
    stop: function () {
      if (!this.busy || !this._abort) return;
      try { this._abort.abort(); } catch (_) {}
      this._abort = null;
      this._finishStream(this._stream, true);
    },

    /* Yeniden üret: son bot yanıtını at, aynı soruyu tekrar sor. */
    regenerate: function () {
      if (this.busy || !this._lastQ) return;
      var last = this.messages[this.messages.length - 1];
      if (last && last.role === 'bot' && last.type !== 'menu') this.messages.pop();
      this._save();
      this._ask(this._lastQ);
    },

    _ask: function (question) {
      var self = this;
      this._lastQ = question;
      this._stream = '';
      this._setLoading(true);

      // Sunucu hafızası (WS1) kaynak-doğru; `history` yalnızca hafıza kapalı/
      // arızalı olduğunda kullanılan yedek yol için hâlâ gönderilir.
      var history = this.messages.slice(-8)
        .filter(function (m) { return m.type !== 'menu'; })
        .map(function (m) {
          return { role: m.role === 'user' ? 'user' : 'bot', text: m.text || '' };
        });

      var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      this._abort = ctrl;

      // EventSource KULLANILMAZ: POST gövdesi ve X-CSRFToken başlığı gönderemez
      // (csrf.js yalnızca fetch'i sarar). fetch + ReadableStream ile SSE okunur.
      fetch('/ask/stream', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ question: question, history: history }),
        signal:  ctrl ? ctrl.signal : undefined
      })
      .then(function (r) {
        if (!r.ok) return self._fallbackAsk(r, question);
        if (!r.body || !r.body.getReader) return self._fallbackAsk(null, question);
        self._beginStream();
        return self._consume(r.body.getReader());
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;  // Durdur: kısmi metin kalır
        self._finishStream('', false, t('coach.conn_error'));
      });
    },

    /* Akış başlamadıysa (503/402/400 veya ReadableStream yok) bloklayıcı /ask'e
       düş — eski, kanıtlanmış yol. Böylece streaming desteklenmeyen tarayıcıda
       veya kota/kapı reddinde sohbet çalışmaya devam eder. */
    _fallbackAsk: function (resp, question) {
      var self = this;
      if (resp) {
        return resp.json().catch(function () { return {}; }).then(function (d) {
          if (resp.status === 402 || resp.status === 400 || resp.status === 503) {
            self._finishStream('', false, d.error || t('coach.no_reply'));
            return;
          }
          return self._plainAsk(question);
        });
      }
      return this._plainAsk(question);
    },

    _plainAsk: function (question) {
      var self = this;
      var history = this.messages.slice(-8)
        .filter(function (m) { return m.type !== 'menu'; })
        .map(function (m) {
          return { role: m.role === 'user' ? 'user' : 'bot', text: m.text || '' };
        });
      return fetch('/ask', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ question: question, history: history })
      })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        self._finishStream('', false, d.answer || d.error || t('coach.no_reply'));
      })
      .catch(function () {
        self._finishStream('', false, t('coach.conn_error'));
      });
    },

    /* ── SSE okuma ── */
    _consume: function (reader) {
      var self = this;
      var dec  = new TextDecoder();
      var buf  = '';
      var step = function (res) {
        if (res.done) { self._finishStream(self._stream, false); return; }
        buf += dec.decode(res.value, { stream: true });
        // Çerçeveler boş satırla ayrılır; yarım kalan son parça tamponda bekler.
        var frames = buf.split('\n\n');
        buf = frames.pop();
        for (var i = 0; i < frames.length; i++) self._frame(frames[i]);
        if (!self.busy) return;  // done/error işlendi — okumayı bitir
        return reader.read().then(step);
      };
      return reader.read().then(step);
    },

    _frame: function (raw) {
      var ev = '', data = '';
      var lines = raw.split('\n');
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.indexOf('event:') === 0)      ev    = line.slice(6).trim();
        else if (line.indexOf('data:') === 0)  data += line.slice(5).trim();
      }
      if (!ev) return;
      var d = {};
      try { d = data ? JSON.parse(data) : {}; } catch (_) { return; }

      if (ev === 'delta')      this._appendDelta(d.text || '');
      else if (ev === 'done')  this._finishStream(d.text || this._stream, false);
      else if (ev === 'error') this._finishStream('', false, d.message || t('coach.no_reply'));
      /* 'meta' (conversation_id): şimdilik bilgi amaçlı — WS6'da izleme için kullanılacak */
    },

    _beginStream: function () {
      this.messages.push({ role: 'bot', text: '', time: this._time(), streaming: true });
      this._render();
      this._scrollBottom();
    },

    _appendDelta: function (text) {
      if (!text) return;
      this._stream += text;
      var self = this;
      // Token başına yeniden boyama YOK: bir sonraki kareye kadar biriktir
      // (markdown ayrıştırma + sanitize her token'da koşmasın).
      if (this._raf) return;
      this._raf = requestAnimationFrame(function () {
        self._raf = 0;
        var el = document.getElementById('cw-stream');
        if (!el) return;
        el.innerHTML = self._md(self._stream);
        self._stickBottom();
      });
    },

    /* Akışı sonlandır. finalText: sunucunun kanonik (denetlenmiş) metni.
       stopped: kullanıcı Durdur'a bastı. errorText: dostça hata mesajı. */
    _finishStream: function (finalText, stopped, errorText) {
      if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; }
      this._abort = null;

      var last = this.messages[this.messages.length - 1];
      var text = errorText || finalText || this._stream || '';
      if (stopped && text) text += '\n\n_' + t('coach.stopped') + '_';

      if (last && last.streaming) {
        if (text) {
          last.text = text;
          delete last.streaming;
        } else {
          this.messages.pop();  // hiç metin gelmedi → boş balon bırakma
        }
      } else if (text) {
        this.messages.push({ role: 'bot', text: text, time: this._time() });
      }

      this._stream = '';
      this._save();
      this._setLoading(false);
      this._scrollBottom();
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
      // #cw-scan is absolutely positioned inside #cw-window, which is opacity:0 /
      // pointer-events:none while closed — the scanner is only reachable once its
      // host is open, and no launcher reopens it. Idempotent: toggle only if closed.
      if (!this.open) this.toggle();
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
      var idempotencyHeaders = mealWriteHeaders();
      fetch('/meal-log', {
        method:  'POST',
        headers: idempotencyHeaders,
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

    _time: function (iso) {
      var d = iso ? new Date(iso) : new Date();
      if (isNaN(d.getTime())) d = new Date();
      return d.toLocaleTimeString(window.LOCALE === 'en' ? 'en-US' : 'tr-TR',
                                  { hour: '2-digit', minute: '2-digit' });
    },

    _save: function () {
      try {
        var keep = this.messages.slice(-MAX_MESSAGES).filter(function (m) { return !m.streaming; });
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(keep));
      } catch (_) {}
    },

    /* Markdown → SANITIZE edilmiş HTML. DOMPurify olmadan HTML'e ASLA dokunma:
       model çıktısı (ve araç sonuçları) güvenilmez girdidir → XSS vektörü.
       Kütüphaneler henüz yüklenmediyse escape'li düz metne düş. */
    _md: function (text) {
      var raw = String(text == null ? '' : text);
      if (window.marked && window.DOMPurify) {
        try {
          return window.DOMPurify.sanitize(window.marked.parse(raw));
        } catch (_) { /* aşağıdaki yedeğe düş */ }
      }
      return this._esc(raw)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
    },

    _push: function (role, text) {
      this.messages.push({ role: role, text: text, time: this._time() });
      this._save();
      this._render();
      this._scrollBottom();
    },

    _pushMenu: function (result) {
      var picks = (result.coach_picks || []).length;
      this.messages.push({ role: 'bot', type: 'menu', data: result,
                           text: 'Menü analizi (' + picks + ' öneri)', time: this._time() });
      this._save();
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
      var lastIdx = this.messages.length - 1;
      var html = this.messages.map(function (m, i) {
        if (m.type === 'menu' && m.data) {
          return '<div class="cw-row cw-bot cw-menu-row">' +
                   self._menuHtml(m.data) +
                   '<div class="cw-ts">' + m.time + '</div>' +
                 '</div>';
        }
        var cls = m.role === 'user' ? 'cw-user' : 'cw-bot';
        // Kullanıcı metni ASLA markdown'dan geçmez (yalnızca escape) — kendi
        // girdisini HTML'e çeviren bir yol açmanın hiçbir faydası yok.
        var body = m.role === 'user' ? self._esc(m.text || '') : self._md(m.text || '');
        // Akan balon: id ile işaretlenir ki delta'lar TÜM listeyi yeniden
        // boyamadan yalnızca bu düğüme yazsın.
        var bubbleId = m.streaming ? ' id="cw-stream"' : '';
        var typing = (m.streaming && !m.text) ? ' cw-typing-live' : '';
        var row = '<div class="cw-row ' + cls + '">' +
                    '<div class="cw-bubble cw-md' + typing + '"' + bubbleId + '>' + body + '</div>' +
                    '<div class="cw-ts">' + m.time + '</div>';
        // Son bot yanıtının altına "Yeniden üret" (akış bitmişken).
        if (!self.busy && i === lastIdx && m.role === 'bot' && !m.streaming && self._lastQ) {
          row += '<button class="cw-regen" data-action="CW.regenerate">↻ ' +
                 t('coach.regenerate') + '</button>';
        }
        return row + '</div>';
      }).join('');
      if (this.busy && !(this.messages[lastIdx] && this.messages[lastIdx].streaming)) {
        html += '<div class="cw-row cw-bot"><div class="cw-bubble cw-typing"><span></span><span></span><span></span></div></div>';
      }
      container.innerHTML = html;
    },

    _atBottom: function (el) {
      // 40px tolerans: kullanıcı dibe yakınsa "takip ediyor" say.
      return (el.scrollHeight - el.scrollTop - el.clientHeight) < 40;
    },

    _scrollBottom: function () {
      var el = document.getElementById('cw-msgs');
      if (el) requestAnimationFrame(function () { el.scrollTop = el.scrollHeight; });
    },

    /* Akış sırasında: kullanıcı yukarı kaydırıp eski mesajları okuyorsa onu
       ZORLA aşağı çekme — yalnızca zaten dipteyse takip et. */
    _stickBottom: function () {
      var el = document.getElementById('cw-msgs');
      if (el && this._atBottom(el)) el.scrollTop = el.scrollHeight;
    },

    _setLoading: function (state) {
      this.busy = state;
      var send = document.getElementById('cw-send');
      var stop = document.getElementById('cw-stop');
      if (send) send.classList.toggle('cw-hidden', state);
      if (stop) stop.classList.toggle('cw-hidden', !state);
      this._render();
      this._scrollBottom();
    },

    _toast: function (msg, type) {
      var n = document.getElementById('cw-notify');
      if (!n) return;
      cwSetNotify(n, type, msg);
      n.classList.add('cw-show');
      clearTimeout(this._toastT);
      this._toastT = setTimeout(function () { n.classList.remove('cw-show'); }, 3500);
    }
  };

  /* Boot after DOM is ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { CW.init(); });
  } else {
    CW.init();
  }
})();
