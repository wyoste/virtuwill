// Today: one day on one screen — meals (B | L | D | S), journal, habits, activity, weight, money.
import { h, api, fmt, card, stat, pageHead, isoToday, addDays, empty, toast } from '../lib.js';
import { quickAdd, recordRow, mealColumns } from '../forms.js';
import { dayMoney } from '../moneyparts.js';

export async function render(view, { params, navigate }) {
  const date = params.get('date') || isoToday();
  const data = await api('/api/v1/today?date=' + date);
  const reload = () => render(view.replaceChildren() || view, { params, navigate });
  const isToday = date === isoToday();
  const a = data.activity || {};
  const goals = Object.fromEntries(data.goals.map(g => [g.metric, g]));
  const week = data.week || {};

  const dayNav = h('div', { class: 'ws-daynav' },
    h('a', { class: 'btn small', href: '/app?date=' + addDays(date, -1), 'aria-label': 'Previous day' }, '←'),
    // Jump straight to any day: the browser's calendar (a date wheel on a phone).
    h('input', { type: 'date', class: 'ws-input ws-daypick', value: date, min: '1900-01-01', max: '2100-12-31', 'aria-label': 'Go to a date',
                 'data-untracked': '', onchange: e => { if (e.target.value && e.target.value !== date) navigate('/app?date=' + e.target.value); } }),
    isToday ? null : h('a', { class: 'btn small', href: '/app' }, 'Today'),
    h('a', { class: 'btn small', href: '/app?date=' + addDays(date, 1), 'aria-label': 'Next day' }, '→'));

  const calories = a.total_calories;
  const target = a.calorie_target ?? goals.daily_calories?.target;
  view.append(
    pageHead(isToday ? 'Today' : fmt.day(date), fmt.longDay(date), dayNav),
    quickAdd(date, reload),
    h('div', { class: 'ws-stats' },
      stat('Workout days this week', `${week.qualifying_days ?? 0} / ${fmt.num(week.target_days ?? goals.workout_days_per_week?.target ?? 5)}`,
           `${fmt.num(a.workout_minutes ?? 0)} min today · ${a.qualifying_workout_day ? 'counts' : `${fmt.num(goals.qualifying_workout_minutes?.target ?? 45)}+ min counts`}`,
           week.goal_met ? 'good' : null),
      stat('Logged calories', calories != null ? fmt.num(calories) : '—',
           (target ? `target ${fmt.num(target)}` : 'no target set') + ' · ' + (a.nutrition_complete ? 'day complete' : 'day not marked complete'),
           calories != null && target && a.nutrition_complete ? (calories <= target ? 'good' : 'bad') : null),
      stat('Weight', a.weight != null ? `${fmt.num(a.weight, 1)} lb` : '—',
           goals.weight ? `7-day morning avg ${fmt.num(goals.weight.current_value, 1)} · goal ${fmt.num(goals.weight.target, 1)}` : 'morning readings drive the trend'),
      stat(isToday ? 'Spent today' : 'Spent this day', fmt.money(data.money.spend.day),
           `${fmt.money(data.money.spend.week)} this week · ` + (data.money.through ? `bank data through ${fmt.day(data.money.through)}` : 'no bank data yet'))),
  );

  // Meals, B | L | D | S, as on the journal page
  view.append(h('div', { class: 'ws-today-meals' }, mealColumns(date, data.meals, reload)));

  // Journal and habits
  const entry = data.entry;
  const habitRow = h('div', { class: 'ws-habits' }, data.habits.map(habit => {
    const btn = h('button', { class: 'ws-habit' + (habit.polarity === 'limit' ? ' limit' : ''), 'aria-pressed': String(habit.done),
      title: habit.origin === 'derived' ? 'Ticked from what you logged today; click to set it yourself' : null },
      habit.label, habit.origin === 'derived' ? h('small', {}, 'auto') : null);
    btn.onclick = async () => {
      // A habit belongs to the day: setting one needs no journal entry.
      try { await api(`/api/v1/days/${date}/habits`, { method: 'PUT', body: { [habit.habit]: !habit.done } }); reload(); }
      catch (e) { toast(e.message, 'error'); }
    };
    return btn;
  }));
  view.append(h('div', { class: 'ws-grid two' },
    card(h('span', {}, 'Journal', h('a', { class: 'btn small', href: '/app/journal/' + date }, entry ? 'Open entry' : 'Write today’s entry')),
      entry
        ? h('div', {},
            entry.quote ? h('p', { class: 'ws-note' }, `“${entry.quote}”${entry.quoteAuthor ? ' — ' + entry.quoteAuthor : ''}`) : null,
            h('p', { class: 'ws-entry-body' }, plainText(entry.freeWrite).slice(0, 420) + (plainText(entry.freeWrite).length > 420 ? '…' : '')))
        : h('p', { class: 'ws-note' }, 'No entry for this day yet.'),
      h('h3', { class: 'ws-note', style: { margin: '14px 0 8px', fontWeight: 600 } }, 'Habits'),
      habitRow,
      h('p', { class: 'ws-note', style: { marginTop: '8px' } }, 'Run, lift and drink tick themselves from what you log; anything you set yourself wins.')),
    card('Logged', logged(data, reload))));

  // Money: where the accounts stood, what was spent, and this day's transactions
  view.append(dayMoney(data.money, date, { onChange: reload }));
}

function logged(data, reload) {
  // Meals have their own columns above.
  const groups = [['workouts', 'Workouts', data.workouts], ['weigh-ins', 'Weigh-ins', data.weighIns], ['drinks', 'Drinks', data.drinks]];
  const any = groups.some(([, , rows]) => rows.length);
  if (!any) return empty('Nothing logged yet. Use the buttons above.');
  return h('div', {}, groups.filter(([, , rows]) => rows.length).map(([kind, label, rows]) => h('div', {},
    h('h3', { class: 'ws-note', style: { fontWeight: 600, margin: '6px 0 0' } }, label),
    h('ul', { class: 'ws-list' }, rows.map(r => recordRow(kind, r, reload))))));
}

export function plainText(html) {
  // DOMParser builds an inert document: nothing in it loads or runs.
  return (new DOMParser().parseFromString(html || '', 'text/html').body.textContent || '').trim();
}
