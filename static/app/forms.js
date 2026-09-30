// One editor per kind of record, used wherever that record can be logged
// (Today, Health, Journal). Each resolves true when something was saved.
import { h, api, dialog, field, values, toast, isoToday, confirmDelete } from './lib.js';

const WORKOUT_TYPES = ['Strength', 'Cardio', 'HIIT', 'Mobility / recovery', 'Dog walk', 'Other'];
const DISTANCE_TYPES = new Set(['Cardio', 'Dog walk']);      // the only workouts with a distance or a route
const CIRCUIT = [['rounds', 'Rounds', 1, 100], ['exercises_per_round', 'Exercises per round', 1, 50],
                 ['work_seconds', 'Seconds per exercise', 1, 3600], ['exercise_rest_seconds', 'Rest between exercises (s)', 0, 3600],
                 ['round_rest_seconds', 'Rest between rounds (s)', 0, 3600]];
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

// The length of a HIIT circuit in seconds: work and rest within each round, rest between rounds.
export function circuitSeconds(c) {
  if (!c?.rounds || !c?.exercises_per_round || !c?.work_seconds) return null;
  const n = c.exercises_per_round;
  return c.rounds * (n * c.work_seconds + (n - 1) * (c.exercise_rest_seconds || 0)) + (c.rounds - 1) * (c.round_rest_seconds || 0);
}
const clock = s => `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ''}`;
export function circuitText(c) {
  if (!c) return null;
  return [`${c.rounds} × ${c.exercises_per_round} exercises`, `${c.work_seconds}s on`,
          c.exercise_rest_seconds ? `${c.exercise_rest_seconds}s rest` : null,
          c.round_rest_seconds ? `${c.round_rest_seconds}s between rounds` : null].filter(Boolean).join(' · ');
}
export const liftsText = lifts => (lifts || []).map(l => `${l.lift} ${l.sets}×`).join(', ');

// Strength: pick lifts (a filtered checklist, plus any lift typed in), then the sets of each.
function liftPicker(catalog, chosen) {
  const picked = new Map(chosen.map(l => [l.lift, l.sets]));
  const list = h('div', { class: 'ws-lift-options', role: 'group', 'aria-label': 'Lifts' });
  const sets = h('div', { class: 'ws-lift-sets' });
  const filter = h('input', { type: 'search', class: 'ws-input', placeholder: 'Filter lifts', 'aria-label': 'Filter lifts', 'data-untracked': '' });
  const extra = h('input', { class: 'ws-input', placeholder: 'A lift not listed', 'aria-label': 'Add a lift', 'data-untracked': '' });
  const all = () => [...new Set([...catalog.map(c => c.lift), ...picked.keys()])];
  function drawOptions() {
    const q = filter.value.trim().toLowerCase();
    const groups = new Map();
    for (const c of catalog) if (!q || c.lift.toLowerCase().includes(q)) groups.set(c.muscle_group || 'Added', [...(groups.get(c.muscle_group || 'Added') || []), c.lift]);
    for (const name of picked.keys()) if (!catalog.some(c => c.lift === name) && (!q || name.toLowerCase().includes(q))) groups.set('Added', [...(groups.get('Added') || []), name]);
    list.replaceChildren(...[...groups].map(([group, names]) => h('fieldset', {}, h('legend', {}, group), names.map(name =>
      h('label', { class: 'ws-check' }, h('input', { type: 'checkbox', checked: picked.has(name), 'data-untracked': '',
        onchange: e => { if (e.target.checked) picked.set(name, 3); else picked.delete(name); drawSets(); } }), h('span', {}, name))))),
      groups.size ? '' : h('p', { class: 'ws-note' }, 'No lift matches. Add it below.'));
  }
  function drawSets() {
    sets.replaceChildren(...(picked.size ? [...picked].map(([name, n]) => h('div', { class: 'ws-lift-row' },
      h('span', {}, name),
      h('label', {}, 'Sets ', h('input', { type: 'number', class: 'ws-input', min: 1, max: 50, step: 1, value: n, 'aria-label': `Sets of ${name}`,
        oninput: e => picked.set(name, e.target.value) })),
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Remove ${name}`, onclick: () => { picked.delete(name); drawSets(); drawOptions(); } }, '×')))
      : [h('p', { class: 'ws-note' }, 'Tick the lifts you did, then set the sets of each.')]));
  }
  function add() {
    const name = extra.value.trim().replace(/\s+/g, ' ');
    if (!name) return;
    const match = all().find(x => x.toLowerCase() === name.toLowerCase()) || name;
    if (!picked.has(match)) picked.set(match, 3);
    extra.value = ''; drawOptions(); drawSets();
  }
  filter.oninput = drawOptions;
  extra.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); add(); } };
  drawOptions(); drawSets();
  return {
    el: h('div', { class: 'ws-field wide ws-lifts' }, h('span', {}, 'Lifts'), filter, list,
      h('div', { class: 'ws-lift-add' }, extra, h('button', { type: 'button', class: 'btn small', onclick: add }, 'Add')), sets),
    value: () => [...picked].map(([lift, n]) => ({ lift, sets: n })),
  };
}

// A cardio workout or dog walk can carry its distance and the route it took (a GPX or TCX file from
// Strava, Garmin Connect or a running app); the route fills in a blank distance and time.
// A strength workout lists its lifts and sets; a HIIT workout describes its circuit.
export async function workout(date, record = null) {
  const r = record || { workout_date: date, workout_type: 'Strength' };
  const catalog = await api('/api/v1/health/lifts').catch(() => []);
  const route = h('input', { type: 'file', accept: '.gpx,.tcx,application/gpx+xml,application/vnd.garmin.tcx+xml', 'aria-label': 'Route file' });
  const type = field('Type', 'workout_type', { kind: 'select', options: WORKOUT_TYPES, value: r.workout_type });
  const minutes = field('Minutes', 'minutes', { kind: 'number', value: r.minutes ?? '', min: 0, max: 1440, step: 1 });
  const lifts = liftPicker(catalog, r.lifts || []);
  const total = h('p', { class: 'ws-note wide', 'aria-live': 'polite' });
  const circuitFields = CIRCUIT.map(([key, label, min, max]) => field(label, 'circuit_' + key, { kind: 'number', min, max, step: 1,
    value: r.circuit?.[key] ?? (min === 0 ? 0 : '') }));
  const part = (...children) => h('div', { class: 'ws-part' }, children);
  const distancePart = part(
    field('Distance', 'distance', { kind: 'number', value: r.distance ?? '', min: 0, max: 1000, step: 0.01, placeholder: 'e.g. 3.1' }),
    field('Unit', 'distance_unit', { kind: 'select', options: [['mi', 'miles'], ['km', 'kilometres']], value: r.distance_unit || 'mi' }),
    h('label', { class: 'ws-field wide' }, h('span', {}, r.has_route ? 'Replace the route (GPX or TCX)' : 'Route map (GPX or TCX file, optional)'), route,
      h('small', { class: 'ws-note' }, 'Export the activity from Strava, Garmin Connect or your running app. A blank distance or time is filled in from it.')));
  const liftPart = part(lifts.el);
  const circuitPart = part(h('p', { class: 'ws-note wide' }, 'The circuit. Leave Minutes blank to use its length.'), ...circuitFields, total);
  const f = form(
    field('Date', 'workout_date', { kind: 'date', value: r.workout_date, required: true }),
    type, minutes,
    field('Activity', 'activity', { value: r.activity, placeholder: 'e.g. Run, Bike, Upper body' }),
    distancePart, liftPart, circuitPart,
    field('Note', 'note', { value: r.note, wide: true }));
  const circuit = () => {
    const v = values(f), c = {};
    for (const [key] of CIRCUIT) c[key] = v['circuit_' + key] === null ? null : Number(v['circuit_' + key]);
    return c;
  };
  const show = () => {
    const kind = type.querySelector('select').value;
    distancePart.hidden = !DISTANCE_TYPES.has(kind);
    liftPart.hidden = kind !== 'Strength';
    circuitPart.hidden = kind !== 'HIIT';
    const secs = circuitSeconds(circuit());
    total.textContent = secs ? `Circuit length: ${clock(secs)}` : 'Fill in rounds, exercises and seconds to see the length.';
  };
  f.addEventListener('input', show);
  f.addEventListener('change', show);
  show();
  // Once the workout exists, a retry (say, after a route file was refused) updates it rather than logging it twice.
  let id = record?.workout_id;
  return edit(record ? 'Edit workout' : 'Log a workout', f, async body => {
    const kind = body.workout_type;
    for (const [key] of CIRCUIT) delete body['circuit_' + key];
    if (!DISTANCE_TYPES.has(kind)) body.distance = null;
    body.lifts = kind === 'Strength' ? lifts.value() : [];
    body.circuit = kind === 'HIIT' ? circuit() : null;
    const saved = await api(id ? `/api/v1/health/workouts/${id}` : '/api/v1/health/workouts', { method: id ? 'PUT' : 'POST', body });
    id = saved.workout_id;
    if (DISTANCE_TYPES.has(kind) && route.files.length) {
      const file = new FormData();
      file.append('file', route.files[0]);
      await api(`/api/v1/health/workouts/${id}/route`, { method: 'POST', form: file });
    }
  });
}

// ── Distance, pace and the route map ────────────────────────────────────────
const isRide = w => /bik|cycl|ride|spin/i.test(`${w.activity} ${w.note}`);
export function pace(w) {
  if (!w.distance || !w.minutes) return null;
  const unit = w.distance_unit || 'mi';
  if (isRide(w)) return `${(w.distance / (w.minutes / 60)).toFixed(1)} ${unit === 'mi' ? 'mph' : 'km/h'}`;
  const per = w.minutes / w.distance;
  return `${Math.floor(per)}:${String(Math.round((per % 1) * 60)).padStart(2, '0')} /${unit}`;
}

let leaflet = null;
function loadLeaflet() {
  leaflet = leaflet || new Promise((resolve, reject) => {
    if (window.L) return resolve(window.L);
    document.head.append(h('link', { rel: 'stylesheet', href: 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' }));
    const s = h('script', { src: 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' });
    s.onload = () => resolve(window.L);
    s.onerror = () => { leaflet = null; reject(new Error('The map couldn’t load. Check your connection.')); };
    document.head.append(s);
  });
  return leaflet;
}

export async function routeMap(w) {
  const route = await api(`/api/v1/health/workouts/${w.workout_id}/route`);
  const unit = w.distance_unit || 'mi';
  const km = Number(route.distance_km);
  const facts = [
    ['Distance', w.distance ? `${w.distance} ${unit}` : `${(unit === 'mi' ? km / 1.609344 : km).toFixed(2)} ${unit}`],
    ['Time', w.minutes ? `${Math.round(w.minutes)} min` : null],
    [isRide(w) ? 'Speed' : 'Pace', pace(w)],
    ['Climbing', route.elevation_gain_m ? `${Math.round(unit === 'mi' ? route.elevation_gain_m * 3.28084 : route.elevation_gain_m)} ${unit === 'mi' ? 'ft' : 'm'}` : null],
    ['Started', route.started_at ? new Date(route.started_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) : null],
  ].filter(([, v]) => v);
  const map = h('div', { class: 'ws-route-map', role: 'img', 'aria-label': 'Map of the route' });
  const body = h('div', {}, map, h('dl', { class: 'ws-route-facts' }, facts.map(([k, v]) => h('div', {}, h('dt', {}, k), h('dd', {}, v)))),
    route.file_name ? h('p', { class: 'ws-note' }, 'From ' + route.file_name) : null);
  const shown = dialog(`${w.activity || w.workout_type} · ${w.workout_date}`, body, [['Close', null]]);
  try {
    const L = await loadLeaflet();
    const m = L.map(map, { scrollWheelZoom: false });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(m);
    const line = L.polyline(route.points, { color: '#1B3F27', weight: 4, opacity: 0.9 }).addTo(m);
    const dot = (at, color, label) => L.circleMarker(at, { radius: 7, color: '#fff', weight: 2, fillColor: color, fillOpacity: 1 }).bindTooltip(label).addTo(m);
    dot(route.points[0], '#2F7A4B', 'Start');
    dot(route.points.at(-1), '#E3B040', 'Finish');
    m.fitBounds(line.getBounds(), { padding: [20, 20] });
    setTimeout(() => m.invalidateSize(), 60);
  } catch (e) { map.replaceChildren(h('p', { class: 'ws-note warn' }, e.message)); }
  await shown;
}

let foodsCache = null;
export function forgetFoods() { foodsCache = null; }   // after a food is added or edited
async function foods() {
  foodsCache = foodsCache || await api('/api/v1/health/foods');
  return foodsCache;
}

// Food groups, in the order lists show them.
export const FOOD_GROUPS = ['Grains', 'Fruit', 'Vegetables', 'Protein', 'Dairy', 'Other'];
export function byGroup(list) {
  const groups = new Map(FOOD_GROUPS.map(g => [g, []]));
  for (const f of list) (groups.get(f.category) || groups.get('Other')).push(f);
  return [...groups].filter(([, fs]) => fs.length);
}

const NUTRIENTS = [['calories', 'kcal', 0], ['protein_g', 'g protein', 0], ['carbs_g', 'g carbs', 0], ['fat_g', 'g fat', 0], ['fiber_g', 'g fiber', 0]];

// Nutrition for `servings` of a saved food.
function scaled(food, servings) {
  return Object.fromEntries(NUTRIENTS.map(([k]) => [k, food[k] == null ? null : Math.round(food[k] * servings * 10) / 10]));
}

function summary(n) {
  const parts = NUTRIENTS.filter(([k]) => n[k] != null).map(([k, label]) => `${Math.round(n[k])} ${label}`);
  return parts.length ? parts.join(' · ') : 'nutrition unknown';
}

let recipesCache = null;
export function forgetRecipes() { recipesCache = null; }   // after a go-to meal is built or edited
async function recipes() {
  recipesCache = recipesCache || await api('/api/v1/health/recipes').catch(() => []);
  return recipesCache;
}

const roundQty = n => Math.round(n * 10000) / 10000;   // a sixth of a batch stays a sixth

// Pick saved foods and set each one's servings. `chosen` (food_id → servings) is edited
// in place; with `optional` (a Set of food_ids) each food can be marked optional too.
// A food with presets (a handful of chips, half a bag) offers them as one-tap amounts.
function foodChooser(list, chosen, { optional = null, onChange = () => {} } = {}) {
  const byId = new Map(list.map(f => [f.food_id, f]));
  const search = h('input', { class: 'ws-input', type: 'search', placeholder: 'Search saved foods', 'aria-label': 'Search saved foods' });
  const group = h('select', { class: 'ws-input ws-group-filter', 'aria-label': 'Food group' },
    h('option', { value: '' }, 'All groups'), ...byGroup(list).map(([g]) => h('option', { value: g }, g)));
  const picker = h('div', { class: 'ws-picker', role: 'group', 'aria-label': 'Saved foods' });
  const rows = h('ul', { class: 'ws-list ws-meal-items' });

  const drawPicker = () => {
    const q = search.value.trim().toLowerCase();
    const shown = list.filter(f => (!q || f.name.toLowerCase().includes(q)) && (!group.value || f.category === group.value)).slice(0, 150);
    picker.replaceChildren(...byGroup(shown).flatMap(([g, fs]) => [h('div', { class: 'ws-pick-group' }, g), ...fs.map(f => {
      const box = h('input', { type: 'checkbox', checked: chosen.has(f.food_id) });
      box.onchange = () => { box.checked ? chosen.set(f.food_id, 1) : (chosen.delete(f.food_id), optional?.delete(f.food_id)); drawRows(); };
      return h('label', { class: 'ws-pick' }, box, h('span', {}, f.name,
        h('small', {}, [f.unit, f.calories != null ? Math.round(f.calories) + ' kcal' : null].filter(Boolean).join(' · '))));
    })]));
    if (!shown.length) picker.replaceChildren(h('p', { class: 'ws-note' }, list.length ? 'No saved foods match.' : 'No saved foods yet — add them in Health › Food.'));
  };

  const drawRows = () => {
    rows.replaceChildren(...[...chosen].map(([id, servings]) => {
      const f = byId.get(id);
      const meta = h('div', { class: 'ws-row-meta' }, summary(scaled(f, servings)));
      const qty = h('input', { class: 'ws-input ws-servings', type: 'number', step: 'any', min: '0.01', value: servings,
                               'aria-label': 'Servings of ' + f.name });
      const set = v => { chosen.set(id, v); meta.textContent = summary(scaled(f, v)); onChange(); };
      qty.oninput = () => { const v = Number(qty.value); if (v > 0) set(v); };
      const presets = (f.presets || []).map(([label, v]) => h('button', { type: 'button', class: 'btn small',
        title: `${v} × ${f.unit}`, onclick: () => { qty.value = v; set(Number(v)); } }, label));
      let flag = null;
      if (optional) {
        const box = h('input', { type: 'checkbox', checked: optional.has(id) });
        box.onchange = () => { box.checked ? optional.add(id) : optional.delete(id); onChange(); };
        flag = h('label', { class: 'ws-check ws-optional' }, box, h('span', {}, 'optional'));
      }
      return h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, f.name), meta,
          presets.length ? h('div', { class: 'ws-presets' }, ...presets) : null),
        h('div', { class: 'ws-row-end' }, flag, qty, h('span', { class: 'ws-note' }, f.unit ? '× ' + f.unit : 'servings'),
          h('button', { type: 'button', class: 'btn small danger', 'aria-label': 'Remove ' + f.name,
                        onclick: () => { chosen.delete(id); optional?.delete(id); drawRows(); drawPicker(); } }, '✕')));
    }));
    onChange();
  };

  search.oninput = drawPicker;
  group.onchange = drawPicker;
  drawPicker();
  drawRows();
  return { el: [h('div', { class: 'ws-usual', dataset: { untracked: '' } }, search, group), picker, rows], redraw: () => { drawPicker(); drawRows(); } };
}

// A meal is one or more saved foods, each with its own servings and nutrition
// (an egg burrito: eggs, tortillas, olive oil). Tick every food at once from the
// list; the meal's totals are their sum. With no foods, nutrition is typed in.
// A go-to meal is a head start: picking one (or opening the editor from it) preselects
// its foods, all still editable. Its optional extras (meatballs, cheese) are one tap
// away. A batch makes several portions (a Mexican scramble makes 6): logging starts at one
// portion, its foods scaled to match, and changing the portions rescales them.
export async function meal(date, record = null, { recipeId = null, slot = null } = {}) {
  const r = record || { meal_date: date, slot: slot || guessSlot(), status: 'eaten' };
  const [list, usualMeals] = await Promise.all([foods(), recipes()]);
  const byId = new Map(list.map(f => [f.food_id, f]));

  // Chosen foods: food_id → servings, in the order ticked.
  const chosen = new Map();
  for (const item of r.items || []) if (item.food_id && byId.has(item.food_id)) chosen.set(item.food_id, Number(item.quantity) || 1);
  if (!chosen.size && r.food_id && byId.has(r.food_id)) chosen.set(r.food_id, Number(r.quantity) || 1);

  const totals = h('p', { class: 'ws-meal-total', 'aria-live': 'polite' });
  const manual = h('details', { class: 'ws-manual' }, h('summary', {}, 'Or enter nutrition by hand'),
    h('div', { class: 'ws-form' },
      field('Calories', 'calories', { kind: 'number', value: r.calories ?? '', min: 0 }),
      field('Protein g', 'protein_g', { kind: 'number', value: r.protein_g ?? '', min: 0 }),
      field('Carbs g', 'carbs_g', { kind: 'number', value: r.carbs_g ?? '', min: 0 }),
      field('Fat g', 'fat_g', { kind: 'number', value: r.fat_g ?? '', min: 0 }),
      field('Fiber g', 'fiber_g', { kind: 'number', value: r.fiber_g ?? '', min: 0 })));
  if (!chosen.size && r.calories != null) manual.open = true;

  const drawTotals = () => {
    const sum = Object.fromEntries(NUTRIENTS.map(([k]) => [k, null]));
    for (const [id, servings] of chosen) {
      const n = scaled(byId.get(id), servings);
      for (const [k] of NUTRIENTS) if (n[k] != null) sum[k] = Math.round(((sum[k] || 0) + n[k]) * 10) / 10;
    }
    totals.textContent = chosen.size ? `Meal total: ${summary(sum)}` : 'Tick one or more foods, or enter nutrition by hand.';
    manual.hidden = chosen.size > 0;
    drawExtras();
  };

  let current = null, lastEaten = 1, eaten = null;
  const extras = h('div', { class: 'ws-usual-extras' });
  const drawExtras = () => extras.replaceChildren(...(current ? current.ingredients : [])
    .filter(i => i.optional && byId.has(i.food_id) && !chosen.has(i.food_id))
    .map(i => h('button', { type: 'button', class: 'btn small', title: 'Optional — add it, then set how many',
                            onclick: () => { chosen.set(i.food_id, roundQty(Number(i.quantity) * (Number(eaten?.value) || 1))); chooser.redraw(); } },
                '+ ' + byId.get(i.food_id).name)));
  const chooser = foodChooser(list, chosen, { onChange: drawTotals });

  let usual = null;
  if (usualMeals.length) {
    const pick = h('select', { class: 'ws-input', 'aria-label': 'Go-to meal' },
      h('option', { value: '' }, 'Start from a go-to meal…'), ...usualMeals.map(x => h('option', { value: x.recipe_id }, x.name)));
    eaten = h('input', { class: 'ws-input ws-servings', type: 'number', step: '0.5', min: '0.25', value: 1,
                         'aria-label': 'Portions eaten', title: 'How many portions you ate' });
    const ofBatch = h('span', { class: 'ws-note' }, 'portion');
    const apply = () => {
      current = usualMeals.find(x => String(x.recipe_id) === String(pick.value)) || null;
      if (!current) { ofBatch.textContent = 'portion'; drawExtras(); return; }
      const portions = Number(current.portions) || 1;
      ofBatch.textContent = portions === 1 ? 'portion' : `of ${+portions.toFixed(2)} portions`;
      const part = (Number(eaten.value) > 0 ? Number(eaten.value) : 1) / portions;
      chosen.clear();
      for (const i of current.ingredients) if (!i.optional && byId.has(i.food_id)) chosen.set(i.food_id, roundQty(Number(i.quantity) * part));
      lastEaten = Number(eaten.value) > 0 ? Number(eaten.value) : 1;
      const name = f.elements.description;
      if (!name.value || usualMeals.some(x => x.name === name.value)) name.value = current.name;
      chooser.redraw();
    };
    pick.onchange = apply;
    // More or fewer portions scale every food, extras included (8 meatballs for 2 portions).
    eaten.oninput = () => {
      const n = Number(eaten.value);
      if (!(n > 0) || n === lastEaten) return;
      for (const [id, qty] of chosen) chosen.set(id, roundQty(qty * n / lastEaten));
      lastEaten = n;
      chooser.redraw();
    };
    usual = h('div', { class: 'ws-usual' }, pick, eaten, ofBatch);
    if (recipeId != null) { pick.value = String(recipeId); queueMicrotask(apply); }
  }

  // Save what you ate as a go-to meal too. If it was one portion of a batch, say how many
  // portions the batch made; the go-to meal keeps the whole batch.
  const keep = h('input', { type: 'checkbox', 'data-untracked': '' });
  const keepName = h('input', { class: 'ws-input', placeholder: 'Go-to meal name', 'aria-label': 'Go-to meal name' });
  const keepEaten = h('input', { class: 'ws-input ws-servings', type: 'number', min: '0.25', step: '0.25', value: 1, 'aria-label': 'Portions you ate' });
  const keepPortions = h('input', { class: 'ws-input ws-servings', type: 'number', min: '1', step: '1', value: 1, 'aria-label': 'Portions the batch made' });
  const keepNote = h('span', { class: 'ws-note' });
  const keepFields = h('div', { class: 'ws-usual', hidden: true }, keepName,
    h('span', { class: 'ws-note' }, 'this was'), keepEaten, h('span', { class: 'ws-note' }, 'of'), keepPortions, h('span', { class: 'ws-note' }, 'portions'));
  const drawKeep = () => {
    const existing = usualMeals.find(x => x.name.toLowerCase() === keepName.value.trim().toLowerCase());
    keepNote.textContent = existing ? ` — updates your go-to meal “${existing.name}”` : '';
  };
  keep.onchange = () => {
    keepFields.hidden = !keep.checked;
    if (keep.checked && !keepName.value) keepName.value = f.elements.description.value || '';
    if (keep.checked && current) {   // started from a go-to meal: same batch, same portions
      keepEaten.value = Number(eaten?.value) || 1;
      keepPortions.value = Number(current.portions) || 1;
    }
    drawKeep();
  };
  keepName.oninput = drawKeep;
  const keepBox = h('div', { class: 'ws-field wide ws-keep', dataset: { untracked: '' } },
    h('label', { class: 'ws-check' }, keep, h('span', {}, 'Also save as a go-to meal', keepNote)), keepFields);

  const f = form(
    field('Date', 'meal_date', { kind: 'date', value: r.meal_date, required: true }),
    field('Meal', 'slot', { kind: 'select', options: SLOTS, value: r.slot }),
    field('Status', 'status', { kind: 'select', options: [['eaten', 'Eaten'], ['planned', 'Planned']], value: r.status }),
    field('Name (optional)', 'description', { value: r.description, wide: true, placeholder: 'e.g. Egg burrito — defaults to the foods' }),
    h('div', { class: 'ws-field wide' }, h('span', {}, 'Foods in this meal'), usual, extras, ...chooser.el, totals),
    keepBox,
    manual,
    field('Note', 'note', { value: r.note, wide: true }));
  return edit(record ? 'Edit meal' : 'Log a meal', f, async body => {
    const items = [...chosen].map(([food_id, quantity]) => ({ food_id, quantity }));
    if (items.length) NUTRIENTS.forEach(([k]) => delete body[k]);
    if (!body.description && !items.length) throw new Error('Name the meal or tick at least one food.');
    if (keep.checked) {
      // Saved first, so a problem with it keeps the dialog open before the meal is logged.
      const name = keepName.value.trim() || body.description;
      const portions = Math.max(1, Math.round(Number(keepPortions.value) || 1));
      const ate = Number(keepEaten.value) > 0 ? Number(keepEaten.value) : 1;
      if (!name) throw new Error('Name the go-to meal.');
      if (!items.length) throw new Error('A go-to meal needs saved foods: tick at least one.');
      const existing = usualMeals.find(x => x.name.toLowerCase() === name.toLowerCase());
      // Foods scale up to the whole batch; optional extras stay optional, per portion.
      const wasOptional = new Set((existing || current)?.ingredients.filter(i => i.optional).map(i => i.food_id) || []);
      const recipe = { name, portions, ingredients: items.map(i => wasOptional.has(i.food_id)
        ? { food_id: i.food_id, quantity: roundQty(i.quantity / ate), optional: true }
        : { food_id: i.food_id, quantity: roundQty(i.quantity * portions / ate), optional: false }) };
      await (existing ? api(`/api/v1/health/recipes/${existing.recipe_id}`, { method: 'PUT', body: { ...recipe, name: existing.name } })
                      : api('/api/v1/health/recipes', { method: 'POST', body: recipe }));
      forgetRecipes();
      toast(existing ? `Go-to meal “${existing.name}” updated` : `Saved “${name}” to your go-to meals`);
      keep.checked = false;   // a retry after a failed meal save doesn't save it twice
      keepFields.hidden = true;
    }
    body.items = items;
    return record ? api(`/api/v1/health/meals/${record.meal_id}`, { method: 'PUT', body })
                  : api('/api/v1/health/meals', { method: 'POST', body });
  });
}

// Build or edit a go-to meal: a name, how many portions the batch makes, and its foods,
// each with servings for the whole batch. Optional foods (meatballs, cheese) are offered
// when logging, not preselected, and their amount is per portion.
// Resolves true when saved or deleted.
export async function goToMeal(recipe = null) {
  const list = await foods();
  const byId = new Map(list.map(f => [f.food_id, f]));
  const chosen = new Map(), optional = new Set();
  for (const i of recipe?.ingredients || []) {
    if (!byId.has(i.food_id)) continue;
    chosen.set(i.food_id, Number(i.quantity));
    if (i.optional) optional.add(i.food_id);
  }
  const totals = h('p', { class: 'ws-meal-total', 'aria-live': 'polite' });
  const portions = field('Makes (portions)', 'portions', { kind: 'number', value: recipe?.portions ?? 1, min: 0.25, step: 'any' });
  const drawTotals = () => {
    const sum = Object.fromEntries(NUTRIENTS.map(([k]) => [k, null]));
    for (const [id, servings] of chosen) {
      if (optional.has(id)) continue;
      const n = scaled(byId.get(id), servings);
      for (const [k] of NUTRIENTS) if (n[k] != null) sum[k] = Math.round(((sum[k] || 0) + n[k]) * 10) / 10;
    }
    const each = Number(portions.querySelector('input').value) > 0 ? Number(portions.querySelector('input').value) : 1;
    const per = Object.fromEntries(Object.entries(sum).map(([k, v]) => [k, v == null ? null : v / each]));
    const extra = optional.size ? ' · before optional extras' : '';
    totals.textContent = !chosen.size ? 'Tick the foods that go in it.'
      : each === 1 ? `Per portion: ${summary(sum)}${extra}`
      : `Per portion: ${summary(per)}${extra} — whole batch ${Math.round(sum.calories ?? 0)} kcal`;
  };
  portions.querySelector('input').addEventListener('input', drawTotals);
  const chooser = foodChooser(list, chosen, { optional, onChange: drawTotals });
  const f = form(
    field('Name', 'name', { value: recipe?.name, required: true, wide: true, placeholder: 'e.g. Sushi Bowls' }),
    portions,
    h('p', { class: 'ws-note', style: { flexBasis: '100%', margin: 0 } }, 'Enter the foods for the whole batch as you cook it. Optional extras (meatballs, cheese) are per portion.'),
    h('div', { class: 'ws-field wide' }, h('span', {}, 'Foods, for the whole batch'), ...chooser.el, totals));
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  const buttons = recipe ? [['Delete', 'delete'], ['Cancel', null], ['Save', true]] : [['Cancel', null], ['Save', true]];
  for (;;) {
    const choice = await dialog(recipe ? 'Edit go-to meal' : 'Build a go-to meal', h('div', {}, f, error), buttons);
    if (!choice) return false;
    try {
      if (choice === 'delete') {
        if (!await confirmDelete(`the go-to meal “${recipe.name}”`)) continue;
        await api(`/api/v1/health/recipes/${recipe.recipe_id}`, { method: 'DELETE' });
      } else {
        const v = values(f);
        const body = { name: v.name, portions: Number(v.portions) || 1,
                       ingredients: [...chosen].map(([food_id, quantity]) => ({ food_id, quantity, optional: optional.has(food_id) })) };
        if (!body.name) throw new Error('Name the go-to meal.');
        if (!body.ingredients.length) throw new Error('Tick at least one food.');
        await (recipe ? api(`/api/v1/health/recipes/${recipe.recipe_id}`, { method: 'PUT', body })
                      : api('/api/v1/health/recipes', { method: 'POST', body }));
      }
      forgetRecipes();
      return true;
    } catch (e) { error.textContent = e.message; }
  }
}

// The day's meals as four columns, as on the paper journal page: B | L | D | S, with
// snacks and dessert after dinner. Tap a meal to edit it; + adds one to that column.
const MEAL_COLUMNS = [['B', 'Breakfast', ['breakfast']], ['L', 'Lunch', ['lunch']], ['D', 'Dinner', ['dinner']],
                      ['S', 'Snacks & dessert', ['snack', 'meal']]];
// With readOnly, the same columns only show the meals (e.g. a preview before logging).
export function mealColumns(date, meals, onChange, { readOnly = false } = {}) {
  if (readOnly) return h('div', { class: 'ws-meal-cols' }, MEAL_COLUMNS.map(([letter, label, slots]) => {
    const rows = meals.filter(m => slots.includes(m.slot));
    return h('section', { class: 'ws-meal-col', 'aria-label': label },
      h('header', {}, h('span', { class: 'ws-meal-letter', 'aria-hidden': 'true' }, letter), h('span', { class: 'ws-meal-label' }, label)),
      h('ul', {}, rows.length ? rows.map(m => h('li', {}, h('div', { class: 'ws-meal-item' }, h('span', {}, m.description || '(meal)'),
        m.calories != null ? h('small', {}, Math.round(m.calories) + ' kcal') : null))) : h('li', { class: 'ws-note' }, '—')));
  }));
  return h('div', { class: 'ws-meal-cols' }, MEAL_COLUMNS.map(([letter, label, slots]) => {
    const rows = meals.filter(m => slots.includes(m.slot));
    const kcal = rows.reduce((sum, m) => sum + (m.status === 'planned' ? 0 : Number(m.calories) || 0), 0);
    return h('section', { class: 'ws-meal-col', 'aria-label': label },
      h('header', {}, h('span', { class: 'ws-meal-letter', 'aria-hidden': 'true' }, letter), h('span', { class: 'ws-meal-label' }, label),
        kcal ? h('span', { class: 'ws-meal-kcal' }, Math.round(kcal) + ' kcal') : null),
      h('ul', {}, rows.map(m => h('li', {},
        h('button', { type: 'button', class: 'ws-meal-item', title: 'Edit this meal',
                      onclick: async () => { if (await meal(date, m)) onChange(); } },
          h('span', {}, m.description || '(meal)'),
          h('small', {}, [m.status === 'planned' ? 'planned' : null, m.calories != null ? Math.round(m.calories) + ' kcal' : 'kcal unknown'].filter(Boolean).join(' · '))),
        h('button', { type: 'button', class: 'ws-meal-x', 'aria-label': 'Delete ' + (m.description || 'meal'),
                      onclick: async () => { if (await remove('meals', m.meal_id, 'meal')) onChange(); } }, '✕')))),
      h('button', { type: 'button', class: 'btn small ws-meal-add', 'aria-label': 'Add to ' + label,
                    onclick: async () => { if (await meal(date, null, { slot: slots[0] })) onChange(); } }, '+ Add'));
  }));
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
                title: [r.activity || r.workout_type, r.distance != null ? `${Number(r.distance)} ${r.distance_unit || 'mi'}` : null,
                        r.minutes != null ? Math.round(r.minutes) + ' min' : null].filter(Boolean).join(' · '),
                meta: [r.activity ? r.workout_type : null, pace(r), liftsText(r.lifts), circuitText(r.circuit),
                       r.is_dog_walk ? "doesn't count toward the workout goal" : null, r.note].filter(Boolean).join(' · '),
                extra: r.has_route ? h('button', { class: 'btn small', 'aria-label': 'Route map', onclick: () => routeMap(r).catch(e => toast(e.message, 'error')) }, '🗺 Map') : null },
    meals: { id: r.meal_id, label: 'meal', editor: meal, date: r.meal_date,
             title: (r.description || '(meal)') + (r.items?.length > 1 ? ` · ${r.items.length} foods` : ''),
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
    h('div', { class: 'ws-row-end' }, spec.extra || null,
      h('button', { class: 'btn small', onclick: async () => { if (await spec.editor(spec.date, r)) onChange(); } }, 'Edit'),
      h('button', { class: 'btn small danger', 'aria-label': 'Delete ' + spec.label, onclick: async () => { if (await remove(kind, spec.id, spec.label)) onChange(); } }, '✕')));
}

export function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : ''; }
