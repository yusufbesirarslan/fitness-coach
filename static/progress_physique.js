/* ══════════════════════════════════════════════════════════════════════
   progress_physique.js — PHYSIQUE PROGRESS consumer (Progress redesign PR4)

   Hard rule for this file: it RENDERS canonical facts, it does not decide.
   The server chose the body area, the recent checks, and whether a persisted
   comparison exists. Comparability arrives as comparable / limited /
   not_comparable only. An unknown token is contract drift and is rendered
   as the isolated unavailable state, never as comparison content. This
   file never:

     - compares two analyses to infer "improved"
     - upgrades limited → comparable
     - invents a comparison while one is missing
     - scores, percentages, or diagnoses a physique

   Persisted comparison prose is untrusted. Every string from the payload is
   written with textContent (or setAttribute for alt/src). No innerHTML of
   analysis fields.

   tests/test_progress_physique_ui.py enforces that structurally.

   progress.js only calls window.FitXPhysiqueProgress.load() — it keeps no
   physique rendering of its own.
   ══════════════════════════════════════════════════════════════════════ */

(function () {
  var __t = (window.t) || function (k) { return k; };
  var ENDPOINT = '/api/progress/physique';
  var GALLERY = '/pump-check-gallery';

  var REGION_LABELS = {
    full_body: 'progress.physique_region_full_body',
    upper_body: 'progress.physique_region_upper_body',
    lower_body: 'progress.physique_region_lower_body',
    back: 'progress.physique_region_back',
    arms: 'progress.physique_region_arms',
    legs: 'progress.physique_region_legs'
  };

  var COMPARABILITY_LABELS = {
    comparable: 'progress.physique_comparability_comparable',
    limited: 'progress.physique_comparability_limited',
    not_comparable: 'progress.physique_comparability_not_comparable'
  };

  var STATES = {
    empty: true,
    single_check: true,
    history_only: true,
    comparison_available: true
  };

  var _selectedRegion = null;

  function _el(id) { return document.getElementById(id); }

  function _text(tag, value, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? '' : String(value);
    return node;
  }

  function _safeHttpUrl(value) {
    if (typeof value !== 'string') return null;
    if (value.indexOf('https://') === 0 || value.indexOf('http://') === 0) {
      return value;
    }
    return null;
  }

  function _formatDate(iso) {
    if (typeof iso !== 'string' || !iso) return '';
    var parsed = new Date(iso);
    if (isNaN(parsed.getTime())) return '';
    try {
      return new Intl.DateTimeFormat(window.LOCALE || 'tr', {
        year: 'numeric', month: 'short', day: 'numeric'
      }).format(parsed);
    } catch (e) {
      return iso.slice(0, 10);
    }
  }

  function _regionLabel(region) {
    var key = REGION_LABELS[region];
    return key ? __t(key) : (region || '');
  }

  function _clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function _galleryLink(labelKey) {
    var a = document.createElement('a');
    a.className = 'pp-link';
    a.href = GALLERY;
    a.textContent = __t(labelKey);
    return a;
  }

  function _figure(url, alt, caption) {
    var fig = document.createElement('figure');
    fig.className = 'pp-figure';
    var safe = _safeHttpUrl(url);
    if (safe) {
      var img = document.createElement('img');
      img.src = safe;
      img.alt = alt || '';
      img.loading = 'lazy';
      fig.appendChild(img);
    } else {
      fig.appendChild(_text('div', __t('progress.physique_image_unavailable'), 'pp-image-missing'));
    }
    if (caption) {
      var cap = document.createElement('figcaption');
      cap.className = 'pp-caption';
      cap.textContent = caption;
      fig.appendChild(cap);
    }
    return fig;
  }

  function _list(title, items, limit) {
    if (!items || !items.length) return null;
    var wrap = document.createElement('div');
    wrap.className = 'pp-list';
    wrap.appendChild(_text('h4', title, 'pp-list-h'));
    var ul = document.createElement('ul');
    var max = typeof limit === 'number' ? Math.min(limit, items.length) : items.length;
    var i;
    for (i = 0; i < max; i += 1) {
      if (typeof items[i] !== 'string') continue;
      ul.appendChild(_text('li', items[i]));
    }
    if (!ul.firstChild) return null;
    wrap.appendChild(ul);
    return wrap;
  }

  function _renderRegions(container, payload) {
    var regions = payload.regions || [];
    if (regions.length < 2) return;
    var group = document.createElement('div');
    group.className = 'pp-regions';
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', __t('progress.physique_area_label'));
    var selected = payload.selected_region;
    regions.forEach(function (region) {
      if (!region || typeof region.body_region !== 'string') return;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pp-chip';
      btn.setAttribute('role', 'radio');
      var isOn = region.body_region === selected;
      btn.setAttribute('aria-checked', isOn ? 'true' : 'false');
      btn.setAttribute('tabindex', isOn ? '0' : '-1');
      btn.dataset.region = region.body_region;
      btn.textContent = _regionLabel(region.body_region);
      btn.addEventListener('click', function () {
        load(region.body_region);
      });
      group.appendChild(btn);
    });
    group.addEventListener('keydown', function (event) {
      var keys = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 1, ArrowUp: -1 };
      var delta = keys[event.key];
      if (!delta) return;
      var chips = group.querySelectorAll('[role="radio"]');
      var i;
      var idx = 0;
      for (i = 0; i < chips.length; i += 1) {
        if (chips[i] === document.activeElement) idx = i;
      }
      var next = chips[(idx + delta + chips.length) % chips.length];
      if (next) {
        event.preventDefault();
        next.focus();
        load(next.dataset.region);
      }
    });
    container.appendChild(group);
  }

  function _empty(container, payload) {
    var wrap = document.createElement('div');
    wrap.className = 'empty-state';
    wrap.appendChild(_text('div', __t('progress.physique_empty_title'), 'empty-title'));
    wrap.appendChild(_text('p', __t('progress.physique_empty_desc'), 'empty-desc'));
    if (payload && payload.legacy_gallery_available) {
      wrap.appendChild(_text('p', __t('progress.physique_legacy_note'), 'pp-note'));
    }
    wrap.appendChild(_galleryLink('progress.physique_add_check'));
    container.appendChild(wrap);
  }

  function _recent(container, checks, titleKey) {
    if (!checks || !checks.length) return;
    if (titleKey) container.appendChild(_text('p', __t(titleKey), 'pp-note'));
    var strip = document.createElement('div');
    strip.className = 'pp-strip';
    checks.forEach(function (check) {
      if (!check) return;
      var when = _formatDate(check.captured_at);
      strip.appendChild(_figure(
        check.image_url,
        __t('progress.physique_alt_check', {
          area: _regionLabel(check.body_region),
          date: when
        }),
        when
      ));
    });
    container.appendChild(strip);
  }

  function _single(container, payload) {
    container.appendChild(_text('p', __t('progress.physique_single_title'), 'pp-status'));
    container.appendChild(_text('p', __t('progress.physique_single_desc'), 'pp-note'));
    _recent(container, payload.recent_checks);
    container.appendChild(_galleryLink('progress.physique_add_check'));
  }

  function _historyOnly(container, payload) {
    container.appendChild(_text('p', __t('progress.physique_history_title'), 'pp-status'));
    container.appendChild(_text('p', __t('progress.physique_history_desc'), 'pp-note'));
    _recent(container, payload.recent_checks);
    container.appendChild(_galleryLink('progress.physique_view_all'));
  }

  function _comparison(container, payload) {
    var comparison = payload.comparison || {};
    var comparability = comparison.comparability;
    if (!COMPARABILITY_LABELS[comparability]) {
      _unavailable(container);
      return;
    }
    var analysis = comparison.analysis || {};
    var pair = document.createElement('div');
    pair.className = 'pp-compare';
    pair.setAttribute('data-comparability', comparability);

    var badge = _text('p', __t(COMPARABILITY_LABELS[comparability]), 'pp-reliability');
    badge.setAttribute('data-comparability', comparability);
    pair.appendChild(badge);

    if (comparison.current_is_latest_check === false) {
      pair.appendChild(_text('p', __t('progress.physique_latest_saved'), 'pp-note'));
    }

    var frames = document.createElement('div');
    frames.className = 'pp-pair';
    var baselineWhen = _formatDate(comparison.baseline_captured_at);
    var currentWhen = _formatDate(comparison.current_captured_at);
    frames.appendChild(_figure(
      comparison.baseline_image_url,
      __t('progress.physique_alt_baseline', { date: baselineWhen }),
      __t('progress.physique_baseline') + (baselineWhen ? ' · ' + baselineWhen : '')
    ));
    frames.appendChild(_figure(
      comparison.current_image_url,
      __t('progress.physique_alt_current', { date: currentWhen }),
      __t('progress.physique_current') + (currentWhen ? ' · ' + currentWhen : '')
    ));
    pair.appendChild(frames);

    if (comparability === 'not_comparable') {
      pair.appendChild(_text('p', __t('progress.physique_not_comparable_title'), 'pp-status'));
      pair.appendChild(_text('p', __t('progress.physique_not_comparable_desc'), 'pp-note'));
      var reasons = _list(
        __t('progress.physique_limitations'),
        (analysis.comparability_reasons || []).concat(analysis.limitations || []),
        4
      );
      if (reasons) pair.appendChild(reasons);
    } else {
      if (comparability === 'limited') {
        pair.appendChild(_text('p', __t('progress.physique_limited_label'), 'pp-limited'));
      }
      if (typeof analysis.summary === 'string' && analysis.summary) {
        pair.appendChild(_text('p', analysis.summary, 'pp-summary'));
      }
      if (comparability === 'limited') {
        var limits = _list(
          __t('progress.physique_limitations'),
          (analysis.limitations || []).concat(analysis.comparability_reasons || []),
          4
        );
        if (limits) pair.appendChild(limits);
      }
      var changes = _list(__t('progress.physique_observed'), analysis.observed_changes, 2);
      if (changes) pair.appendChild(changes);
      var stable = _list(__t('progress.physique_stable'), analysis.stable_areas, 2);
      if (stable) pair.appendChild(stable);
      var focus = _list(__t('progress.physique_focus'), analysis.focus_areas, 2);
      if (focus) pair.appendChild(focus);
    }

    if (typeof analysis.next_check_guidance === 'string' && analysis.next_check_guidance) {
      var guide = document.createElement('div');
      guide.className = 'pp-guidance';
      guide.appendChild(_text('h4', __t('progress.physique_guidance'), 'pp-list-h'));
      guide.appendChild(_text('p', analysis.next_check_guidance));
      pair.appendChild(guide);
    }

    container.appendChild(pair);
    container.appendChild(_galleryLink('progress.physique_view_all'));
  }

  function _unavailable(container) {
    _clear(container);
    container.removeAttribute('data-state');
    container.setAttribute('data-status', 'unavailable');
    container.appendChild(_text('p', __t('progress.physique_unavailable'), 'prog-note'));
  }

  function _render(container, payload) {
    _clear(container);
    if (!payload || typeof payload !== 'object' || !STATES[payload.state]) {
      _unavailable(container);
      return;
    }
    container.removeAttribute('data-status');
    container.setAttribute('data-state', payload.state);
    if (payload.selected_region) {
      container.setAttribute('data-region', payload.selected_region);
      _selectedRegion = payload.selected_region;
    }
    _renderRegions(container, payload);
    if (payload.selected_region) {
      container.appendChild(_text(
        'p',
        __t('progress.physique_selected_area', { area: _regionLabel(payload.selected_region) }),
        'pp-area'
      ));
    }
    if (payload.state === 'empty') {
      _empty(container, payload);
      return;
    }
    if (payload.state === 'single_check') {
      _single(container, payload);
      return;
    }
    if (payload.state === 'history_only') {
      _historyOnly(container, payload);
      return;
    }
    if (payload.state === 'comparison_available' && payload.comparison) {
      if (!COMPARABILITY_LABELS[payload.comparison.comparability]) {
        _unavailable(container);
        return;
      }
      _comparison(container, payload);
      return;
    }
    _unavailable(container);
  }

  function load(region) {
    var box = _el('physique-body');
    if (!box) return Promise.resolve();
    var url = ENDPOINT;
    var chosen = typeof region === 'string' && region ? region : _selectedRegion;
    if (chosen) url += '?region=' + encodeURIComponent(chosen);
    return fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(function (d) { _render(box, d); })
      .catch(function () { _unavailable(box); });
  }

  window.FitXPhysiqueProgress = { load: load };
})();
