/**
 * garden-page.js — the public Garden page: what the garden is about, photos you
 * can narrow to a bed or a plant type, the map, and what grows in each bed.
 * Photos and their tags are managed in the workspace (Site › Garden).
 * Exposes: window.VW.GardenPage (onEnter)
 */
'use strict';
window.VW = window.VW || {};

window.VW.GardenPage = (() => {
  const h = (...a) => window.VW.h(...a);
  const HERO_IMAGE = '/static/91070B97-0D46-40F6-8858-1C32F1E88A41.jpeg';
  let data = null;
  let filter = { bed: '', plant: '' };

  async function onEnter() {
    const root = document.getElementById('garden-root');
    try {
      data = await VW.getJSON('/api/v1/garden');
    } catch (e) {
      root.replaceChildren(h('div', { class: 'site-wrap' }, h('p', { class: 'site-muted' }, 'The garden could not load. ' + e.message)));
      return;
    }
    document.title = 'Garden · Will Yoste';
    const map = document.getElementById('gdn-viewer-section');
    root.replaceChildren(header(), gallery(), h('div', { class: 'site-wrap gd-map', id: 'gd-map' }), beds());
    document.getElementById('gd-map').append(map);
    GDN?.Viewer?.init?.();
  }

  const bedName = id => data.beds.find(b => b.id === id)?.name || id;
  const plant = id => data.species.find(s => s.id === id);
  const plantLabel = id => { const s = plant(id); return s ? `${s.emoji ? s.emoji + ' ' : ''}${s.name}` : id; };

  function header() {
    const t = data.totals;
    return h('header', { class: 'gd-hero' }, h('div', { class: 'site-wrap gd-hero-in' },
      h('div', { class: 'gd-hero-text' },
        h('div', { class: 'site-eyebrow' }, 'The garden'),
        h('h1', { class: 'site-title' }, data.text.note || 'The garden'),
        data.text.philosophy ? h('p', { class: 'site-lede' }, data.text.philosophy) : null,
        h('ul', { class: 'gd-totals', 'aria-label': 'The garden in numbers' },
          [[t.beds, 'beds'], [t.plants, 'plants'], [t.species, 'plant types'], [t.photos, 'photos']].map(([n, label]) =>
            h('li', {}, h('strong', {}, String(n)), ' ', label)))),
      h('img', { class: 'gd-hero-img', src: HERO_IMAGE, alt: '' })));
  }

  // ── Photos ─────────────────────────────────────────────────────────────────
  function gallery() {
    const section = h('section', { class: 'site-wrap gd-section', id: 'gd-photos', 'aria-labelledby': 'gd-photos-h' });
    const draw = () => {
      const shown = data.photos.filter(p => (!filter.bed || p.beds.includes(filter.bed)) && (!filter.plant || p.species.includes(filter.plant)));
      const tagged = data.species.filter(s => s.photos);
      section.replaceChildren(...[   // replaceChildren would print a null as text
        h('div', { class: 'gd-section-hd' }, h('h2', { id: 'gd-photos-h' }, 'Photos'),
          h('span', { class: 'site-muted', 'aria-live': 'polite' }, `${shown.length} of ${data.photos.length}`)),
        data.photos.length ? h('div', { class: 'gd-filters' },
          h('div', { class: 'site-chips', role: 'group', 'aria-label': 'Show photos of a bed' },
            [['', 'Every bed'], ...data.beds.filter(b => b.photos).map(b => [b.id, b.name])].map(([id, label]) =>
              h('button', { class: 'site-chip', 'aria-pressed': String(filter.bed === id), onclick: () => { filter.bed = id; draw(); } }, label))),
          tagged.length ? h('select', { class: 'site-input gd-select', 'aria-label': 'Show photos of a plant type',
            onchange: e => { filter.plant = e.target.value; draw(); } },
            h('option', { value: '' }, 'Every plant type'),
            tagged.map(s => h('option', { value: s.id, selected: filter.plant === s.id }, `${s.emoji ? s.emoji + ' ' : ''}${s.name} (${s.photos})`))) : null) : null,
        shown.length ? h('div', { class: 'gd-grid' }, shown.map((p, i) => h('button', { class: 'gd-photo', onclick: () => open(shown, i) },
          h('img', { src: p.url, alt: p.caption || 'Garden photo', loading: 'lazy' }),
          p.caption ? h('span', {}, p.caption) : null)))
          : h('p', { class: 'site-muted gd-empty' }, data.photos.length ? 'No photos of that yet.' : 'No photos yet — check back soon.')].filter(Boolean));
    };
    draw();
    section.redraw = draw;
    return section;
  }

  function open(list, index) {
    const box = document.getElementById('gd-lightbox');
    const show = i => {
      const p = list[i];
      box.replaceChildren(...[
        h('button', { class: 'gd-lb-close', 'aria-label': 'Close', onclick: () => box.close() }, '×'),
        h('figure', {}, h('img', { src: p.url, alt: p.caption || 'Garden photo' }),
          h('figcaption', {},
            p.caption ? h('strong', {}, p.caption) : null,
            p.date ? h('span', { class: 'site-muted' }, new Date(p.date + 'T12:00:00').toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })) : null,
            h('div', { class: 'gd-lb-tags' }, [...p.beds.map(id => ['bed', id, bedName(id)]), ...p.species.map(id => ['plant', id, plantLabel(id)])]
              .map(([kind, id, label]) => h('button', { class: 'site-chip', onclick: () => { box.close(); filter = { bed: kind === 'bed' ? id : '', plant: kind === 'plant' ? id : '' };
                const g = document.getElementById('gd-photos'); g.redraw(); g.scrollIntoView({ behavior: 'smooth' }); } }, label))))),
        list.length > 1 ? h('div', { class: 'gd-lb-nav' },
          h('button', { class: 'site-btn site-btn-ghost', 'aria-label': 'Previous photo', onclick: () => show((i - 1 + list.length) % list.length) }, '‹'),
          h('span', { class: 'site-muted' }, `${i + 1} / ${list.length}`),
          h('button', { class: 'site-btn site-btn-ghost', 'aria-label': 'Next photo', onclick: () => show((i + 1) % list.length) }, '›')) : null].filter(Boolean));
    };
    show(index);
    box.onkeydown = e => { if (e.key === 'ArrowLeft') box.querySelector('[aria-label="Previous photo"]')?.click(); if (e.key === 'ArrowRight') box.querySelector('[aria-label="Next photo"]')?.click(); };
    box.onclick = e => { if (e.target === box) box.close(); };
    box.showModal();
  }

  // ── Beds ───────────────────────────────────────────────────────────────────
  function beds() {
    return h('section', { class: 'site-wrap gd-section', 'aria-labelledby': 'gd-beds-h' },
      h('div', { class: 'gd-section-hd' }, h('h2', { id: 'gd-beds-h' }, 'What’s growing')),
      h('div', { class: 'gd-beds' }, data.beds.map(b => h('article', { class: 'gd-bed', style: { borderTopColor: b.color } },
        h('div', { class: 'gd-bed-hd' }, h('h3', {}, b.name), h('span', { class: 'site-muted' }, b.plants ? `${b.plants} plants` : 'Resting')),
        b.species.length ? h('ul', { class: 'gd-bed-plants' }, b.species.map(s => h('li', {}, `${s.emoji ? s.emoji + ' ' : ''}${s.name}`))) : null,
        b.photos ? h('button', { class: 'gd-bed-photos', onclick: () => {
          filter = { bed: b.id, plant: '' };
          const g = document.getElementById('gd-photos'); g.redraw(); g.scrollIntoView({ behavior: 'smooth' });
        } }, `${b.photos} photo${b.photos === 1 ? '' : 's'} →`) : null))));
  }

  return { onEnter };
})();
