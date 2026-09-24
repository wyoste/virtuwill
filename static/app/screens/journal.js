// Journal: one entry per date, with that day's health beside it.
// Meals are logged in Health › Food; habits run/lift/drink tick themselves.
import { h, api, fmt, card, pageHead, isoToday, addDays, empty, toast, run, cleanHTML, textToHTML, dialog, field, values } from '../lib.js';
import { setDirty } from '../main.js';
import { plainText } from './today.js';

export async function render(view, { path, params, navigate }) {
  const date = path.split('/')[3];
  return date ? editor(view, date, navigate) : list(view, params);
}

// ── All entries ──────────────────────────────────────────────────────────────
async function list(view, params) {
  const entries = await api('/api/journal/entries');
  const q = (params.get('q') || '').toLowerCase();
  const search = h('input', { class: 'ws-input', type: 'search', placeholder: 'Search entries, quotes, tags…', value: q, 'aria-label': 'Search entries' });
  const newDate = h('input', { class: 'ws-input', type: 'date', value: isoToday(), 'aria-label': 'Date for a new entry' });
  const body = h('div');
  view.append(
    pageHead('Journal', `${entries.length} entries${entries.length ? ' · since ' + fmt.month(entries[entries.length - 1].date) : ''}`,
      newDate, h('button', { class: 'btn primary', onclick: () => { location.href = '/app/journal/' + newDate.value; } }, 'Write')),
    h('div', { class: 'ws-filters' }, search), body);

  const draw = () => {
    const term = search.value.trim().toLowerCase();
    const shown = entries.filter(e => !term || [e.quote, e.quoteAuthor, plainText(e.freeWrite), ...(e.tags || [])].join(' ').toLowerCase().includes(term));
    if (!shown.length) { body.replaceChildren(empty(term ? 'No entries match.' : 'No entries yet. Pick a date and write.')); return; }
    const months = new Map();
    for (const e of shown) {
      const key = e.date.slice(0, 7);
      if (!months.has(key)) months.set(key, []);
      months.get(key).push(e);
    }
    body.replaceChildren(...[...months].map(([month, rows]) => card(fmt.month(month + '-01'),
      h('ul', { class: 'ws-list' }, rows.map(e => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' },
          h('a', { class: 'ws-row-title', href: '/app/journal/' + e.date }, fmt.day(e.date)),
          h('div', { class: 'ws-row-meta' }, (e.quote ? `“${e.quote.slice(0, 80)}” · ` : '') + plainText(e.freeWrite).slice(0, 140))),
        h('div', { class: 'ws-row-end' }, (e.tags || []).slice(0, 3).map(t => h('span', { class: 'ws-chip' }, t)),
          e.health?.qualifyingWorkoutDay ? h('span', { class: 'ws-chip good' }, 'workout') : null)))))));
  };
  search.oninput = draw;
  draw();
}

// ── One day ──────────────────────────────────────────────────────────────────
async function editor(view, date, navigate) {
  const [entries, template, day] = await Promise.all([
    api('/api/journal/entries'), api('/api/accounts-template').catch(() => []), api('/api/v1/today?date=' + date)]);
  const entry = entries.find(e => e.date === date);
  const e = entry || { id: 'e' + Date.now(), date, quote: '', quoteAuthor: '', freeWrite: '', tags: [], habits: {},
                       accounts: template.map(a => ({ institution: a.institution, name: a.name, balance: '' })) };
  let dirty = false;
  const mark = () => { dirty = true; saveBtn.textContent = 'Save entry'; };
  setDirty(() => dirty);

  const quote = h('input', { class: 'ws-input', value: e.quote, placeholder: 'Quote of the day', oninput: mark, 'aria-label': 'Quote' });
  const author = h('input', { class: 'ws-input', value: e.quoteAuthor, placeholder: 'Who said it', oninput: mark, 'aria-label': 'Quote author' });
  const tags = h('input', { class: 'ws-input', value: (e.tags || []).join(', '), placeholder: 'Tags, separated by commas', oninput: mark, 'aria-label': 'Tags' });
  const writing = h('div', { class: 'ws-input ws-writing', contenteditable: 'true', role: 'textbox', 'aria-multiline': 'true',
                             'aria-label': 'Journal entry', html: cleanHTML(e.freeWrite), oninput: mark });
  writing.addEventListener('paste', event => {
    // Paste as plain text so formatting and scripts from other pages never come along.
    event.preventDefault();
    document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
  });

  // Habits: what you set, else what the day's records show.
  const manual = { ...(e.habits || {}) };
  const habitRow = h('div', { class: 'ws-habits' });
  const drawHabits = () => habitRow.replaceChildren(...day.habits.map(habit => {
    const set = habit.habit in manual;
    const done = set ? manual[habit.habit] : habit.origin === 'derived';
    return h('button', { class: 'ws-habit' + (habit.polarity === 'limit' ? ' limit' : ''), 'aria-pressed': String(done),
      onclick: () => { manual[habit.habit] = !done; mark(); drawHabits(); } },
      habit.label, !set && habit.origin === 'derived' ? h('small', {}, 'auto') : null);
  }));
  drawHabits();

  // Balance check-in: what each account showed that day (kept as an observation).
  const accounts = (e.accounts || []).map(a => ({ ...a }));
  const accountRows = h('div', { class: 'ws-form stack' });
  const drawAccounts = () => accountRows.replaceChildren(
    ...(accounts.length ? accounts.map((a, i) => h('div', { class: 'ws-form' },
      h('input', { class: 'ws-input', style: { flex: '2 1 160px' }, value: a.institution, placeholder: 'Institution', 'aria-label': 'Institution',
                   oninput: ev => { a.institution = ev.target.value; mark(); } }),
      h('input', { class: 'ws-input', style: { flex: '2 1 140px' }, value: a.name, placeholder: 'Account', 'aria-label': 'Account',
                   oninput: ev => { a.name = ev.target.value; mark(); } }),
      h('input', { class: 'ws-input', style: { flex: '1 1 110px' }, type: 'number', step: '0.01', value: a.balance ?? '', placeholder: 'Balance',
                   'aria-label': 'Balance', oninput: ev => { a.balance = ev.target.value; mark(); } }),
      h('button', { class: 'btn small danger', 'aria-label': 'Remove account', onclick: () => { accounts.splice(i, 1); mark(); drawAccounts(); } }, '✕')))
      : [h('p', { class: 'ws-note' }, 'No balances recorded for this day.')]),
    h('div', {}, h('button', { class: 'btn small', onclick: () => { accounts.push({ institution: '', name: '', balance: '' }); drawAccounts(); } }, '+ Account')));
  drawAccounts();

  const saveBtn = h('button', { class: 'btn primary' }, entry ? 'Saved' : 'Save entry');
  saveBtn.onclick = () => run(saveBtn, async () => {
    const body = { id: e.id, date, quote: quote.value.trim(), quoteAuthor: author.value.trim(), freeWrite: cleanHTML(writing.innerHTML),
                   tags: tags.value.split(',').map(t => t.trim()).filter(Boolean), habits: manual, accounts,
                   source: e.source || 'manual', createdAt: e.createdAt };
    await api('/api/journal/entry', { method: 'POST', body });
    dirty = false;
    saveBtn.textContent = 'Saved';
    toast('Entry saved');
  });

  const photo = h('input', { type: 'file', accept: 'image/*', hidden: true });
  photo.onchange = () => run(null, async () => {
    const file = photo.files[0];
    if (!file) return;
    toast('Transcribing the page…');
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    const { data } = await api('/api/journal/ocr', { method: 'POST', body: { base64, mediaType: file.type }, quiet: true });
    if (data.quote && !quote.value) quote.value = data.quote;
    if (data.quoteAuthor && !author.value) author.value = data.quoteAuthor;
    if (data.freeWrite) writing.innerHTML = cleanHTML(writing.innerHTML + (writing.textContent.trim() ? '<br><br>' : '') + textToHTML(data.freeWrite));
    const meals = Object.entries(data.meals || {}).filter(([, text]) => text && text.trim());
    const slots = { B: 'breakfast', L: 'lunch', D: 'dinner' };
    if (meals.length && await dialog('Log the meals from this page?',
        h('ul', {}, meals.map(([k, text]) => h('li', {}, `${slots[k]}: ${text}`))), [['Skip', false], ['Log meals', true]])) {
      for (const [k, text] of meals) {
        await api('/api/v1/health/meals', { method: 'POST', body: { meal_date: date, slot: slots[k], description: text.trim() }, quiet: true });
      }
      toast('Meals logged in Health › Food');
    }
    mark();
  });

  const remove = entry ? h('button', { class: 'btn danger', onclick: async () => {
    if (!(await dialog('Delete this entry?', h('p', {}, 'The day’s workouts, meals and balances stay; only the entry is removed.'),
                       [['Cancel', false], ['Delete', true]]))) return;
    await run(null, async () => { await api('/api/journal/entry/' + entry.id, { method: 'DELETE' }); dirty = false; navigate('/app/journal'); });
  } }, 'Delete') : null;

  const hd = day.activity || {};
  view.append(
    pageHead(fmt.longDay(date), entry ? 'Journal entry' : 'New entry',
      h('a', { class: 'btn small', href: '/app/journal/' + addDays(date, -1), 'aria-label': 'Previous day' }, '←'),
      h('a', { class: 'btn small', href: '/app/journal/' + addDays(date, 1), 'aria-label': 'Next day' }, '→'),
      h('button', { class: 'btn', onclick: () => photo.click() }, 'Transcribe a photo'), photo, remove, saveBtn),
    h('div', { class: 'ws-grid two' },
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: '16px', minWidth: 0 } },
        card(null, h('div', { class: 'ws-form' }, h('div', { style: { flex: '3 1 240px' } }, quote), h('div', { style: { flex: '1 1 140px' } }, author)),
          h('div', { style: { marginTop: '10px' } }, writing), h('div', { style: { marginTop: '10px' } }, tags)),
        card('Habits', habitRow, h('p', { class: 'ws-note', style: { marginTop: '8px' } },
          '“auto” means the day’s records ticked it (a strength workout, a run, a drink). Click to set it yourself.')),
        card('Balance check-in', h('p', { class: 'ws-note', style: { marginBottom: '8px' } },
          'What each account showed today. Kept as that day’s observation, never rewritten by later corrections.'), accountRows)),
      card(h('span', {}, 'That day', h('a', { class: 'btn small', href: '/app?date=' + date }, 'Open in Today')),
        h('div', { class: 'ws-stats' },
          h('div', {}, h('div', { class: 'ws-stat-value' }, fmt.num(hd.workout_minutes ?? 0)), h('div', { class: 'ws-stat-sub' }, 'workout minutes')),
          h('div', {}, h('div', { class: 'ws-stat-value' }, hd.total_calories != null ? fmt.num(hd.total_calories) : '—'), h('div', { class: 'ws-stat-sub' }, 'logged calories')),
          h('div', {}, h('div', { class: 'ws-stat-value' }, hd.weight != null ? fmt.num(hd.weight, 1) : '—'), h('div', { class: 'ws-stat-sub' }, 'lb'))),
        section('Workouts', day.workouts.map(w => `${w.activity || w.workout_type}${w.minutes != null ? ' · ' + Math.round(w.minutes) + ' min' : ''}`)),
        section('Meals', day.meals.map(m => `${m.slot}: ${m.description || '(meal)'}${m.calories != null ? ' · ' + Math.round(m.calories) + ' kcal' : ''}`)),
        section('Weigh-ins', day.weighIns.map(w => `${w.value} ${w.unit}${w.is_morning ? ' · morning' : ''}`)),
        section('Money', day.money.transactions.map(t => `${t.merchant || '—'} · ${fmt.money(t.amount)}`)),
        h('p', { class: 'ws-note', style: { marginTop: '10px' } }, 'Log workouts, meals and weigh-ins from Today or Health; they appear here.'))));
}

function section(title, items) {
  return h('div', { style: { marginTop: '12px' } }, h('div', { class: 'ws-note', style: { fontWeight: 600 } }, title),
    items.length ? h('ul', { class: 'ws-list' }, items.map(t => h('li', { class: 'ws-row', style: { padding: '6px 0' } }, t)))
                 : h('div', { class: 'ws-note' }, 'None'));
}
