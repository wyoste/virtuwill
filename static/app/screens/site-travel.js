// Site › Travel: the stops on the public map, grouped by city, each with its photos;
// and the countries and states visited.
import { setDirty } from '../main.js';
import { h, api, card, pageHead, empty, toast, run, field, values, dialog, confirmDelete, editable, saveAll, photoPicker, uploadEach, megabytes } from '../lib.js';

const TYPES = [['visited', '📍 Visited'], ['recommend', '⭐ Recommend'], ['wishlist', '🌟 Want to go']];
const typeLabel = t => TYPES.find(x => x[0] === t)?.[1] || t;

export async function render(view) {
  const d = await api('/api/v1/travel');
  editing = false; closeOpen = null;
  setDirty(() => editing);        // an open, changed stop counts as unsaved work
  const redraw = () => { view.replaceChildren(); return render(view); };

  // Stops grouped by city. Cities in the chosen order: by their latest visit (newest or oldest first), or A–Z.
  // Stops without a date go last; within a city, stops follow the same order.
  const byCity = new Map();
  for (const p of d.places) {
    const city = p.city || 'Other places';
    byCity.set(city, [...(byCity.get(city) || []), p]);
  }
  const latest = city => byCity.get(city).map(p => p.visited || '').sort().at(-1) || '';
  const byDate = (a, b, newest) => (!a) - (!b) || (newest ? b.localeCompare(a) : a.localeCompare(b));
  const cities = [...byCity.keys()].sort((a, b) => order === 'az' ? a.localeCompare(b)
    : byDate(latest(a), latest(b), order === 'newest') || a.localeCompare(b));
  for (const stops of byCity.values()) {
    stops.sort((a, b) => order === 'az' ? a.name.localeCompare(b.name) : byDate(a.visited || '', b.visited || '', order === 'newest') || a.name.localeCompare(b.name));
  }
  const sorter = h('label', { class: 'ws-sort' }, h('span', {}, 'Order'),
    h('select', { class: 'ws-input', 'aria-label': 'Order stops', 'data-untracked': '', onchange: e => {
      order = e.target.value;
      try { localStorage.setItem('vw_travel_order', order); } catch { /* private mode */ }
      redraw();
    } }, [['newest', 'Newest first'], ['oldest', 'Oldest first'], ['az', 'A–Z']].map(([v, t]) => h('option', { value: v, selected: v === order }, t))));

  view.append(
    pageHead('Travel', 'Stops on the public Travel map, with their photos.', sorter, h('a', { class: 'btn', href: '/travel', target: '_blank' }, 'View ↗'),
      h('button', { class: 'btn primary', onclick: async () => { if (await editStop({})) redraw(); } }, '+ Add a stop')),
    d.places.length ? h('div', { class: 'ws-cities' }, cities.map(city => card(h('span', {}, city, h('span', { class: 'ws-note' },
        [latest(city) ? new Date(latest(city) + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : 'No date',
         `${byCity.get(city).length} stop${byCity.get(city).length === 1 ? '' : 's'}`].join(' · '))),
      h('ul', { class: 'ws-list' }, byCity.get(city).map(p => stop(p, redraw))))))
      : card(null, empty('No stops yet. Add one: search for the place, then attach photos.')),
    regions(d));
}

// One line per stop: what and when, how many photos, the start of its story. Edit opens it in place,
// with the whole form and its photos; only one stop is open at a time.
let closeOpen = null;
let order = (() => { try { return localStorage.getItem('vw_travel_order') || 'newest'; } catch { return 'newest'; } })();
let editing = false;

function stop(p, redraw) {
  const panel = h('div', { class: 'ws-stop-edit', hidden: true });
  const edit = h('button', { class: 'btn small', 'aria-expanded': 'false', onclick: () => (panel.hidden ? open() : close()) }, 'Edit');
  const row = h('li', { class: 'ws-row ws-stop' },
    h('div', { class: 'ws-row-main' },
      h('div', { class: 'ws-row-title' }, p.name),
      h('div', { class: 'ws-row-meta' }, [typeLabel(p.type),
        p.visited ? new Date(p.visited + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : null,
        p.photoItems.length ? `${p.photoItems.length} photo${p.photoItems.length === 1 ? '' : 's'}` : 'No photos'].filter(Boolean).join(' · ')),
      p.note ? h('div', { class: 'ws-stop-note' }, p.note) : null),
    h('div', { class: 'ws-row-end' }, edit,
      h('button', { class: 'btn small danger', 'aria-label': 'Remove ' + p.name, onclick: async () => {
        if (!(await confirmDelete(p.name + (p.photoItems.length ? ' and its photos' : '')))) return;
        await run(null, async () => { await api(`/api/v1/travel/places/${p.id}`, { method: 'DELETE' }); editing = false; redraw(); });
      } }, '✕')),
    panel);

  function close() {
    panel.hidden = true; panel.replaceChildren(); edit.textContent = 'Edit'; edit.setAttribute('aria-expanded', 'false');
    row.classList.remove('open'); editing = false; closeOpen = null;
  }
  async function open() {
    if (closeOpen) {
      if (editing && !confirm('Close the stop you’re editing without saving?')) return;
      closeOpen();
    }
    closeOpen = close;
    const form = await stopForm(p);
    panel.replaceChildren(
      form.el,
      h('section', { class: 'ws-stop-photos' }, h('h4', {}, `Photos (${p.photoItems.length})`),
        p.photoItems.length ? h('div', { class: 'ws-stop-photo-grid' }, p.photoItems.map(ph => photoCard(p, ph, redraw)))
          : h('p', { class: 'ws-note' }, 'No photos yet. Add some above.')),
      form.error,
      h('div', { class: 'ws-stop-actions' },
        h('button', { class: 'btn', type: 'button', onclick: close }, 'Cancel'),
        h('button', { class: 'btn primary', type: 'button', onclick: async e => {
          const b = e.currentTarget;
          b.disabled = true;
          if (await form.submit()) { editing = false; closeOpen = null; redraw(); } else b.disabled = false;
        } }, 'Save stop')));
    panel.addEventListener('input', () => { editing = true; });
    panel.hidden = false; edit.textContent = 'Close'; edit.setAttribute('aria-expanded', 'true'); row.classList.add('open');
    panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  return row;
}

// A photo in an open stop: its caption saves as soon as it's changed; × removes it.
function photoCard(p, ph, redraw) {
  const caption = h('input', { class: 'ws-input', value: ph.caption, placeholder: 'Add a caption', 'aria-label': 'Caption', 'data-untracked': '' });
  const state = h('small', { class: 'ws-note' });
  caption.onchange = () => run(null, async () => {
    await api(`/api/v1/travel/places/${p.id}/photos/${ph.position}`, { method: 'PUT', body: { caption: caption.value }, quiet: true });
    ph.caption = caption.value; state.textContent = 'Saved';
  });
  return h('figure', { class: 'ws-stop-photo' },
    h('img', { src: ph.url, alt: ph.caption || p.name, loading: 'lazy' }),
    h('figcaption', {}, caption, state),
    h('button', { class: 'btn small danger ws-stop-photo-del', 'aria-label': 'Remove photo', onclick: async () => {
      if (!(await confirmDelete('this photo'))) return;
      await run(null, async () => { await api(`/api/v1/travel/places/${p.id}/photos/${ph.position}`, { method: 'DELETE' }); editing = false; redraw(); });
    } }, '×'));
}

// ── Adding or editing a stop ─────────────────────────────────────────────────
// Pick the country from a searchable list (it places the pin), optionally name
// the city and the place, and attach photos in the same step.
let countries = null;
async function countryList() {
  countries = countries || await fetch('/static/data/countries.json').then(r => r.json());
  return countries;
}

// Names people type that the list spells differently.
const ALIASES = { US: ['united states', 'usa', 'america'], GB: ['uk', 'united kingdom', 'britain', 'great britain', 'england', 'scotland', 'wales'],
                  AE: ['uae'], KR: ['korea', 'south korea'], CZ: ['czech republic'], NL: ['holland'], RU: ['russia'] };

// A searchable list: type to narrow it, pick one. picker.value → the chosen country or null.
function countryPicker(list, initial) {
  let value = initial || null;
  const search = h('input', { class: 'ws-input', type: 'search', placeholder: 'Search countries', autocomplete: 'off',
    'aria-label': 'Search countries', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'ws-country-list', 'data-untracked': '' });
  const chosen = h('div', { class: 'ws-country-chosen', 'aria-live': 'polite' });
  const options = h('ul', { class: 'ws-country-list', id: 'ws-country-list', role: 'listbox', 'aria-label': 'Countries' });
  const hidden = h('input', { type: 'hidden', name: 'country', value: value?.name || '' });   // tracked, so a change counts as an edit
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    // Best matches first: the code or a common name ("usa", "uk"), then names starting with it, then containing it.
    const rank = c => {
      const name = c.name.toLowerCase();
      if (c.code.toLowerCase() === q || (ALIASES[c.code] || []).some(a => a.startsWith(q))) return 0;
      return name.startsWith(q) ? 1 : name.includes(q) ? 2 : 3;
    };
    const shown = q ? list.map(c => [rank(c), c]).filter(([r]) => r < 3).sort((a, b) => a[0] - b[0]).map(([, c]) => c) : list;
    options.replaceChildren(...shown.slice(0, 80).map(c => h('li', { role: 'option', 'aria-selected': String(value?.name === c.name) },
      h('button', { type: 'button', onclick: () => pick(c) }, flag(c.code), ' ', c.name))),
      ...(shown.length ? [] : [h('li', { class: 'ws-note' }, 'No country matches.')]));
    chosen.replaceChildren(value ? h('strong', {}, flag(value.code), ' ', value.name) : h('span', { class: 'ws-note' }, 'No country chosen yet'));
    options.hidden = !!value && !q && document.activeElement !== search;   // folded away once chosen
    search.setAttribute('aria-expanded', String(!options.hidden));
  };
  const pick = c => { value = c; hidden.value = c.name; search.value = ''; search.blur(); search.placeholder = 'Search to change the country';
                      draw(); hidden.dispatchEvent(new Event('input', { bubbles: true })); };
  search.oninput = draw;
  search.onfocus = () => { options.hidden = false; search.setAttribute('aria-expanded', 'true'); };
  search.placeholder = value ? 'Search to change the country' : 'Search countries';
  search.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); options.querySelector('button')?.click(); }
  });
  draw();
  return { el: h('div', { class: 'ws-field wide' }, h('span', {}, 'Country'), chosen, search, options, hidden), get value() { return value; } };
}

const flag = code => code ? String.fromCodePoint(...[...code.toUpperCase()].map(ch => 127397 + ch.charCodeAt(0))) : '🏳';

// The state (or region) list for a country: a select, filled when the country is chosen.
function regionPicker(initial) {
  let country = null;
  const select = h('select', { name: 'region', 'aria-label': 'State or region' });
  const label = h('span', {}, 'State or region (optional)');
  const el = h('label', { class: 'ws-field' }, label, select);
  const load = async (c, keep) => {
    country = c;
    label.textContent = c?.code === 'US' ? 'State' : 'State or region (optional)';
    const list = c ? await api('/api/v1/travel/regions?country=' + encodeURIComponent(c.code), { quiet: true }).catch(() => []) : [];
    select.replaceChildren(h('option', { value: '' }, list.length ? (c?.code === 'US' ? 'Choose a state' : 'Any') : '—'),
      ...list.map(r => h('option', { value: r.code, selected: r.code === keep }, r.name)));
    el.hidden = !list.length;
  };
  return { el, load, get value() { return select.value; }, set value(v) { select.value = v; },
           get name() { return select.selectedOptions[0]?.value ? select.selectedOptions[0].textContent : ''; },
           onchange(fn) { select.addEventListener('change', fn); } };
}

// A searchable list of real cities in the chosen country (and state). Typing a
// place that isn't listed still works: it's found on the map service, or pinned at the country.
function cityPicker(initial) {
  let value = initial || null;           // {name, region, lat, lng} for a listed city, {name} for a typed one
  let scope = { country: null, region: '' };
  const search = h('input', { class: 'ws-input', type: 'search', placeholder: 'Search cities', autocomplete: 'off', value: value?.name || '',
    'aria-label': 'Search cities', role: 'combobox', 'aria-expanded': 'false', 'aria-controls': 'ws-city-list', 'data-untracked': '' });
  const hidden = h('input', { type: 'hidden', name: 'town', value: value?.name || '' });
  const options = h('ul', { class: 'ws-country-list', id: 'ws-city-list', role: 'listbox', 'aria-label': 'Cities', hidden: true });
  const note = h('div', { class: 'ws-note' });
  let timer = 0, asked = 0;
  const show = list => {
    const q = search.value.trim();
    options.replaceChildren(...list.map(c => h('li', { role: 'option' }, h('button', { type: 'button', onclick: () => pick(c) },
      c.name, c.region_name && !scope.region ? h('span', { class: 'ws-note' }, ' · ' + c.region_name) : null))),
      ...(q ? [h('li', {}, h('button', { type: 'button', class: 'ws-city-typed', onclick: () => pick({ name: q }) }, `Use “${q}” as typed`))] : []));
    options.hidden = !q;
    search.setAttribute('aria-expanded', String(!options.hidden));
  };
  const find = () => {
    clearTimeout(timer);
    const q = search.value.trim();
    if (!q || !scope.country) { show([]); return; }
    timer = setTimeout(async () => {
      const ticket = ++asked;
      const params = new URLSearchParams({ q, country: scope.country.code, ...(scope.region ? { region: scope.region } : {}) });
      const list = await api('/api/v1/travel/cities?' + params, { quiet: true }).catch(() => []);
      if (ticket === asked) show(list);
    }, 150);
  };
  const pick = c => {
    value = c;
    hidden.value = c.name; search.value = c.name;
    options.hidden = true; search.setAttribute('aria-expanded', 'false');
    note.textContent = c.lat === undefined ? 'Not in the list: the pin goes where the map service finds it, else at the country’s centre.' : '';
    hidden.dispatchEvent(new Event('input', { bubbles: true }));
    picked(c);
  };
  let picked = () => {};
  search.oninput = () => { value = null; hidden.value = search.value.trim(); hidden.dispatchEvent(new Event('input', { bubbles: true })); find(); };
  search.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); options.querySelector('button')?.click(); } });
  return {
    el: h('div', { class: 'ws-field wide' }, h('span', {}, 'City or town'), search, options, note, hidden),
    setScope(country, region, reset) {
      scope = { country, region };
      if (reset) { value = null; search.value = ''; hidden.value = ''; note.textContent = ''; options.hidden = true; }
      search.disabled = !country;
      search.placeholder = country ? `Search cities in ${country.name}` : 'Choose a country first';
    },
    onpick(fn) { picked = fn; },
    get value() { return value || (search.value.trim() ? { name: search.value.trim() } : null); },
  };
}

// Where a pin goes: the exact position if given, else the chosen city, else the city found on the map service, else the country's centre.
async function position(v, country, city) {
  if (v.lat !== null && v.lng !== null) return { lat: Number(v.lat), lng: Number(v.lng) };
  if (city?.lat !== undefined) return { lat: city.lat, lng: city.lng };
  if (city?.name) {
    try {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 4000);
      const params = new URLSearchParams({ format: 'json', limit: '1', city: city.name, ...(country.code ? { countrycodes: country.code.toLowerCase() } : { country: country.name }) });
      const found = await (await fetch('https://nominatim.openstreetmap.org/search?' + params, { signal: controller.signal })).json();
      if (found.length) return { lat: Number(found[0].lat), lng: Number(found[0].lon), display: found[0].display_name };
    } catch { /* offline or blocked: use the country's centre */ }
  }
  return { lat: country.lat, lng: country.lng };
}

// The stop's form: used in the Add a stop dialog and when a stop is opened for editing.
// submit() saves it (and any photos chosen) and returns true, or shows what's wrong and returns false.
async function stopForm(p = {}) {
  const list = await countryList();
  // An existing stop's city reads "Town, Country": find its country, and the town before it.
  const parts = (p.city || '').split(',').map(x => x.trim()).filter(Boolean);
  const existing = list.find(c => c.code === p.country) || list.find(c => c.name === parts.at(-1)) || null;
  const town = list.some(c => c.name === parts.at(-1)) ? parts.slice(0, -1).join(', ') : (p.city || '');
  const country = countryPicker(list, existing);
  const region = regionPicker();
  // A saved stop keeps its position unless its city changes.
  const city = cityPicker(town ? { name: town, region: p.region, lat: p.lat, lng: p.lng, kept: true } : null);
  const photos = photoPicker({ hint: 'or drop them here — they’re attached to this stop' });
  city.setScope(existing, p.region || '');
  if (existing) await region.load(existing, p.region || '');
  else region.el.hidden = true;
  country.el.addEventListener('input', async () => {
    await region.load(country.value, '');
    city.setScope(country.value, '', true);
  });
  region.onchange(() => city.setScope(country.value, region.value, true));
  city.onpick(c => { if (c.region && region.value !== c.region) { region.value = c.region; city.setScope(country.value, region.value); } });
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    country.el, region.el, city.el,
    field('Place name (optional)', 'name', { value: p.name && p.name !== p.city && p.name !== town ? p.name : '', placeholder: 'e.g. Café Tortoni' }),
    field('Type', 'type', { kind: 'select', options: TYPES, value: p.type || 'visited' }),
    field('Visited', 'visited', { kind: 'date', value: p.visited || '' }),
    field('About this place (shown when a visitor opens the pin)', 'note', { kind: 'textarea', value: p.note, wide: true,
      placeholder: 'What it was like, what to do there, who you were with…' }),
    h('div', { class: 'ws-field wide' }, h('span', {}, p.id ? 'Add more photos' : 'Photos'), photos.el),
    field('Caption for these photos (optional)', 'caption', { wide: true }),
    h('p', { class: 'ws-note wide' }, 'A visited stop shades its country (and US state) on the map by itself.'),
    h('details', { class: 'ws-manual' }, h('summary', {}, 'Exact position (optional)'),
      h('p', { class: 'ws-note' }, 'Leave blank to place the pin at the city, or at the country’s centre. Right-click a spot in any map app to copy its coordinates.'),
      h('div', { class: 'ws-form' },
        field('Latitude', 'lat', { kind: 'number', step: 'any', value: p.id ? p.lat : '' }),
        field('Longitude', 'lng', { kind: 'number', step: 'any', value: p.id ? p.lng : '' }))));
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  // Once the stop exists, a retry (say, after a photo was refused) updates it rather than adding it again.
  let id = p.id;
  const sent = new Set();            // photos already saved, so a retry doesn't send them twice
  // Adding photos to a saved stop leaves the stop alone: no re-save, no position lookup, just the photos.
  let detailsChanged = !p.id;
  const touched = e => { if (!photos.el.contains(e.target) && e.target.name !== 'caption') detailsChanged = true; };
  form.addEventListener('input', touched);
  form.addEventListener('change', touched);
  async function submit() {
    const v = values(form);
    if (id && !detailsChanged) {
      if (!photos.files().length) { error.textContent = ''; return true; }
      try { await sendPhotos(id, v); } catch (e) { error.textContent = e.message; return false; }
      error.textContent = '';
      toast(`Added ${photos.files().length} photo${photos.files().length === 1 ? '' : 's'} to ${p.name}`);
      return true;
    }
    const c = country.value;
    if (!c) { error.textContent = 'Choose a country.'; return false; }
    const where = city.value;
    // Moving an existing stop to another country or town moves its pin too, unless exact coordinates were typed.
    const moved = p.id && (c.code !== existing?.code || (where?.name || '') !== town || (where && !where.kept));
    if (moved && Number(v.lat) === p.lat && Number(v.lng) === p.lng) { v.lat = null; v.lng = null; }
    error.textContent = 'Saving…';
    try {
      const at = await position(v, c, where);
      const body = { name: v.name || where?.name || c.name, city: [where?.name, c.name].filter(Boolean).join(', '), lat: at.lat, lng: at.lng,
                     type: v.type, visited: v.visited, note: v.note, display: at.display || (moved ? '' : p.display || ''),
                     country: c.code, region: region.value || where?.region || '' };
      const saved = await api(id ? `/api/v1/travel/places/${id}` : '/api/v1/travel/places', { method: id ? 'PUT' : 'POST', body, quiet: true });
      id = saved.id;
      detailsChanged = false;        // saved: a retry after a refused photo only sends the photos
      await sendPhotos(id, v);
      error.textContent = '';
      toast(p.id ? 'Stop saved' : `Pinned ${body.name}${photos.files().length ? ' with ' + photos.files().length + ' photo' + (photos.files().length === 1 ? '' : 's') : ''}`);
      return true;
    } catch (e) { error.textContent = e.message; return false; }
  }
  // Full-size originals, one at a time; a retry sends only the ones not yet saved.
  async function sendPhotos(stopId, v) {
    const chosen = photos.files();
    if (!chosen.length) return;
    await uploadEach(chosen, `/api/v1/travel/places/${stopId}/photos`, { caption: v.caption || '' }, { done: sent,
      onProgress: (n, total, file) => { error.textContent = `Uploading photo ${n} of ${total} (${megabytes(file.size)})…`; } });
  }
  return { el: form, error, submit, created: () => id !== p.id };
}

// Adding a stop happens in a dialog.
async function editStop(p = {}) {
  const f = await stopForm(p);
  for (;;) {
    if (!(await dialog(p.id ? 'Edit stop' : 'Add a stop', h('div', {}, f.el, f.error), [['Cancel', null], ['Save', true]]))) return f.created();
    if (await f.submit()) return true;
  }
}

// Regions: the ones stops mark by themselves, and any marked by hand (one form, one Save).
function regions(d) {
  const auto = d.visited.from_stops || { countries: [], states: [] };
  const manual = key => d.visited[key].filter(code => !auto[key].includes(code));
  const form = h('form', { class: 'ws-form stack', onsubmit: e => e.preventDefault() },
    field('Other countries (two-letter codes, e.g. MX, JP)', 'countries', { value: manual('countries').join(', '), wide: true }),
    field('Other US states (e.g. TX, MS)', 'states', { value: manual('states').join(', '), wide: true }),
    h('div', {}, h('button', { class: 'btn primary', onclick: e => saveAll(e.currentTarget) }, 'Save')));
  const codes = text => (text || '').split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
  editable(form, () => {
    const v = values(form);
    return api('/api/v1/travel/visited', { method: 'PUT', body: { countries: codes(v.countries), states: codes(v.states) }, quiet: true });
  });
  const list = (label, items) => h('div', { class: 'ws-row-meta' }, h('strong', {}, label + ': '), items.length ? items.join(', ') : 'none yet');
  return card(h('span', {}, 'Countries and states visited',
      h('span', { class: 'ws-note' }, `${d.visited.countries.length} ${d.visited.countries.length === 1 ? 'country' : 'countries'} · ${d.visited.states.length} US state${d.visited.states.length === 1 ? '' : 's'}`)),
    h('p', { class: 'ws-note', style: { marginBottom: '6px' } }, 'Shaded on the public map. Your visited stops count by themselves:'),
    list('From stops — countries', auto.countries), list('From stops — US states', auto.states),
    h('p', { class: 'ws-note', style: { margin: '12px 0 6px' } }, 'Add places you’ve been without a stop on the map:'),
    form);
}
