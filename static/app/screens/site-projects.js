// Site › Projects: what the public Projects page shows, in what order.
import { h, api, card, pageHead, empty, toast, run, dialog, field, values, confirmDelete } from '../lib.js';

export async function render(view) {
  const projects = await api('/api/v1/projects?view=owner');
  const redraw = () => { view.replaceChildren(); render(view); };
  const upload = h('input', { type: 'file', accept: '.html', hidden: true });
  upload.onchange = () => run(null, async () => {
    const form = new FormData();
    form.append('file', upload.files[0]);
    await api('/api/portfolio/upload', { method: 'POST', form });
    toast('Project page uploaded');
    redraw();
  });
  view.append(
    pageHead('Projects', 'Technology projects visitors can explore. Order, hide or edit each one.',
      h('button', { class: 'btn primary', onclick: () => upload.click() }, 'Upload a project page (.html)'), upload),
    card(null, projects.length ? h('ul', { class: 'ws-list' }, projects.map((p, i) => h('li', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, p.title),
        h('div', { class: 'ws-row-meta' }, [p.tag, p.builtin ? 'written here' : 'uploaded page', p.chips.slice(0, 4).join(', ')].filter(Boolean).join(' · '))),
      h('div', { class: 'ws-row-end' },
        h('span', { class: 'ws-chip ' + (p.visible ? 'good' : '') }, p.visible ? 'Live' : 'Hidden'),
        h('button', { class: 'btn small', disabled: i === 0, 'aria-label': 'Move up', onclick: () => move(projects, i, -1, redraw) }, '↑'),
        h('button', { class: 'btn small', disabled: i === projects.length - 1, 'aria-label': 'Move down', onclick: () => move(projects, i, 1, redraw) }, '↓'),
        h('button', { class: 'btn small', onclick: () => edit(p, redraw) }, 'Edit'),
        h('button', { class: 'btn small danger', 'aria-label': 'Remove ' + p.title, onclick: async () => {
          if (!(await confirmDelete(p.title))) return;
          await run(null, async () => { await api('/api/v1/projects/' + encodeURIComponent(p.id), { method: 'DELETE' }); redraw(); });
        } }, '✕'))))) : empty('No projects yet.')));
}

async function move(projects, i, delta, redraw) {
  const order = [...projects];
  order.splice(i + delta, 0, order.splice(i, 1)[0]);
  await run(null, async () => {
    for (const [position, p] of order.entries()) {
      await api('/api/v1/projects/' + encodeURIComponent(p.id), { method: 'PUT', body: { position }, quiet: position < order.length - 1 });
    }
    redraw();
  });
}

async function edit(p, redraw) {
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Title', 'title', { value: p.title, wide: true }), field('Tag', 'tag', { value: p.tag }),
    field('Technologies (comma separated)', 'chips', { value: p.chips.join(', '), wide: true }),
    field('Card summary', 'description', { kind: 'textarea', value: p.description, wide: true }),
    p.builtin ? field('Subtitle', 'subtitle', { value: p.subtitle, wide: true }) : null,
    p.builtin ? field('Overview', 'overview', { kind: 'textarea', value: p.overview, wide: true }) : null,
    field('Show on the public site', 'visible', { kind: 'checkbox', value: p.visible }));
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  for (;;) {
    if (!(await dialog('Edit project', h('div', {}, form, error), [['Cancel', null], ['Save', true]]))) return;
    try {
      const body = values(form);
      body.chips = (body.chips || '').split(',').map(s => s.trim()).filter(Boolean);
      await api('/api/v1/projects/' + encodeURIComponent(p.id), { method: 'PUT', body });
      redraw();
      return;
    } catch (e) { error.textContent = e.message; }
  }
}
