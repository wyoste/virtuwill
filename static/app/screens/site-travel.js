// Site › Travel: places on the public map, and countries and states visited.
import { h, api, card, pageHead, empty, toast, run, field, values, dialog } from '../lib.js';

const TYPES = [['visited', 'Visited'], ['recommend', 'Recommend'], ['wishlist', 'Want to go']];

export async function render(view) {
  const d = await api('/api/v1/travel');
  const redraw = () => { view.replaceChildren(); render(view); };
  const places = d.places;
  const savePlaces = next => run(null, async () => { await api('/api/v1/travel/places', { method: 'PUT', body: next }); redraw(); });

  const countries = h('input', { class: 'ws-input', value: d.visited.countries.join(', '), 'aria-label': 'Countries visited' });
  const states = h('input', { class: 'ws-input', value: d.visited.states.join(', '), 'aria-label': 'US states visited' });
  const codes = el => el.value.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);

  view.append(
    pageHead('Travel', 'The public Travel map shows these places and regions.', h('a', { class: 'btn', href: '/travel', target: '_blank' }, 'View ↗'),
      h('button', { class: 'btn primary', onclick: async () => { const p = await editPlace(); if (p) savePlaces([...places, p]); } }, '+ Place')),
    h('div', { class: 'ws-grid two' },
      card(`Places (${places.length})`, places.length ? h('ul', { class: 'ws-list' }, places.map((p, i) => h('li', { class: 'ws-row' },
        h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, p.display || p.name),
          h('div', { class: 'ws-row-meta' }, [p.city, TYPES.find(t => t[0] === p.type)?.[1], `${p.lat.toFixed(3)}, ${p.lng.toFixed(3)}`].filter(Boolean).join(' · '))),
        h('div', { class: 'ws-row-end' },
          h('button', { class: 'btn small', onclick: async () => { const next = await editPlace(p); if (next) savePlaces(places.map((x, j) => j === i ? next : x)); } }, 'Edit'),
          h('button', { class: 'btn small danger', 'aria-label': 'Remove ' + p.name, onclick: () => savePlaces(places.filter((_, j) => j !== i)) }, '✕'))))) : empty('No places yet.')),
      card('Regions visited', h('div', { class: 'ws-form stack' },
        h('label', { class: 'ws-field' }, h('span', {}, 'Countries (two-letter codes, e.g. US, MX)'), countries),
        h('label', { class: 'ws-field' }, h('span', {}, 'US states (e.g. TX, MS)'), states),
        h('div', {}, h('button', { class: 'btn primary', onclick: e => run(e.target, async () => {
          await api('/api/v1/travel/visited', { method: 'PUT', body: { countries: codes(countries), states: codes(states) } });
          toast('Saved'); redraw();
        }) }, 'Save regions'))))));
}

async function editPlace(p = {}) {
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Name', 'name', { value: p.name, required: true, wide: true }), field('City', 'city', { value: p.city }),
    field('Type', 'type', { kind: 'select', options: TYPES, value: p.type || 'visited' }),
    field('Latitude', 'lat', { kind: 'number', step: 'any', value: p.lat ?? '', required: true }),
    field('Longitude', 'lng', { kind: 'number', step: 'any', value: p.lng ?? '', required: true }),
    field('Note', 'note', { value: p.note, wide: true }));
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  for (;;) {
    if (!(await dialog(p.name ? 'Edit place' : 'Add a place', h('div', {}, form, error, h('p', { class: 'ws-note' }, 'Tip: right-click a spot in any map app to copy its coordinates.')),
        [['Cancel', null], ['Save', true]]))) return null;
    const v = values(form);
    const lat = Number(v.lat), lng = Number(v.lng);
    if (!v.name) { error.textContent = 'A place needs a name.'; continue; }
    if (!(Math.abs(lat) <= 90 && Math.abs(lng) <= 180) || v.lat === null || v.lng === null) { error.textContent = 'Latitude must be −90…90 and longitude −180…180.'; continue; }
    return { ...p, id: p.id || Date.now(), name: v.name, city: v.city || '', type: v.type, lat, lng, note: v.note || '', photos: p.photos || [] };
  }
}
