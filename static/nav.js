/* ═══════════════════════════════════════════════════════
   AxisAI — HYBRID NAVIGATION v3.0
   Drawer · Swipe gestures
   ═══════════════════════════════════════════════════════ */

(function() {
  const drawer = document.getElementById('fx-drawer');
  const backdrop = document.getElementById('fx-drawer-backdrop');

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

  /* ── Escape key closes drawer ──────────────────────── */
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      fxCloseDrawer();
    }
  });
})();
