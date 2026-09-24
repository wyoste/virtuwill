// Site › Travel: the stops on the public map, grouped by city, each with its photos;
// and the countries and states visited.
import { h, api, card, pageHead, empty, toast, run, field, values, dialog, confirmDelete, editable, saveAll } from '../lib.js';

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
      h('button', { class: 'btn primary', onclick: async () => { if (await editStop()) redraw(); } }, '+ Add a stop')),
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

// Add or edit a stop. Searching fills in the city and map position.
async function editStop(p = {}) {
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Name', 'name', { value: p.name, required: true, wide: true, placeholder: 'e.g. Café Tortoni' }),
    field('City', 'city', { value: p.city, placeholder: 'e.g. Buenos Aires, Argentina' }),
    field('Type', 'type', { kind: 'select', options: TYPES, value: p.type || 'visited' }),
    field('Visited', 'visited', { kind: 'date', value: p.visited || '' }),
    field('Note', 'note', { value: p.note, wide: true }),
    field('Latitude', 'lat', { kind: 'number', step: 'any', value: p.lat ?? '' }),
    field('Longitude', 'lng', { kind: 'number', step: 'any', value: p.lng ?? '' }));
  const set = (name, value) => { form.elements[name].value = value; };
  const results = h('ul', { class: 'ws-list ws-geo' });
  const query = h('input', { class: 'ws-input', type: 'search', placeholder: 'Search a place or city', 'aria-label': 'Search a place or city', 'data-untracked': '' });
  const find = async () => {
    const q = query.value.trim() || [form.elements.name.value, form.elements.city.value].filter(Boolean).join(', ');
    if (!q) return;
    results.replaceChildren(h('li', { class: 'ws-note' }, 'Searching…'));
    try {
      const r = await fetch('https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&limit=5&q=' + encodeURIComponent(q));
      const found = await r.json();
      results.replaceChildren(...(found.length ? found.map(f => h('li', {}, h('button', { type: 'button', class: 'btn link', onclick: () => {
        const a = f.address || {};
        const city = a.city || a.town || a.village || a.municipality || a.county || '';
        set('lat', Number(f.lat).toFixed(6)); set('lng', Number(f.lon).toFixed(6));
        if (!form.elements.city.value) set('city', [city, a.country].filter(Boolean).join(', '));
        if (!form.elements.name.value) set('name', f.name || city);
        form.dataset.display = f.display_name;
        results.replaceChildren(h('li', { class: 'ws-note' }, '✓ ' + f.display_name));
      } }, f.display_name))) : [h('li', { class: 'ws-note' }, 'Nothing found. Enter the coordinates instead.')]));
    } catch {
      results.replaceChildren(h('li', { class: 'ws-note warn' }, 'Search is unavailable. Enter the coordinates instead (right-click a spot in any map app to copy them).'));
    }
  };
  query.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); find(); } });
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  const body = h('div', {}, h('div', { class: 'ws-form', style: { marginBottom: '8px' } }, query,
    h('button', { type: 'button', class: 'btn', onclick: find }, 'Find')), results, form, error);
  for (;;) {
    if (!(await dialog(p.id ? 'Edit stop' : 'Add a stop', body, [['Cancel', null], ['Save', true]]))) return false;
    const v = values(form);
    try {
      const payload = { ...v, display: form.dataset.display || p.display || '' };
      await api(p.id ? `/api/v1/travel/places/${p.id}` : '/api/v1/travel/places', { method: p.id ? 'PUT' : 'POST', body: payload });
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
