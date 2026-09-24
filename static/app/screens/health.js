// Health: Overview · Activity · Food · Body · Goals, over the shared tables.
import { h, api, fmt, card, stat, pageHead, tabs, isoToday, addDays, empty, toast, run, field, values } from '../lib.js';
import { quickAdd, recordRow, meal as mealEditor, cap } from '../forms.js';

const TABS = [['/app/health', 'Overview'], ['/app/health/activity', 'Activity'], ['/app/health/food', 'Food'],
              ['/app/health/body', 'Body'], ['/app/health/goals', 'Goals']];

export async function render(view, ctx) {
  const tab = ctx.path.split('/')[3] || 'overview';
  const redraw = () => { view.replaceChildren(); render(view, ctx); };
  const head = pageHead('Health', {
    overview: 'Progress against your goals', activity: 'Workouts by day and week', food: 'Meals, drinks and saved foods',
    body: 'Weigh-ins and the trend', goals: 'Targets and the profile they come from' }[tab], quickAdd(isoToday(), redraw));
  view.append(head, tabs(TABS, ctx.path));
  const screens = { overview, activity, food, body, goals };
  await (screens[tab] || overview)(view, redraw, ctx);
}

// ── Overview ─────────────────────────────────────────────────────────────────
async function overview(view) {
  const d = await api('/api/v1/health/overview');
  const goals = Object.fromEntries(d.goals.map(g => [g.metric, g]));
  const week = d.weeks[0] || {};
  const today = d.days.find(x => x.day === isoToday()) || {};
  const latest = d.weights[0];
  view.append(
    h('div', { class: 'ws-stats' },
      stat('Workout days this week', `${week.qualifying_days ?? 0} / ${fmt.num(goals.workout_days_per_week?.target ?? 5)}`,
           `${fmt.num(week.workout_minutes ?? 0)} min · ${fmt.num(goals.qualifying_workout_minutes?.target ?? 45)}+ min days count`, week.goal_met ? 'good' : null),
      stat('Weight', latest ? `${fmt.num(latest.morning_avg_7d ?? latest.weight, 1)} lb` : '—',
           latest ? `7-day morning average · BMI ${fmt.num(latest.bmi, 1)}${goals.weight ? ' · goal ' + fmt.num(goals.weight.target, 1) : ''}` : 'log a morning weigh-in'),
      stat('Logged calories today', today.total_calories != null ? fmt.num(today.total_calories) : '—',
           `target ${fmt.num(today.calorie_target ?? goals.daily_calories?.target)} · ${today.nutrition_complete ? 'day complete' : 'partial log'}`),
      stat('Drinks this week', fmt.num(week.beers ?? 0, 1), `${week.days_outside_alcohol_rules ?? 0} days outside your rules`,
           (week.days_outside_alcohol_rules ?? 0) > 0 ? 'bad' : null)),
    h('div', { class: 'ws-grid two' },
      card('Last 4 weeks', h('div', { class: 'ws-days', 'aria-label': 'Workout days, last 28 days' },
        [...d.days].reverse().map(x => h('div', { class: 'ws-day' + (x.qualifying_workout_day ? ' hit' : x.workout_minutes > 0 ? ' some' : ''),
          title: `${fmt.day(x.day)} · ${fmt.num(x.workout_minutes)} min` }, x.day.slice(8)))),
        h('p', { class: 'ws-note', style: { marginTop: '8px' } }, 'Green: a qualifying day. Light: some activity. Dog walks are tracked but don’t count.'),
        h('table', { class: 'ws-table', style: { marginTop: '12px' } },
          h('thead', {}, h('tr', {}, h('th', {}, 'Week of'), h('th', { class: 'num' }, 'Days'), h('th', { class: 'num' }, 'Minutes'), h('th', { class: 'num' }, 'Drinks'))),
          h('tbody', {}, d.weeks.slice(0, 6).map(w => h('tr', {},
            h('td', {}, fmt.day(w.week_start)), h('td', { class: 'num' }, `${w.qualifying_days}${w.goal_met ? ' ✓' : ''}`),
            h('td', { class: 'num' }, fmt.num(w.workout_minutes)), h('td', { class: 'num' }, fmt.num(w.beers, 1))))))),
      card('Goals', h('ul', { class: 'ws-list' }, d.goals.map(g => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, goalName(g.metric)),
          h('div', { class: 'ws-row-meta' }, `${directionText(g.direction)} ${fmt.num(g.target, 1)} ${g.unit} per ${g.period}`)),
        h('div', { class: 'ws-row-end' }, h('span', { class: 'ws-amount' }, g.current_value != null ? fmt.num(g.current_value, 1) : '—'),
          g.met == null ? h('span', { class: 'ws-chip' }, 'no data') : h('span', { class: 'ws-chip ' + (g.met ? 'good' : 'warn') }, g.met ? 'on track' : 'not yet')))))),
    ));
}

export function goalName(metric) {
  return { workout_days_per_week: 'Workout days a week', qualifying_workout_minutes: 'Minutes for a day to count',
           beers_per_day: 'Drinks on a drinking day', weight: 'Weight', bmi: 'BMI', daily_calories: 'Daily calories' }[metric] || metric;
}
function directionText(d) { return { at_least: 'At least', at_most: 'At most', below: 'Fewer than' }[d] || ''; }

// ── Activity ─────────────────────────────────────────────────────────────────
async function activity(view, redraw) {
  const from = addDays(isoToday(), -60);
  const [workouts, overviewData] = await Promise.all([api('/api/v1/health/workouts?from=' + from), api('/api/v1/health/overview')]);
  view.append(h('div', { class: 'ws-grid two' },
    card('Weeks', h('table', { class: 'ws-table' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Week of'), h('th', { class: 'num' }, 'Qualifying days'), h('th', { class: 'num' }, 'Minutes'), h('th', { class: 'num' }, 'Dog walks'))),
      h('tbody', {}, overviewData.weeks.map(w => h('tr', {}, h('td', {}, fmt.day(w.week_start)),
        h('td', { class: 'num' }, `${w.qualifying_days} / ${fmt.num(w.target_days)}${w.goal_met ? ' ✓' : ''}`),
        h('td', { class: 'num' }, fmt.num(w.workout_minutes)), h('td', { class: 'num' }, fmt.num(w.dog_walk_minutes))))))),
    card('Workouts · last 60 days', workouts.length
      ? h('ul', { class: 'ws-list' }, groupByDay(workouts, 'workout_date').map(([day, rows]) => [
          h('li', { class: 'ws-note', style: { fontWeight: 600, paddingTop: '10px' } }, fmt.day(day)),
          rows.map(r => recordRow('workouts', r, redraw))]))
      : empty('No workouts in the last 60 days.'))));
}

function groupByDay(rows, key) {
  const map = new Map();
  for (const r of rows) { if (!map.has(r[key])) map.set(r[key], []); map.get(r[key]).push(r); }
  return [...map];
}

// ── Food ─────────────────────────────────────────────────────────────────────
async function food(view, redraw, { params }) {
  const date = params.get('date') || isoToday();
  const [meals, drinks, day, foods, recipes, shopping] = await Promise.all([
    api('/api/v1/health/meals?date=' + date), api('/api/v1/health/drinks?date=' + date),
    api(`/api/v1/health/days?from=${date}&to=${date}`), api('/api/v1/health/foods'), api('/api/v1/health/recipes'),
    api('/api/v1/health/shopping')]);
  const d = day[0] || {};
  const complete = h('input', { type: 'checkbox', checked: !!d.nutrition_complete, id: 'day-complete' });
  complete.onchange = () => run(complete, async () => {
    await api(`/api/v1/health/days/${date}/complete`, { method: 'PUT', body: { complete: complete.checked } });
    redraw();
  });
  const nav = h('div', { class: 'ws-daynav' },
    h('a', { class: 'btn small', href: '/app/health/food?date=' + addDays(date, -1), 'aria-label': 'Previous day' }, '←'),
    h('strong', {}, fmt.day(date)),
    h('a', { class: 'btn small', href: '/app/health/food?date=' + addDays(date, 1), 'aria-label': 'Next day' }, '→'));
  view.append(
    h('div', { class: 'ws-filters' }, nav),
    h('div', { class: 'ws-stats' },
      stat('Logged calories', d.total_calories != null ? fmt.num(d.total_calories) : '—',
           `target ${fmt.num(d.calorie_target)}${d.alcohol_calories ? ' · includes ' + fmt.num(d.alcohol_calories) + ' from drinks' : ''}`),
      stat('Protein', d.protein_g != null ? fmt.num(d.protein_g) + ' g' : '—', `carbs ${fmt.num(d.carbs_g)} g · fat ${fmt.num(d.fat_g)} g · fiber ${fmt.num(d.fiber_g)} g`),
      stat('Planned', fmt.num(d.planned_calories ?? 0), `${d.meals_planned ?? 0} planned meals`)),
    h('label', { class: 'ws-check', for: 'day-complete' }, complete,
      h('span', {}, 'I logged everything I ate this day (otherwise totals are a partial log, not a low-calorie day)')),
    h('div', { class: 'ws-grid two' },
      card(h('span', {}, 'Meals', h('button', { class: 'btn small', onclick: async () => { if (await mealEditor(date)) redraw(); } }, '+ Meal')),
        meals.length ? h('ul', { class: 'ws-list' }, meals.map(m => recordRow('meals', m, redraw))) : empty('No meals logged for this day.')),
      card('Drinks', drinks.length ? h('ul', { class: 'ws-list' }, drinks.map(x => recordRow('drinks', x, redraw))) : empty('No drinks this day.'))),
    h('div', { class: 'ws-grid two' },
      foodLibrary(foods, redraw),
      card('Recipes', recipes.length ? h('ul', { class: 'ws-list' }, recipes.map(r => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, r.name),
          h('div', { class: 'ws-row-meta' }, r.ingredients.map(i => `${i.quantity} × ${i.name}`).join(', '))),
        h('div', { class: 'ws-row-end' }, h('span', { class: 'ws-amount' }, fmt.num(r.calories) + ' kcal'))))) : empty('No recipes yet.'))),
    card(h('span', {}, 'Shopping list', h('span', { class: 'ws-note' }, 'read-only · edited in the Finance tracker until Money replaces it')),
      shopping.length ? h('ul', { class: 'ws-list' }, shopping.map(s => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title', style: s.done ? { textDecoration: 'line-through', opacity: .6 } : null }, s.name),
          h('div', { class: 'ws-row-meta' }, [s.quantity_text, s.note].filter(Boolean).join(' · '))),
        s.price_cap != null ? h('span', { class: 'ws-amount' }, 'up to ' + fmt.money(s.price_cap)) : null))) : empty('The shopping list is empty.')));
}

function foodLibrary(foods, redraw) {
  const search = h('input', { class: 'ws-input', type: 'search', placeholder: 'Search saved foods', 'aria-label': 'Search saved foods' });
  const list = h('ul', { class: 'ws-list' });
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = foods.filter(f => !q || f.name.toLowerCase().includes(q)).slice(0, 40);
    list.replaceChildren(...shown.map(f => h('li', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, f.name),
        h('div', { class: 'ws-row-meta' }, [f.unit, f.protein_g != null ? fmt.num(f.protein_g) + ' g protein' : null].filter(Boolean).join(' · '))),
      h('span', { class: 'ws-amount' }, f.calories != null ? fmt.num(f.calories) + ' kcal' : '—'),
      h('button', { class: 'btn small', onclick: () => editFood(f, redraw) }, 'Edit'))));
    if (!shown.length) list.replaceChildren(h('li', { class: 'ws-note' }, 'No foods match.'));
  };
  search.oninput = draw;
  draw();
  return card(h('span', {}, `Saved foods (${foods.length})`, h('button', { class: 'btn small', onclick: () => editFood(null, redraw) }, '+ Food')),
    search, list);
}

async function editFood(food, redraw) {
  const { dialog } = await import('../lib.js');
  const f = food || {};
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Name', 'name', { value: f.name, required: true, wide: true }), field('Serving', 'unit', { value: f.unit, placeholder: 'e.g. 1 cup' }),
    field('Calories', 'calories', { kind: 'number', value: f.calories ?? '' }), field('Protein g', 'protein_g', { kind: 'number', value: f.protein_g ?? '' }),
    field('Carbs g', 'carbs_g', { kind: 'number', value: f.carbs_g ?? '' }), field('Fat g', 'fat_g', { kind: 'number', value: f.fat_g ?? '' }),
    field('Fiber g', 'fiber_g', { kind: 'number', value: f.fiber_g ?? '' }), field('Label note', 'reference_note', { value: f.reference_note, wide: true }));
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  for (;;) {
    const choice = await dialog(food ? 'Edit food' : 'Save a food', h('div', {}, form, error), [['Cancel', null], ['Save', true]]);
    if (!choice) return;
    try {
      const body = values(form);
      if (food) await api('/api/v1/health/foods/' + encodeURIComponent(food.food_id), { method: 'PUT', body });
      else await api('/api/v1/health/foods', { method: 'POST', body });
      toast('Food saved');
      redraw();
      return;
    } catch (e) { error.textContent = e.message; }
  }
}

// ── Body ─────────────────────────────────────────────────────────────────────
async function body(view, redraw) {
  const [weighIns, overviewData] = await Promise.all([api('/api/v1/health/weigh-ins?limit=120'), api('/api/v1/health/overview')]);
  const trend = [...overviewData.weights].reverse();
  view.append(h('div', { class: 'ws-grid two' },
    card('Trend', trend.length ? chart(trend) : empty('Log morning weigh-ins to see the trend.'),
      h('p', { class: 'ws-note', style: { marginTop: '8px' } },
        'The line is the 7-day average of morning readings; dots are each day’s latest reading. Other readings are kept as references.')),
    card('Weigh-ins', weighIns.length ? h('ul', { class: 'ws-list' }, groupByDay(weighIns, 'measured_on').map(([day, rows]) => [
      h('li', { class: 'ws-note', style: { fontWeight: 600, paddingTop: '10px' } }, fmt.day(day)),
      rows.map(r => recordRow('weigh-ins', r, redraw))])) : empty('No weigh-ins yet.'))));
}

function chart(points) {
  const W = 560, H = 200, pad = 28;
  const values = points.flatMap(p => [p.weight, p.morning_avg_7d]).filter(v => v != null);
  const min = Math.min(...values) - 1, max = Math.max(...values) + 1;
  const x = i => pad + (i * (W - pad * 2)) / Math.max(points.length - 1, 1);
  const y = v => H - pad - ((v - min) * (H - pad * 2)) / (max - min || 1);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Weight from ${points[0].day} to ${points[points.length - 1].day}`);
  svg.style.width = '100%';
  const line = points.map((p, i) => p.morning_avg_7d != null ? `${x(i)},${y(p.morning_avg_7d)}` : null).filter(Boolean).join(' ');
  svg.innerHTML = `<polyline fill="none" stroke="#109ACC" stroke-width="2.5" points="${line}"/>` +
    points.map((p, i) => p.weight != null ? `<circle cx="${x(i)}" cy="${y(p.weight)}" r="2.6" fill="#8a9ab0"/>` : '').join('') +
    `<text x="4" y="${y(max - 1) + 4}" font-size="11" fill="#6b7a90">${(max - 1).toFixed(0)}</text>` +
    `<text x="4" y="${y(min + 1) + 4}" font-size="11" fill="#6b7a90">${(min + 1).toFixed(0)}</text>`;
  return svg;
}

// ── Goals ────────────────────────────────────────────────────────────────────
async function goals(view, redraw) {
  const [goalList, profile] = await Promise.all([api('/api/v1/health/goals'), api('/api/v1/health/profile')]);
  const days = [[1, 'Mon'], [2, 'Tue'], [3, 'Wed'], [4, 'Thu'], [5, 'Fri'], [6, 'Sat'], [7, 'Sun']];
  const chosen = new Set(profile.alcohol_days || []);
  const dayButtons = h('div', { class: 'ws-chips' }, days.map(([n, label]) => {
    const b = h('button', { type: 'button', class: 'ws-chip', 'aria-pressed': String(chosen.has(n)) }, label);
    b.onclick = () => { chosen.has(n) ? chosen.delete(n) : chosen.add(n); b.setAttribute('aria-pressed', String(chosen.has(n))); };
    return b;
  }));
  const form = h('form', { class: 'ws-form' },
    field('Height (inches)', 'height_in', { kind: 'number', value: profile.height_in ?? '', step: 0.5 }),
    field('Age', 'age', { kind: 'number', value: profile.age ?? '' }),
    field('Goal', 'mode', { kind: 'select', value: profile.mode || 'maintain', options: [['loss', 'Lose weight'], ['maintain', 'Maintain'], ['gain', 'Gain']] }),
    field('BMI goal', 'bmi_goal', { kind: 'number', value: profile.bmi_goal ?? '', step: 0.1 }),
    field('Daily calorie target', 'calorie_target', { kind: 'number', value: profile.calorie_target ?? '', step: 10 }),
    field('Drinks on a drinking day: fewer than', 'drink_boundary', { kind: 'number', value: profile.drink_boundary ?? 3, step: 1 }),
    h('div', { class: 'ws-field wide' }, h('span', {}, 'Days drinking is fine'), dayButtons),
    h('button', { class: 'btn primary', type: 'submit' }, 'Save profile'));
  form.onsubmit = event => {
    event.preventDefault();
    run(form.querySelector('button[type=submit]'), async () => {
      await api('/api/v1/health/profile', { method: 'PUT', body: { ...values(form), alcohol_days: [...chosen].sort() } });
      toast('Profile saved; weight, BMI and calorie goals updated');
      redraw();
    });
  };
  view.append(h('div', { class: 'ws-grid two' },
    card('Profile', h('p', { class: 'ws-note', style: { marginBottom: '10px' } },
      'Weight, BMI and daily-calorie goals follow from these. Changes are kept in the profile history.'), form),
    card('Goals', h('ul', { class: 'ws-list' }, goalList.map(g => {
      const input = h('input', { class: 'ws-input', type: 'number', step: 'any', value: g.target, style: { width: '96px' }, 'aria-label': goalName(g.metric) + ' target' });
      return h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, goalName(g.metric)),
          h('div', { class: 'ws-row-meta' }, g.editable ? `${directionText(g.direction)} ${g.unit} per ${g.period}` : `Follows your profile (${g.derived_from})`)),
        g.editable
          ? h('div', { class: 'ws-row-end' }, input, h('button', { class: 'btn small', onclick: ev => run(ev.target, async () => {
              await api('/api/v1/health/goals/' + g.metric, { method: 'PUT', body: { target: input.value } }); toast('Goal saved'); redraw(); }) }, 'Save'))
          : h('span', { class: 'ws-amount' }, `${fmt.num(g.target, 1)} ${g.unit}`));
    })))));
}

export { cap };
