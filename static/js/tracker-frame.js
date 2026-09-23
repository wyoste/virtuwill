/* Runs first inside the opaque-origin tracker frame. No cookies or network. */
(() => {
  'use strict';
  const boot = window.TRACKER_BOOT;
  let sequence = 0;
  const comparable = state => {
    const copy = { ...state };
    // Opening Finance refreshes this timestamp without changing actual records.
    if (boot.kind === 'finance') delete copy.updatedAt;
    return JSON.stringify(copy);
  };
  let previous = boot.state ? comparable(boot.state) : null;
  const memory = new Map();
  if (boot.state) memory.set(boot.key, JSON.stringify(boot.state));
  const send = payload => parent.postMessage({ channel: 'vw-tracker', kind: boot.kind, ...payload }, boot.origin);
  window.trackerExportCleanup = doc => {
    doc.querySelector('#vw-tracker-bridge')?.remove();
    for (const script of doc.querySelectorAll('script:not([type="application/json"])')) {
      script.textContent = script.textContent.replaceAll('await trackerConfirm(', 'confirm(').replaceAll('Saving to server…', 'Saved on this browser');
    }
    for (const p of doc.querySelectorAll('p')) {
      if (p.textContent === 'Changes save to your private server and are available across devices. They do not modify the original workbook.') p.textContent = 'Changes save in this browser when storage is available. They do not modify the original workbook or sync across devices.';
    }
  };
  window.trackerConfirm = message => new Promise(resolve => {
    const dialog = document.createElement('dialog');
    const text = document.createElement('p'); text.textContent = message;
    const cancel = document.createElement('button'); cancel.textContent = 'Cancel';
    const accept = document.createElement('button'); accept.textContent = 'Restore backup';
    const finish = answer => { dialog.close(); dialog.remove(); resolve(answer); };
    cancel.onclick = () => finish(false);
    accept.onclick = () => finish(true);
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    dialog.setAttribute('aria-label', 'Restore backup confirmation');
    dialog.append(text, cancel, accept); document.body.append(dialog); dialog.showModal(); cancel.focus();
  });
  Object.defineProperty(window, 'localStorage', { value: {
    getItem: key => memory.get(key) ?? null,
    setItem(key, value) {
      const text = String(value);
      const state = key === boot.key ? JSON.parse(text) : null;
      memory.set(key, text);
      if (state) {
        const next = comparable(state);
        if (next !== previous) {
          previous = next;
          send({ type: 'save', sequence: ++sequence, state });
        } else {
          queueMicrotask(() => { const label = document.getElementById('status'); if (label && !sequence) label.textContent = 'Saved to server'; });
        }
      }
    },
    removeItem: key => memory.delete(key),
    clear: () => memory.clear(),
    key: index => [...memory.keys()][index] ?? null,
    get length() { return memory.size; },
  }, configurable: false });
  send({ type: 'ready', revision: boot.revision });
  addEventListener('message', event => {
    if (event.source !== parent || event.origin !== boot.origin || event.data?.channel !== 'vw-tracker') return;
    const message = event.data;
    const status = document.getElementById('status');
    if (message.type === 'saved' && message.sequence === sequence && status) status.textContent = 'Saved to server';
    if (message.type === 'error' && status) status.textContent = 'Not saved — export a backup';
  });
  addEventListener('DOMContentLoaded', () => {
    // Finance only saves after editing; persist its fully expanded defaults now.
    // Health already saves during startup, so do not queue a duplicate write.
    if (!sequence && typeof save === 'function') save();
  });
})();
