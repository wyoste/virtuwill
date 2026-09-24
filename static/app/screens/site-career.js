// Site › Career: everything the public Career page shows — the header and ethos,
// experience, projects, skills, certifications and education.
import { h, api, card, pageHead, tabs, empty, toast, run, dialog, field, values, confirmDelete } from '../lib.js';

const TABS = [['/app/site/career', 'Profile & ethos'], ['/app/site/career/experience', 'Experience'],
              ['/app/site/career/projects', 'Projects'], ['/app/site/career/skills', 'Skills & education']];

export async function render(view, ctx) {
  const tab = ctx.path.split('/')[4] || 'profile';
  const d = await api('/api/v1/career?view=owner');
  const redraw = () => { view.replaceChildren(); return render(view, ctx); };
  view.append(pageHead('Career', 'Your CV, work ethos and projects — one public page.',
      h('a', { class: 'btn', href: '/career', target: '_blank' }, 'View ↗')),
    tabs(TABS, ctx.path));
  return ({ profile, experience, projects, skills }[tab] || profile)(view, d, redraw);
}

// ── A list of records edited in a dialog ─────────────────────────────────────
// fields: [name, label, kind, options]; kinds: text, textarea, month, lines (one per line), csv (comma separated), checkbox, select, int
function editor(title, fields, item = {}) {
  const shown = (name, kind) => {
    const v = item[name];
    if (kind === 'month') return v ? v.slice(0, 7) : '';
    if (kind === 'lines') return (v || []).join('\n');
    if (kind === 'csv') return (v || []).join(', ');
    return v ?? (kind === 'checkbox' ? true : '');
  };
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() }, fields.map(([name, label, kind = 'text', opts = {}]) =>
    field(label, name, { kind: kind === 'lines' ? 'textarea' : ['csv', 'int'].includes(kind) ? (kind === 'int' ? 'number' : 'text') : kind,
      value: shown(name, kind), wide: opts.wide ?? ['textarea', 'lines', 'csv'].includes(kind), options: opts.options, placeholder: opts.placeholder })));
  const read = () => {
    const out = values(form);
    for (const [name, , kind] of fields) {
      if (kind === 'month') out[name] = out[name] ? out[name] + '-01' : null;
      if (kind === 'lines') out[name] = (out[name] || '').split('\n').map(s => s.trim()).filter(Boolean);
      if (kind === 'csv') out[name] = (out[name] || '').split(',').map(s => s.trim()).filter(Boolean);
    }
    return out;
  };
  return { form, read, title };
}

async function edit(spec, save) {
  const error = h('p', { class: 'ws-note warn', role: 'alert' });
  for (;;) {
    if (!(await dialog(spec.title, h('div', {}, spec.form, error), [['Cancel', null], ['Save', true]]))) return false;
    try { await save(spec.read()); return true; }
    catch (e) { error.textContent = e.message; }
  }
}

function list(items, { url, idKey, fields, noun, label, meta, redraw, extra, create = {} }) {
  const add = h('button', { class: 'btn small', onclick: async () => {
    if (await edit(editor('Add ' + noun, fields, create), body => api(url, { method: 'POST', body: { ...create, ...body } }))) redraw();
  } }, '+ Add ' + noun);
  return h('div', {},
    items.length ? h('ul', { class: 'ws-list' }, items.map(item => h('li', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, label(item)), meta ? h('div', { class: 'ws-row-meta' }, meta(item)) : null),
      h('div', { class: 'ws-row-end' },
        extra ? extra(item) : null,
        h('button', { class: 'btn small', onclick: async () => {
          if (await edit(editor('Edit ' + noun, fields, item), body => api(`${url}/${item[idKey]}`, { method: 'PUT', body }))) redraw();
        } }, 'Edit'),
        h('button', { class: 'btn small danger', 'aria-label': 'Delete ' + label(item), onclick: async () => {
          if (!(await confirmDelete(label(item)))) return;
          await run(null, async () => { await api(`${url}/${item[idKey]}`, { method: 'DELETE' }); redraw(); });
        } }, '✕'))))) : empty(`No ${noun}s yet.`),
    h('div', { style: { marginTop: '10px' } }, add));
}

const monthYear = iso => iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : 'present';

// ── Profile & ethos ──────────────────────────────────────────────────────────
function profile(view, d, redraw) {
  const p = d.profile;
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Name', 'full_name', { value: p.full_name }), field('Headline', 'headline', { value: p.headline }),
    field('Organization', 'organization', { value: p.organization }), field('Location', 'location', { value: p.location }),
    field('Email', 'email', { value: p.email }), field('Phone (shown publicly when set)', 'phone', { value: p.phone }),
    field('LinkedIn URL', 'linkedin_url', { value: p.linkedin_url, wide: true }),
    field('Ethos eyebrow', 'ethos_eyebrow', { value: p.ethos_eyebrow }),
    field('Ethos headline', 'ethos_headline', { value: p.ethos_headline, wide: true }),
    field('Ethos summary', 'ethos_summary', { kind: 'textarea', value: p.ethos_summary, wide: true }),
    h('div', { style: { flexBasis: '100%' } }, h('button', { class: 'btn primary', onclick: e => run(e.currentTarget, async () => {
      await api('/api/v1/career/profile', { method: 'PUT', body: values(form) });
      toast('Profile saved');
    }) }, 'Save profile')));

  const cvInput = h('input', { type: 'file', accept: 'application/pdf', hidden: true });
  cvInput.onchange = () => run(null, async () => {
    const f = new FormData();
    f.append('file', cvInput.files[0]);
    await api('/api/v1/career/cv', { method: 'POST', form: f });
    toast('CV uploaded');
    redraw();
  });
  const hl = (section, noun, fields) => list(d.highlights[section], {
    url: '/api/v1/career/highlights', idKey: 'highlight_id', noun, redraw, create: { section, position: d.highlights[section].length },
    fields: [...fields, ['position', 'Order', 'int']], label: i => `${i.icon ? i.icon + ' ' : ''}${i.title}`, meta: i => i.body });

  view.append(
    card('Header and ethos', form),
    card(h('span', {}, 'CV download', h('span', {}, h('a', { class: 'btn small', href: '/career/cv' }, 'Download current'), ' ',
        h('button', { class: 'btn small', onclick: () => cvInput.click() }, 'Upload new PDF'), cvInput,
        p.cv_uploaded ? h('button', { class: 'btn small', onclick: e => run(e.currentTarget, async () => {
          await api('/api/v1/career/cv', { method: 'DELETE' }); redraw();
        }) }, 'Use the bundled PDF') : null)),
      h('p', { class: 'ws-note' }, p.cv_uploaded ? 'Visitors download the PDF you uploaded.' : 'Visitors download the PDF bundled with the site. Upload a newer one to replace it.')),
    h('div', { class: 'ws-grid two' },
      card('Impact figures', hl('impact', 'figure', [['title', 'Figure ($6M, 8yr)'], ['body', 'What it measures', 'textarea']])),
      card('Pillars', hl('pillar', 'pillar', [['icon', 'Icon (one emoji)'], ['title', 'Name'], ['body', 'What it means', 'textarea']]))),
    card('What I bring', hl('strength', 'strength', [['title', 'Strength'], ['body', 'Detail', 'textarea']])));
}

// ── Experience ───────────────────────────────────────────────────────────────
const ROLE_FIELDS = [['title', 'Title'], ['organization', 'Organization'], ['location', 'Location'],
  ['slug', 'Short name (for links)', 'text', { placeholder: 'e.g. greystar' }],
  ['started_on', 'Started', 'month'], ['ended_on', 'Ended (blank if current)', 'month'],
  ['summary', 'Summary (optional)', 'textarea'], ['bullets', 'Accomplishments, one per line', 'lines'],
  ['tags', 'Technologies, comma separated', 'csv'], ['visible', 'Show on the public page', 'checkbox']];

function experience(view, d, redraw) {
  view.append(card(null, list(d.roles, {
    url: '/api/v1/career/roles', idKey: 'role_id', noun: 'role', fields: ROLE_FIELDS, redraw,
    label: r => `${r.title} · ${r.organization}`,
    meta: r => [`${monthYear(r.started_on)} – ${monthYear(r.ended_on)}`, `${r.bullets.length} accomplishments`,
                `${d.projects.filter(p => p.role_id === r.role_id).length} projects`, r.visible ? null : 'hidden'].filter(Boolean).join(' · '),
  })), h('p', { class: 'ws-note' }, 'Roles are ordered by date, newest first. Link projects to a role on the Projects tab.'));
}

// ── Projects ─────────────────────────────────────────────────────────────────
function projects(view, d, redraw) {
  const upload = h('input', { type: 'file', accept: '.html', hidden: true });
  upload.onchange = () => run(null, async () => {
    const form = new FormData();
    form.append('file', upload.files[0]);
    await api('/api/portfolio/upload', { method: 'POST', form });
    toast('Project page uploaded');
    redraw();
  });
  const roles = [['', 'No role (personal or other)'], ...d.roles.map(r => [String(r.role_id), `${r.organization} — ${r.title}`])];
  const roleName = id => d.roles.find(r => r.role_id === id)?.organization;
  const all = d.projects;
  view.append(
    h('div', { class: 'ws-filters' }, h('button', { class: 'btn', onclick: () => upload.click() }, 'Upload a project walkthrough (.html)'), upload),
    card(null, all.length ? h('ul', { class: 'ws-list' }, all.map((p, i) => h('li', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, p.title),
        h('div', { class: 'ws-row-meta' }, [p.tag, roleName(p.role_id) || 'no role', p.builtin ? 'written here' : 'uploaded walkthrough',
          p.chips.slice(0, 4).join(', ')].filter(Boolean).join(' · '))),
      h('div', { class: 'ws-row-end' },
        h('span', { class: 'ws-chip ' + (p.visible ? 'good' : '') }, p.visible ? 'Live' : 'Hidden'),
        h('button', { class: 'btn small', disabled: i === 0, 'aria-label': 'Move up', onclick: () => move(all, i, -1, redraw) }, '↑'),
        h('button', { class: 'btn small', disabled: i === all.length - 1, 'aria-label': 'Move down', onclick: () => move(all, i, 1, redraw) }, '↓'),
        h('button', { class: 'btn small', onclick: () => editProject(p, roles, redraw) }, 'Edit'),
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

async function editProject(p, roles, redraw) {
  const spec = editor('Edit project', [
    ['title', 'Title', 'text', { wide: true }], ['tag', 'Tag'],
    ['role_id', 'From role', 'select', { options: roles }],
    ['chips', 'Technologies, comma separated', 'csv'], ['description', 'Card summary', 'textarea'],
    ...(p.builtin ? [['subtitle', 'Subtitle', 'text', { wide: true }], ['overview', 'Overview', 'textarea']] : []),
    ['visible', 'Show on the public page', 'checkbox']], { ...p, role_id: p.role_id ? String(p.role_id) : '' });
  if (await edit(spec, body => api('/api/v1/projects/' + encodeURIComponent(p.id), { method: 'PUT', body }))) redraw();
}

// ── Skills, certifications and education ─────────────────────────────────────
function skills(view, d, redraw) {
  view.append(
    card('Skill groups', list(d.skills, {
      url: '/api/v1/career/skill-groups', idKey: 'group_id', noun: 'skill group', redraw, create: { position: d.skills.length },
      fields: [['label', 'Group'], ['skills', 'Skills, comma separated', 'csv'], ['featured', 'Emphasise, comma separated', 'csv'], ['position', 'Order', 'int']],
      label: g => g.label, meta: g => g.skills.join(', ') })),
    h('div', { class: 'ws-grid two' },
      card('Certifications', list(d.highlights.certification, {
        url: '/api/v1/career/highlights', idKey: 'highlight_id', noun: 'certification', redraw,
        create: { section: 'certification', position: d.highlights.certification.length },
        fields: [['icon', 'Icon (one emoji)'], ['title', 'Certification'], ['body', 'Short name (PMP)'], ['position', 'Order', 'int']],
        label: c => `${c.icon ? c.icon + ' ' : ''}${c.title}`, meta: c => c.body })),
      card('Education', list(d.education, {
        url: '/api/v1/career/education', idKey: 'education_id', noun: 'degree', redraw,
        fields: [['degree', 'Degree'], ['field_of_study', 'Field of study'], ['school', 'School'], ['location', 'Location'],
                 ['finished_on', 'Finished', 'month'], ['grade', 'Grade (optional)'], ['highlight_label', 'Highlight label'],
                 ['highlight', 'Highlight', 'textarea'], ['visible', 'Show on the public page', 'checkbox']],
        label: e => `${e.degree}${e.field_of_study ? ', ' + e.field_of_study : ''}`,
        meta: e => [e.school, e.finished_on ? monthYear(e.finished_on) : null, e.visible ? null : 'hidden'].filter(Boolean).join(' · ') }))));
}
