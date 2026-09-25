// Settings: site switches, the journal's balance accounts, retired trackers, diagnostics.
import { h, api, fmt, card, pageHead, toast, run, editable, saveAll, dialog, field, values, confirmDelete, empty } from '../lib.js';

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
    apiAccess(),
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

// ── API access: tokens for scheduled jobs (e.g. a Claude task pushing balances and transactions) ─
function apiAccess() {
  const list = h('div', {}, h('p', { class: 'ws-note' }, 'Loading…'));
  const draw = async () => {
    const tokens = await api('/api/v1/api-tokens');
    list.replaceChildren(tokens.length ? h('ul', { class: 'ws-list' }, tokens.map(t => h('li', { class: 'ws-row', style: t.revoked_at ? { opacity: .55 } : null },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, t.name, ' ', h('code', { class: 'ws-note' }, t.token_prefix + '…')),
        h('div', { class: 'ws-row-meta' }, [t.scopes.join(', '), 'made ' + new Date(t.created_at).toLocaleDateString(),
          t.last_used_at ? `last used ${new Date(t.last_used_at).toLocaleString()} (${t.use_count}×)` : 'never used',
          t.revoked_at ? 'revoked' : null].filter(Boolean).join(' · '))),
      t.revoked_at ? null : h('button', { class: 'btn small danger', onclick: async () => {
        if (!(await confirmDelete(`access for “${t.name}” (revoke the token)`))) return;
        await run(null, async () => { await api('/api/v1/api-tokens/' + t.token_id, { method: 'DELETE' }); draw(); });
      } }, 'Revoke')))) : empty('No tokens yet.'));
  };
  const make = async () => {
    const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
      field('What will use it', 'name', { placeholder: 'e.g. Claude daily finance', wide: true }),
      field('Can push balances and transactions', 'write', { kind: 'checkbox', value: true }),
      field('Can read what’s loaded (status)', 'read', { kind: 'checkbox', value: true }));
    if (!(await dialog('New API token', form, [['Cancel', null], ['Make token', true]]))) return;
    const v = values(form);
    const scopes = [v.write && 'finance:write', v.read && 'finance:read'].filter(Boolean);
    const made = await run(null, () => api('/api/v1/api-tokens', { method: 'POST', body: { name: v.name, scopes } }));
    if (!made) return;
    const origin = location.origin;
    const example = `curl -X POST ${origin}/api/ingest/v1/finance \\\n  -H "X-VirtuWill-Token: ${made.token}" -H "Content-Type: application/json" \\\n  -d '{"source": "claude-daily", "balances": [{"account_mask": "1234", "as_of": "2026-09-25", "balance": 1234.56}]}'`;
    const copy = text => navigator.clipboard?.writeText(text).then(() => toast('Copied'), () => toast('Copy it by hand', 'error'));
    await dialog('Copy this token now', h('div', {},
      h('p', { class: 'ws-note warn' }, 'It won’t be shown again. Store it where the job keeps secrets (for a Claude routine: its environment’s secrets).'),
      h('pre', { class: 'ws-token' }, made.token), h('button', { class: 'btn small', type: 'button', onclick: () => copy(made.token) }, 'Copy token'),
      h('p', { class: 'ws-note', style: { marginTop: '12px' } }, 'Try it:'), h('pre', { class: 'ws-token' }, example),
      h('p', { class: 'ws-note' }, 'The API describes itself at ', h('code', {}, origin + '/api/ingest/v1'), ' (send the token).')),
      [['Done', true]]);
    draw();
  };
  draw();
  return card(h('span', {}, 'API access', h('button', { class: 'btn small', onclick: make }, '+ New token')),
    h('p', { class: 'ws-note', style: { marginBottom: '8px' } },
      'Tokens let a scheduled job — such as a Claude task — push balances and transactions to Money without signing in. ',
      'Its loads appear in Money › Imports, labelled with the token’s name.'), list);
}
