/* ═══════════════════════════════════════════════════════
   FITX — HYBRID NAVIGATION v3.0
   Drawer · QR FAB · Swipe gestures
   ═══════════════════════════════════════════════════════ */

(function() {
  const drawer = document.getElementById('fx-drawer');
  const backdrop = document.getElementById('fx-drawer-backdrop');
  const overlay = document.getElementById('fx-qr-overlay');

  /* ── Drawer ────────────────────────────────────────── */
  window.fxOpenDrawer = function() {
    if (!drawer || !backdrop) return;
    drawer.classList.add('open');
    backdrop.classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  window.fxCloseDrawer = function() {
    if (!drawer || !backdrop) return;
    drawer.classList.remove('open');
    backdrop.classList.remove('open');
    document.body.style.overflow = '';
  };

  window.fxToggleDrawer = function() {
    if (drawer && drawer.classList.contains('open')) {
      fxCloseDrawer();
    } else {
      fxOpenDrawer();
    }
  };

  if (backdrop) {
    backdrop.addEventListener('click', fxCloseDrawer);
  }

  /* ── Swipe-to-close drawer ─────────────────────────── */
  let touchStartX = 0;
  let touchCurrentX = 0;
  let isDragging = false;

  if (drawer) {
    drawer.addEventListener('touchstart', function(e) {
      touchStartX = e.touches[0].clientX;
      isDragging = true;
    }, { passive: true });

    drawer.addEventListener('touchmove', function(e) {
      if (!isDragging) return;
      touchCurrentX = e.touches[0].clientX;
      const dx = touchCurrentX - touchStartX;
      if (dx < 0) {
        drawer.style.transition = 'none';
        drawer.style.transform = 'translateX(' + dx + 'px)';
      }
    }, { passive: true });

    drawer.addEventListener('touchend', function() {
      if (!isDragging) return;
      isDragging = false;
      const dx = touchCurrentX - touchStartX;
      drawer.style.transition = '';
      drawer.style.transform = '';
      if (dx < -80) {
        fxCloseDrawer();
      }
      touchStartX = 0;
      touchCurrentX = 0;
    }, { passive: true });
  }

  /* ── Escape key closes drawer and overlay ──────────── */
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      fxCloseDrawer();
      fxCloseQR();
    }
  });

  /* ── QR Overlay ────────────────────────────────────── */
  var qrScanner = null;
  var qrLibLoaded = typeof Html5Qrcode !== 'undefined';

  function _startQRScanner() {
    if (qrScanner) return;
    var reader = document.getElementById('fx-qr-reader');
    if (!reader || typeof Html5Qrcode === 'undefined') return;
    qrScanner = new Html5Qrcode('fx-qr-reader');
    qrScanner.start(
      { facingMode: 'environment' },
      { fps: 10, qrbox: { width: 220, height: 220 } },
      function(decoded) {
        qrScanner.stop().then(function() { qrScanner = null; });
        fxCloseQR();
        var url = decoded.trim();
        if (!/^https?:\/\//i.test(url) && /^[a-zA-Z0-9]/.test(url)) {
          url = 'https://' + url;
        }
        window.location.href = '/menu-assistant?url=' + encodeURIComponent(url);
      }
    ).catch(function() {});
  }

  window.fxLaunchQR = function() {
    if (window.location.pathname === '/menu-assistant') {
      var startBtn = document.getElementById('btn-start');
      if (startBtn) { startBtn.click(); return; }
    }

    if (!overlay) {
      window.location.href = '/menu-assistant?scan=auto';
      return;
    }

    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';

    if (qrLibLoaded) {
      _startQRScanner();
    } else {
      var s = document.createElement('script');
      s.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
      s.onload = function() { qrLibLoaded = true; _startQRScanner(); };
      document.head.appendChild(s);
    }
  };

  window.fxCloseQR = function() {
    if (!overlay) return;
    overlay.classList.remove('open');
    document.body.style.overflow = '';
    if (qrScanner) {
      qrScanner.stop().catch(function() {});
      qrScanner = null;
    }
  };

  window.fxSubmitQRUrl = function() {
    var input = document.getElementById('fx-qr-url-input');
    if (!input || !input.value.trim()) return;
    fxCloseQR();
    window.location.href = '/menu-assistant?url=' + encodeURIComponent(input.value.trim());
  };

  /* ── localStorage avatar on all pages ──────────────── */
  var savedAvatar = localStorage.getItem('fitx_user_avatar');
  if (savedAvatar) {
    document.querySelectorAll('.header-avatar, .sidebar-user a').forEach(function(el) {
      var img = el.querySelector('img');
      if (img) { img.src = savedAvatar; }
      else {
        var i = document.createElement('img');
        i.src = savedAvatar;
        i.style.cssText = 'width:100%;height:100%;border-radius:50%;object-fit:cover;';
        el.textContent = '';
        el.appendChild(i);
      }
    });
    var drawerAvatar = document.querySelector('.drawer-profile-avatar');
    if (drawerAvatar) {
      var di = drawerAvatar.querySelector('img');
      if (di) { di.src = savedAvatar; }
      else {
        var ni = document.createElement('img');
        ni.src = savedAvatar;
        ni.style.cssText = 'width:100%;height:100%;border-radius:50%;object-fit:cover;';
        drawerAvatar.textContent = '';
        drawerAvatar.appendChild(ni);
      }
    }
  }

  /* ── Auto-start scanner from URL param ─────────────── */
  if (window.location.pathname === '/menu-assistant') {
    var params = new URLSearchParams(window.location.search);
    if (params.get('scan') === 'auto') {
      window.addEventListener('load', function() {
        var startBtn = document.getElementById('btn-start');
        if (startBtn) startBtn.click();
      });
    }
    var preUrl = params.get('url');
    if (preUrl) {
      window.addEventListener('load', function() {
        var urlInput = document.getElementById('url-input');
        if (urlInput) urlInput.value = preUrl;
        if (typeof processUrl === 'function') {
          processUrl(preUrl);
        }
      });
    }
  }
})();
