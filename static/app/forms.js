// One editor per kind of record, used wherever that record can be logged
// (Today, Health, Journal). Each resolves true when something was saved.
import { h, api, dialog, field, values, toast, isoToday } from './lib.js';

const WORKOUT_TYPES = ['Strength', 'Cardio', 'Mobility / recovery', 'Dog walk', 'Other'];
const SLOTS = [['breakfast', 'Breakfast'], ['lunch', 'Lunch'], ['dinner', 'Dinner'], ['snack', 'Snack'], ['meal', 'Other']];

async function edit(title, form, save) {
  // Keep the dialog open while the save is in flight; show errors inline.
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  for (;;) {
    const choice = await dialog(title, h('div', {}, form, error), [['Cancel', null], ['Save', true]]);
    if (!choice) return false;
    try { await save(values(form)); return true; }
    catch (e) { error.textContent = e.message; }
  }
}

function form(...fields) {
  return h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() }, fields);
}

export async function workout(date, record = null) {
  const r = record || { workout_date: date, workout_type: 'Strength' };
  const f = form(
    field('Date', 'workout_date', { kind: 'date', value: r.workout_date, required: true }),
    field('Type', 'workout_type', { kind: 'select', options: WORKOUT_TYPES, value: r.workout_type }),
    field('Minutes', 'minutes', { kind: 'number', value: r.minutes ?? '', min: 0, max: 1440, step: 1 }),
    field('Activity', 'activity', { value: r.activity, placeholder: 'e.g. Run, Bike, Upper body' }),
    field('Note', 'note', { value: r.note, wide: true }));
  return edit(record ? 'Edit workout' : 'Log a workout', f, body => record
    ? api(`/api/v1/health/workouts/${record.workout_id}`, { method: 'PUT', body })
    : api('/api/v1/health/workouts', { method: 'POST', body }));
}

let foodsCache = null;
async function foods() {
  foodsCache = foodsCache || await api('/api/v1/health/foods');
  return foodsCache;
}

export async function meal(date, record = null) {
  const r = record || { meal_date: date, slot: guessSlot(), status: 'eaten' };
  const list = await foods();
  const foodSelect = field('From a saved food (fills nutrition)', 'food_id', { kind: 'select', value: r.food_id || '',
    options: [['', '— none —'], ...list.map(x => [x.food_id, `${x.name}${x.unit ? ' · ' + x.unit : ''}${x.calories != null ? ' · ' + Math.round(x.calories) + ' kcal' : ''}`])], wide: true });
  const f = form(
    field('Date', 'meal_date', { kind: 'date', value: r.meal_date, required: true }),
    field('Meal', 'slot', { kind: 'select', options: SLOTS, value: r.slot }),
    field('Status', 'status', { kind: 'select', options: [['eaten', 'Eaten'], ['planned', 'Planned']], value: r.status }),
    field('What', 'description', { value: r.description, wide: true, placeholder: 'e.g. Chicken salad' }),
    foodSelect,
    field('Servings', 'quantity', { kind: 'number', value: r.quantity ?? '', step: 0.25, min: 0 }),
    field('Calories', 'calories', { kind: 'number', value: r.calories ?? '', min: 0 }),
    field('Protein g', 'protein_g', { kind: 'number', value: r.protein_g ?? '', min: 0 }),
    field('Carbs g', 'carbs_g', { kind: 'number', value: r.carbs_g ?? '', min: 0 }),
    field('Fat g', 'fat_g', { kind: 'number', value: r.fat_g ?? '', min: 0 }),
    field('Fiber g', 'fiber_g', { kind: 'number', value: r.fiber_g ?? '', min: 0 }),
    field('Note', 'note', { value: r.note, wide: true }));
  return edit(record ? 'Edit meal' : 'Log a meal', f, body => {
    if (!body.description && !body.food_id) throw new Error('Describe the meal or pick a saved food.');
    return record ? api(`/api/v1/health/meals/${record.meal_id}`, { method: 'PUT', body })
                  : api('/api/v1/health/meals', { method: 'POST', body });
  });
}

function guessSlot() {
  const hour = new Date().getHours();
  return hour < 11 ? 'breakfast' : hour < 15 ? 'lunch' : hour < 21 ? 'dinner' : 'snack';
}

export async function weighIn(date, record = null) {
  const r = record || { measured_on: date, unit: 'lb', is_morning: new Date().getHours() < 11 };
  const f = form(
    field('Date', 'measured_on', { kind: 'date', value: r.measured_on, required: true }),
    field('Weight', 'value', { kind: 'number', value: r.value ?? '', step: 0.1, min: 20, required: true }),
    field('Unit', 'unit', { kind: 'select', options: [['lb', 'lb'], ['kg', 'kg']], value: r.unit }),
    field('Morning reading (drives the trend)', 'is_morning', { kind: 'checkbox', value: r.is_morning }),
    field('Note', 'note', { value: r.note, wide: true }));
  return edit(record ? 'Edit weigh-in' : 'Log a weigh-in', f, body => {
    if (!record && body.measured_on === isoToday()) body.measured_at = new Date().toISOString();
    return record ? api(`/api/v1/health/weigh-ins/${record.measurement_id}`, { method: 'PUT', body })
                  : api('/api/v1/health/weigh-ins', { method: 'POST', body });
  });
}

export async function drink(date, record = null) {
  const r = record || { drink_date: date, containers: 1, oz_per_container: 12, abv_pct: 5 };
  const f = form(
    field('Date', 'drink_date', { kind: 'date', value: r.drink_date, required: true }),
    field('What', 'name', { value: r.name, placeholder: 'e.g. IPA' }),
    field('How many', 'containers', { kind: 'number', value: r.containers ?? 1, step: 0.5, min: 0, required: true }),
    field('Ounces each', 'oz_per_container', { kind: 'number', value: r.oz_per_container ?? '', step: 0.5, min: 0 }),
    field('ABV %', 'abv_pct', { kind: 'number', value: r.abv_pct ?? '', step: 0.1, min: 0, max: 100 }),
    field('Calories', 'calories', { kind: 'number', value: r.calories ?? '', min: 0 }));
  return edit(record ? 'Edit drink' : 'Log a drink', f, body => record
    ? api(`/api/v1/health/drinks/${record.drink_id}`, { method: 'PUT', body })
    : api('/api/v1/health/drinks', { method: 'POST', body }));
}

export async function remove(kind, id, label) {
  const ok = await dialog(`Delete this ${label}?`, h('p', {}, 'This cannot be undone.'), [['Cancel', false], ['Delete', true]]);
  if (!ok) return false;
  await api(`/api/v1/health/${kind}/${id}`, { method: 'DELETE' });
  return true;
}

// Quick-add buttons: the same editors from any screen. onSaved re-renders the caller.
export function quickAdd(date, onSaved) {
  const go = fn => async () => { if (await fn(date)) { toast('Saved'); onSaved(); } };
  return h('div', { class: 'ws-quick' },
    h('button', { class: 'btn', onclick: go(workout) }, '+ Workout'),
    h('button', { class: 'btn', onclick: go(meal) }, '+ Meal'),
    h('button', { class: 'btn', onclick: go(weighIn) }, '+ Weigh-in'),
    h('button', { class: 'btn', onclick: go(drink) }, '+ Drink'));
}

// Rows for a day's records with edit/delete, shared by Today and Health.
export function recordRow(kind, r, onChange) {
  const spec = {
    workouts: { id: r.workout_id, label: 'workout', editor: workout, date: r.workout_date,
                title: `${r.activity || r.workout_type}${r.minutes != null ? ' · ' + Math.round(r.minutes) + ' min' : ''}`,
                meta: [r.activity ? r.workout_type : null, r.is_dog_walk ? "doesn't count toward the workout goal" : null, r.note].filter(Boolean).join(' · ') },
    meals: { id: r.meal_id, label: 'meal', editor: meal, date: r.meal_date,
             title: r.description || '(meal)',
             meta: [cap(r.slot), r.status === 'planned' ? 'planned' : null, r.calories != null ? Math.round(r.calories) + ' kcal' : 'calories unknown',
                    r.protein_g != null ? Math.round(r.protein_g) + ' g protein' : null].filter(Boolean).join(' · ') },
    'weigh-ins': { id: r.measurement_id, label: 'weigh-in', editor: weighIn, date: r.measured_on,
                   title: `${r.value} ${r.unit}`, meta: [r.is_morning ? 'morning' : 'reference', r.note].filter(Boolean).join(' · ') },
    drinks: { id: r.drink_id, label: 'drink', editor: drink, date: r.drink_date,
              title: `${r.containers} × ${r.name || 'drink'}`,
              meta: [r.standard_drinks != null ? r.standard_drinks + ' standard drinks' : null, r.calories != null ? Math.round(r.calories) + ' kcal' : null].filter(Boolean).join(' · ') },
  }[kind];
  return h('li', { class: 'ws-row' },
    h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, spec.title), spec.meta ? h('div', { class: 'ws-row-meta' }, spec.meta) : null),
    h('div', { class: 'ws-row-end' },
      h('button', { class: 'btn small', onclick: async () => { if (await spec.editor(spec.date, r)) onChange(); } }, 'Edit'),
      h('button', { class: 'btn small danger', 'aria-label': 'Delete ' + spec.label, onclick: async () => { if (await remove(kind, spec.id, spec.label)) onChange(); } }, '✕')));
}

export function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : ''; }
