/**
 * core.js — shared helpers every module may use while it loads
 *
 * Loaded before any other module, so registrations made at load time
 * (e.g. admin.js's unsaved-changes guard) always find VW.Dirty.
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
