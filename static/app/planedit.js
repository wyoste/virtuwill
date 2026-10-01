// Editing the money plan in Money (the Finance tracker is retired): budgets, bills, other
// income, set-asides, paycheck deposits, pay and the retirement plan. One spec per kind,
// matching finance.PLAN on the server, drives one dialog.
import { h, api, dialog, field, values, toast, confirmDelete } from './lib.js';

const FREQ = ['Weekly', 'Biweekly', 'Monthly', 'Annual'];
const money = (name, label, opts = {}) => [name, label, { kind: 'number', step: '0.01', min: 0, ...opts }];
const SPECS = {
  budgets: { noun: 'budget', id: 'budget_id', name: r => r.name, fields: [
    ['name', 'Budget', { required: true, placeholder: 'e.g. Eating out' }],
    ['basis', 'How the amount is set', { kind: 'select', options: [['fixed', 'A fixed monthly amount'], ['per_paycheck', 'An amount per paycheck'],
      ['recurring_expenses', 'The sum of its recurring bills'], ['unset', 'No amount yet']] }],
    money('amount_monthly', 'Monthly amount ($)', { show: r => r.basis === 'fixed' }),
    money('per_paycheck', 'Per paycheck ($)', { show: r => r.basis === 'per_paycheck' }),
    ['paychecks_per_year', 'Paychecks a year', { kind: 'number', min: 1, max: 60, step: 1, show: r => r.basis === 'per_paycheck' }],
    ['categories', 'Spending categories it covers (comma separated)', { wide: true, list: 'categories', placeholder: 'e.g. Dining, Coffee' }],
    ['note', 'Note', { wide: true }]] },
  bills: { noun: 'bill', id: 'expense_id', name: r => r.name, fields: [
    ['name', 'Bill', { required: true, placeholder: 'e.g. Phone' }], money('amount', 'Amount ($)'),
    ['frequency', 'How often', { kind: 'select', options: FREQ, default: 'Monthly' }], ['due_day', 'Due day of the month', { kind: 'number', min: 1, max: 31, step: 1 }],
    ['category', 'Category', { required: true, list: 'categories' }],
    ['status', 'Status', { kind: 'select', options: ['Active', 'Unconfirmed', 'Expiring', 'Canceled'] }],
    ['account_id', 'Paid from', { kind: 'select', accounts: true }], ['note', 'Note', { wide: true }]] },
  incomes: { noun: 'income', id: 'income_id', name: r => r.name, fields: [
    ['name', 'Income', { required: true, placeholder: 'e.g. Side work' }], money('amount', 'Amount ($)', { required: true }),
    ['frequency', 'How often', { kind: 'select', options: FREQ, default: 'Monthly' }], ['note', 'Note', { wide: true }]] },
  allocations: { noun: 'set-aside', id: 'allocation_id', name: r => r.name, fields: [
    ['name', 'Set aside for', { required: true, placeholder: 'e.g. Travel' }], money('amount', 'Amount ($)', { required: true }),
    ['cadence', 'How often', { kind: 'select', options: FREQ, default: 'Biweekly' }], ['account_id', 'Kept in', { kind: 'select', accounts: true }],
    ['note', 'Note', { wide: true }]] },
  deposits: { noun: 'deposit', id: 'position', name: r => r.destination_text, fields: [
    ['destination_text', 'Goes to', { required: true, placeholder: 'e.g. Checking' }], ['account_id', 'Account', { kind: 'select', accounts: true }],
    money('amount', 'Amount per paycheck ($)', { required: true })] },
  pay: { noun: 'pay', id: 'as_of', name: r => 'Pay as of ' + r.as_of, fields: [
    ['as_of', 'As of', { kind: 'date', required: true }], money('net_pay', 'Net pay per check ($)', { required: true }),
    money('gross_pay', 'Gross pay per check ($)', { required: true }),
    ['checks_per_year', 'Checks a year', { kind: 'number', min: 1, max: 60, step: 1, required: true }],
    ['anchor_date', 'A known pay date', { kind: 'date', required: true }],
    money('gross_ytd', 'Gross so far this year ($)'), money('net_ytd', 'Net so far this year ($)'), money('bonus_ytd', 'Bonus so far this year ($)')] },
  'retirement-plan': { noun: 'retirement plan', id: 'as_of', name: r => 'Retirement plan as of ' + r.as_of, fields: [
    ['as_of', 'As of', { kind: 'date', required: true }],
    money('employee_per_check', '401(k): you, per check ($)'), money('employer_per_check', '401(k): employer, per check ($)'),
    money('employee_ytd', '401(k): you, this year ($)'), money('employer_ytd', '401(k): employer, this year ($)'),
    money('deferral_limit', '401(k) limit this year ($)'), money('other_deferrals', 'Other deferrals this year ($)'),
    ['remaining_checks', 'Paychecks left this year', { kind: 'number', min: 0, max: 60, step: 1 }],
    money('ira_per_check', 'IRA per check ($)'), money('ira_limit', 'IRA limit this year ($)'), money('ira_actual', 'IRA so far this year ($)')] },
};

let options = null;
async function planOptions() {
  options = options || await api('/api/v1/money/plan-options');
  return options;
}

// Add (record = null, or defaults to start from) or edit one plan record; calls onChange after a save or delete.
// fresh: start a new record from `record`'s values (e.g. a new pay record from the latest one).
export async function editPlan(kind, record, onChange, { fresh = false } = {}) {
  const spec = SPECS[kind];
  const opts = await planOptions();
  const existing = record && record[spec.id] != null && !fresh ? record : null;
  const start = record || {};
  const lists = h('datalist', { id: 'plan-categories' }, opts.categories.map(c => h('option', { value: c })));
  const controls = spec.fields.map(([name, label, o = {}]) => {
    let value = start[name] ?? o.default ?? '';
    if (name === 'categories' && Array.isArray(value)) value = value.join(', ');
    const f = o.accounts
      ? field(label, name, { kind: 'select', value: value || '', options: [['', '—'], ...opts.accounts.filter(a => !a.is_liability || kind === 'bills')
          .map(a => [a.account_id, `${a.name}${a.mask ? ' ••' + a.mask : ''}`])] })
      : field(label, name, { ...o, value });
    if (o.list) f.querySelector('input')?.setAttribute('list', 'plan-categories');
    return [f, o.show];
  });
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() }, lists, controls.map(([f]) => f));
  const sync = () => { const v = values(form); for (const [f, show] of controls) if (show) f.hidden = !show(v); };
  form.addEventListener('change', sync); sync();
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  const id = existing ? encodeURIComponent(existing[spec.id]) : null;
  const buttons = [['Cancel', null], ...(existing ? [['Delete', 'delete']] : []), ['Save', 'save']];
  for (;;) {
    const choice = await dialog(existing ? `Edit ${spec.noun}` : `Add ${/^[aeiou]/.test(spec.noun) ? 'an' : 'a'} ${spec.noun}`, h('div', {}, form, error), buttons);
    if (!choice) return false;
    try {
      if (choice === 'delete') {
        if (!(await confirmDelete(spec.name(existing)))) continue;
        await api(`/api/v1/money/plan/${kind}/${id}`, { method: 'DELETE', quiet: true });
        toast('Deleted');
      } else {
        const body = values(form);
        if ('categories' in body) body.categories = (body.categories || '').split(',').map(c => c.trim()).filter(Boolean);
        if ('account_id' in body) body.account_id = body.account_id || null;
        await api(existing ? `/api/v1/money/plan/${kind}/${id}` : `/api/v1/money/plan/${kind}`,
                  { method: existing ? 'PUT' : 'POST', body, quiet: true });
        toast('Saved');
        options = null;     // a new category may have been added
      }
      onChange?.();
      return true;
    } catch (e) { error.textContent = e.message; }
  }
}

// Open the editor for a stored record by id (screens show views, not the stored rows).
export async function editPlanById(kind, recordId, onChange) {
  const rows = await api(`/api/v1/money/plan/${kind}`);
  const record = rows.find(r => String(r[SPECS[kind].id]) === String(recordId));
  if (!record) { toast('That record no longer exists.', 'error'); onChange?.(); return false; }
  return editPlan(kind, record, onChange, { fresh: false });
}

// A small button that adds or edits.
export function planButton(label, kind, record, onChange, cls = 'btn small') {
  return h('button', { class: cls, type: 'button', onclick: () => (record && record[SPECS[kind].id] != null && record[SPECS[kind].id] !== ''
    ? editPlanById(kind, record[SPECS[kind].id], onChange) : editPlan(kind, record, onChange, { fresh: true })) }, label);
}
