/* Profile page (Phase 5 · Surface 3). Top-level globals for data-action + listeners. */
var __t = (window.t) || function (k) { return k; };
var selectedGoal = (document.body.getAttribute('data-goal') || '');
var avatarSaving = false;
var profileSaving = false;
var _editOpener = null;

function toast(msg, type) {
  type = type || 'info';
  var wrap = document.getElementById('toast-wrap');
  if (!wrap) return;
  var el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(function () { el.style.opacity = '0'; setTimeout(function () { el.remove(); }, 300); }, 3000);
}

function selectGoal(goal, el) {
  document.querySelectorAll('.pf-goal .pf-choice').forEach(function (c) {
    c.classList.remove('selected');
    c.setAttribute('aria-pressed', 'false');
  });
  if (el) { el.classList.add('selected'); el.setAttribute('aria-pressed', 'true'); }
  selectedGoal = goal;
}

function updateAvatarLetter() {
  var display = document.getElementById('avatar-display');
  if (!display || display.querySelector('img')) return;
  var uname = document.getElementById('username');
  var letter = ((uname && uname.value) || 'U')[0].toUpperCase();
  var span = display.querySelector('span');
  if (span) span.textContent = letter;
}

function openEditSheet(btn) {
  _editOpener = btn || document.activeElement;
  var sheet = document.getElementById('edit-sheet');
  sheet.classList.add('open');
  var dialog = sheet.querySelector('.sheet');
  var first = sheet.querySelector('input, button, [tabindex]');
  if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
  else if (dialog) { dialog.focus(); }
}

function closeEditSheet() {
  var sheet = document.getElementById('edit-sheet');
  sheet.classList.remove('open');
  if (_editOpener) { try { _editOpener.focus({ preventScroll: true }); } catch (e) { _editOpener.focus(); } }
}

async function saveProfile() {
  if (profileSaving || avatarSaving) return;
  profileSaving = true;
  var btn = document.getElementById('save-btn');
  var fileInput = document.getElementById('avatar-file-input');
  btn.disabled = true;
  if (fileInput) fileInput.disabled = true;
  btn.textContent = __t('common.saving');
  var tw = document.getElementById('target_weight');
  var payload = {
    full_name: document.getElementById('full_name').value.trim(),
    username: document.getElementById('username').value.trim(),
    goal: selectedGoal,
    target_weight: tw ? tw.value.trim() : ''
  };
  var willReload = false;
  try {
    var res = await fetch('/edit-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var data = await res.json();
    if (!res.ok) { toast(data.error || __t('common.error'), 'error'); }
    else { willReload = true; toast(data.message, 'success'); setTimeout(function () { location.reload(); }, 800); }
  } catch (e) {
    toast(__t('common.conn_error'), 'error');
  } finally {
    profileSaving = false;
    btn.textContent = __t('common.save');
    if (!willReload) {
      btn.disabled = false;
      if (fileInput) fileInput.disabled = false;
    }
  }
}

async function saveAvatar(dataUrl) {
  var fileInput = document.getElementById('avatar-file-input');
  var btn = document.getElementById('save-btn');
  try {
    var res = await fetch('/edit-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_picture: dataUrl })
    });
    var data = await res.json();
    if (!res.ok) { toast(data.error || __t('common.error'), 'error'); return; }
    var display = document.getElementById('avatar-display');
    var img = display.querySelector('img') || document.createElement('img');
    img.src = dataUrl;
    img.alt = 'Profil';
    var letter = display.querySelector('span');
    if (letter) letter.remove();
    if (!display.querySelector('img')) display.insertBefore(img, display.querySelector('.pf-avatar-overlay'));
    toast(data.message, 'success');
  } catch (e) {
    toast(__t('common.conn_error'), 'error');
  } finally {
    avatarSaving = false;
    if (fileInput) { fileInput.disabled = false; fileInput.value = ''; }
    if (btn) btn.disabled = false;
  }
}

document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  var sheet = document.getElementById('edit-sheet');
  if (sheet && sheet.classList.contains('open')) { closeEditSheet(); }
});

document.addEventListener('DOMContentLoaded', function () {
  var uname = document.getElementById('username');
  if (uname) uname.addEventListener('input', updateAvatarLetter);

  var fileInput = document.getElementById('avatar-file-input');
  if (fileInput) fileInput.addEventListener('change', function (e) {
    if (avatarSaving || profileSaving) return;
    var file = e.target.files[0];
    if (!file) return;
    if (file.size > 374000) { toast(__t('editprofile.avatar_max'), 'error'); return; }
    avatarSaving = true;
    fileInput.disabled = true;
    document.getElementById('save-btn').disabled = true;
    var reader = new FileReader();
    reader.onload = function (ev) {
      if (ev.target.result.length > 500000) {
        avatarSaving = false;
        fileInput.disabled = false;
        document.getElementById('save-btn').disabled = false;
        toast(__t('editprofile.avatar_max'), 'error');
        return;
      }
      saveAvatar(ev.target.result);
    };
    reader.onerror = function () {
      avatarSaving = false;
      fileInput.disabled = false;
      document.getElementById('save-btn').disabled = false;
      toast(__t('common.error'), 'error');
    };
    reader.readAsDataURL(file);
  });

  document.querySelectorAll('.wearable-connect').forEach(function (btn) {
    btn.addEventListener('click', function () { window.location.href = '/api/auth/wearable/' + btn.dataset.provider; });
  });
  document.querySelectorAll('.wearable-sync').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      btn.disabled = true;
      try {
        var res = await fetch('/api/wearables/' + btn.dataset.provider + '/sync', { method: 'POST' });
        var data = await res.json();
        if (!res.ok) { toast(data.error || __t('wearables.sync_failed'), 'error'); }
        else { toast(__t('wearables.sync_done'), 'success'); setTimeout(function () { location.reload(); }, 700); }
      } catch (e) { toast(__t('common.conn_error'), 'error'); }
      finally { btn.disabled = false; }
    });
  });
});
