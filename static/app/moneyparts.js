// Money building blocks shared by Today, Journal and the Money screens: account balances,
// the "update balance" dialog, the daily-spend strip and one day's money.
import { h, api, fmt, card, dialog, field, values, toast, empty, isoToday } from './lib.js';

// What to show for an account now. A balance read off a statement or the bank's
// app is exact on its date; after that, posted activity moves it. The moved
// figure is only trusted when both charges and payments/deposits have loaded
// since (a spending-only export has purchases but not the payments against them).
export function balanceNow(b) {
  if (b.balance == null) {
    return { value: null, note: b.transactions_since ? `no balance yet · ${fmt.money(b.outflows_since)} of charges loaded` : 'no balance yet' };
  }
  if (!b.transactions_since) return { value: b.balance, note: `as of ${fmt.day(b.as_of)}` };
  if (b.estimate_complete) {
    return { value: b.estimated_balance, estimated: true,
             note: `estimated · ${fmt.money(b.balance)} on ${fmt.day(b.as_of)} plus ${b.transactions_since} posted since` };
  }
  return { value: b.balance, stale: true,
           note: `as of ${fmt.day(b.as_of)} · ${fmt.money(b.outflows_since)} of charges since, payments not loaded` };
}

export function balanceTotals(balances) {
  let cash = 0, owed = 0, stale = 0;
  for (const b of balances) {
    const { value, stale: old } = balanceNow(b);
    if (value == null || old) stale++;
    if (value == null) continue;
    if (b.is_liability) owed += value; else cash += value;
  }
  return { cash, owed, net: cash - owed, stale };
}

export function balanceList(balances, onChange, asOf) {
  if (!balances.length) return empty('No balances yet. Import a statement, or record one read off the bank’s app.', updateButton(onChange, asOf));
  const groups = [['Cash & savings', balances.filter(b => !b.is_liability)], ['Owed', balances.filter(b => b.is_liability)]];
  return h('div', {},
    groups.filter(([, rows]) => rows.length).map(([label, rows]) => h('div', {},
      h('div', { class: 'ws-list-head' }, label),
      h('ul', { class: 'ws-list' }, rows.map(b => {
        const now = balanceNow(b);
        return h('li', { class: 'ws-row' },
          h('div', { class: 'ws-row-main' },
            h('div', { class: 'ws-row-title' }, b.name, b.mask ? h('span', { class: 'ws-note' }, ' ••' + b.mask) : null),
            h('div', { class: 'ws-row-meta' + (now.stale ? ' warn' : '') }, now.note)),
          h('div', { class: 'ws-row-end' },
            h('span', { class: 'ws-amount' + (now.estimated ? ' est' : '') }, now.value == null ? '—' : fmt.money(now.value)),
            h('button', { class: 'btn small link', title: 'Record today’s balance', 'aria-label': 'Update ' + b.name + ' balance',
                          onclick: () => updateBalance(onChange, b.account_id, asOf) }, 'Update')));
      })))),
    h('div', { style: { marginTop: '10px' } }, updateButton(onChange, asOf)));
}

function updateButton(onChange, asOf) {
  return h('button', { class: 'btn small', onclick: () => updateBalance(onChange, null, asOf) }, 'Record a balance');
}

// A balance read off a portal or app, recorded as of a day.
export async function updateBalance(onChange, accountId, asOf) {
  const d = await api('/api/v1/money/balances');
  if (!d.accounts.length) { toast('Import a statement first so the account exists.', 'error'); return; }
  const form = h('form', { class: 'ws-form stack', onsubmit: e => e.preventDefault() },
    field('Account', 'account_id', { kind: 'select', value: accountId || d.accounts[0].account_id,
      options: d.accounts.map(a => [a.account_id, `${a.name}${a.mask ? ' ••' + a.mask : ''}`]) }),
    field('Balance', 'balance', { kind: 'number', step: '0.01', required: true }),
    field('As of', 'as_of', { kind: 'date', value: asOf || isoToday() }),
    h('p', { class: 'ws-note' }, 'For a card or loan, enter what you owe as a positive number.'));
  const ok = await dialog('Record a balance', form, [['Cancel', null], ['Save', true]]);
  if (!ok) return;
  try {
    await api('/api/v1/money/balances', { method: 'POST', body: values(form) });
    onChange?.();
  } catch (e) { toast(e.message, 'error'); }
}

// Spending per day as small bars; the selected day is outlined.
export function spendStrip(days, { selected, budgetPerDay, dayLink = day => '/app?date=' + day } = {}) {
  const max = Math.max(1, budgetPerDay || 0, ...days.map(d => d.amount));
  return h('div', { class: 'ws-spend' },
    h('div', { class: 'ws-spend-bars', role: 'img', 'aria-label': 'Spending per day, last ' + days.length + ' days' },
      budgetPerDay ? h('div', { class: 'ws-spend-line', style: { bottom: (budgetPerDay / max) * 100 + '%' }, title: 'Budget per day ' + fmt.money(budgetPerDay) }) : null,
      days.map(d => h('a', { href: dayLink(d.day), class: 'ws-spend-bar' + (d.day === selected ? ' on' : '') + (budgetPerDay && d.amount > budgetPerDay ? ' over' : ''),
        title: `${fmt.day(d.day)}: ${fmt.money(d.amount)} · ${d.transactions} transaction${d.transactions === 1 ? '' : 's'}` },
        h('span', { style: { height: Math.max(d.amount > 0 ? 3 : 0, (d.amount / max) * 100) + '%' } })))),
    h('div', { class: 'ws-spend-axis' }, h('span', {}, fmt.day(days[0]?.day)), h('span', {}, fmt.day(days.at(-1)?.day))));
}

// Today / week / month against the monthly budget.
export function spendSummary(spend) {
  const budget = spend.month_budget || 0;
  return h('div', { class: 'ws-spend-sum' },
    [['Day', spend.day], ['Week', spend.week], ['Month', spend.month]].map(([label, value]) => h('div', {},
      h('div', { class: 'ws-stat-label' }, label), h('div', { class: 'ws-amount', style: { fontSize: '18px' } }, fmt.money(value)))),
    budget ? h('div', { style: { gridColumn: '1 / -1' } },
      h('div', { class: 'ws-bar' + (spend.month > budget ? ' over' : ''), role: 'img', 'aria-label': Math.round((spend.month / budget) * 100) + '% of the month’s budget' },
        h('span', { style: { width: Math.min(100, (spend.month / budget) * 100) + '%' } })),
      h('div', { class: 'ws-note', style: { marginTop: '4px' } }, `${fmt.money(spend.month)} of ${fmt.money(budget)} budgeted this month`)) : null);
}

// One day's money (from /api/v1/today's money): balances as they stood that day, spending for the
// day, week and month, and the day's transactions. Today and Journal both show it.
// dayLink: where a bar in the spending strip goes (Today stays on Today, Journal on Journal).
export function dayMoney(money, date, { onChange, dayLink } = {}) {
  const totals = balanceTotals(money.balances);
  const days = new Date(date.slice(0, 4), Number(date.slice(5, 7)), 0).getDate();
  const tx = money.transactions;
  return h('div', {},
    h('div', { class: 'ws-grid two' },
      card(h('span', {}, 'Balances', h('span', { class: 'ws-note' }, `net ${fmt.money(totals.net)}`)),
        balanceList(money.balances, onChange, date)),
      card(h('span', {}, 'Spending', h('a', { class: 'btn small', href: '/app/money' }, 'Money')),
        spendSummary(money.spend),
        spendStrip(money.spend.days, { selected: date, dayLink, budgetPerDay: money.spend.month_budget ? money.spend.month_budget / days : 0 }))),
    card(h('span', {}, 'Money on this day', h('a', { class: 'btn small', href: '/app/money/transactions?month=' + date.slice(0, 7) }, 'All transactions')),
      tx.length ? h('ul', { class: 'ws-list' }, tx.map(t => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, t.merchant || '—'),
          h('div', { class: 'ws-row-meta' }, [t.category, t.account_name, t.kind === 'movement' ? 'transfer' : null, t.has_receipt ? 'receipt matched' : null].filter(Boolean).join(' · '))),
        h('span', { class: 'ws-amount' + (t.amount < 0 ? ' in' : '') }, (t.amount < 0 ? '+' : '') + fmt.money(Math.abs(t.amount))))))
        : empty(money.through && date > money.through
            ? `Bank data runs through ${fmt.day(money.through)}; this day isn’t loaded yet.`
            : 'No bank transactions on this day.')));
}
