/* Parent-side admin integration. Imported code never receives the CSRF token. */
'use strict';
window.VW = window.VW || {};
window.VW.Trackers = (() => {
  const names = { finance: 'Finance', health: 'Health' };
  const sessions = new Map();
  const root = kind => document.getElementById('adm-tracker-' + kind);
  const endpoint = kind => '/api/admin/trackers/' + kind;
  const dirty = () => [...sessions.values()].some(s => s.pending || s.saving || s.failed);

  function status(kind, message, error = false) {
    const el = root(kind)?.querySelector('.tracker-status');
    if (el) { el.textContent = message; el.classList.toggle('tracker-error', error); }
  }

  async function responseJSON(response) {
    const value = await response.json();
    if (!response.ok) throw Object.assign(new Error(value.error || 'Could not save tracker.'), { status: response.status });
    return value;
  }

  function shell(kind) {
    const host = root(kind);
    host.replaceChildren();
    const bar = document.createElement('div');
    bar.className = 'tracker-toolbar';
    const label = document.createElement('span');
    label.className = 'tracker-status';
    label.setAttribute('role', 'status');
    label.setAttribute('aria-live', 'polite');
    label.textContent = 'Loading private tracker…';
    const reload = document.createElement('button');
    reload.className = 'adm-btn-secondary';
    reload.textContent = 'Reload';
    reload.onclick = () => {
      const s = sessions.get(kind);
      if ((s?.pending || s?.saving || s?.failed) && !confirm('Unsaved changes will be lost. Export a backup inside the tracker first. Reload anyway?')) return;
      sessions.delete(kind);
      open(kind);
    };
    bar.append(label, reload);
    host.append(bar);
    return host;
  }

  async function open(kind) {
    if (!names[kind] || !VW.Auth?.isAdmin?.()) return;
    if (sessions.has(kind)) return;
    const host = shell(kind);
    const s = { kind, frame: null, revision: null, pending: null, saving: false, failed: false, csrf: '' };
    sessions.set(kind, s);
    try {
      const meta = await responseJSON(await fetch(endpoint(kind), { cache: 'no-store' }));
      if (sessions.get(kind) !== s) return;
      s.csrf = meta.csrf;
      s.storage = meta.storage;
      if (!meta.configured) {
        status(kind, 'Import your original ' + names[kind] + ' HTML to get started.');
        const setup = document.createElement('div');
        setup.className = 'tracker-setup';
        const text = document.createElement('p');
        text.textContent = 'Choose Yoste-' + names[kind] + '.html. Your file and records are stored privately on this server. If you added records in the standalone app, export its latest JSON backup and restore it here after import.';
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.html,text/html';
        input.setAttribute('aria-label', 'Import ' + names[kind] + ' tracker HTML');
        input.onchange = async () => {
          const file = input.files[0];
          if (!file) return;
          if (file.size > 5 * 1024 * 1024) { status(kind, 'File exceeds the 5 MB limit.', true); return; }
          input.disabled = true;
          status(kind, 'Importing privately…');
          try {
            const data = new FormData(); data.append('file', file);
            await responseJSON(await fetch(endpoint(kind) + '/install', { method: 'POST', headers: { 'X-Tracker-CSRF': s.csrf }, body: data }));
            sessions.delete(kind); open(kind);
          } catch (error) { status(kind, error.message, true); input.disabled = false; }
        };
        setup.append(text, input); host.append(setup);
        return;
      }
      const frame = document.createElement('iframe');
      frame.className = 'tracker-frame';
      frame.title = names[kind] + ' goal tracker';
      frame.setAttribute('sandbox', 'allow-scripts allow-downloads allow-modals allow-forms');
      frame.referrerPolicy = 'no-referrer';
      s.frame = frame;
      frame.src = '/admin/trackers/' + kind + '/frame?origin=' + encodeURIComponent(location.origin);
      frame.onload = () => { if (s.revision === null) status(kind, 'Tracker could not open. Reload or sign in again.', true); };
      host.append(frame);
    } catch (error) {
      status(kind, error.message, true);
      if (error.status === 401) clear();
    }
  }

  async function flush(s) {
    if (s.saving || s.failed || !s.pending || s.revision === null) return;
    s.saving = true;
    const job = s.pending;
    s.pending = null;
    status(s.kind, 'Saving to server…');
    try {
      const result = await responseJSON(await fetch(endpoint(s.kind), {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Tracker-CSRF': s.csrf },
        body: JSON.stringify({ state: job.state, revision: s.revision }),
      }));
      if (sessions.get(s.kind) !== s) return;
      s.revision = result.revision;
      // An opaque-origin frame requires '*'; source identity is checked on receive.
      s.frame.contentWindow.postMessage({ channel: 'vw-tracker', type: 'saved', sequence: job.sequence }, '*');
      // Saving the record and updating the dashboards are separate outcomes.
      if (result.synced === false) {
        status(s.kind, 'Record saved · dashboard update failed: ' + (result.syncError || 'unknown error') + '. Your data is safe; the next save retries.', true);
      } else {
        status(s.kind, 'Saved to server · dashboards updated');
        if (s.kind === 'health') window.VW?.HealthDashboard?.load?.();
      }
    } catch (error) {
      s.failed = true;
      s.pending = s.pending || job;
      status(s.kind, error.message + ' Export a backup before reloading.', true);
      s.frame?.contentWindow.postMessage({ channel: 'vw-tracker', type: 'error' }, '*');
      if (error.status === 401) clear();
    } finally {
      s.saving = false;
      if (sessions.get(s.kind) === s && s.pending && !s.failed) flush(s);
    }
  }

  addEventListener('message', event => {
    const msg = event.data;
    if (!msg || msg.channel !== 'vw-tracker' || event.origin !== 'null') return;
    const s = sessions.get(msg.kind);
    if (!s?.frame || event.source !== s.frame.contentWindow) return;
    if (msg.type === 'ready' && s.revision === null && Number.isInteger(msg.revision)) {
      s.revision = msg.revision;
      status(s.kind, s.storage === 'lakebase' ? 'Connected · changes save to Lakebase automatically' : 'Connected · changes save automatically');
    }
    if (msg.type === 'save' && Number.isInteger(msg.sequence) && msg.state?.version === 1) {
      s.pending = { sequence: msg.sequence, state: msg.state };
      flush(s);
    }
  });

  function clear() {
    sessions.clear();
    for (const kind of Object.keys(names)) root(kind)?.replaceChildren();
  }
  addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  // Clear sensitive frames if another tab signs out or the session expires.
  async function checkAuth() {
    if (!sessions.size || document.hidden) return;
    try {
      const r = await fetch('/api/admin/status', { cache: 'no-store' });
      if (!(await r.json()).logged_in) { clear(); VW.Auth.setAdmin(false); window.go('home'); }
    } catch { /* Keep unsaved edits available for export while offline. */ }
  }
  setInterval(checkAuth, 30000);
  document.addEventListener('visibilitychange', checkAuth);
  return { open, clear, isDirty: dirty };
})();
