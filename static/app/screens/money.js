// Money: Overview · Transactions · Receipts · Accounts · Budgets · Goals, plus
// the Finance tracker, which still owns and edits these records until the
// import screens replace it.
import { h, api, fmt, card, stat, pageHead, tabs, empty } from '../lib.js';
import { setDirty } from '../main.js';

const TABS = [['/app/money', 'Overview'], ['/app/money/transactions', 'Transactions'], ['/app/money/receipts', 'Receipts'],
              ['/app/money/accounts', 'Accounts & balances'], ['/app/money/budgets', 'Budgets & bills'],
              ['/app/money/goals', 'Goals & retirement'], ['/app/money/editor', 'Finance tracker']];

export async function render(view, ctx) {
  const tab = ctx.path.split('/')[3] || 'overview';
  view.append(pageHead('Money', {
    overview: 'Balances, this month’s budgets and goals', transactions: 'Bank activity with receipt lines and sources',
    receipts: 'Receipts and the bank charge each one explains', accounts: 'Every account and its balance over time',
    budgets: 'Budgets against spending, and recurring bills', goals: 'Savings goals, retirement and the pay plan',
    editor: 'Where these records are edited for now' }[tab]), tabs(TABS, ctx.path));
  if (tab !== 'editor') view.append(h('p', { class: 'ws-note' },
    'Read-only here: the Finance tracker still owns these records. Edit them in ', h('a', { href: '/app/money/editor' }, 'Finance tracker'), '.'));
  const screens = { overview, transactions, receipts, accounts, budgets, goals, editor };
  return (screens[tab] || overview)(view, ctx);
}

function amount(n, { flip = false } = {}) {
  // Money leaving is positive in the model; show money coming in with a + and a label color.
  const incoming = flip ? n > 0 : n < 0;
  return h('span', { class: 'ws-amount' + (incoming ? ' in' : '') }, (incoming ? '+' : '') + fmt.money(Math.abs(n)));
}

function bar(value, max, tone) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return h('div', { class: 'ws-bar' + (tone ? ' ' + tone : ''), role: 'img', 'aria-label': Math.round(pct) + '%' }, h('span', { style: { width: pct + '%' } }));
}

// ── Overview ─────────────────────────────────────────────────────────────────
async function overview(view) {
  const d = await api('/api/v1/money/overview');
  const assets = d.balances.filter(b => !b.is_liability).reduce((s, b) => s + (b.balance || 0), 0);
  const owed = d.balances.filter(b => b.is_liability).reduce((s, b) => s + (b.balance || 0), 0);
  const spent = d.budgets.reduce((s, b) => s + (b.actual || 0), 0);
  const budgeted = d.budgets.reduce((s, b) => s + (b.budget || 0), 0);
  view.append(
    h('div', { class: 'ws-stats' },
      stat('Assets', fmt.money(assets), `${d.balances.filter(b => !b.is_liability).length} accounts, latest known balances`),
      stat('Owed', fmt.money(owed), 'cards and loans'),
      stat(`Spent in ${fmt.month(d.month)}`, fmt.money(spent), `of ${fmt.money(budgeted)} budgeted`, spent > budgeted && budgeted ? 'bad' : null),
      stat('Bank data through', d.through ? fmt.day(d.through) : '—', 'the latest transaction loaded')),
    h('div', { class: 'ws-grid two' },
      card(`Budgets · ${fmt.month(d.month)}`, d.budgets.length ? h('ul', { class: 'ws-list' }, d.budgets.map(b => h('li', { class: 'ws-row', style: { display: 'block' } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '8px' } }, h('strong', {}, b.name),
          h('span', { class: 'ws-amount' }, `${fmt.money(b.actual || 0)} / ${fmt.money(b.budget)}`)),
        bar(b.actual || 0, b.budget || 0, b.over_budget ? 'over' : null)))) : empty('No budgets yet.')),
      card('Balances', h('ul', { class: 'ws-list' }, d.balances.map(b => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, b.name),
          h('div', { class: 'ws-row-meta' }, [b.account_type.replace('_', ' '), b.as_of ? 'as of ' + fmt.day(b.as_of) : null, b.balance_kind].filter(Boolean).join(' · '))),
        h('span', { class: 'ws-amount' }, fmt.money(b.balance))))))),
    h('div', { class: 'ws-grid two' },
      card('Savings goals', d.goals.length ? h('ul', { class: 'ws-list' }, d.goals.map(g => h('li', { class: 'ws-row', style: { display: 'block' } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '8px' } }, h('strong', {}, g.name),
          h('span', { class: 'ws-amount' }, `${fmt.money(g.balance)} / ${fmt.money(g.target)}`)),
        bar(g.balance || 0, g.target || 0, 'good'),
        h('div', { class: 'ws-row-meta' }, [g.due_on ? 'by ' + fmt.day(g.due_on) : null, g.contribution_per_check ? fmt.money(g.contribution_per_check) + ' per paycheck' : null].filter(Boolean).join(' · '))))) : empty('No savings goals.')),
      card(`Spending by category · ${fmt.month(d.month)}`, d.spendingByCategory.length ? h('table', { class: 'ws-table' },
        h('tbody', {}, d.spendingByCategory.map(s => h('tr', {}, h('td', {}, s.category), h('td', { class: 'ws-note' }, s.category_group || ''),
          h('td', { class: 'num' }, fmt.money(s.amount)))))) : empty('No spending this month.'))));
}

// ── Transactions ─────────────────────────────────────────────────────────────
async function transactions(view, { params, navigate }) {
  const months = await api('/api/v1/money/months');
  const month = params.get('month') || months[0] || '';
  const query = new URLSearchParams({ month, account: params.get('account') || '', category: params.get('category') || '', q: params.get('q') || '' });
  const d = await api('/api/v1/money/transactions?' + query);
  const filter = (name, options, value) => h('label', { class: 'ws-field' }, h('span', {}, name),
    h('select', { onchange: e => { query.set(name.toLowerCase(), e.target.value); navigate('/app/money/transactions?' + query); } },
      options.map(([v, t]) => h('option', { value: v, selected: v === value }, t))));
  const search = h('input', { type: 'search', value: params.get('q') || '', placeholder: 'Merchant or description' });
  search.onchange = () => { query.set('q', search.value); navigate('/app/money/transactions?' + query); };
  const out = d.transactions.filter(t => t.amount > 0 && t.kind === 'expense').reduce((s, t) => s + t.amount, 0);
  view.append(
    h('div', { class: 'ws-filters' },
      filter('Month', months.map(m => [m, fmt.month(m + '-01')]), month),
      filter('Account', [['', 'All accounts'], ...d.accounts.map(a => [a.account_id, a.name])], params.get('account') || ''),
      filter('Category', [['', 'All categories'], ...d.categories.map(c => [c, c])], params.get('category') || ''),
      h('label', { class: 'ws-field' }, h('span', {}, 'Search'), search)),
    h('p', { class: 'ws-note' }, `${d.transactions.length} transactions · ${fmt.money(out)} of spending. Open a row for its receipt lines and source files.`),
    card(null, d.transactions.length ? h('div', {}, d.transactions.map(t => h('details', { class: 'ws-expand' },
      h('summary', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, t.merchant || t.description_raw || '—'),
          h('div', { class: 'ws-row-meta' }, [fmt.day(t.posted_on), t.category, t.account_name, t.movement_type ? 'transfer' : null, t.is_pending ? 'pending' : null].filter(Boolean).join(' · '))),
        h('div', { class: 'ws-row-end' }, t.receipts.length ? h('span', { class: 'ws-chip good' }, 'receipt ✓') : null, amount(t.amount))),
      h('div', { class: 'ws-lines' },
        t.lines.length ? t.lines.map(l => h('div', {}, h('span', {}, `${l.item_name}${l.item_category ? ' · ' + l.item_category : ''}`), h('span', {}, fmt.money(l.amount))))
                       : h('div', {}, h('span', { class: 'ws-note' }, t.receipts.length ? 'Receipt matched; no line items.' : 'No receipt matched.')),
        t.sources.length ? h('div', { class: 'ws-note', style: { marginTop: '6px' } }, 'Sources: ' + t.sources.map(s => `${s.file}${s.row ? ' ' + s.row : ''}`).join(' · ')) : null,
        t.description_raw && t.description_raw !== t.merchant ? h('div', { class: 'ws-note' }, 'Bank text: ' + t.description_raw) : null))))
      : empty('No transactions match.')));
}

// ── Receipts ─────────────────────────────────────────────────────────────────
async function receipts(view, { params, navigate }) {
  const months = await api('/api/v1/money/months');
  const month = params.get('month') || months[0] || '';
  const rows = await api('/api/v1/money/receipts?month=' + month);
  const status = { matched: ['good', 'matched to bank'], partial: ['warn', 'partly matched'], unmatched: ['bad', 'no bank match'] };
  view.append(
    h('div', { class: 'ws-filters' }, h('label', { class: 'ws-field' }, h('span', {}, 'Month'),
      h('select', { onchange: e => navigate('/app/money/receipts?month=' + e.target.value) },
        months.map(m => h('option', { value: m, selected: m === month }, fmt.month(m + '-01')))))),
    card(null, rows.length ? h('div', {}, rows.map(r => h('details', { class: 'ws-expand' },
      h('summary', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, r.merchant || 'Receipt'),
          h('div', { class: 'ws-row-meta' }, [fmt.day(r.purchased_on), r.store_location, `${r.items.length} lines`, r.savings ? `saved ${fmt.money(r.savings)}` : null].filter(Boolean).join(' · '))),
        h('div', { class: 'ws-row-end' }, h('span', { class: 'ws-chip ' + (status[r.match_status]?.[0] || '') }, status[r.match_status]?.[1] || r.match_status || '—'),
          h('span', { class: 'ws-amount' }, fmt.money(r.total)))),
      h('div', { class: 'ws-lines' }, r.items.map(i => h('div', {},
        h('span', {}, `${i.item_name}${i.quantity && i.quantity !== 1 ? ' × ' + i.quantity : ''}${i.item_category ? ' · ' + i.item_category : ''}${i.planned === false ? ' · unplanned' : ''}`),
        h('span', {}, fmt.money(i.amount) + (i.discount ? ` (−${fmt.money(i.discount)})` : ''))))))))
      : empty('No receipts this month.')));
}

// ── Accounts ─────────────────────────────────────────────────────────────────
async function accounts(view) {
  const d = await api('/api/v1/money/accounts');
  view.append(card(null, h('div', { class: 'ws-table-wrap' }, h('table', { class: 'ws-table' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Account'), h('th', {}, 'Type'), h('th', {}, 'As of'), h('th', { class: 'num' }, 'Balance'), h('th', {}, 'History'))),
    h('tbody', {}, d.accounts.map(a => h('tr', { style: a.is_active ? null : { opacity: .55 } },
      h('td', {}, h('strong', {}, a.name), h('div', { class: 'ws-note' }, [a.institution, a.mask ? '••' + a.mask : null].filter(Boolean).join(' '))),
      h('td', {}, a.account_type.replace('_', ' ') + (a.is_liability ? ' (owed)' : '')),
      h('td', {}, a.as_of ? fmt.day(a.as_of) : '—'),
      h('td', { class: 'num' }, fmt.money(a.balance)),
      h('td', {}, a.history.length > 1 ? spark(a.history.map(p => p.balance)) : h('span', { class: 'ws-note' }, `${a.history.length} reading${a.history.length === 1 ? '' : 's'}`)))))))),
    d.statements.length ? card('Statements reconciled', h('table', { class: 'ws-table' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Statement'), h('th', {}, 'Period'), h('th', { class: 'num' }, 'Closing'), h('th', { class: 'num' }, 'Difference'))),
      h('tbody', {}, d.statements.map(s => h('tr', {}, h('td', {}, s.original_filename || s.statement_id),
        h('td', {}, `${fmt.day(s.period_start)} – ${fmt.day(s.period_end)}`), h('td', { class: 'num' }, fmt.money(s.closing_balance)),
        h('td', { class: 'num' }, s.difference ? h('span', { class: 'bad' }, fmt.money(s.difference)) : '✓')))))) : null);
}

function spark(values) {
  const W = 120, H = 28, min = Math.min(...values), max = Math.max(...values);
  const pts = values.map((v, i) => `${(i * W) / (values.length - 1)},${H - 3 - ((v - min) * (H - 6)) / (max - min || 1)}`).join(' ');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', W); svg.setAttribute('height', H);
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = `<polyline fill="none" stroke="#109ACC" stroke-width="1.8" points="${pts}"/>`;
  return svg;
}

// ── Budgets ──────────────────────────────────────────────────────────────────
async function budgets(view, { params, navigate }) {
  const months = await api('/api/v1/money/months');
  const d = await api('/api/v1/money/budgets' + (params.get('month') ? '?month=' + params.get('month') : ''));
  const month = d.month.slice(0, 7);
  view.append(
    h('div', { class: 'ws-filters' }, h('label', { class: 'ws-field' }, h('span', {}, 'Month'),
      h('select', { onchange: e => navigate('/app/money/budgets?month=' + e.target.value) },
        months.map(m => h('option', { value: m, selected: m === month }, fmt.month(m + '-01')))))),
    h('div', { class: 'ws-grid two' },
      card('Budgets', h('table', { class: 'ws-table' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Budget'), h('th', { class: 'num' }, 'Monthly'), h('th', { class: 'num' }, 'Spent'), h('th', { class: 'num' }, 'Left'))),
        h('tbody', {}, d.budgets.map(b => h('tr', {}, h('td', {}, b.name, b.note ? h('div', { class: 'ws-note' }, b.note) : null),
          h('td', { class: 'num' }, fmt.money(b.monthly_amount)), h('td', { class: 'num' }, fmt.money(b.actual ?? 0)),
          h('td', { class: 'num' }, h('span', { class: b.over_budget ? 'bad' : '' }, fmt.money(b.remaining ?? b.monthly_amount)))))))),
      card('Recurring bills', h('ul', { class: 'ws-list' }, d.bills.map(b => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, b.name),
          h('div', { class: 'ws-row-meta' }, [b.frequency, b.due_day ? 'due day ' + b.due_day : null, b.category, b.is_current ? null : 'ended'].filter(Boolean).join(' · '))),
        h('span', { class: 'ws-amount' }, fmt.money(b.monthly_amount) + '/mo')))))),
    card('Spending by category', h('table', { class: 'ws-table' }, h('tbody', {}, d.spending.map(s => h('tr', {},
      h('td', {}, s.category), h('td', { class: 'ws-note' }, `${s.transactions} transactions`), h('td', { class: 'num' }, fmt.money(s.amount))))))));
}

// ── Goals & retirement ───────────────────────────────────────────────────────
async function goals(view) {
  const d = await api('/api/v1/money/goals');
  const r = d.retirement || {};
  view.append(
    h('div', { class: 'ws-stats' },
      stat('Retirement balance', fmt.money(r.total_balance), r.balances_as_of ? 'as of ' + fmt.day(r.balances_as_of) : ''),
      stat('401(k) room left this year', fmt.money(r.deferral_room), `limit ${fmt.money(r.deferral_limit)} · projected ${fmt.money(r.projected_remaining_deferrals)} more`),
      stat('IRA', fmt.money(r.ira_actual), `of ${fmt.money(r.ira_limit)} · ${fmt.money(r.ira_per_check)} per check`)),
    h('div', { class: 'ws-grid two' },
      card('Savings goals', d.goals.length ? h('ul', { class: 'ws-list' }, d.goals.map(g => h('li', { class: 'ws-row', style: { display: 'block' } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between' } }, h('strong', {}, g.name), h('span', { class: 'ws-amount' }, fmt.pct(g.pct_of_target))),
        bar(g.balance || 0, g.target || 0, 'good'),
        h('div', { class: 'ws-row-meta' }, `${fmt.money(g.balance)} of ${fmt.money(g.target)}${g.due_on ? ' by ' + fmt.day(g.due_on) : ''}`)))) : empty('No savings goals.')),
      card('Retirement accounts', h('ul', { class: 'ws-list' }, d.retirementAccounts.map(a => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, a.name), h('div', { class: 'ws-row-meta' }, a.as_of ? 'as of ' + fmt.day(a.as_of) : '')),
        h('span', { class: 'ws-amount' }, fmt.money(a.balance))))))),
    card('Pay plan', h('div', { class: 'ws-grid' },
      h('div', {}, h('div', { class: 'ws-note', style: { fontWeight: 600 } }, 'Each paycheck goes to'),
        h('ul', { class: 'ws-list' }, d.deposits.map(x => h('li', { class: 'ws-row' }, h('div', { class: 'ws-row-main' }, x.account_id || x.name || 'Deposit'),
          h('span', { class: 'ws-amount' }, fmt.money(x.amount)))))),
      h('div', {}, h('div', { class: 'ws-note', style: { fontWeight: 600 } }, 'Set aside each paycheck'),
        h('ul', { class: 'ws-list' }, d.allocations.map(x => h('li', { class: 'ws-row' },
          h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, x.name), h('div', { class: 'ws-row-meta' }, x.cadence || '')),
          h('span', { class: 'ws-amount' }, fmt.money(x.amount)))))))));
}

// ── The Finance tracker ──────────────────────────────────────────────────────
function editor(view) {
  view.append(
    h('p', { class: 'ws-note' }, 'Your original Finance tracker, running privately. Its saves update every Money screen. It will be retired once statements and receipts import here directly.'),
    h('div', { class: 'ws-tracker', id: 'adm-tracker-finance' }));
  window.VW.Trackers.open('finance');
  setDirty(() => window.VW.Trackers.isDirty());   // a save in flight must finish before leaving
  return () => window.VW.Trackers.clear();
}
