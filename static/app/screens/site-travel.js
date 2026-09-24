// Site › Travel: the stops on the public map, grouped by city, each with its photos;
// and the countries and states visited.
import { h, api, card, pageHead, empty, toast, run, field, values, dialog, confirmDelete, editable, saveAll, photoPicker } from '../lib.js';

const TYPES = [['visited', '📍 Visited'], ['recommend', '⭐ Recommend'], ['wishlist', '🌟 Want to go']];
const typeLabel = t => TYPES.find(x => x[0] === t)?.[1] || t;

export async function render(view) {
  const d = await api('/api/v1/travel');
  const redraw = () => { view.replaceChildren(); return render(view); };

  // Stops grouped by city, cities A–Z.
  const byCity = new Map();
  for (const p of d.places) {
    const city = p.city || 'Other places';
    byCity.set(city, [...(byCity.get(city) || []), p]);
  }
  const cities = [...byCity.keys()].sort((a, b) => a.localeCompare(b));

  view.append(
    pageHead('Travel', 'Stops on the public Travel map, with their photos.', h('a', { class: 'btn', href: '/travel', target: '_blank' }, 'View ↗'),
      h('button', { class: 'btn primary', onclick: async () => { if (await editStop({}, d.visited)) redraw(); } }, '+ Add a stop')),
    d.places.length ? h('div', { class: 'ws-cities' }, cities.map(city => card(h('span', {}, city, h('span', { class: 'ws-note' },
        `${byCity.get(city).length} stop${byCity.get(city).length === 1 ? '' : 's'}`)),
      h('ul', { class: 'ws-list' }, byCity.get(city).map(p => stop(p, redraw))))))
      : card(null, empty('No stops yet. Add one: search for the place, then attach photos.')),
    regions(d));
}

function stop(p, redraw) {
  const files = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true });
  files.onchange = () => run(null, async () => {
    const f = new FormData();
    for (const file of files.files) f.append('files', file);
    await api(`/api/v1/travel/places/${p.id}/photos`, { method: 'POST', form: f });
    toast(`Added to ${p.name}`);
    redraw();
  });
  return h('li', { class: 'ws-row ws-stop' },
    h('div', { class: 'ws-row-main' },
      h('div', { class: 'ws-row-title' }, p.name),
      h('div', { class: 'ws-row-meta' }, [typeLabel(p.type), p.visited ? new Date(p.visited + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : null,
        p.note].filter(Boolean).join(' · ')),
      p.photoItems.length ? h('div', { class: 'ws-thumbs' }, p.photoItems.map(ph => h('div', { class: 'ws-thumb' },
        h('button', { type: 'button', class: 'ws-thumb-open', 'aria-label': 'Edit photo caption', onclick: () => editPhoto(p, ph, redraw) },
          h('img', { src: ph.url, alt: ph.caption || p.name, loading: 'lazy' })),
        h('small', {}, ph.caption || 'No caption'),
        h('button', { class: 'btn small danger', 'aria-label': 'Remove photo', onclick: async () => {
          if (!(await confirmDelete('this photo'))) return;
          await run(null, async () => { await api(`/api/v1/travel/places/${p.id}/photos/${ph.position}`, { method: 'DELETE' }); redraw(); });
        } }, '×')))) : null),
    h('div', { class: 'ws-row-end' },
      h('button', { class: 'btn small', onclick: () => files.click() }, '+ Photos'), files,
      h('button', { class: 'btn small', onclick: async () => { if (await editStop(p)) redraw(); } }, 'Edit'),
      h('button', { class: 'btn small danger', 'aria-label': 'Remove ' + p.name, onclick: async () => {
        if (!(await confirmDelete(p.name + (p.photoItems.length ? ' and its photos' : '')))) return;
        await run(null, async () => { await api(`/api/v1/travel/places/${p.id}`, { method: 'DELETE' }); redraw(); });
      } }, '✕')));
}

async function editPhoto(p, ph, redraw) {
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    h('img', { src: ph.url, alt: '', class: 'ws-photo-preview' }), field('Caption', 'caption', { value: ph.caption, wide: true }));
  if (!(await dialog('Photo at ' + p.name, form, [['Cancel', null], ['Save', true]]))) return;
  await run(null, async () => { await api(`/api/v1/travel/places/${p.id}/photos/${ph.position}`, { method: 'PUT', body: values(form) }); redraw(); });
}

// ── Adding or editing a stop ─────────────────────────────────────────────────
// Pick the country from a searchable list (it places the pin), optionally name
// the city and the place, and attach photos in the same step.
let countries = null;
async function countryList() {
  countries = countries || await fetch('/static/data/countries.json').then(r => r.json());
  return countries;
}

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
    const shown = list.filter(c => !q || c.name.toLowerCase().includes(q) || c.code.toLowerCase() === q);
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

// Where a pin goes: the exact position if given, else the city found on the map service, else the country's centre.
async function position(v, country) {
  if (v.lat !== null && v.lng !== null) return { lat: Number(v.lat), lng: Number(v.lng) };
  if (v.town) {
    try {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 4000);
      const params = new URLSearchParams({ format: 'json', limit: '1', city: v.town, ...(country.code ? { countrycodes: country.code.toLowerCase() } : { country: country.name }) });
      const found = await (await fetch('https://nominatim.openstreetmap.org/search?' + params, { signal: controller.signal })).json();
      if (found.length) return { lat: Number(found[0].lat), lng: Number(found[0].lon), display: found[0].display_name };
    } catch { /* offline or blocked: use the country's centre */ }
  }
  return { lat: country.lat, lng: country.lng };
}

async function editStop(p = {}, visited = { countries: [], states: [] }) {
  const list = await countryList();
  // An existing stop's city reads "Town, Country": find its country, and the town before it.
  const parts = (p.city || '').split(',').map(x => x.trim()).filter(Boolean);
  const existing = list.find(c => c.name === parts.at(-1)) || null;
  const town = existing ? parts.slice(0, -1).join(', ') : (p.city || '');
  const country = countryPicker(list, existing);
  const photos = photoPicker({ hint: 'or drop them here — they’re attached to this stop' });
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    country.el,
    field('City or town (optional)', 'town', { value: town, placeholder: 'e.g. Florence' }),
    field('Place name (optional)', 'name', { value: p.name && p.name !== p.city ? p.name : '', placeholder: 'e.g. Café Tortoni' }),
    field('Type', 'type', { kind: 'select', options: TYPES, value: p.type || 'visited' }),
    field('Visited', 'visited', { kind: 'date', value: p.visited || '' }),
    field('Note', 'note', { value: p.note, wide: true }),
    h('div', { class: 'ws-field wide' }, h('span', {}, p.id ? 'Add photos' : 'Photos'), photos.el),
    field('Caption for these photos (optional)', 'caption', { wide: true }),
    p.id ? null : field('Also shade this country as visited', 'mark_visited', { kind: 'checkbox', value: true }),
    h('details', { class: 'ws-manual' }, h('summary', {}, 'Exact position (optional)'),
      h('p', { class: 'ws-note' }, 'Leave blank to place the pin at the city, or at the country’s centre. Right-click a spot in any map app to copy its coordinates.'),
      h('div', { class: 'ws-form' },
        field('Latitude', 'lat', { kind: 'number', step: 'any', value: p.id ? p.lat : '' }),
        field('Longitude', 'lng', { kind: 'number', step: 'any', value: p.id ? p.lng : '' }))));
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  for (;;) {
    if (!(await dialog(p.id ? 'Edit stop' : 'Add a stop', h('div', {}, form, error), [['Cancel', null], ['Save', true]]))) return false;
    const v = values(form);
    const c = country.value;
    if (!c) { error.textContent = 'Choose a country.'; continue; }
    // Moving an existing stop to another country or town moves its pin too, unless exact coordinates were typed.
    const moved = p.id && (c.name !== existing?.name || (v.town || '') !== town);
    if (moved && Number(v.lat) === p.lat && Number(v.lng) === p.lng) { v.lat = null; v.lng = null; }
    error.textContent = 'Saving…';
    try {
      const at = await position(v, c);
      const city = [v.town, c.name].filter(Boolean).join(', ');
      const body = { name: v.name || v.town || c.name, city, lat: at.lat, lng: at.lng, type: v.type, visited: v.visited, note: v.note,
                     display: at.display || p.display || '' };
      const saved = await api(p.id ? `/api/v1/travel/places/${p.id}` : '/api/v1/travel/places', { method: p.id ? 'PUT' : 'POST', body, quiet: true });
      if (photos.files().length) {
        const f = new FormData();
        photos.files().forEach(file => f.append('files', file));
        f.append('caption', v.caption || '');
        await api(`/api/v1/travel/places/${saved.id}/photos`, { method: 'POST', form: f, quiet: true });
      }
      if (v.mark_visited && c.code && !visited.countries.includes(c.code)) {
        await api('/api/v1/travel/visited', { method: 'PUT', body: { ...visited, countries: [...visited.countries, c.code] }, quiet: true });
      }
      toast(p.id ? 'Stop saved' : `Pinned ${body.name}${photos.files().length ? ' with ' + photos.files().length + ' photo' + (photos.files().length === 1 ? '' : 's') : ''}`);
      return true;
    } catch (e) { error.textContent = e.message; }
  }
}

// Regions: one form, one Save.
function regions(d) {
  const form = h('form', { class: 'ws-form stack', onsubmit: e => e.preventDefault() },
    field('Countries (two-letter codes, e.g. US, MX)', 'countries', { value: d.visited.countries.join(', '), wide: true }),
    field('US states (e.g. TX, MS)', 'states', { value: d.visited.states.join(', '), wide: true }),
    h('div', {}, h('button', { class: 'btn primary', onclick: e => saveAll(e.currentTarget) }, 'Save')));
  const codes = text => (text || '').split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
  editable(form, () => {
    const v = values(form);
    return api('/api/v1/travel/visited', { method: 'PUT', body: { countries: codes(v.countries), states: codes(v.states) }, quiet: true });
  });
  return card('Countries and states visited', h('p', { class: 'ws-note', style: { marginBottom: '8px' } }, 'Shaded on the public map.'), form);
}
