/**
 * app.js — VirtuWill SPA router + unified auth
 *
 * Unified admin auth (VW.Auth):
 *   One login unlocks BOTH the journal AND the garden planner.
 *   Entry point: the octopus icon in the footer.
 *   On success: journal nav item appears, garden launches in admin mode.
 *
 * Load order: garden.js → music.js → journal.js → resume.js → chat.js → app.js
 */

'use strict';

window.VW = window.VW || {};

// ── Flask session state ───────────────────────────────────────────────────────
const _session = (typeof FLASK_SESSION !== 'undefined') ? FLASK_SESSION : {};

// ── All routable pages ────────────────────────────────────────────────────────
const ALL_PAGES = ['home','journal','music','garden','planner','travel','resume','portfolio','project','contact','admin'];

// ── Gate state ────────────────────────────────────────────────────────────────
let _gateOpen = false;

// ── Unified admin auth ────────────────────────────────────────────────────────
window.VW.Auth = (() => {
  let _admin = false;

  function isAdmin() { return _admin; }

  function setAdmin(val) {
    _admin = val;
    if (!val) VW.Trackers?.clear?.();
    // Green admin banner across all pages
    const banner = document.getElementById('admin-banner');
    if (banner) banner.style.display = val ? 'flex' : 'none';
    // Reveal journal in nav
    const jItem = document.getElementById('journalNavItem');
    if (jItem) jItem.style.display = val ? 'block' : 'none';
    // Reveal admin portal nav item for admin
    const aItem = document.getElementById('adminNavItem');
    if (aItem) aItem.style.display = val ? 'block' : 'none';
    // Reveal garden planner nav item for admin
    const pItem = document.getElementById('plannerNavItem');
    if (pItem) pItem.style.display = val ? 'block' : 'none';
    // Show/hide gallery upload button and quote edit hint
    const galUploadBtn = document.getElementById('gal-upload-btn');
    if (galUploadBtn) galUploadBtn.style.display = val ? 'flex' : 'none';
    const galHint = document.getElementById('gal-quote-hint');
    if (galHint) galHint.style.display = val ? 'block' : 'none';
    // Turn octopus blue/dim
    const dot = document.getElementById('footerOctopus');
    if (dot) dot.style.opacity = val ? '1' : '0.35';
    // Re-render portfolio grid so admin controls appear/disappear
    window.VW?.Resume?.renderPortfolio?.();
    // Re-render music catalog so admin controls appear/disappear
    window.VW?.Music?.init?.();
    // Sync mobile nav drawer admin links
    VW.Nav?.showMobileAdmin?.(val);
    // Re-render gallery admin elements
    GDN?.Gallery?.onAuthChange?.();
    // Re-render contact inbox
    VW.Contact?.onAuthChange?.();
    // Init admin portal if becoming admin
    if (val) VW.Admin?.onAuthChange?.();
  }

  async function login(password) {
    // Single endpoint — admin password unlocks journal session AND garden admin
    const res  = await fetch('/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password }),
    });
    const data = await res.json();
    if (data.ok) {
      setAdmin(true);
      // Also unlock journal session
      await fetch('/api/journal/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      VW.Journal.setUnlocked(true);
      await VW.Journal.loadEntries();
      // Tell garden it has admin access
      VW.Garden.onAdminLogin?.();
      VW.Travel?.onAuthChange?.();
      return true;
    }
    return false;
  }

  async function logout() {
    await fetch('/api/admin/logout', { method: 'POST' });
    await fetch('/api/journal/logout', { method: 'POST' });
    setAdmin(false);
    VW.Journal.setUnlocked(false);
    VW.Travel?.onAuthChange?.();
    return true;
  }

  return { isAdmin, setAdmin, login, logout };
})();

// ── Shared utilities available to all modules ─────────────────────────────
window.VW.esc = s =>
  String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');

// ── Dirty-state registry ──────────────────────────────────────────────────────
// Modules call VW.Dirty.register(pageId, { isDirty, save, label })
// go() checks before navigating away from the current page.
window.VW.Dirty = (() => {
  const _registry = {};

  function register(pageId, { isDirty, save, label }) {
    _registry[pageId] = { isDirty, save, label: label || pageId };
  }

  function isDirty(pageId) {
    return !!_registry[pageId]?.isDirty?.();
  }

  async function saveNow(pageId) {
    const r = _registry[pageId];
    if (r?.save) await r.save();
  }

  function label(pageId) {
    return _registry[pageId]?.label || pageId;
  }

  return { register, isDirty, saveNow, label };
})();

// ── Discreet gate (octopus icon in footer) ────────────────────────────────────
function openAdminGate() { if (!_gateOpen) toggleGate(); }

function toggleGate() {
  _gateOpen = !_gateOpen;
  const panel = document.getElementById('adminGate');
  if (!panel) return;
  panel.style.display = _gateOpen ? 'block' : 'none';
  if (_gateOpen) {
    closeDrawer();
    setTimeout(() => document.getElementById('gatePassInput')?.focus(), 60);
  }
}

function closeGate() {
  _gateOpen = false;
  const panel = document.getElementById('adminGate');
  if (panel) panel.style.display = 'none';
  const err = document.getElementById('gateErrMsg');
  if (err) err.textContent = '';
  const inp = document.getElementById('gatePassInput');
  if (inp) { inp.value = ''; inp.style.borderColor = ''; inp.style.boxShadow = ''; }
}

async function tryAdminLogin() {
  const inp = document.getElementById('gatePassInput');
  const pw  = inp?.value || '';
  const ok  = await VW.Auth.login(pw);
  if (ok) {
    closeGate();
    toast('Admin mode active — journal, garden &amp; music unlocked', 'success');
  } else {
    const err = document.getElementById('gateErrMsg');
    if (err) err.textContent = 'Incorrect password.';
    if (inp) {
      inp.style.borderColor = '#e84235';
      inp.style.boxShadow   = '0 0 0 3px rgba(232,66,53,.08)';
    }
  }
}

// ── Breadcrumb ────────────────────────────────────────────────────────────────
window.VW.Nav = {
  toggleMobile() {
    const drawer  = document.getElementById('nav-mobile-drawer');
    const overlay = document.getElementById('nav-drawer-overlay');
    const btn     = document.getElementById('nav-hamburger');
    const isOpen  = drawer?.classList.contains('open');
    drawer?.classList.toggle('open', !isOpen);
    overlay?.classList.toggle('open', !isOpen);
    btn?.classList.toggle('open', !isOpen);
    btn?.setAttribute('aria-expanded', String(!isOpen));
  },
  closeMobile() {
    const drawer  = document.getElementById('nav-mobile-drawer');
    const overlay = document.getElementById('nav-drawer-overlay');
    const btn     = document.getElementById('nav-hamburger');
    drawer?.classList.remove('open');
    overlay?.classList.remove('open');
    btn?.classList.remove('open');
    btn?.setAttribute('aria-expanded', 'false');
  },
  syncMobileActive(page) {
    document.querySelectorAll('.nav-drawer-link').forEach(el => el.classList.remove('active'));
    document.getElementById('nmd-' + page)?.classList.add('active');
  },
  showMobileAdmin(show) {
    const g = document.getElementById('nmd-admin-group');
    if (g) g.style.display = show ? 'block' : 'none';
  },
  setBreadcrumb(parts) {
    const bc = document.getElementById('breadcrumb');
    if (!bc) return;
    let html = '';
    parts.forEach((label, i) => {
      if (i > 0) html += `<span class="bc-sep">›</span>`;
      if (i === parts.length - 1) {
        html += `<span class="bc-current">${label}</span>`;
      } else if (label === 'Resume') {
        html += `<span class="bc-link" onclick="go('resume')">${label}</span>`;
      } else {
        html += `<span class="bc-link" onclick="VW.Resume.showPortfolio()">${label}</span>`;
      }
    });
    bc.innerHTML = html;
  },
};

// ── Router ────────────────────────────────────────────────────────────────────
let _currentPage = 'home';
let _navigating  = false;   // prevent pushState loop during popstate handling

function go(page, { pushState: push = true, skipDirtyCheck: skipDirty = false } = {}) {
  if (page === 'admin' && !VW.Auth.isAdmin()) { openAdminGate(); return; }
  // Journal requires admin auth
  if (page === 'journal' && !VW.Journal.isUnlocked()) {
    toggleGate(); return;
  }

  // Check for unsaved changes on the current page
  if (!skipDirty && _currentPage && _currentPage !== page
      && window.VW?.Dirty?.isDirty?.(_currentPage)) {
    _showDirtyModal(_currentPage, page, push);
    return;
  }

  // Notify music module when leaving music page
  const musicPage = document.getElementById('page-music');
  if (musicPage?.classList.contains('active')) VW.Music?.onLeave?.();

  ALL_PAGES.forEach(p => {
    const el = document.getElementById('page-' + p);
    if (el) { el.classList.remove('active'); el.style.display = 'none'; }
  });

  const target = document.getElementById('page-' + page);
  if (!target) return;
  target.style.display = (page === 'journal' || page === 'admin' || page === 'planner') ? 'flex' : 'block';
  target.classList.add('active');

  // Highlight the active nav link (desktop + mobile drawer)
  document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
  const activeLink = document.getElementById('nl-' + page);
  if (activeLink) activeLink.classList.add('active');
  VW.Nav.syncMobileActive(page);

  // Push browser history entry so back/forward work
  if (push && !_navigating) {
    const url = '#' + page;
    if (window.location.hash !== url) {
      history.pushState({ page }, '', url);
    }
  }
  _currentPage = page;

  if (page === 'music') {
    VW.Music.init();
    const adminTab = document.getElementById('music-tab-admin');
    if (adminTab) adminTab.style.display = VW.Auth.isAdmin() ? '' : 'none';
  }
  if (page === 'garden') {
    GDN?.Gallery?.init?.();
    GDN?.Viewer?.init?.();
  }
  if (page === 'contact')   VW.Contact?.init?.();
  if (page === 'home')      VW.Blog?.initHome?.();
  if (page === 'admin')     VW.Admin?.init?.();
  if (page === 'planner')   { gdn?.init?.(); }
  if (page === 'travel')    VW.Travel?.init?.();
  if (page === 'journal')   VW.Journal.onEnter();
  if (page === 'resume')    VW.Resume.onEnter();
  if (page === 'portfolio') VW.Resume.onEnterPortfolio();
}

// ── Dirty-state confirm modal ────────────────────────────────────────────────
function _showDirtyModal(fromPage, toPage, pushState) {
  const lbl = window.VW?.Dirty?.label?.(fromPage) || fromPage;
  const modal = document.getElementById('dirty-confirm-modal');
  const msg   = document.getElementById('dirty-confirm-msg');
  if (!modal) {
    // Fallback: native browser confirm
    if (confirm(`You have unsaved changes in ${lbl}.\nSave before leaving?`)) {
      window.VW.Dirty.saveNow(fromPage).then(() => go(toPage, { pushState, skipDirtyCheck: true }));
    } else {
      go(toPage, { pushState, skipDirtyCheck: true });
    }
    return;
  }
  if (msg) msg.textContent = `You have unsaved changes in "${lbl}". What would you like to do?`;
  modal.classList.add('open');
  // Wire buttons
  document.getElementById('dirty-btn-save').onclick = async () => {
    modal.classList.remove('open');
    await window.VW.Dirty.saveNow(fromPage);
    go(toPage, { pushState, skipDirtyCheck: true });
  };
  document.getElementById('dirty-btn-discard').onclick = () => {
    modal.classList.remove('open');
    go(toPage, { pushState, skipDirtyCheck: true });
  };
  document.getElementById('dirty-btn-cancel').onclick = () => {
    modal.classList.remove('open');
  };
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'success', dur = 4000) {
  const container = document.getElementById('toasts');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = `<div class="t-dot"></div>${msg}`;
  container.appendChild(el);
  setTimeout(() => el.remove(), dur);
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Restore server-side admin session if present
  if (_session.adminLoggedIn) {
    VW.Auth.setAdmin(true);
    VW.Journal.setUnlocked(true);
    VW.Journal.loadEntries();
  }

  VW.Chat.init();

  // Warn on tab/window close if there are unsaved changes
  window.addEventListener('beforeunload', e => {
    if (_currentPage && window.VW?.Dirty?.isDirty?.(_currentPage)) {
      e.preventDefault();
      e.returnValue = ''; // required for Chrome
    }
  });
  // Click-outside gate dismissal
  document.addEventListener('click', e => {
    const gate    = document.getElementById('adminGate');
    const octoNav = document.getElementById('nav-octo-btn');
    const oct     = document.getElementById('footerOctopus');
    const mobileTrigger = document.getElementById('nmd-admin-login');
    const triggers = [octoNav, oct, mobileTrigger].filter(Boolean);
    const clickedTrigger = triggers.some(trigger => trigger.contains(e.target));
    if (gate && !gate.contains(e.target) && !clickedTrigger && _gateOpen) closeGate();
  });

  // ── Browser back/forward ──────────────────────────────────────────────────
  window.addEventListener('popstate', e => {
    const page = e.state?.page || _pageFromHash() || 'home';
    _navigating = true;
    VW.Nav.closeMobile();
    go(page, { pushState: false });
    _navigating = false;
  });

  // ── Resolve initial page ──────────────────────────────────────────────────
  // On a fresh page load always start on Home — the hash may be stale from
  // a previous session. Hash-based routing only applies during the current
  // session (back/forward via popstate). This prevents the app opening on
  // whatever page the user happened to be on last time.
  history.replaceState({ page: 'home' }, '', '#home');
  go('home', { pushState: false });
});

function _pageFromHash() {
  const hash = window.location.hash.replace('#', '').toLowerCase();
  return ALL_PAGES.includes(hash) ? hash : null;
}

// ── Global exports ────────────────────────────────────────────────────────────
window.go            = go;
window.toast         = toast;
window.toggleGate    = toggleGate;
window.openAdminGate = openAdminGate;
window.closeGate     = closeGate;
window.tryAdminLogin = tryAdminLogin;
