/**
 * core.js — shared helpers every module may use while it loads
 *
 * Loaded before any other module, so every page script can rely on them.
 */
'use strict';
window.VW = window.VW || {};

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

// VW.h('a', {href: '/music', class: 'x'}, 'text', child) → element (text is never parsed as HTML)
window.VW.h = function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : value);
  }
  const add = list => list.forEach(c => {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) add(c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  });
  add(children);
  return el;
};

// Fetch JSON; throws with the server's message on failure.
window.VW.getJSON = async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || 'Could not load (' + r.status + ')'), { status: r.status });
  return data;
};
