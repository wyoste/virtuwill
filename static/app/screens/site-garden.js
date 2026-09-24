// Site › Garden: the bed planner, photos, and the Garden page's text.
import { h, api, card, pageHead, empty, toast, run, confirmDelete } from '../lib.js';

export async function render(view) {
  const [photos, note] = await Promise.all([api('/api/garden/photos'), api('/api/v1/site-text/garden.gallery_note')]);
  const redraw = () => { view.replaceChildren(); render(view); };

  // The planner canvas lives in the page and is moved in and out of view.
  const planner = document.querySelector('#page-planner');
  const host = h('div', { class: 'ws-planner' });
  if (planner) { planner.classList.add('active'); host.append(planner); }

  const files = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true });
  files.onchange = () => run(null, async () => {
    const form = new FormData();
    for (const f of files.files) form.append('files', f);
    await api('/api/garden/photo', { method: 'POST', form });
    toast('Photos added');
    redraw();
  });
  const noteBox = h('textarea', { class: 'ws-input', rows: 3 }, note.value || '');
  view.append(
    pageHead('Garden', 'Beds and plants drive the public Garden map. Save in the planner after changes.',
      h('a', { class: 'btn', href: '/garden', target: '_blank' }, 'View ↗')),
    host,
    card(h('span', {}, `Photos (${photos.length})`, h('button', { class: 'btn small', onclick: () => files.click() }, '+ Photos')), files,
      photos.length ? h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '8px' } },
        photos.map(p => h('figure', { style: { margin: 0, position: 'relative' } },
          h('img', { src: p.url, alt: p.caption || 'Garden photo', loading: 'lazy', style: { width: '100%', aspectRatio: '1', objectFit: 'cover', borderRadius: '8px' } }),
          h('button', { class: 'btn small danger', style: { position: 'absolute', top: '4px', right: '4px' }, 'aria-label': 'Delete photo',
            onclick: async () => { if (await confirmDelete('this photo')) run(null, async () => { await api('/api/garden/photo/' + p.id, { method: 'DELETE' }); redraw(); }); } }, '✕'))))
        : empty('No photos yet.')),
    card('Gallery note', noteBox, h('button', { class: 'btn small', style: { marginTop: '8px' }, onclick: e => run(e.target, async () => {
      await api('/api/v1/site-text/garden.gallery_note', { method: 'PUT', body: { value: noteBox.value } }); toast('Saved');
    }) }, 'Save note')));
  requestAnimationFrame(() => window.gdn?.init?.());

  return () => {
    const home = document.getElementById('ws-planner-home');
    if (planner && home) home.append(planner);
  };
}
