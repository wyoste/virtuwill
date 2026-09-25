// Settings: site switches, the journal's balance accounts, retired trackers, diagnostics.
import { h, api, fmt, card, pageHead, toast, run, editable, saveAll } from '../lib.js';

export async function render(view) {
  const [settings, template] = await Promise.all([api('/api/settings'), api('/api/accounts-template')]);
  // A switch: saved as soon as it's flipped.
  const chat = h('input', { type: 'checkbox', checked: settings['site.chat_enabled'] === true, id: 'set-chat', 'data-untracked': '' });
  chat.onchange = () => run(chat, async () => {
    await api('/api/settings', { method: 'PUT', body: { 'site.chat_enabled': chat.checked } });
    toast('Saved. Visitors see the change on their next page load.');
  });

  const accounts = template.map(a => ({ institution: a.institution, name: a.name }));
  const rows = h('div', { class: 'ws-form stack' });
  const draw = () => rows.replaceChildren(...accounts.map((a, i) => h('div', { class: 'ws-form' },
    h('input', { class: 'ws-input', style: { flex: '1 1 160px' }, value: a.institution, placeholder: 'Institution', 'aria-label': 'Institution', oninput: e => { a.institution = e.target.value; } }),
    h('input', { class: 'ws-input', style: { flex: '1 1 160px' }, value: a.name, placeholder: 'Account', 'aria-label': 'Account', oninput: e => { a.name = e.target.value; } }),
    h('button', { class: 'btn small danger', 'aria-label': 'Remove', onclick: () => { accounts.splice(i, 1); draw(); list.touch(); } }, '✕'))),
    h('div', { class: 'ws-form' }, h('button', { class: 'btn small', onclick: () => { accounts.push({ institution: '', name: '' }); draw(); } }, '+ Account'),
      h('button', { class: 'btn small primary', onclick: e => saveAll(e.currentTarget) }, 'Save')));
  draw();
  const list = editable(rows, () => api('/api/accounts-template', { method: 'POST', body: accounts.filter(a => a.institution || a.name) }));

  const diag = h('div', {}, h('p', { class: 'ws-note' }, 'Loading…'));
  view.append(
    pageHead('Settings', 'Site switches, the journal check-in list, and what is deployed.'),
    h('div', { class: 'ws-grid two' },
      card('Site', h('label', { class: 'ws-check', for: 'set-chat' }, chat,
        h('span', {}, h('strong', {}, 'Show the “Chat with Will” button'), h('br'),
          h('small', {}, 'On every public page. It needs the chat server to be reachable from visitors’ browsers.')))),
      card('Journal balance check-in', h('p', { class: 'ws-note', style: { marginBottom: '8px' } },
        'Accounts a new journal entry starts with, in this order.'), rows)),
    card('Retired trackers', h('p', { class: 'ws-note' },
      'The Health tracker is retired: Health in the workspace owns those records now. Its document and last state are kept in the database. ',
      'The Finance tracker is still the editor for Money until statement and receipt imports arrive; open it under ', h('a', { href: '/app/money/editor' }, 'Money › Finance tracker'), '.')),
    card(h('span', {}, 'Diagnostics', h('button', { class: 'btn small', onclick: () => loadDiagnostics(diag) }, 'Refresh')), diag));
  loadDiagnostics(diag);
}

async function loadDiagnostics(host) {
  let d;
  try { d = await api('/api/admin/diagnostics'); }
  catch (e) { host.replaceChildren(h('p', { class: 'bad' }, 'Could not load diagnostics: ' + e.message)); return; }
  const ok = v => h('span', { class: v ? 'ok' : 'bad' }, v ? '✓ ' : '✗ ');
  const db = d.database || {};
  const rows = [
    ['Deployed commit', d.commit || 'unknown'],
    ['Database', db.reachable ? [ok(true), `${db.name} · ${db.timezone}`] : [ok(false), db.configured ? 'not reachable: ' + (db.error || '') : 'not attached to the app']],
  ];
  if (d.schema) rows.push(['Schema', [d.schema.version || 'none', d.schema.pending.length ? ' · pending: ' + d.schema.pending.join(', ') : '',
    d.schema.edited.length ? h('span', { class: 'bad' }, ' · edited after applying: ' + d.schema.edited.join(', ')) : '']]);
  for (const kind of ['finance', 'health']) {
    const t = (d.trackers || []).find(x => x.kind === kind);
    rows.push([kind[0].toUpperCase() + kind.slice(1) + ' tracker', t ? [ok(true), `installed · revision ${t.revision} · saved ${new Date(t.savedAt).toLocaleString()}`] : [ok(false), 'not installed']]);
  }
  for (const s of d.syncs || []) rows.push(['Sync · ' + s.source, [ok(s.ok), new Date(s.syncedAt).toLocaleString(), s.error ? ' · ' + s.error : '']]);
  if (d.media) rows.push(['Files', `${d.media.files} registered · ${d.media.stored_in_database} kept in the database`]);
  host.replaceChildren(h('table', { class: 'ws-diag' }, h('tbody', {}, rows.map(([k, v]) => h('tr', {}, h('th', {}, k), h('td', {}, v))))));
}
