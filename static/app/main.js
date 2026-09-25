// The workspace: one sidebar, one screen at a time, real URLs under /app.
import { h, api, toast, dialog, unsaved, saveAll, forgetEdits, setDiscard } from './lib.js';

const NAV = [
  { href: '/app', label: 'Today', icon: '☀' },
  { href: '/app/journal', label: 'Journal', icon: '✎' },
  { href: '/app/health', label: 'Health', icon: '♥', children: [
    ['/app/health', 'Overview'], ['/app/health/activity', 'Activity'], ['/app/health/food', 'Food'],
    ['/app/health/body', 'Body'], ['/app/health/goals', 'Goals']] },
  { href: '/app/money', label: 'Money', icon: '$', children: [
    ['/app/money', 'Overview'], ['/app/money/transactions', 'Transactions'], ['/app/money/receipts', 'Receipts'],
    ['/app/money/accounts', 'Accounts & balances'], ['/app/money/budgets', 'Budgets & bills'],
    ['/app/money/goals', 'Goals & retirement'], ['/app/money/imports', 'Imports'], ['/app/money/editor', 'Finance tracker']] },
  { href: '/app/site', label: 'Site', icon: '◎', children: [
    ['/app/site/music', 'Music'], ['/app/site/career', 'Career'], ['/app/site/writing', 'Writing'],
    ['/app/site/garden', 'Garden'], ['/app/site/travel', 'Travel'], ['/app/site/messages', 'Messages']] },
  { href: '/app/settings', label: 'Settings', icon: '⚙' },
];

// path prefix → screen module
const SCREENS = [
  ['/app/journal', () => import('./screens/journal.js')],
  ['/app/health', () => import('./screens/health.js')],
  ['/app/money', () => import('./screens/money.js')],
  ['/app/site/music', () => import('./screens/site-music.js')],
  ['/app/site/career', () => import('./screens/site-career.js')],
  ['/app/site/writing', () => import('./screens/site-writing.js')],
  ['/app/site/garden', () => import('./screens/site-garden.js')],
  ['/app/site/travel', () => import('./screens/site-travel.js')],
  ['/app/site/messages', () => import('./screens/site-messages.js')],
  ['/app/site', null],                                    // → Music
  ['/app/settings', () => import('./screens/settings.js')],
  ['/app', () => import('./screens/today.js')],
];

let cleanup = null;
let dirtyCheck = null;
let shown = location.pathname + location.search;   // the screen on view, for Back/Forward that is cancelled

// Screens with unsaved edits register a check; leaving asks first.
export function setDirty(fn) { dirtyCheck = fn; }

export function navigate(href, { replace = false } = {}) {
  if (replace) history.replaceState({}, '', href); else history.pushState({}, '', href);
  render();
}

async function confirmLeave() {
  const own = dirtyCheck?.(), edited = unsaved();
  if (!own && !edited) return true;
  // Screens with their own editor (journal, song, post) save through it; the rest can be saved from here.
  const choice = await dialog('Leave without saving?', h('p', {}, 'You have unsaved changes on this screen.'),
    [['Stay', null], ['Discard changes', 'discard'], ...(edited && !own ? [['Save & leave', 'save']] : [])]);
  if (!choice) return false;
  if (choice === 'save' && !(await saveAll())) return false;
  dirtyCheck = null;
  forgetEdits();
  return true;
}

function sidebar() {
  const path = location.pathname.replace(/\/$/, '') || '/app';
  const active = href => path === href || (href !== '/app' && path.startsWith(href + '/'));
  return h('nav', { class: 'ws-nav', 'aria-label': 'Workspace' },
    NAV.map(item => {
      const open = item.children && (active(item.href) || item.children.some(([href]) => active(href)));
      const isOn = item.href === '/app' ? path === '/app' : active(item.href);
      return h('div', { class: 'ws-nav-group' + (open ? ' open' : '') },
        h('a', { href: item.children ? item.children[0][0] : item.href, class: 'ws-nav-item' + (isOn ? ' on' : ''),
                 'aria-current': isOn && !item.children ? 'page' : null },
          h('span', { class: 'ws-nav-icon', 'aria-hidden': 'true' }, item.icon), h('span', {}, item.label)),
        open ? h('div', { class: 'ws-subnav' }, item.children.map(([href, label]) => {
          // A child is current on its own page and its sub-pages (Career › Experience), unless another child is exact.
          const on = path === href || (path.startsWith(href + '/') && !item.children.some(([other]) => other === path));
          return h('a', { href, class: on ? 'on' : null, 'aria-current': on ? 'page' : null }, label);
        })) : null);
    }));
}

async function render() {
  const path = location.pathname.replace(/\/$/, '') || '/app';
  if (path === '/app/site') return navigate('/app/site/music', { replace: true });
  if (path === '/app/site/projects') return navigate('/app/site/career/projects', { replace: true });
  document.getElementById('ws-sidebar-nav').replaceChildren(sidebar());
  document.body.classList.remove('nav-open');
  const main = document.getElementById('ws-main');
  if (cleanup) { try { cleanup(); } catch { /* screen already gone */ } cleanup = null; }
  dirtyCheck = null;
  forgetEdits();
  shown = location.pathname + location.search;
  const match = SCREENS.find(([prefix]) => path === prefix || path.startsWith(prefix + '/'));
  main.replaceChildren(h('div', { class: 'ws-loading' }, 'Loading…'));
  try {
    const module = await match[1]();
    const view = h('div', { class: 'ws-view' });
    main.replaceChildren(view);
    cleanup = (await module.render(view, { path, params: new URLSearchParams(location.search), navigate })) || null;
    main.focus({ preventScroll: true });
    const title = view.querySelector('h1')?.textContent;
    document.title = (title ? title + ' · ' : '') + 'VirtuWill workspace';
  } catch (error) {
    main.replaceChildren(h('div', { class: 'ws-error' }, h('h1', {}, 'This screen could not load'), h('p', {}, error.message),
      h('button', { class: 'btn', onclick: render }, 'Try again')));
  }
}

// ── Links: in-app navigation without reloads ─────────────────────────────────
document.addEventListener('click', async event => {
  const a = event.target.closest('a[href]');
  if (!a || a.target || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith('/app')) return;
  event.preventDefault();
  if (url.pathname + url.search === location.pathname + location.search) return;
  if (await confirmLeave()) navigate(url.pathname + url.search);
});
// Back and Forward ask too; staying puts the address back.
addEventListener('popstate', async () => {
  if (await confirmLeave()) render();
  else history.pushState({}, '', shown);
});
setDiscard(() => render());
addEventListener('beforeunload', event => { if (dirtyCheck?.() || unsaved()) { event.preventDefault(); event.returnValue = ''; } });

// ── Sign-in ──────────────────────────────────────────────────────────────────
function signIn() {
  const error = h('p', { class: 'ws-signin-error', role: 'alert' });
  const password = h('input', { type: 'password', name: 'password', autocomplete: 'current-password', required: true, 'aria-label': 'Password' });
  const form = h('form', { class: 'ws-signin' },
    h('img', { src: '/static/brand/wy-tile-512.png', alt: '', class: 'ws-signin-logo' }),
    h('h1', {}, 'Sign in'), h('p', { class: 'ws-sub' }, 'Your workspace: today, journal, health, money and the site.'),
    password, h('button', { class: 'btn primary', type: 'submit' }, 'Sign in'), error,
    h('a', { href: '/', class: 'ws-signin-back' }, '← Back to the site'));
  form.onsubmit = async event => {
    event.preventDefault();
    error.textContent = '';
    try {
      await api('/api/admin/login', { method: 'POST', body: { username: 'admin', password: password.value }, quiet: true });
      // Continue to the page that was asked for (a bookmark, or where the session expired).
      const next = new URLSearchParams(location.search).get('next') || location.pathname + location.search;
      location.href = next.startsWith('/app') && !next.includes('signin=') ? next : '/app';
    } catch (e) {
      error.textContent = e.status === 401 ? 'That password is not right.' : e.message;
      password.select();
    }
  };
  document.body.replaceChildren(h('main', { class: 'ws-signin-page' }, form));
  password.focus();
}

async function signOut() {
  await api('/api/admin/logout', { method: 'POST', quiet: true }).catch(() => {});
  location.href = '/';
}

// ── Start ────────────────────────────────────────────────────────────────────
if (!window.WORKSPACE?.signedIn) {
  signIn();
} else {
  document.getElementById('ws-signout').onclick = signOut;
  document.getElementById('ws-menu').onclick = () => {
    const open = document.body.classList.toggle('nav-open');
    document.getElementById('ws-menu').setAttribute('aria-expanded', String(open));
  };
  document.getElementById('ws-scrim').onclick = () => document.body.classList.remove('nav-open');
  render();
}

export { toast };
