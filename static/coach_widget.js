/* AI Coach Floating Chat Widget — self-injecting, no template include needed.
   Add <script src="/static/coach_widget.js"></script> before </body>. */
(function () {
  'use strict';

  /* ── 1. Inject CSS ── */
  var style = document.createElement('style');
  style.textContent = [
    '#cw-root{position:fixed;bottom:calc(var(--action-bar-h,68px) + 15px);right:20px;z-index:9998;display:flex;flex-direction:column;align-items:flex-end;gap:12px;pointer-events:none}',
    '@media(min-width:1024px){#cw-root{bottom:36px;right:36px}}',

    '#cw-window{position:relative;width:360px;height:500px;background:#1E1E1E;border:1px solid #333;border-radius:16px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 25px 60px rgba(0,0,0,.72),0 0 0 1px rgba(204,255,0,.04);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);pointer-events:none;opacity:0;transform:translateY(14px) scale(.96);transition:opacity .22s cubic-bezier(.4,0,.2,1),transform .22s cubic-bezier(.4,0,.2,1)}',
    '#cw-window.cw-open{opacity:1;transform:translateY(0) scale(1);pointer-events:all}',
    '@media(max-width:480px){#cw-window{width:calc(100vw - 32px);height:68vh;border-radius:14px}}',

    '#cw-header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid #2A2A2A;flex-shrink:0;background:#1E1E1E}',
    '#cw-hleft{display:flex;align-items:center;gap:10px}',
    '#cw-avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#CCFF00,#99cc00);display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0;box-shadow:0 0 12px rgba(204,255,0,.3)}',
    '#cw-htitle{font-family:"DM Sans",sans-serif;font-size:14px;font-weight:600;color:#fff;line-height:1}',
    '#cw-close{width:30px;height:30px;border-radius:8px;background:rgba(255,255,255,.05);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s;color:#888;flex-shrink:0}',
    '#cw-close:hover{background:rgba(255,255,255,.1);color:#ddd}',
    '#cw-close svg{width:15px;height:15px}',

    '#cw-msgs{flex:1;overflow-y:auto;padding:14px 14px 8px;display:flex;flex-direction:column;gap:8px;scrollbar-width:thin;scrollbar-color:#2E2E2E transparent}',
    '#cw-msgs::-webkit-scrollbar{width:4px}',
    '#cw-msgs::-webkit-scrollbar-thumb{background:#2E2E2E;border-radius:2px}',
    '.cw-empty{margin:auto;text-align:center;color:#4A4A4A;font-family:"DM Sans",sans-serif;font-size:13px;font-style:italic;padding:24px 0}',

    '.cw-row{display:flex;flex-direction:column;max-width:86%}',
    '.cw-row.cw-user{align-self:flex-end;align-items:flex-end}',
    '.cw-row.cw-bot{align-self:flex-start;align-items:flex-start}',
    '.cw-row.cw-menu-row{max-width:100%;width:100%;align-self:stretch}',
    '.cw-bubble{padding:9px 13px;border-radius:12px;font-family:"DM Sans",sans-serif;font-size:13.5px;line-height:1.62;font-weight:300;word-break:break-word}',
    '.cw-user .cw-bubble{background:transparent;border:1px solid #CCFF00;color:#CCFF00;border-bottom-right-radius:4px}',
    '.cw-bot .cw-bubble{background:#2A2A2A;color:#E2E2E2;border-bottom-left-radius:4px}',
    '.cw-ts{font-size:10px;color:#484848;font-family:"DM Sans",sans-serif;margin-top:3px;padding:0 2px}',

    '.cw-typing{display:flex;gap:5px;align-items:center;padding:12px 14px;min-height:40px}',
    '.cw-typing span{width:7px;height:7px;border-radius:50%;background:#CCFF00;opacity:.4;animation:cw-b 1.3s ease-in-out infinite}',
    '.cw-typing span:nth-child(2){animation-delay:.18s}',
    '.cw-typing span:nth-child(3){animation-delay:.36s}',
    '@keyframes cw-b{0%,80%,100%{transform:translateY(0);opacity:.4}40%{transform:translateY(-5px);opacity:1}}',

    /* ── Menu / dish result block (rendered in chat stream) ── */
    '.cw-menu{display:flex;flex-direction:column;gap:8px}',
    '.cw-menu-rem{display:flex;gap:8px;flex-wrap:wrap;font-family:"DM Sans",sans-serif;font-size:10.5px;color:#606068;margin-bottom:2px}',
    '.cw-menu-rem b{color:#CCFF00;font-weight:600}',
    '.cw-menu-coach{background:linear-gradient(145deg,rgba(18,18,22,.95),rgba(22,24,30,.95));border:1px solid rgba(204,255,0,.12);border-radius:12px;padding:14px}',
    '.cw-menu-coach-title{font-family:"DM Sans",sans-serif;font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#CCFF00;display:flex;align-items:center;gap:8px;margin-bottom:10px}',
    '.cw-menu-badge{font-size:9px;font-weight:700;letter-spacing:.08em;background:rgba(204,255,0,.12);border:1px solid rgba(204,255,0,.2);padding:2px 8px;border-radius:4px;color:#CCFF00}',
    '.cw-cat{margin-top:4px}',
    '.cw-cat-head{font-family:"DM Sans",sans-serif;font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#CCFF00;display:flex;align-items:center;gap:8px;padding:8px 0 6px}',
    '.cw-cat-head::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,rgba(204,255,0,.15),transparent)}',
    '.cw-cat-count{font-size:11px;font-weight:600;letter-spacing:0;color:#505058}',
    '.cw-dish{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:12px;margin-bottom:8px}',
    '.cw-dish.top{border-color:rgba(204,255,0,.18)}',
    '.cw-dish-h{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:6px}',
    '.cw-dish-name{font-family:"DM Sans",sans-serif;font-size:14px;font-weight:600;color:#F2F2F2;line-height:1.3}',
    '.cw-dish-score{font-family:"DM Sans",sans-serif;font-size:13px;font-weight:700;padding:2px 8px;border-radius:6px;white-space:nowrap}',
    '.cw-dish-score.high{background:rgba(204,255,0,.1);color:#CCFF00}',
    '.cw-dish-score.mid{background:rgba(255,176,32,.1);color:#FFB020}',
    '.cw-dish-score.low{background:rgba(255,77,77,.08);color:#FF6B6B}',
    '.cw-dish-reason{font-size:11.5px;color:#808088;line-height:1.45;margin-bottom:8px}',
    '.cw-dish-macros{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}',
    '.cw-dish-macro{font-size:11px;color:#606068;font-weight:500}',
    '.cw-dish-macro span{color:#C8C8D0;font-weight:600}',
    '.cw-dish-warns{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}',
    '.cw-dish-warn{font-size:10px;font-weight:600;background:rgba(255,77,77,.08);border:1px solid rgba(255,77,77,.18);color:#FF6B6B;padding:3px 8px;border-radius:4px}',
    '.cw-dish-add{font-size:12px;font-weight:600;color:#CCFF00;background:rgba(204,255,0,.06);border:1px solid rgba(204,255,0,.15);border-radius:6px;padding:7px 12px;cursor:pointer;font-family:"DM Sans",sans-serif;transition:all .2s}',
    '.cw-dish-add:hover{background:rgba(204,255,0,.12);border-color:rgba(204,255,0,.3)}',
    '.cw-dish-add:disabled{opacity:.4;cursor:default}',

    '#cw-irow{display:flex;align-items:center;border-top:1px solid #2A2A2A;padding:10px 12px;gap:8px;flex-shrink:0;background:#1E1E1E}',
    '#cw-input{flex:1;background:#121212;border:1px solid #2E2E2E;border-radius:10px;padding:9px 13px;font-family:"DM Sans",sans-serif;font-size:13.5px;color:#fff;outline:none;transition:border-color .15s,box-shadow .15s}',
    '#cw-input::placeholder{color:#484848}',
    '#cw-input:focus{border-color:rgba(204,255,0,.38);box-shadow:0 0 0 3px rgba(204,255,0,.06)}',
    '#cw-qr{width:38px;height:38px;border-radius:10px;background:transparent;border:1px solid #2E2E2E;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:border-color .15s,background .15s;flex-shrink:0}',
    '#cw-qr:hover{border-color:rgba(204,255,0,.4);background:rgba(204,255,0,.06)}',
    '#cw-qr svg{width:18px;height:18px;stroke:#CCFF00;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}',
    '#cw-send{width:38px;height:38px;border-radius:10px;background:#CCFF00;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s,transform .15s,opacity .15s;flex-shrink:0}',
    '#cw-send:hover{background:#d6ff1a;transform:scale(1.06)}',
    '#cw-send:active{transform:scale(.95)}',
    '#cw-send.cw-busy{opacity:.5;cursor:not-allowed;transform:none!important}',
    '#cw-send svg{width:16px;height:16px;color:#121212}',

    /* ── QR option popover ── */
    '#cw-qr-menu{position:absolute;left:12px;bottom:60px;background:#252525;border:1px solid #383838;border-radius:12px;padding:6px;box-shadow:0 12px 32px rgba(0,0,0,.6);display:none;flex-direction:column;gap:2px;z-index:20;min-width:180px}',
    '#cw-qr-menu.cw-open{display:flex}',
    '.cw-qr-opt{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;background:transparent;border:none;cursor:pointer;color:#E2E2E2;font-family:"DM Sans",sans-serif;font-size:13.5px;text-align:left;transition:background .15s;width:100%}',
    '.cw-qr-opt:hover{background:rgba(204,255,0,.08);color:#fff}',
    '.cw-qr-opt svg{width:17px;height:17px;stroke:#CCFF00;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}',

    /* ── URL input panel ── */
    '#cw-urlbox{position:absolute;left:12px;right:12px;bottom:60px;background:#252525;border:1px solid #383838;border-radius:12px;padding:12px;box-shadow:0 12px 32px rgba(0,0,0,.6);display:none;z-index:20}',
    '#cw-urlbox.cw-open{display:block}',
    '#cw-urlbox-label{font-family:"DM Sans",sans-serif;font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#808088;margin-bottom:8px}',
    '#cw-urlbox-row{display:flex;gap:8px}',
    '#cw-url-input{flex:1;background:#121212;border:1px solid #2E2E2E;border-radius:8px;padding:9px 12px;font-family:"DM Sans",sans-serif;font-size:13px;color:#fff;outline:none}',
    '#cw-url-input:focus{border-color:rgba(204,255,0,.38)}',
    '#cw-url-go{background:#CCFF00;color:#121212;border:none;border-radius:8px;padding:9px 14px;font-weight:700;font-size:12px;cursor:pointer;font-family:"DM Sans",sans-serif;white-space:nowrap}',

    /* ── In-widget scanner overlay ── */
    '#cw-scan{position:absolute;inset:0;background:#141414;z-index:30;display:none;flex-direction:column;padding:14px}',
    '#cw-scan.cw-open{display:flex}',
    '#cw-scan-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}',
    '#cw-scan-title{font-family:"DM Sans",sans-serif;font-size:13px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#CCFF00}',
    '#cw-scan-close{width:30px;height:30px;border-radius:8px;background:rgba(255,255,255,.05);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;color:#888}',
    '#cw-scan-close svg{width:15px;height:15px}',
    '#cw-scan-reader{width:100%;border-radius:10px;overflow:hidden;background:#000;min-height:240px}',
    '#cw-scan-reader video{border-radius:10px}',
    '#cw-scan-hint{font-family:"DM Sans",sans-serif;font-size:12px;color:#808088;text-align:center;margin-top:12px}',
    '#cw-scan-status{font-family:"DM Sans",sans-serif;font-size:12px;text-align:center;margin-top:8px;color:#CCFF00;min-height:16px}',

    '#cw-fab{width:56px;height:56px;border-radius:50%;background:#CCFF00;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(204,255,0,.35),0 2px 8px rgba(0,0,0,.55);transition:transform .2s ease,box-shadow .2s ease,opacity .2s ease;position:relative;flex-shrink:0;pointer-events:all}',
    '#cw-fab:hover{transform:scale(1.08);box-shadow:0 6px 28px rgba(204,255,0,.5),0 4px 12px rgba(0,0,0,.5)}',
    '#cw-fab svg{width:24px;height:24px;color:#121212}',
    '#cw-fab.cw-hidden{opacity:0;pointer-events:none;transform:scale(.8)}',

    '#cw-badge{position:absolute;top:-2px;right:-2px;width:15px;height:15px;border-radius:50%;background:#FF3B30;border:2px solid #121212;display:none;animation:cw-bp 1.6s ease-in-out infinite}',
    '@keyframes cw-bp{0%,100%{box-shadow:0 0 0 0 rgba(255,59,48,.6)}50%{box-shadow:0 0 0 5px rgba(255,59,48,0)}}',

    '#cw-notify{position:fixed;bottom:calc(var(--action-bar-h,68px) + 75px);right:20px;background:#1E1E1E;border:1px solid rgba(204,255,0,.22);border-radius:10px;padding:10px 15px;font-family:"DM Sans",sans-serif;font-size:13px;color:#E0E0E0;box-shadow:0 4px 20px rgba(0,0,0,.55);z-index:9999;opacity:0;transform:translateY(8px);transition:opacity .25s ease,transform .25s ease;pointer-events:none;display:flex;align-items:center;gap:9px;max-width:250px}',
    '#cw-notify.cw-show{opacity:1;transform:translateY(0)}',
    '@media(min-width:1024px){#cw-notify{bottom:96px;right:36px}}'
  ].join('');
  document.head.appendChild(style);

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
          'QR Tara' +
        '</button>' +
        '<button class="cw-qr-opt" id="cw-qr-url" role="menuitem">' +
          '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>' +
          'URL Gir' +
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
        '<input type="text" id="cw-input" placeholder="Bir şey sor..." autocomplete="off">' +
        '<button id="cw-send" aria-label="Gönder">' +
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

    '<button id="cw-fab" aria-label="Koçuna sor">' +
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
  // jsdelivr: CSP script-src yalnızca 'self' + cdn.jsdelivr.net'e izin verir
  // (unpkg.com politika dışıydı ve tarayıcı yüklemeyi engelliyordu).
  var QR_LIB_SRC   = 'https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js';

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
        this._push('bot', 'Merhaba! 💪 Ben AI fitness koçunum. Antrenman, beslenme veya sağlıkla ilgili her şeyi sorabilirsin. Menü taramak için soldaki QR ikonuna dokun.');
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
        self._push('bot', d.answer || d.error || 'Yanıt alınamadı.');
      })
      .catch(function () {
        self._setLoading(false);
        self._push('bot', 'Bağlantı hatası. Lütfen tekrar dene.');
      });
    },

    receiveCheckinFeedback: function (text) {
      this._push('bot', '📊 Check-in Geri Bildirimi\n\n' + text);
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
      var time = now.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
      this.messages.push({ role: role, text: text, time: time });
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.messages.slice(-MAX_MESSAGES))); } catch (_) {}
      this._render();
      this._scrollBottom();
    },

    _pushMenu: function (result) {
      var now  = new Date();
      var time = now.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
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
        container.innerHTML = '<div class="cw-empty">Koçuna bir şey sor...</div>';
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
      n.innerHTML = '<span style="font-size:15px">💬</span>Koçundan yeni bir mesaj var!';
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
