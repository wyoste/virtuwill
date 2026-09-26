// Site › Garden: photos tagged to beds and plant types, the bed planner, and the Garden page's text.
import { setDirty } from '../main.js';
import { h, api, card, pageHead, tabs, empty, toast, run, dialog, field, values, confirmDelete, editable, saveAll, photoPicker } from '../lib.js';

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
    const b = h('button', { type: 'button', class: 'ws-chip', 'aria-pressed': String(chosen.has(o.id)), 'data-label': o.label.toLowerCase() }, '#' + VW.GardenTags.normalize(o.label));
    b.onclick = () => { chosen.has(o.id) ? chosen.delete(o.id) : chosen.add(o.id); b.setAttribute('aria-pressed', String(chosen.has(o.id))); };
    return b;
  }));
  if (search) search.oninput = () => row.querySelectorAll('button').forEach(b => {
    b.hidden = !!search.value && !b.dataset.label.includes(search.value.toLowerCase()) && b.getAttribute('aria-pressed') !== 'true';
  });
  return h('div', { class: 'ws-field wide' }, h('span', {}, label), search, row);
}

const bedOptions = d => d.beds.map(b => ({ id: b.id, label: b.name }));
const plantOptions = d => d.species.map(s => ({ id: s.id, label: s.name }));

function photoFields(d, origin, onchange) {
  const beds = new Set(origin ? [origin.bed] : []), plants = new Set(origin ? [origin.species] : []);
  const plantings = new Set(origin ? [origin.id] : []), tags = VW.GardenTags.editor();
  const picker = photoPicker({ onchange, hint: 'or drop them here. Each photo keeps its original capture date when available.' });
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    origin ? h('p', { class: 'ws-note wide' }, `Photos of ${origin.name}`) : null,
    h('div', { class: 'ws-field wide' }, picker.el),
    chips(bedOptions(d), beds, 'Beds — choose any that apply'), chips(plantOptions(d), plants, 'Plant types'),
    chips((d.plantings || []).map(p => ({ id: p.id, label: p.name })), plantings, 'Individual plants'),
    tags.el, field('Caption', 'caption'), field('Capture date override (optional)', 'taken_on', { kind: 'date', value: '' }),
    h('p', { class: 'ws-note wide' }, 'Leave the date blank to use each photo’s original metadata. Photos without capture metadata stay undated. An override applies to every selected photo.'));
  function payload() {
    if (!picker.files().length) throw new Error('Choose photos to upload.');
    const f = new FormData(), v = values(form);
    picker.files().forEach(file => f.append('files', file));
    f.append('caption', v.caption || ''); f.append('taken_on', v.taken_on || '');
    beds.forEach(id => f.append('beds', id)); plants.forEach(id => f.append('species', id));
    plantings.forEach(id => f.append('plantings', id)); tags.values().forEach(tag => f.append('tags', tag));
    return f;
  }
  return { form, picker, payload };
}

async function uploadForPlant(id) {
  if (window.gdn?.isDirty()) { toast('Save your planner changes before adding photos.', 'error'); return; }
  const d = await api('/api/v1/garden');
  const origin = (d.plantings || []).find(p => p.id === id);
  if (!origin) { toast('Save this plant in the planner first.', 'error'); return; }
  const fields = photoFields(d, origin), error = h('p', { role: 'alert' });
  const box = h('dialog', { class: 'ws-dialog' }, h('h2', {}, `Photos of ${origin.name}`), fields.form, error);
  const upload = h('button', { type: 'button', class: 'btn primary', onclick: async () => {
    upload.disabled = true; error.textContent = '';
    try {
      await api('/api/v1/garden/photos', { method: 'POST', form: fields.payload() });
      toast('Photos added'); box.close();
    } catch (e) { error.textContent = e.message; }
    finally { upload.disabled = false; }
  } }, 'Upload');
  box.append(h('div', { class: 'ws-dialog-actions' }, h('button', { type: 'button', class: 'btn', onclick: () => box.close() }, 'Cancel'), upload));
  box.addEventListener('close', () => box.remove()); document.body.append(box); box.showModal();
}

// ── Photos ───────────────────────────────────────────────────────────────────
function photos(view, d, ctx, redraw) {
  const origin = (d.plantings || []).find(p => p.id === ctx.params.get('planting'));
  let upload;
  const fields = photoFields(d, origin, () => upload?.touch());
  const form = fields.form;
  form.append(h('div', { class: 'ws-field wide' }, h('button', { type: 'button', class: 'btn primary',
    onclick: e => { if (fields.picker.files().length) upload.touch(); saveAll(e.currentTarget); } }, 'Upload')));
  upload = editable(form, () => api('/api/v1/garden/photos', { method: 'POST', form: fields.payload(), quiet: true }), { then: redraw });

  // Filter the gallery by bed or plant type.
  const params = ctx.params;
  const byBed = params.get('bed') || '', byPlant = params.get('plant') || '';
  const shown = d.photos.filter(p => (!byBed || p.beds.includes(byBed)) && (!byPlant || p.species.includes(byPlant)));
  const filter = (name, label, options, value) => h('label', { class: 'ws-field' }, h('span', {}, label),
    h('select', { 'data-untracked': '', onchange: e => { const q = new URLSearchParams(params); if (e.target.value) q.set(name, e.target.value); else q.delete(name);
      history.replaceState({}, '', '/app/site/garden' + (q.toString() ? '?' + q : '')); ctx.params = q; redraw(); } },
      h('option', { value: '' }, 'Any'), options.map(o => h('option', { value: o.id, selected: o.id === value }, o.label))));
  const bedName = id => d.beds.find(b => b.id === id)?.name || id;
  const plantName = id => plantOptions(d).find(s => s.id === id)?.label || id;

  view.append(
    card('Add photos', form),
    card('Beds', h('div', { class: 'ws-garden-beds' }, d.beds.map(b => h('section', { class: 'ws-garden-bed' },
      h('h3', {}, b.name), h('p', { class: 'ws-note' }, `${b.plants} plants · ${b.photos} photos`),
      h('div', { class: 'garden-tags' }, (d.plantings || []).filter(p => p.bed === b.id).map(p =>
        h('button', { type: 'button', class: 'garden-tag', title: `Add photos of ${p.name}`, onclick: () => run(null, () => uploadForPlant(p.id)) },
          VW.PlantArt.thumbnail(p.species), p.name, ' · Add photos'))))))),
    card(h('span', {}, `Photos (${shown.length}${shown.length !== d.photos.length ? ' of ' + d.photos.length : ''})`),
      h('div', { class: 'ws-filters', style: { marginBottom: '12px' } }, filter('bed', 'Bed', bedOptions(d), byBed), filter('plant', 'Plant type', plantOptions(d), byPlant)),
      shown.length ? h('div', { class: 'ws-photo-grid' }, shown.map(p => h('button', { type: 'button', class: 'ws-photo', onclick: () => editPhoto(p, d, redraw) },
        h('img', { src: p.url, alt: p.caption || 'Garden photo', loading: 'lazy' }),
        h('span', { class: 'ws-photo-meta' }, p.caption ? h('strong', {}, p.caption) : null,
          h('span', { class: 'garden-tags' }, [...p.beds.map(bedName), ...p.species.map(plantName), ...(p.tags || [])].map(t => VW.GardenTags.pill(t))),
          h('small', {}, p.date || 'Capture date unknown')))))
        : empty(d.photos.length ? 'No photos match.' : 'No photos yet. Add some above.')));
}

async function editPhoto(p, d, redraw) {
  const beds = new Set(p.beds), plants = new Set(p.species), plantings = new Set(p.plantings || []);
  const tags = VW.GardenTags.editor(p.tags || []);
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    h('img', { src: p.url, alt: '', class: 'ws-photo-preview' }),
    field('Caption', 'caption', { value: p.caption, wide: true }), field('Taken on', 'taken_on', { kind: 'date', value: p.date || '' }),
    chips(bedOptions(d), beds, 'Beds — choose any that apply'), chips(plantOptions(d), plants, 'Plant types'),
    chips((d.plantings || []).map(p => ({ id: p.id, label: p.name })), plantings, 'Individual plants'), tags.el);
  const choice = await dialog('Photo', form, [['Delete', 'delete'], ['Cancel', null], ['Save', true]]);
  if (choice === 'delete') {
    if (!(await confirmDelete('this photo'))) return;
    await run(null, async () => { await api('/api/garden/photo/' + encodeURIComponent(p.id), { method: 'DELETE' }); redraw(); });
  } else if (choice) {
    await run(null, async () => {
      await api('/api/v1/garden/photos/' + encodeURIComponent(p.id), { method: 'PUT',
        body: { ...values(form), beds: [...beds], species: [...plants], plantings: [...plantings], tags: tags.values() } });
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
  const onUpload = e => run(null, () => uploadForPlant(e.detail.plantingId));
  window.addEventListener('garden:upload-photo', onUpload);
  const el = document.querySelector('#page-planner');
  const host = h('div', { class: 'ws-planner' });
  if (el) { el.classList.add('active'); host.append(el); }
  view.append(h('p', { class: 'ws-note' }, 'Draw beds and place plants; use the planner’s own Save when you’re done. Photos are tagged to these beds and plant types.'), host);
  requestAnimationFrame(() => window.gdn?.init?.());
  setDirty(() => window.gdn?.isDirty?.());   // beds and plants not yet saved in the planner
  return () => {
    window.removeEventListener('garden:upload-photo', onUpload);
    const home = document.getElementById('ws-planner-home');
    if (el && home) home.append(el);
  };
}
