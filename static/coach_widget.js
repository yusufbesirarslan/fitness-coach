/* AI Coach Floating Chat Widget — self-injecting, no template include needed.
   Add <script src="/static/coach_widget.js"></script> before </body>. */
(function () {
  'use strict';

  /* ── 1. Inject CSS ── */
  var style = document.createElement('style');
  style.textContent = [
    '#cw-root{position:fixed;bottom:24px;right:24px;z-index:9998;display:flex;flex-direction:column;align-items:flex-end;gap:12px;pointer-events:none}',
    '@media(max-width:768px){#cw-root{bottom:80px;right:16px}}',

    '#cw-window{width:360px;height:500px;background:#1E1E1E;border:1px solid #333;border-radius:16px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 25px 60px rgba(0,0,0,.72),0 0 0 1px rgba(204,255,0,.04);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);pointer-events:none;opacity:0;transform:translateY(14px) scale(.96);transition:opacity .22s cubic-bezier(.4,0,.2,1),transform .22s cubic-bezier(.4,0,.2,1)}',
    '#cw-window.cw-open{opacity:1;transform:translateY(0) scale(1);pointer-events:all}',
    '@media(max-width:480px){#cw-window{width:calc(100vw - 32px);height:68vh;border-radius:14px}}',

    '#cw-header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid #2A2A2A;flex-shrink:0;background:#1E1E1E}',
    '#cw-hleft{display:flex;align-items:center;gap:10px}',
    '#cw-avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#CCFF00,#99cc00);display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0;box-shadow:0 0 12px rgba(204,255,0,.3)}',
    '#cw-htitle{font-family:"DM Sans",sans-serif;font-size:14px;font-weight:600;color:#fff;line-height:1;margin-bottom:4px}',
    '#cw-hstatus{display:flex;align-items:center;gap:5px;font-family:"DM Sans",sans-serif;font-size:11px;color:#888}',
    '.cw-pdot{width:6px;height:6px;border-radius:50%;background:#CCFF00;flex-shrink:0;animation:cw-dp 2.2s ease-in-out infinite}',
    '@keyframes cw-dp{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.85)}}',
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
    '.cw-bubble{padding:9px 13px;border-radius:12px;font-family:"DM Sans",sans-serif;font-size:13.5px;line-height:1.62;font-weight:300;word-break:break-word}',
    '.cw-user .cw-bubble{background:transparent;border:1px solid #CCFF00;color:#CCFF00;border-bottom-right-radius:4px}',
    '.cw-bot .cw-bubble{background:#2A2A2A;color:#E2E2E2;border-bottom-left-radius:4px}',
    '.cw-ts{font-size:10px;color:#484848;font-family:"DM Sans",sans-serif;margin-top:3px;padding:0 2px}',

    '.cw-typing{display:flex;gap:5px;align-items:center;padding:12px 14px;min-height:40px}',
    '.cw-typing span{width:7px;height:7px;border-radius:50%;background:#CCFF00;opacity:.4;animation:cw-b 1.3s ease-in-out infinite}',
    '.cw-typing span:nth-child(2){animation-delay:.18s}',
    '.cw-typing span:nth-child(3){animation-delay:.36s}',
    '@keyframes cw-b{0%,80%,100%{transform:translateY(0);opacity:.4}40%{transform:translateY(-5px);opacity:1}}',

    '#cw-irow{display:flex;align-items:center;border-top:1px solid #2A2A2A;padding:10px 12px;gap:8px;flex-shrink:0;background:#1E1E1E}',
    '#cw-input{flex:1;background:#121212;border:1px solid #2E2E2E;border-radius:10px;padding:9px 13px;font-family:"DM Sans",sans-serif;font-size:13.5px;color:#fff;outline:none;transition:border-color .15s,box-shadow .15s}',
    '#cw-input::placeholder{color:#484848}',
    '#cw-input:focus{border-color:rgba(204,255,0,.38);box-shadow:0 0 0 3px rgba(204,255,0,.06)}',
    '#cw-send{width:38px;height:38px;border-radius:10px;background:#CCFF00;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s,transform .15s,opacity .15s;flex-shrink:0}',
    '#cw-send:hover{background:#d6ff1a;transform:scale(1.06)}',
    '#cw-send:active{transform:scale(.95)}',
    '#cw-send.cw-busy{opacity:.5;cursor:not-allowed;transform:none!important}',
    '#cw-send svg{width:16px;height:16px;color:#121212}',

    '#cw-fab{width:56px;height:56px;border-radius:50%;background:#CCFF00;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(204,255,0,.35),0 2px 8px rgba(0,0,0,.55);transition:transform .2s ease,box-shadow .2s ease,opacity .2s ease;position:relative;flex-shrink:0;pointer-events:all}',
    '#cw-fab:hover{transform:scale(1.08);box-shadow:0 6px 28px rgba(204,255,0,.5),0 4px 12px rgba(0,0,0,.5)}',
    '#cw-fab svg{width:24px;height:24px;color:#121212}',
    '#cw-fab.cw-hidden{opacity:0;pointer-events:none;transform:scale(.8)}',

    '#cw-badge{position:absolute;top:-2px;right:-2px;width:15px;height:15px;border-radius:50%;background:#FF3B30;border:2px solid #121212;display:none;animation:cw-bp 1.6s ease-in-out infinite}',
    '@keyframes cw-bp{0%,100%{box-shadow:0 0 0 0 rgba(255,59,48,.6)}50%{box-shadow:0 0 0 5px rgba(255,59,48,0)}}',

    '#cw-notify{position:fixed;bottom:96px;right:24px;background:#1E1E1E;border:1px solid rgba(204,255,0,.22);border-radius:10px;padding:10px 15px;font-family:"DM Sans",sans-serif;font-size:13px;color:#E0E0E0;box-shadow:0 4px 20px rgba(0,0,0,.55);z-index:9999;opacity:0;transform:translateY(8px);transition:opacity .25s ease,transform .25s ease;pointer-events:none;display:flex;align-items:center;gap:9px;max-width:250px}',
    '#cw-notify.cw-show{opacity:1;transform:translateY(0)}',
    '@media(max-width:768px){#cw-notify{bottom:160px;right:16px}}'
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
            '<div id="cw-hstatus"><span class="cw-pdot"></span>Online</div>' +
          '</div>' +
        '</div>' +
        '<button id="cw-close" aria-label="Kapat">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
          '</svg>' +
        '</button>' +
      '</div>' +
      '<div id="cw-msgs" role="log" aria-live="polite"></div>' +
      '<div id="cw-irow">' +
        '<input type="text" id="cw-input" placeholder="Bir şey sor..." autocomplete="off">' +
        '<button id="cw-send" aria-label="Gönder">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' +
            '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>' +
          '</svg>' +
        '</button>' +
      '</div>' +
    '</div>' +

    '<button id="cw-fab" aria-label="Koçuna sor">' +
      '<span id="cw-badge" aria-hidden="true"></span>' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
      '</svg>' +
    '</button>' +

  '</div>' +
  '<div id="cw-notify" aria-live="assertive">' +
    '<span style="font-size:15px">💬</span>' +
    'Koçundan yeni bir mesaj var!' +
  '</div>';

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

  /* ── 4. CW object ── */
  var STORAGE_KEY  = 'fc_coach_messages';
  var MAX_MESSAGES = 60;

  var CW = window.CW = {
    open:     false,
    busy:     false,
    unread:   false,
    messages: [],

    init: function () {
      try {
        var s = sessionStorage.getItem(STORAGE_KEY);
        this.messages = s ? JSON.parse(s) : [];
      } catch (_) { this.messages = []; }

      if (this.messages.length === 0) {
        this._push('bot', 'Merhaba! 💪 Ben AI fitness koçunum. Antrenman, beslenme veya sağlıkla ilgili her şeyi sorabilirsin.');
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
      fetch('/ask', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ question: question })
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

    _push: function (role, text) {
      var now  = new Date();
      var time = now.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
      this.messages.push({ role: role, text: text, time: time });
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.messages.slice(-MAX_MESSAGES))); } catch (_) {}
      this._render();
      this._scrollBottom();
    },

    _render: function () {
      var container = document.getElementById('cw-msgs');
      if (!container) return;
      if (!this.messages.length && !this.busy) {
        container.innerHTML = '<div class="cw-empty">Koçuna bir şey sor...</div>';
        return;
      }
      var html = this.messages.map(function (m) {
        var cls = m.role === 'user' ? 'cw-user' : 'cw-bot';
        var esc = m.text
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
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

    _showNotify: function () {
      var n = document.getElementById('cw-notify');
      if (!n) return;
      n.classList.add('cw-show');
      setTimeout(function () { n.classList.remove('cw-show'); }, 5000);
    }
  };

  /* Boot after DOM is ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { CW.init(); });
  } else {
    CW.init();
  }
})();
