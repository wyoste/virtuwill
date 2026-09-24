// Money: Overview · Transactions · Receipts · Accounts · Budgets · Goals ·
// Imports, plus the Finance tracker, which still edits budgets, goals and the
// pay plan. Bank activity, receipts and pay statements load through Imports.
import { h, api, fmt, card, stat, pageHead, tabs, empty, run, toast } from '../lib.js';
import { setDirty } from '../main.js';
import { balanceList, balanceTotals, spendStrip, spendSummary } from '../moneyparts.js';

const TABS = [['/app/money', 'Overview'], ['/app/money/transactions', 'Transactions'], ['/app/money/receipts', 'Receipts'],
              ['/app/money/accounts', 'Accounts & balances'], ['/app/money/budgets', 'Budgets & bills'],
              ['/app/money/goals', 'Goals & retirement'], ['/app/money/imports', 'Imports'], ['/app/money/editor', 'Finance tracker']];

export async function render(view, ctx) {
  const tab = ctx.path.split('/')[3] || 'overview';
  view.append(pageHead('Money', {
    overview: 'Balances, spending, pay and goals', imports: 'Load statements, pay stubs and receipt exports', transactions: 'Bank activity with receipt lines and sources',
    receipts: 'Receipts and the bank charge each one explains', accounts: 'Every account and its balance over time',
    budgets: 'Budgets against spending, and recurring bills', goals: 'Savings goals, retirement and the pay plan',
    editor: 'Where these records are edited for now' }[tab]), tabs(TABS, ctx.path));
  if (['budgets', 'goals'].includes(tab)) view.append(h('p', { class: 'ws-note' },
    'Budgets, goals and the pay plan are edited in the ', h('a', { href: '/app/money/editor' }, 'Finance tracker'), '.'));
  const screens = { overview, transactions, receipts, accounts, budgets, goals, imports, editor };
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
// Full analysis by default; "Goals only" shows savings goals and retirement.
// The choice is a site setting, so it sticks across devices.
async function overview(view, ctx) {
  const d = await api('/api/v1/money/overview');
  const reload = () => { view.replaceChildren(); return render(view, ctx); };
  const mode = d.mode === 'goals' ? 'goals' : 'full';
  const toggle = h('div', { class: 'ws-seg', role: 'group', 'aria-label': 'Overview shows' },
    [['full', 'Full analysis'], ['goals', 'Goals only']].map(([value, label]) => h('button', {
      type: 'button', 'aria-pressed': String(mode === value),
      onclick: e => mode !== value && run(e.currentTarget, async () => {
        await api('/api/settings', { method: 'PUT', body: { 'money.overview_mode': value } });
        await reload();
      }) }, label)));
  view.append(h('div', { class: 'ws-filters', style: { justifyContent: 'space-between', alignItems: 'center' } },
    toggle, h('span', { class: 'ws-note' }, d.through ? `Bank data through ${fmt.day(d.through)}` : 'No bank data yet — load some in Imports')));

  if (mode === 'goals') return goalsView(view, d);

  const totals = balanceTotals(d.balances);
  const spent = d.budgets.reduce((s, b) => s + (b.actual || 0), 0);
  const budgeted = d.budgets.reduce((s, b) => s + (b.budget || 0), 0);
  const days = new Date(d.spend.days.at(-1)?.day.slice(0, 4), Number(d.spend.days.at(-1)?.day.slice(5, 7)), 0).getDate() || 30;
  view.append(
    h('div', { class: 'ws-stats' },
      stat('Net worth', fmt.money(totals.net), totals.stale ? `${totals.stale} balance${totals.stale === 1 ? ' is' : 's are'} out of date or missing` : 'cash and savings less what’s owed', totals.net < 0 ? 'bad' : null),
      stat('Cash & savings', fmt.money(totals.cash), `${d.balances.filter(b => !b.is_liability).length} accounts`),
      stat('Owed', fmt.money(totals.owed), 'cards and loans'),
      stat(`Spent in ${fmt.month(d.month)}`, fmt.money(spent), budgeted ? `of ${fmt.money(budgeted)} budgeted` : 'no budgets set',
           budgeted && spent > budgeted ? 'bad' : null)),
    h('div', { class: 'ws-grid two' },
      card('Balances', balanceList(d.balances, reload)),
      card(h('span', {}, 'Daily spending', h('span', { class: 'ws-note' }, 'last 14 days')),
        spendSummary(d.spend),
        spendStrip(d.spend.days, { selected: d.spend.days.at(-1)?.day, budgetPerDay: d.spend.month_budget ? d.spend.month_budget / days : 0 }))),
    h('div', { class: 'ws-grid two' },
      card(`Budgets · ${fmt.month(d.month)}`, d.budgets.length ? h('ul', { class: 'ws-list' }, d.budgets.map(b => h('li', { class: 'ws-row', style: { display: 'block' } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '8px' } }, h('strong', {}, b.name),
          h('span', { class: 'ws-amount' }, fmt.money(b.actual || 0) + (b.budget ? ' / ' + fmt.money(b.budget) : ''))),
        b.budget ? bar(b.actual || 0, b.budget, b.over_budget ? 'over' : null) : h('div', { class: 'ws-row-meta' }, 'no amount set')))) : empty('No budgets yet.')),
      card(`Spending by category · ${fmt.month(d.month)}`, hbars(d.spendingByCategory.map(s => [s.category, s.amount, `${s.transactions} transactions`]))
        || empty('No spending this month.'))),
    h('div', { class: 'ws-grid two' },
      card('Spending by month', hbars([...d.monthlySpending].reverse().map(m => [fmt.month(m.month_start), m.amount])) || empty('No spending loaded.')),
      card(h('span', {}, 'Top merchants', h('span', { class: 'ws-note' }, 'last 90 days')),
        hbars(d.topMerchants.map(m => [m.merchant || '—', m.amount, `${m.transactions} transactions`])) || empty('No spending in the last 90 days.'))),
    h('div', { class: 'ws-grid two' },
      card('Pay', payCard(d)),
      card('Cash flow by pay period', d.cashFlow.length ? h('div', { class: 'ws-table-wrap' }, h('table', { class: 'ws-table' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Period'), h('th', { class: 'num' }, 'In'), h('th', { class: 'num' }, 'Spent'), h('th', { class: 'num' }, 'Card payments'), h('th', { class: 'num' }, 'Left'))),
        h('tbody', {}, d.cashFlow.map(c => {
          const left = (c.income_received || 0) - (c.spending || 0);
          return h('tr', {}, h('td', { class: 'nowrap' }, `${shortDay(c.period_start)} – ${shortDay(c.period_end)}`),
            h('td', { class: 'num' }, fmt.money(c.income_received)), h('td', { class: 'num' }, fmt.money(c.spending)),
            h('td', { class: 'num' }, fmt.money(c.card_payments)), h('td', { class: 'num' }, h('span', { class: left < 0 ? 'bad' : '' }, fmt.money(left))));
        })))) : empty('No pay periods yet.'))),
    h('div', { class: 'ws-grid two' },
      card(h('span', {}, 'Groceries by item category', h('span', { class: 'ws-note' }, 'last 6 months, from receipt lines')),
        hbars(d.groceries.map(g => [g.item_category || 'Review', g.amount, `${fmt.num(g.lines)} lines`])) || empty('No receipt lines loaded.')),
      card('Savings goals', goalList(d.goals))));
  view.append(retirementStats(d.retirement));
}

function goalsView(view, d) {
  const saved = d.goals.reduce((s, g) => s + (g.balance || 0), 0);
  const target = d.goals.reduce((s, g) => s + (g.target || 0), 0);
  view.append(
    h('div', { class: 'ws-stats' },
      stat('Saved toward goals', fmt.money(saved), (target ? `of ${fmt.money(target)} · ` : '') + `${d.goals.length} goals`),
      stat('Per paycheck', fmt.money(d.goals.reduce((s, g) => s + (g.contribution_per_check || 0), 0)), 'set aside for goals')),
    card('Savings goals', goalList(d.goals)),
    retirementStats(d.retirement));
}

function goalList(goals) {
  return goals.length ? h('ul', { class: 'ws-list' }, goals.map(g => h('li', { class: 'ws-row', style: { display: 'block' } },
    h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '8px' } }, h('strong', {}, g.name),
      h('span', { class: 'ws-amount' }, fmt.money(g.balance ?? 0) + (g.target ? ' / ' + fmt.money(g.target) : ''))),
    g.target ? bar(g.balance || 0, g.target, 'good') : null,
    h('div', { class: 'ws-row-meta' }, [g.target ? fmt.pct(g.pct_of_target) : 'no target set', g.due_on ? 'by ' + fmt.day(g.due_on) : null,
      g.contribution_per_check ? fmt.money(g.contribution_per_check) + ' per paycheck' : null].filter(Boolean).join(' · ')))))
    : empty('No savings goals.');
}

function retirementStats(r = {}) {
  if (r.total_balance == null && r.deferral_limit == null) return null;
  return h('div', { class: 'ws-stats' },
    stat('Retirement balance', fmt.money(r.total_balance), r.balances_as_of ? 'as of ' + fmt.day(r.balances_as_of) : ''),
    stat('401(k) this year', fmt.money(r.employee_ytd), `you · ${fmt.money(r.employer_ytd)} employer match`),
    stat('401(k) room left', fmt.money(r.deferral_room), `limit ${fmt.money(r.deferral_limit)} · ${fmt.money(r.projected_remaining_deferrals)} more on plan`),
    stat('IRA', fmt.money(r.ira_actual), `of ${fmt.money(r.ira_limit)} · ${fmt.money(r.ira_per_check)} per check`));
}

function payCard(d) {
  const p = d.lastPaycheck;
  if (!p?.paycheck_id) return empty('No pay statements loaded. Import one in Imports.');
  return h('div', {},
    h('div', { class: 'ws-note', style: { marginBottom: '8px' } }, `Last paycheck ${fmt.day(p.pay_date)}${p.employer ? ' · ' + p.employer : ''}`),
    h('div', { class: 'ws-kv' },
      h('span', {}, 'Gross'), h('span', {}, fmt.money(p.gross)),
      h('span', {}, 'Before-tax deductions'), h('span', {}, '−' + fmt.money(p.pre_tax)),
      h('span', {}, 'Taxes'), h('span', {}, '−' + fmt.money(p.taxes)),
      h('span', {}, 'After-tax deductions'), h('span', {}, '−' + fmt.money(p.post_tax)),
      h('strong', { class: 'total' }, 'Net'), h('span', { class: 'total' }, fmt.money(p.net)),
      ...(p.splits || []).flatMap(s => [h('span', { class: 'ws-note' }, '→ account ••' + s.account_mask), h('span', { class: 'ws-note' }, fmt.money(s.amount))])),
    d.pay.length ? h('table', { class: 'ws-table', style: { marginTop: '12px' } },
      h('thead', {}, h('tr', {}, h('th', {}, 'Month'), h('th', { class: 'num' }, 'Checks'), h('th', { class: 'num' }, 'Gross'), h('th', { class: 'num' }, 'Net'))),
      h('tbody', {}, d.pay.slice(0, 6).map(m => h('tr', {}, h('td', { class: 'nowrap' }, new Date(m.month_start + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' })), h('td', { class: 'num' }, m.paychecks),
        h('td', { class: 'num' }, fmt.money(m.gross)), h('td', { class: 'num' }, fmt.money(m.net)))))) : null);
}

// Horizontal bars: [[label, amount, title?]] scaled to the largest.
function hbars(rows) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map(r => r[1] || 0));
  return h('div', { class: 'ws-hbars' }, rows.map(([label, value, title]) => h('div', { class: 'ws-hbar', title: title || null },
    h('span', {}, label), bar(value || 0, max), h('span', { class: 'ws-amount' }, fmt.money(value)))));
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
    h('thead', {}, h('tr', {}, h('th', {}, 'Account'), h('th', { class: 'wide-only' }, 'Type'), h('th', {}, 'As of'), h('th', { class: 'num' }, 'Balance'), h('th', { class: 'wide-only' }, 'History'))),
    h('tbody', {}, d.accounts.map(a => h('tr', { style: a.is_active ? null : { opacity: .55 } },
      h('td', {}, h('strong', {}, a.name), h('div', { class: 'ws-note' }, [a.institution !== a.name ? a.institution : null, a.mask ? '••' + a.mask : null].filter(Boolean).join(' ')),
        h('div', { class: 'ws-note narrow-only' }, a.account_type.replace('_', ' ') + (a.is_liability ? ' (owed)' : ''))),
      h('td', { class: 'wide-only' }, a.account_type.replace('_', ' ') + (a.is_liability ? ' (owed)' : '')),
      h('td', { class: 'nowrap' }, a.as_of ? shortDay(a.as_of) : '—'),
      h('td', { class: 'num' }, fmt.money(a.balance)),
      h('td', { class: 'wide-only' }, a.history.length > 1 ? spark(a.history.map(p => p.balance)) : h('span', { class: 'ws-note' }, `${a.history.length} reading${a.history.length === 1 ? '' : 's'}`)))))))),
    d.statements.length ? card('Statements reconciled', h('div', { class: 'ws-table-wrap' }, h('table', { class: 'ws-table' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Statement'), h('th', {}, 'Period'), h('th', { class: 'num' }, 'Closing'), h('th', { class: 'num' }, 'Off by'))),
      h('tbody', {}, d.statements.map(s => h('tr', {}, h('td', { class: 'ws-clip', title: s.original_filename || s.statement_id }, s.original_filename || s.statement_id),
        h('td', { class: 'nowrap' }, `${shortDay(s.period_start)} – ${shortDay(s.period_end)}`), h('td', { class: 'num' }, fmt.money(s.closing_balance)),
        h('td', { class: 'num' }, s.difference ? h('span', { class: 'bad' }, fmt.money(s.difference)) : '✓'))))))) : null);
}

// "Sep 18" (no weekday) for table cells.
function shortDay(iso) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
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
      card('Savings goals', goalList(d.goals)),
      card('Retirement accounts', h('ul', { class: 'ws-list' }, d.retirementAccounts.map(a => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, a.name), h('div', { class: 'ws-row-meta' }, a.as_of ? 'as of ' + fmt.day(a.as_of) : '')),
        h('span', { class: 'ws-amount' }, fmt.money(a.balance))))))),
    card('Pay plan', h('div', { class: 'ws-grid' },
      h('div', {}, h('div', { class: 'ws-note', style: { fontWeight: 600 } }, 'Each paycheck goes to'),
        h('ul', { class: 'ws-list' }, d.deposits.map(x => h('li', { class: 'ws-row' }, h('div', { class: 'ws-row-main' }, x.name || (x.account_id ? 'Account ••' + x.account_id.replace(/^acct-/, '') : 'Deposit')),
          h('span', { class: 'ws-amount' }, fmt.money(x.amount)))))),
      h('div', {}, h('div', { class: 'ws-note', style: { fontWeight: 600 } }, 'Set aside each paycheck'),
        h('ul', { class: 'ws-list' }, d.allocations.map(x => h('li', { class: 'ws-row' },
          h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, x.name), h('div', { class: 'ws-row-meta' }, x.cadence || '')),
          h('span', { class: 'ws-amount' }, fmt.money(x.amount)))))))));
}

// ── Imports ──────────────────────────────────────────────────────────────────
// Upload what a portal exports (statement or spending-report PDFs, pay
// statements, receipt CSVs) or a structured finance file (.finance.json or the
// CSVs this screen downloads). Nothing is loaded until the preview is committed.
const PARSERS = {
  chase_spending_report: 'Card spending report', chase_card_statement: 'Card statement',
  payroll_earning_statement: 'Pay statements', grocery_receipts_csv: 'Grocery receipts',
  grocery_items_csv: 'Grocery receipt lines', canonical_csv: 'Structured CSV', merged: 'Several files',
};

async function imports(view, ctx) {
  const reload = () => { view.replaceChildren(); return render(view, ctx); };
  const input = h('input', { type: 'file', multiple: true, accept: '.pdf,.csv,.json' });
  const chosen = h('p', { class: 'ws-note' }, 'No files chosen');
  const picked = () => [...input.files];
  input.onchange = () => { chosen.textContent = picked().map(f => f.name).join(', ') || 'No files chosen'; };
  const drop = h('label', { class: 'ws-drop' }, input,
    h('p', { style: { margin: '0 0 6px', fontWeight: 600 } }, 'Drop files here, or click to choose'),
    h('p', { class: 'ws-note' }, 'PDF statements and pay stubs, receipt CSVs, or a structured .finance.json / CSV. Receipts and their line items can go together.'),
    chosen);
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); input.files = e.dataTransfer.files; input.onchange(); });
  const form = () => {
    if (!picked().length) throw new Error('Choose one or more files first.');
    const f = new FormData();
    picked().forEach(file => f.append('files', file));
    return f;
  };
  const result = h('div', {});
  view.append(
    card('Load files',
      drop,
      h('div', { class: 'ws-filters', style: { marginTop: '12px' } },
        h('button', { class: 'btn primary', onclick: e => run(e.currentTarget, async () => {
          const staged = await api('/api/v1/money/imports', { method: 'POST', form: form() });
          result.replaceChildren(importCard(staged, reload, true));
          result.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }) }, 'Preview import'),
        h('button', { class: 'btn', onclick: e => run(e.currentTarget, () => download('/api/v1/money/extract', form())) }, 'Convert to JSON'),
        h('button', { class: 'btn', onclick: e => run(e.currentTarget, () => download('/api/v1/money/extract?format=csv', form())) }, 'Convert to CSV')),
      h('p', { class: 'ws-note', style: { marginTop: '8px' } },
        'Convert only turns the files into structured data to keep or edit; nothing is loaded. ',
        'The same conversion runs from the command line: scripts/extract_finance.py, then scripts/load_finance.py.')),
    result);
  const history = await api('/api/v1/money/imports');
  view.append(card('Recent imports', history.length ? h('div', {}, history.map(i => importCard(i, reload, false))) : empty('Nothing imported yet.')));
}

async function download(url, form) {
  const response = await fetch(url, { method: 'POST', body: form });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `Conversion failed (${response.status})`);
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'finance.json';
  const href = URL.createObjectURL(await response.blob());
  h('a', { href, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(href), 10000);
}

const STATUS = { staged: ['info', 'ready to commit'], committed: ['good', 'loaded'], discarded: ['', 'discarded'] };

function importCard(i, reload, open) {
  const p = i.preview || {};
  const doc = p.document || {};
  const c = p.counts || {};
  const tx = c.transactions || {};
  const summary = [PARSERS[i.parser] || i.parser, p.range ? `${fmt.day(p.range[0])} – ${fmt.day(p.range[1])}` : null,
                   tx.new + tx.seen + tx.taken_over ? `${tx.new + tx.seen + tx.taken_over} transactions` : null,
                   c.paychecks && (c.paychecks.new + c.paychecks.seen) ? `${c.paychecks.new + c.paychecks.seen} paychecks` : null,
                   c.receipts && (c.receipts.new + c.receipts.seen + c.receipts.taken_over) ? `${c.receipts.new + c.receipts.seen + c.receipts.taken_over} receipts` : null]
    .filter(Boolean).join(' · ');
  const checks = Object.entries(p.checks || {}).filter(([, v]) => v && typeof v === 'object' && 'ok' in v);
  const failed = checks.filter(([, v]) => !v.ok);
  const actions = h('div', { class: 'ws-filters', style: { marginTop: '10px' } },
    i.status === 'staged' ? h('button', { class: 'btn primary', onclick: e => run(e.currentTarget, async () => {
      const done = await api(`/api/v1/money/imports/${i.import_id}/commit`, { method: 'POST' });
      toast(done.report?.skipped || 'Loaded into Money.');
      await reload();
    }) }, 'Commit') : null,
    i.status === 'staged' ? h('button', { class: 'btn', onclick: e => run(e.currentTarget, async () => {
      await api(`/api/v1/money/imports/${i.import_id}`, { method: 'DELETE' });
      await reload();
    }) }, 'Discard') : null,
    h('a', { class: 'btn', href: `/api/v1/money/imports/${i.import_id}/bundle` }, 'Download JSON'),
    h('a', { class: 'btn', href: `/api/v1/money/imports/${i.import_id}/bundle?format=csv` }, 'Download CSV'));
  const row = (label, counts) => counts && h('div', {}, h('strong', {}, label),
    [['new', 'new'], ['taken_over', 'replace tracker rows'], ['seen', 'already loaded']]
      .filter(([k]) => counts[k]).map(([k, t]) => h('div', {}, `${fmt.num(counts[k])} ${t}`)),
    !Object.values(counts).some(Boolean) ? h('div', { class: 'ws-note' }, 'none') : null);
  const r = i.report;
  return h('details', { class: 'ws-expand ws-import', open: open || null },
    h('summary', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, i.filename),
        h('div', { class: 'ws-row-meta' }, summary || 'no records found')),
      h('div', { class: 'ws-row-end' },
        failed.length ? h('span', { class: 'ws-chip bad' }, `${failed.length} totals off`) : checks.length ? h('span', { class: 'ws-chip good' }, 'totals check') : null,
        h('span', { class: 'ws-chip ' + (STATUS[i.status]?.[0] || '') }, STATUS[i.status]?.[1] || i.status))),
    h('div', { class: 'ws-import-body' },
      (p.warnings || []).map(w => h('p', { class: 'ws-note warn' }, w)),
      h('div', { class: 'ws-counts' },
        row('Transactions', c.transactions), row('Receipts', c.receipts), row('Paychecks', c.paychecks && { new: c.paychecks.new, seen: c.paychecks.seen }),
        c.statements || c.balances ? h('div', {}, h('strong', {}, 'Statements'), h('div', {}, `${c.statements || 0} statements · ${c.balances || 0} balances`)) : null),
      (p.accounts || []).length ? h('p', { class: 'ws-note' }, 'Accounts: ' + p.accounts.map(a => `••${a.mask}${a.new ? ' (new)' : ''}`).join(', ')) : null,
      checks.length ? h('p', { class: 'ws-note' + (failed.length ? ' warn' : '') },
        failed.length ? `Totals that don’t match the document: ${failed.map(([k]) => k).slice(0, 6).join(', ')}` : `${checks.length} document totals match what was read.`) : null,
      samples(p.samples),
      r && !r.skipped ? h('p', { class: 'ws-note' }, `Loaded ${i.committed_at ? fmt.day(i.committed_at.slice(0, 10)) : ''}: ` + [
        r.transactions && `${r.transactions.new} new, ${r.transactions.taken_over} replaced, ${r.transactions.seen} already there`,
        r.receipts && (r.receipts.new + r.receipts.taken_over + r.receipts.seen) ? `${r.receipts.new + r.receipts.taken_over} receipts (${r.receipt_payments_matched} matched to a charge)` : null,
        r.paychecks ? `${r.paychecks} paychecks` : null, r.statements ? `${r.statements} statements` : null,
        r.accounts_created ? `${r.accounts_created} new accounts` : null].filter(Boolean).join(' · ')) : null,
      r?.skipped ? h('p', { class: 'ws-note' }, r.skipped) : null,
      actions));
}

function samples(s) {
  const rows = [...(s?.new || []).map(t => ['new', t]), ...(s?.taken_over || []).map(t => ['replaces tracker', t])].slice(0, 6);
  if (!rows.length) return null;
  return h('table', { class: 'ws-table', style: { margin: '8px 0' } },
    h('tbody', {}, rows.map(([kind, t]) => h('tr', {}, h('td', {}, fmt.day(t.posted_on)), h('td', {}, t.description),
      h('td', { class: 'ws-note' }, kind), h('td', { class: 'num' }, amount(t.amount))))));
}

// ── The Finance tracker ──────────────────────────────────────────────────────
function editor(view) {
  view.append(
    h('p', { class: 'ws-note' }, 'Your original Finance tracker, running privately. Its saves update every Money screen. Bank activity, receipts and pay statements now load through Imports; rows loaded there replace the tracker’s copies.'),
    h('div', { class: 'ws-tracker', id: 'adm-tracker-finance' }));
  window.VW.Trackers.open('finance');
  setDirty(() => window.VW.Trackers.isDirty());   // a save in flight must finish before leaving
  return () => window.VW.Trackers.clear();
}
