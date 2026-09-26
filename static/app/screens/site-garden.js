// Site › Garden: photos tagged to beds and plant types, the bed planner, and the Garden page's text.
import { setDirty } from '../main.js';
import { h, api, card, pageHead, tabs, empty, toast, run, dialog, field, values, confirmDelete, editable, saveAll, isoToday, photoPicker } from '../lib.js';

const TABS = [['/app/site/garden', 'Photos'], ['/app/site/garden/planner', 'Beds & planner'], ['/app/site/garden/text', 'Page text']];

export async function render(view, ctx) {
  const tab = ctx.path.split('/')[4] || 'photos';
  view.append(pageHead('Garden', 'Photos, beds and the words on the public Garden page.', h('a', { class: 'btn', href: '/garden', target: '_blank' }, 'View ↗')),
    tabs(TABS, ctx.path));
  if (tab === 'planner') return planner(view);
  const d = await api('/api/v1/garden');
  const redraw = () => { view.replaceChildren(); return render(view, ctx); };
  return tab === 'text' ? text(view, d) : photos(view, d, ctx, redraw);
}

// Toggle chips for choosing beds or plant types; the Set holds the chosen ids.
function chips(options, chosen, label) {
  const search = options.length > 12 ? h('input', { class: 'ws-input ws-chip-search', type: 'search', placeholder: 'Find a plant type', 'aria-label': 'Find a plant type', 'data-untracked': '' }) : null;
  const row = h('div', { class: 'ws-chips', role: 'group', 'aria-label': label }, options.map(o => {
    const b = h('button', { type: 'button', class: 'ws-chip', 'aria-pressed': String(chosen.has(o.id)), 'data-label': o.label.toLowerCase() }, o.label);
    b.onclick = () => { chosen.has(o.id) ? chosen.delete(o.id) : chosen.add(o.id); b.setAttribute('aria-pressed', String(chosen.has(o.id))); };
    return b;
  }));
  if (search) search.oninput = () => row.querySelectorAll('button').forEach(b => {
    b.hidden = !!search.value && !b.dataset.label.includes(search.value.toLowerCase()) && b.getAttribute('aria-pressed') !== 'true';
  });
  return h('div', { class: 'ws-field wide' }, h('span', {}, label), search, row);
}

const bedOptions = d => d.beds.map(b => ({ id: b.id, label: b.name }));
const plantOptions = d => d.species.map(s => ({ id: s.id, label: `${s.emoji ? s.emoji + ' ' : ''}${s.name}` }));

// ── Photos ───────────────────────────────────────────────────────────────────
function photos(view, d, ctx, redraw) {
  const beds = new Set(), plants = new Set();
  const picker = photoPicker({ onchange: () => upload.touch(), hint: 'or drop them here. Everything you tag below applies to each photo.' });
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    h('div', { class: 'ws-field wide' }, picker.el),
    chips(bedOptions(d), beds, 'Beds'),
    chips(plantOptions(d), plants, 'Plant types'),
    field('Caption', 'caption', { wide: false }), field('Taken on', 'taken_on', { kind: 'date', value: isoToday() }),
    h('div', { class: 'ws-field wide' }, h('div', {}, h('button', { class: 'btn primary', onclick: e => { if (picker.files().length) upload.touch(); saveAll(e.currentTarget); } }, 'Upload'))));
  const upload = editable(form, async () => {
    if (!picker.files().length) throw new Error('Choose photos to upload, or clear the tags.');
    const f = new FormData();
    for (const file of picker.files()) f.append('files', file);
    const v = values(form);
    f.append('caption', v.caption || '');
    f.append('taken_on', v.taken_on || '');
    beds.forEach(b => f.append('beds', b));
    plants.forEach(p => f.append('species', p));
    await api('/api/v1/garden/photos', { method: 'POST', form: f, quiet: true });
  }, { then: redraw });

  // The gallery redraws on its own, so filtering or editing a photo keeps an upload in progress.
  const gallery = h('div', {});
  const bedName = id => d.beds.find(b => b.id === id)?.name || id;
  const plantName = id => plantOptions(d).find(s => s.id === id)?.label || id;
  const filter = (name, label, options) => h('label', { class: 'ws-field' }, h('span', {}, label),
    h('select', { 'data-untracked': '', onchange: e => {
      const q = new URLSearchParams(ctx.params);
      if (e.target.value) q.set(name, e.target.value); else q.delete(name);
      history.replaceState({}, '', '/app/site/garden' + (q.toString() ? '?' + q : ''));
      ctx.params = q;
      drawGallery();
    } }, h('option', { value: '' }, 'Any'), options.map(o => h('option', { value: o.id, selected: o.id === (ctx.params.get(name) || '') }, o.label))));
  const filters = h('div', { class: 'ws-filters', style: { marginBottom: '12px' } }, filter('bed', 'Bed', bedOptions(d)), filter('plant', 'Plant type', plantOptions(d)));
  const refresh = async () => { d.photos = (await api('/api/v1/garden')).photos; drawGallery(); };
  const title = h('span', {});
  function drawGallery() {
    const byBed = ctx.params.get('bed') || '', byPlant = ctx.params.get('plant') || '';
    const shown = d.photos.filter(p => (!byBed || p.beds.includes(byBed)) && (!byPlant || p.species.includes(byPlant)));
    title.textContent = `Photos (${shown.length}${shown.length !== d.photos.length ? ' of ' + d.photos.length : ''})`;
    gallery.replaceChildren(shown.length ? h('div', { class: 'ws-photo-grid' }, shown.map(p => h('button', { type: 'button', class: 'ws-photo', onclick: () => editPhoto(p, d, refresh) },
        h('img', { src: p.url, alt: p.caption || 'Garden photo', loading: 'lazy' }),
        h('span', { class: 'ws-photo-meta' }, p.caption ? h('strong', {}, p.caption) : null,
          h('span', {}, [...p.beds.map(bedName), ...p.species.map(plantName)].join(' · ') || 'Not tagged')))))
      : empty(d.photos.length ? 'No photos match.' : 'No photos yet. Add some above.'));
  }
  drawGallery();

  view.append(card('Add photos', form), card(title, filters, gallery));
}

async function editPhoto(p, d, redraw) {
  const beds = new Set(p.beds), plants = new Set(p.species);
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    h('img', { src: p.url, alt: '', class: 'ws-photo-preview' }),
    field('Caption', 'caption', { value: p.caption, wide: true }), field('Taken on', 'taken_on', { kind: 'date', value: p.date || '' }),
    chips(bedOptions(d), beds, 'Beds'), chips(plantOptions(d), plants, 'Plant types'));
  const choice = await dialog('Photo', form, [['Delete', 'delete'], ['Cancel', null], ['Save', true]]);
  if (choice === 'delete') {
    if (!(await confirmDelete('this photo'))) return;
    await run(null, async () => { await api('/api/garden/photo/' + encodeURIComponent(p.id), { method: 'DELETE' }); redraw(); });
  } else if (choice) {
    await run(null, async () => {
      await api('/api/v1/garden/photos/' + encodeURIComponent(p.id), { method: 'PUT',
        body: { ...values(form), beds: [...beds], species: [...plants] } });
      toast('Photo saved');
      redraw();
    });
  }
}

// ── Page text: one form, one Save ────────────────────────────────────────────
function text(view, d) {
  const form = h('form', { class: 'ws-form stack', onsubmit: e => e.preventDefault() },
    field('Headline note (shown at the top of the Garden page)', 'note', { value: d.text.note, wide: true }),
    field('Gardening philosophy', 'philosophy', { kind: 'textarea', value: d.text.philosophy, wide: true }),
    h('div', {}, h('button', { class: 'btn primary', onclick: e => saveAll(e.currentTarget) }, 'Save')));
  editable(form, async () => {
    const v = values(form);
    await api('/api/v1/site-text/garden.gallery_note', { method: 'PUT', body: { value: v.note || '' }, quiet: true });
    await api('/api/v1/site-text/garden.hero', { method: 'PUT', body: { value: v.philosophy || '' }, quiet: true });
  });
  view.append(card(null, form));
}

// ── The bed planner (the drawing tool lives in the page and is moved in and out) ─
function planner(view) {
  const el = document.querySelector('#page-planner');
  const host = h('div', { class: 'ws-planner' });
  if (el) { el.classList.add('active'); host.append(el); }
  view.append(h('p', { class: 'ws-note' }, 'Draw beds and place plants; use the planner’s own Save when you’re done. Photos are tagged to these beds and plant types.'), host);
  requestAnimationFrame(() => window.gdn?.init?.());
  setDirty(() => window.gdn?.isDirty?.());   // beds and plants not yet saved in the planner
  return () => {
    const home = document.getElementById('ws-planner-home');
    if (el && home) home.append(el);
  };
}
