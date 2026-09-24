/**
 * app.js — the public site's router.
 *
 * Real URLs: /music/<song>, /projects/<id>, /writing/<post>, /garden, /travel,
 * /resume, /contact. Old #page links still work. The public site is read-only:
 * editing happens in the owner's workspace at /app.
 *
 * Load order: page modules first, app.js last.
 */
'use strict';

window.VW = window.VW || {};
const _session = (typeof FLASK_SESSION !== 'undefined') ? FLASK_SESSION : {};

const PAGES = ['home', 'music', 'projects', 'writing', 'garden', 'travel', 'resume', 'contact'];
// Old hash routes and pages that moved.
const MOVED = { portfolio: '/projects', project: '/projects', blog: '/writing', notes: '/contact',
                admin: '/app', journal: '/app/journal', planner: '/app/site/garden' };

// Public pages never show edit controls; the owner edits in the workspace.
window.VW.Auth = { isAdmin: () => false, setAdmin() {}, isOwner: () => !!_session.adminLoggedIn };

window.VW.Nav = {
  toggleMobile() {
    const drawer = document.getElementById('nav-mobile-drawer');
    const open = !drawer?.classList.contains('open');
    drawer?.classList.toggle('open', open);
    document.getElementById('nav-drawer-overlay')?.classList.toggle('open', open);
    const btn = document.getElementById('nav-hamburger');
    btn?.classList.toggle('open', open);
    btn?.setAttribute('aria-expanded', String(open));
  },
  closeMobile() {
    document.getElementById('nav-mobile-drawer')?.classList.remove('open');
    document.getElementById('nav-drawer-overlay')?.classList.remove('open');
    const btn = document.getElementById('nav-hamburger');
    btn?.classList.remove('open');
    btn?.setAttribute('aria-expanded', 'false');
  },
  setBreadcrumb() { /* retired with the portfolio breadcrumb */ },
};

// ── Router ────────────────────────────────────────────────────────────────────
function parse(pathname) {
  const parts = pathname.replace(/\/+$/, '').split('/').filter(Boolean).map(decodeURIComponent);
  return { page: parts[0] || 'home', sub: parts[1] || null };
}

function go(target, { push = true } = {}) {
  // go('music') and go('/music/voodoo') both work.
  const path = target.startsWith('/') ? target : (target === 'home' ? '/' : '/' + target);
  const { page, sub } = parse(path);
  if (MOVED[page]) { location.href = MOVED[page]; return; }
  const known = PAGES.includes(page);

  document.querySelectorAll('.page').forEach(el => { el.classList.remove('active'); el.style.display = ''; });
  const el = document.getElementById('page-' + (known ? page : 'notfound'));
  el?.classList.add('active');

  document.querySelectorAll('.nav-link, .nav-drawer-link').forEach(a => {
    const on = a.id === 'nl-' + page || a.id === 'nmd-' + page;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });

  if (push && location.pathname !== path) history.pushState({}, '', path);
  VW.Nav.closeMobile();
  window.scrollTo(0, 0);

  const titles = { home: 'Will Yoste', garden: 'Garden · Will Yoste', travel: 'Travel · Will Yoste',
                   resume: 'Resume · Will Yoste', contact: 'Say hi · Will Yoste' };
  if (titles[page]) document.title = titles[page];
  if (!known) { document.title = 'Not found · Will Yoste'; return; }

  if (page === 'home')     VW.Blog?.initHome?.();
  if (page === 'music')    VW.Music.onEnter(sub);
  if (page === 'projects') VW.Projects.onEnter(sub);
  if (page === 'writing')  VW.Writing.onEnter(sub);
  if (page === 'garden')   { GDN?.Gallery?.init?.(); GDN?.Viewer?.init?.(); }
  if (page === 'travel')   VW.Travel?.init?.();
  if (page === 'resume')   VW.Resume?.onEnter?.();
  if (page === 'contact')  VW.Contact?.init?.();
  document.getElementById('main')?.focus({ preventScroll: true });
}

// In-site links (data-link, or any same-origin link to a public page) navigate without reloading.
document.addEventListener('click', event => {
  const a = event.target.closest('a[href]');
  if (!a || a.target || a.hasAttribute('download') || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || /^\/(app|api|static|resume\/download)(\/|$)/.test(url.pathname)) return;
  if (!a.hasAttribute('data-link') && !PAGES.includes(parse(url.pathname).page)) return;
  event.preventDefault();
  go(url.pathname);
});

window.addEventListener('popstate', () => go(location.pathname, { push: false }));

function toast(msg, type = 'success', dur = 4000) {
  const container = document.getElementById('toasts');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.append(Object.assign(document.createElement('div'), { className: 't-dot' }), String(msg).replace(/<[^>]*>/g, ''));
  container.appendChild(el);
  setTimeout(() => el.remove(), dur);
}

document.addEventListener('DOMContentLoaded', () => {
  VW.Chat?.init?.();
  // Old links like /#resume keep working.
  const hash = location.hash.replace('#', '').toLowerCase();
  if (hash && location.pathname === '/' && (PAGES.includes(hash) || MOVED[hash])) {
    if (MOVED[hash]) { location.replace(MOVED[hash]); return; }
    history.replaceState({}, '', hash === 'home' ? '/' : '/' + hash);
  }
  go(location.pathname, { push: false });
});

window.go = go;
window.toast = toast;
