/**
 * store.js — server sync for page data that used to live only in localStorage
 *
 * The server copy is the source of truth; localStorage stays as a cache so the
 * pages' existing synchronous reads keep working. When the server has nothing
 * yet and an admin opens the page, this browser's copy is uploaded once.
 */
'use strict';
window.VW = window.VW || {};

window.VW.Store = (() => {
  const NAMES = {
    vw_travel_pins:     'travel_pins',
    vw_travel_visited:  'travel_visited',
    vw_garden_note:     'garden_gallery_note',
    vw_garden_hero:     'garden_gallery_hero',
    vw_portfolio_state: 'portfolio_layout',
  };

  function _local(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }

  // Refresh the cached copy from the server. Resolves true if it changed.
  async function pull(key) {
    const name = NAMES[key];
    if (!name) return false;
    try {
      const r = await fetch('/api/data/' + name, { cache: 'no-store' });
      if (!r.ok) return false;
      const { data } = await r.json();
      if (typeof data !== 'string') {
        if (_local(key) !== null) push(key);
        return false;
      }
      if (data === _local(key)) return false;
      localStorage.setItem(key, data);
      return true;
    } catch { return false; }
  }

  // Save the cached copy to the server (admin only).
  async function push(key) {
    const name = NAMES[key];
    const value = _local(key);
    if (!name || value === null || !window.VW?.Auth?.isAdmin?.()) return;
    try {
      const r = await fetch('/api/data/' + name, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: value }),
      });
      if (!r.ok) console.warn('Could not save ' + name + ' to the server (' + r.status + ')');
    } catch (error) { console.warn('Could not save ' + name + ' to the server', error); }
  }

  return { pull, push };
})();
