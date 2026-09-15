/* AxisAI — canonical Modal/Sheet behaviour (WEB-UX4-PR2 · F-11 / F-38).
 *
 * WHY THIS EXISTS
 * The Modal/Sheet primitive already exists in components.css and is documented
 * in docs/design-system.md, but it shipped with NO behaviour — so the product
 * grew 21 bespoke `role="dialog"` surfaces of which exactly 1 trapped focus,
 * 3 restored it and 2 used `inert`. This completes the EXISTING primitive; it
 * is not a component framework and it must not become one.
 *
 * WHAT IT IS NOT
 * No component registry, no auto-wiring, no MutationObserver, no polling. The
 * lifecycle is explicit: a consumer calls open() and close(). PR2 migrates no
 * product dialog — each journey PR migrates its own.
 *
 * CONTRACT
 *   AxisModal.open(root, options)   root: the .modal-backdrop / .sheet-backdrop
 *     options.dismissible  default true. false => Escape and backdrop clicks
 *                          do not close (destructive confirmations).
 *     options.initialFocus Element or selector focused on open. Defaults to
 *                          the first focusable descendant, else the dialog.
 *     options.onClose      called after teardown.
 *   AxisModal.close(root)
 *   AxisModal.isOpen(root)
 *
 * GUARANTEES
 *   focus-on-open · Tab/Shift+Tab containment · Escape honouring the
 *   dismissibility policy · background isolation via `inert` · focus restored
 *   to the opener when it is still usable · nested-safe LIFO cleanup ·
 *   no handler left bound after teardown.
 */
(function () {
  'use strict';

  if (window.AxisModal) { return; }

  var FOCUSABLE = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  // LIFO. Nested dialogs unwind in order; the background stays isolated until
  // the OUTERMOST dialog closes.
  var stack = [];

  function focusable(root) {
    return Array.prototype.filter.call(
      root.querySelectorAll(FOCUSABLE),
      function (el) {
        if (el.hasAttribute('inert') || el.closest('[inert]')) { return false; }
        if (el.getAttribute('aria-hidden') === 'true') { return false; }
        var rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) { return false; }
        var style = window.getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none';
      });
  }

  /* Isolate everything that is not this dialog. Siblings are marked rather
   * than <body>, so the dialog itself stays reachable. Only the first (i.e.
   * outermost) dialog isolates; nested dialogs are already inside it.
   *
   * The walk goes all the way up to <body>, marking siblings at EVERY level.
   * Marking only the backdrop's immediate siblings would silently leave the
   * rest of the page interactive whenever a consumer nests the backdrop
   * instead of placing it at top level - a half-isolated dialog is precisely
   * the defect F-38 is about, and it would be invisible in review. */
  function isolate(root) {
    var marked = [];
    for (var node = root; node && node !== document.body; node = node.parentElement) {
      var parent = node.parentElement;
      if (!parent) { break; }
      Array.prototype.forEach.call(parent.children, function (sibling) {
        if (sibling === node || sibling.hasAttribute('inert')) { return; }
        sibling.setAttribute('inert', '');
        sibling.setAttribute('data-axis-modal-inert', '');
        marked.push(sibling);
      });
    }
    return marked;
  }

  function release(marked) {
    marked.forEach(function (el) {
      if (el.hasAttribute('data-axis-modal-inert')) {
        el.removeAttribute('inert');
        el.removeAttribute('data-axis-modal-inert');
      }
    });
  }

  /* Only restore to an element that can actually take focus now: an opener
   * that was removed or hidden while the dialog was open would otherwise
   * strand focus on <body> (or on a detached node). */
  function restorable(el) {
    if (!el || !el.isConnected || typeof el.focus !== 'function') { return false; }
    var rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) { return false; }
    return !el.closest('[inert]');
  }

  function entryFor(root) {
    for (var i = stack.length - 1; i >= 0; i -= 1) {
      if (stack[i].root === root) { return stack[i]; }
    }
    return null;
  }

  function onKeydown(event) {
    var top = stack[stack.length - 1];
    if (!top) { return; }

    if (event.key === 'Escape') {
      if (!top.dismissible) { return; }   // destructive confirmations stay put
      event.preventDefault();
      close(top.root);
      return;
    }

    if (event.key !== 'Tab') { return; }

    var items = focusable(top.root);
    if (!items.length) {
      event.preventDefault();
      top.root.focus();
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;

    if (!top.root.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function open(root, options) {
    if (!root || entryFor(root)) { return null; }
    var opts = options || {};

    var entry = {
      root: root,
      dismissible: opts.dismissible !== false,
      opener: opts.opener || document.activeElement,
      onClose: typeof opts.onClose === 'function' ? opts.onClose : null,
      // Only the outermost dialog isolates the page behind it.
      marked: stack.length === 0 ? isolate(root) : []
    };

    root.classList.add('open');
    if (!root.hasAttribute('role')) { root.setAttribute('role', 'dialog'); }
    root.setAttribute('aria-modal', 'true');
    if (!root.hasAttribute('tabindex')) { root.setAttribute('tabindex', '-1'); }

    stack.push(entry);
    if (stack.length === 1) {
      document.addEventListener('keydown', onKeydown, true);
    }

    var target = null;
    if (typeof opts.initialFocus === 'string') {
      target = root.querySelector(opts.initialFocus);
    } else if (opts.initialFocus && typeof opts.initialFocus.focus === 'function') {
      target = opts.initialFocus;
    }
    if (!target) { target = focusable(root)[0] || root; }
    target.focus();

    return entry;
  }

  function close(root) {
    var entry = entryFor(root);
    if (!entry) { return; }

    stack.splice(stack.indexOf(entry), 1);
    if (stack.length === 0) {
      document.removeEventListener('keydown', onKeydown, true);
    }

    root.classList.remove('open');
    root.removeAttribute('aria-modal');
    release(entry.marked);

    // Nested: hand focus back to the dialog underneath, not to the page.
    var parent = stack[stack.length - 1];
    if (parent) {
      var inner = focusable(parent.root)[0] || parent.root;
      inner.focus();
    } else if (restorable(entry.opener)) {
      entry.opener.focus();
    } else if (document.body) {
      // Never leave focus on a detached node.
      var fallback = document.querySelector('main, [role="main"]') || document.body;
      if (!fallback.hasAttribute('tabindex')) { fallback.setAttribute('tabindex', '-1'); }
      fallback.focus();
    }

    if (entry.onClose) { entry.onClose(); }
  }

  window.AxisModal = {
    open: open,
    close: close,
    isOpen: function (root) { return !!entryFor(root); }
  };
}());
