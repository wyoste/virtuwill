// Shared helpers for the workspace: DOM building, the API client, formatting.

// h('div', {class: 'x', onclick: fn}, child, [children], 'text') → element
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (key === 'value') el.value = value;
    else if (key === 'checked' || key === 'selected' || key === 'disabled') el[key] = !!value;
    else if (key === 'html') el.innerHTML = value;          // only for trusted, static markup
    else el.setAttribute(key, value === true ? '' : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

// ── API client ───────────────────────────────────────────────────────────────
// Every write reports its outcome in the status line; failures are never silent.
export async function api(path, { method = 'GET', body, form, quiet = false } = {}) {
  const options = { method, cache: 'no-store', headers: {} };
  if (form) options.body = form;
  else if (body !== undefined) { options.body = JSON.stringify(body); options.headers['Content-Type'] = 'application/json'; }
  if (method !== 'GET' && !quiet) status('Saving…', 'pending');
  let response, data;
  try {
    response = await fetch(path, options);
    data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  } catch (error) {
    status('Offline — not saved', 'error');
    throw Object.assign(new Error('Could not reach the server. Check your connection and try again.'), { status: 0 });
  }
  if (!response.ok) {
    const message = data?.error || `Request failed (${response.status})`;
    if (response.status === 401) { location.href = '/app?signin=1&next=' + encodeURIComponent(location.pathname + location.search); }
    if (method !== 'GET' && !quiet) status('Not saved', 'error');
    throw Object.assign(new Error(message), { status: response.status, data });
  }
  if (method !== 'GET' && !quiet) status('Saved', 'ok');
  return data;
}

let statusTimer;
export function status(text, kind) {
  const el = document.getElementById('ws-status');
  if (!el) return;
  el.textContent = text;
  el.dataset.kind = kind;
  clearTimeout(statusTimer);
  if (kind === 'ok') statusTimer = setTimeout(() => { el.textContent = ''; el.dataset.kind = ''; }, 2500);
}

export function toast(message, kind = 'ok') {
  const host = document.getElementById('ws-toasts');
  const el = h('div', { class: 'ws-toast ' + kind, role: kind === 'error' ? 'alert' : 'status' }, message);
  host.append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 3500);
}

// Run an async action from a button: disables it, reports errors as a toast.
export async function run(button, action) {
  if (button) button.disabled = true;
  try { return await action(); }
  catch (error) { toast(error.message, 'error'); return undefined; }
  finally { if (button) button.disabled = false; }
}

// ── Formatting ───────────────────────────────────────────────────────────────
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
export const fmt = {
  money: n => (n === null || n === undefined || n === '') ? '—' : usd.format(Number(n)),
  num: (n, digits = 0) => (n === null || n === undefined || Number.isNaN(Number(n))) ? '—'
    : Number(n).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 }),
  day: iso => iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : '—',
  longDay: iso => iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }) : '—',
  month: iso => iso ? new Date(iso.slice(0, 7) + '-15T12:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' }) : '—',
  time: iso => iso ? new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : '',
  pct: n => (n === null || n === undefined) ? '—' : Math.round(Number(n) * 100) + '%',
};

export function isoToday() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
export function addDays(iso, n) {
  const d = new Date(iso + 'T12:00:00');
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

// ── Building blocks ──────────────────────────────────────────────────────────
export function pageHead(title, sub, ...actions) {
  return h('header', { class: 'ws-head' },
    h('div', {}, h('h1', {}, title), sub ? h('p', { class: 'ws-sub' }, sub) : null),
    actions.length ? h('div', { class: 'ws-head-actions' }, actions) : null);
}

export function card(title, ...body) {
  return h('section', { class: 'ws-card' }, title ? h('h2', { class: 'ws-card-title' }, title) : null, body);
}

export function stat(label, value, sub, tone) {
  return h('div', { class: 'ws-stat' + (tone ? ' ' + tone : '') },
    h('div', { class: 'ws-stat-value' }, value), h('div', { class: 'ws-stat-label' }, label),
    sub ? h('div', { class: 'ws-stat-sub' }, sub) : null);
}

export function empty(text, action) {
  return h('div', { class: 'ws-empty' }, h('p', {}, text), action || null);
}

export function tabs(items, active) {
  return h('nav', { class: 'ws-tabs', 'aria-label': 'Sections' },
    items.map(([href, label]) => h('a', { href, class: href === active ? 'on' : null, 'aria-current': href === active ? 'page' : null }, label)));
}

// A labelled form control. kind: text | number | date | time | select | textarea | checkbox
export function field(label, name, { kind = 'text', value = '', options, step, min, max, placeholder, required, wide } = {}) {
  let control;
  const id = 'f-' + name + '-' + Math.random().toString(36).slice(2, 7);
  if (kind === 'select') {
    control = h('select', { id, name }, options.map(o => {
      const [v, t] = Array.isArray(o) ? o : [o, o];
      return h('option', { value: v, selected: String(v) === String(value) }, t);
    }));
  } else if (kind === 'textarea') {
    control = h('textarea', { id, name, rows: 4, placeholder }, value ?? '');
  } else if (kind === 'checkbox') {
    control = h('input', { id, name, type: 'checkbox', checked: !!value });
    return h('label', { class: 'ws-check', for: id }, control, h('span', {}, label));
  } else {
    control = h('input', { id, name, type: kind, value: value ?? '', step, min, max, placeholder, required });
  }
  return h('label', { class: 'ws-field' + (wide ? ' wide' : ''), for: id }, h('span', {}, label), control);
}

// Read a form's named controls into an object; numbers stay text for the server to validate.
export function values(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else out[el.name] = el.value.trim() === '' ? null : el.value.trim();
  }
  return out;
}

// A modal dialog; resolves with the button value, or null when dismissed.
export function dialog(title, body, buttons = [['Cancel', null], ['OK', true]]) {
  return new Promise(resolve => {
    const box = h('dialog', { class: 'ws-dialog' },
      h('h2', {}, title), h('div', { class: 'ws-dialog-body' }, body),
      h('div', { class: 'ws-dialog-actions' }, buttons.map(([label, value], i) =>
        h('button', { class: i === buttons.length - 1 ? 'btn primary' : 'btn', onclick: () => { box.close(); resolve(value); } }, label))));
    box.addEventListener('cancel', () => resolve(null));
    box.addEventListener('close', () => setTimeout(() => box.remove(), 0));
    document.body.append(box);
    box.showModal();
  });
}

export async function confirmDelete(what) {
  return (await dialog('Delete ' + what + '?', h('p', {}, 'This cannot be undone.'), [['Cancel', false], ['Delete', true]])) === true;
}

export function link(href, ...children) {
  return h('a', { href }, children);
}

// Journal text is rich text. Keep simple formatting only: no scripts, styles,
// event handlers or non-http links, whatever was pasted or transcribed.
const ALLOWED = new Set(['P', 'DIV', 'BR', 'B', 'STRONG', 'I', 'EM', 'U', 'UL', 'OL', 'LI', 'A', 'H2', 'H3', 'BLOCKQUOTE', 'SPAN']);
export function cleanHTML(html) {
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  const walk = node => {
    for (const child of [...node.childNodes]) {
      if (child.nodeType === Node.ELEMENT_NODE) {
        if (!ALLOWED.has(child.tagName)) {
          if (['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE', 'SVG', 'MATH'].includes(child.tagName)) child.remove();
          else { walk(child); child.replaceWith(...child.childNodes); }
          continue;
        }
        for (const attr of [...child.attributes]) {
          const keep = child.tagName === 'A' && attr.name === 'href' && /^https?:\/\//i.test(attr.value);
          if (!keep) child.removeAttribute(attr.name);
        }
        if (child.tagName === 'A') { child.setAttribute('rel', 'noopener noreferrer'); child.setAttribute('target', '_blank'); }
        walk(child);
      } else if (child.nodeType !== Node.TEXT_NODE) {
        child.remove();
      }
    }
  };
  walk(doc.body);
  return doc.body.innerHTML;
}

// Plain text as safe HTML paragraphs (for transcribed or typed text).
export function textToHTML(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML.replace(/\n/g, '<br>');
}
