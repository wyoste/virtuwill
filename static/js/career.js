/**
 * career.js — the professional portfolio: CV, work ethos and project explorer on one page.
 *
 *   /career                  header, ethos, experience, projects, skills, education
 *   /career#projects         any section by its anchor
 *   /career/projects/<id>    one project: overview, outcomes, timeline and the role it
 *                            came from, or its uploaded walkthrough in a sandboxed frame
 * Old /resume and /projects links land here. Exposes: window.VW.Career (onEnter)
 */
'use strict';
window.VW = window.VW || {};

window.VW.Career = (() => {
  const h = (...a) => window.VW.h(...a);
  const SECTIONS = [['ethos', 'Ethos'], ['experience', 'Experience'], ['projects', 'Projects'], ['skills', 'Skills'], ['education', 'Education']];
  let data = null;
  let filter = { tech: '', role: '', query: '' };
  let observer = null;

  const month = iso => iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : 'Present';
  function span(start, end) {
    const a = new Date(start + 'T12:00:00'), b = end ? new Date(end + 'T12:00:00') : new Date();
    const months = Math.max(1, (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth());
    const y = Math.floor(months / 12), m = months % 12;
    return [y ? `${y} yr` : '', m ? `${m} mo` : ''].filter(Boolean).join(' ');
  }
  const roleOf = p => data.roles.find(r => r.role_id === p.role_id);

  async function onEnter(sub, rest) {
    const root = document.getElementById('career-root');
    const frameWrap = document.getElementById('career-frame-wrap');
    frameWrap.hidden = true;
    freshFrame();   // unload any walkthrough without adding a history entry
    root.hidden = false;
    if (!data) root.replaceChildren(h('div', { class: 'site-wrap' }, h('p', { class: 'site-muted' }, 'Loading…')));
    try {
      data = data || await VW.getJSON('/api/v1/career');
    } catch (e) {
      root.replaceChildren(h('div', { class: 'site-wrap' }, h('p', { class: 'site-muted' }, 'This page could not load. ' + e.message)));
      return;
    }
    if (sub === 'projects' && rest) {
      const p = data.projects.find(x => x.id === rest);
      if (!p) { root.replaceChildren(h('div', { class: 'site-wrap' }, h('p', {}, 'That project isn’t here.'), h('a', { href: '/career#projects', 'data-link': '' }, '← All projects'))); return; }
      document.title = p.title + ' · ' + data.profile.full_name;
      if (p.url && !p.builtin) {
        // Uploaded walkthroughs run in a sandbox with no access to this site.
        root.hidden = true;
        frameWrap.hidden = false;
        document.getElementById('career-frame-title').textContent = p.title;
        freshFrame(p.url);
        return;
      }
      root.replaceChildren(project(p));
      return;
    }
    document.title = 'Career · ' + data.profile.full_name;
    root.replaceChildren(overview());
    watchSections(root);
    const target = location.hash.slice(1) || (sub === 'projects' ? 'projects' : '');
    if (target) requestAnimationFrame(() => document.getElementById('cr-' + target)?.scrollIntoView());
  }

  // A new frame per walkthrough: changing an existing frame's address would add
  // browser history entries, so Back would stop leaving the page.
  function freshFrame(url) {
    const old = document.getElementById('career-iframe');
    const frame = old.cloneNode(false);
    frame.removeAttribute('src');
    if (url) frame.src = url;
    old.replaceWith(frame);
  }

  // ── The page ───────────────────────────────────────────────────────────────
  function overview() {
    const p = data.profile, hl = data.highlights;
    return h('div', { class: 'cr' },
      h('header', { class: 'cr-hero' }, h('div', { class: 'site-wrap cr-hero-in' },
        h('div', { class: 'cr-id' },
          h('div', { class: 'cr-avatar', 'aria-hidden': 'true' }, (p.full_name || '').split(/\s+/).map(w => w[0]).join('').slice(0, 2)),
          h('div', {},
            h('h1', { class: 'cr-name' }, p.full_name),
            h('p', { class: 'cr-role' }, [p.headline, p.organization].filter(Boolean).join(' · ')),
            h('p', { class: 'cr-meta' }, [p.location, p.email, p.phone].filter(Boolean).join('  ·  ')))),
        h('div', { class: 'cr-actions' },
          h('a', { class: 'site-btn', href: '/career/cv', download: '' }, '⭳ Download CV'),
          p.linkedin_url ? h('a', { class: 'site-btn site-btn-ghost', href: p.linkedin_url, target: '_blank', rel: 'noopener' }, 'LinkedIn ↗') : null,
          h('a', { class: 'site-btn site-btn-ghost', href: '/contact', 'data-link': '' }, 'Say hi')))),
      h('nav', { class: 'cr-tabs', 'aria-label': 'Career sections' }, h('div', { class: 'site-wrap cr-tabs-in' },
        SECTIONS.map(([id, label]) => h('a', { href: '#' + id, 'data-section': id,
          onclick: e => { e.preventDefault(); jump(id); } }, label)))),
      h('div', { class: 'site-wrap cr-body' },
        section('ethos', p.ethos_eyebrow || 'Ethos', p.ethos_headline, p.ethos_summary ? h('p', { class: 'cr-lede' }, p.ethos_summary) : null,
          hl.impact.length ? h('div', { class: 'cr-impact' }, hl.impact.map(i => h('div', {}, h('strong', {}, i.title), h('span', {}, i.body)))) : null,
          hl.pillar.length ? h('div', { class: 'cr-pillars' }, hl.pillar.map(i => h('article', { class: 'cr-card' },
            h('div', { class: 'cr-card-icon', 'aria-hidden': 'true' }, i.icon), h('h3', {}, i.title), h('p', {}, i.body)))) : null,
          hl.strength.length ? h('div', {}, h('h3', { class: 'cr-sub' }, 'What I bring'),
            h('dl', { class: 'cr-strengths' }, hl.strength.map(i => h('div', {}, h('dt', {}, i.title), h('dd', {}, i.body))))) : null),
        section('experience', 'Experience', 'Where I’ve worked', h('ol', { class: 'cr-roles' }, data.roles.map(role))),
        section('projects', 'Projects', 'Explore what I’ve built', explorer()),
        section('skills', 'Skills', 'Tools and certifications',
          h('div', { class: 'cr-skills' }, data.skills.map(g => h('div', { class: 'cr-skill-group' }, h('h3', {}, g.label),
            h('div', { class: 'pj-chips' }, g.skills.map(s => h('span', { class: 'pj-chip' + (g.featured.includes(s) ? ' cr-featured' : '') }, s)))))),
          hl.certification.length ? h('div', { class: 'cr-certs' }, hl.certification.map(c => h('div', { class: 'cr-cert' },
            h('span', { class: 'cr-card-icon', 'aria-hidden': 'true' }, c.icon), h('div', {}, h('strong', {}, c.title), h('span', {}, c.body))))) : null),
        section('education', 'Education', null, h('div', { class: 'cr-edu' }, data.education.map(e => h('article', { class: 'cr-card' },
          h('div', { class: 'cr-edu-head' }, h('div', {}, h('h3', {}, e.degree), e.field_of_study ? h('p', { class: 'cr-strong' }, e.field_of_study) : null,
            h('p', { class: 'site-muted' }, [e.school, e.location].filter(Boolean).join(' · '))),
            h('div', { class: 'cr-period' }, e.finished_on ? month(e.finished_on) : '', e.grade ? h('div', {}, e.grade) : null)),
          e.highlight ? h('div', { class: 'cr-note' }, h('strong', {}, e.highlight_label || 'Highlight'), h('p', {}, e.highlight)) : null))))));
  }

  function section(id, eyebrow, title, ...body) {
    return h('section', { class: 'cr-section', id: 'cr-' + id, 'aria-labelledby': 'cr-h-' + id },
      h('div', { class: 'site-eyebrow' }, eyebrow),
      title ? h('h2', { class: 'cr-h', id: 'cr-h-' + id }, title) : h('h2', { class: 'cr-h visually-hidden', id: 'cr-h-' + id }, eyebrow),
      ...body);
  }

  function role(r) {
    const projects = data.projects.filter(p => p.role_id === r.role_id);
    const bullets = h('ul', { class: 'cr-bullets' }, r.bullets.map(b => h('li', {}, b)));
    const long = r.bullets.length > 4;
    if (long) bullets.classList.add('clipped');
    return h('li', { class: 'cr-role-item' },
      h('div', { class: 'cr-role-when' }, h('strong', {}, `${month(r.started_on)} – ${month(r.ended_on)}`), h('span', {}, span(r.started_on, r.ended_on))),
      h('article', { class: 'cr-card' },
        h('h3', {}, r.title), h('p', { class: 'cr-strong' }, [r.organization, r.location].filter(Boolean).join(' · ')),
        r.summary ? h('p', {}, r.summary) : null,
        bullets,
        long ? h('button', { class: 'cr-more', 'aria-expanded': 'false', onclick: e => {
          const open = bullets.classList.toggle('clipped') === false;
          e.target.setAttribute('aria-expanded', String(open));
          e.target.textContent = open ? 'Show less' : `Show all ${r.bullets.length}`;
        } }, `Show all ${r.bullets.length}`) : null,
        r.tags.length ? h('div', { class: 'pj-chips' }, r.tags.map(t => h('span', { class: 'pj-chip' }, t))) : null,
        projects.length ? h('div', { class: 'cr-role-projects' }, h('span', {}, 'Projects:'), projects.map(p =>
          h('a', { href: '/career/projects/' + encodeURIComponent(p.id), 'data-link': '' }, p.title + ' →'))) : null));
  }

  // ── Project explorer ───────────────────────────────────────────────────────
  function explorer() {
    const techs = [...new Set(data.projects.flatMap(p => p.chips))].sort((a, b) => a.localeCompare(b));
    const roles = data.roles.filter(r => data.projects.some(p => p.role_id === r.role_id));
    const grid = h('div', { class: 'pj-grid' });
    const count = h('p', { class: 'site-muted', 'aria-live': 'polite' });
    const search = h('input', { class: 'site-input', type: 'search', placeholder: 'Search projects', value: filter.query, 'aria-label': 'Search projects' });
    const where = h('select', { class: 'site-input cr-select', 'aria-label': 'Filter by role' },
      h('option', { value: '' }, 'Every role'), roles.map(r => h('option', { value: r.role_id, selected: String(r.role_id) === filter.role }, r.organization)),
      h('option', { value: 'none', selected: filter.role === 'none' }, 'Personal & other'));
    const chips = h('div', { class: 'site-chips', role: 'group', 'aria-label': 'Filter by technology' });
    const drawChips = () => chips.replaceChildren(...[['', 'All'], ...techs.map(t => [t, t])].map(([v, t]) =>
      h('button', { class: 'site-chip', 'aria-pressed': String(filter.tech === v), onclick: () => { filter.tech = v; drawChips(); draw(); } }, t)));
    const draw = () => {
      filter.query = search.value.trim().toLowerCase();
      filter.role = where.value;
      const shown = data.projects.filter(p => (!filter.tech || p.chips.includes(filter.tech))
        && (!filter.role || (filter.role === 'none' ? !p.role_id : String(p.role_id) === filter.role))
        && (!filter.query || [p.title, p.description, p.tag, p.subtitle, ...p.chips, roleOf(p)?.organization].join(' ').toLowerCase().includes(filter.query)));
      count.textContent = `${shown.length} of ${data.projects.length} projects`;
      grid.replaceChildren(...(shown.length ? shown.map(card) : [h('p', { class: 'site-muted' }, 'No projects match.')]));
    };
    search.oninput = draw;
    where.onchange = draw;
    drawChips();
    draw();
    return h('div', {}, h('div', { class: 'site-toolbar' }, search, where, count), techs.length ? chips : null, grid);
  }

  function card(p) {
    const r = roleOf(p);
    return h('a', { class: 'pj-card', href: '/career/projects/' + encodeURIComponent(p.id), 'data-link': '' },
      h('div', { class: 'cr-card-top' }, h('span', { class: 'pj-tag pj-tag-' + (p.tag_class || 'platform') }, p.tag || 'Project'),
        r ? h('span', { class: 'cr-from' }, r.organization) : null),
      h('h3', { class: 'pj-title' }, p.title),
      h('p', { class: 'pj-desc' }, p.description || p.subtitle || ''),
      p.metrics?.length ? h('div', { class: 'pj-metrics' }, p.metrics.slice(0, 2).map(m => h('span', {}, h('strong', {}, m.value), ' ', m.label))) : null,
      h('div', { class: 'pj-chips' }, p.chips.slice(0, 6).map(c => h('span', { class: 'pj-chip' }, c))),
      h('span', { class: 'pj-open' }, p.url && !p.builtin ? 'Open walkthrough →' : 'Read more →'));
  }

  function project(p) {
    const r = roleOf(p);
    return h('div', { class: 'site-wrap' },
      h('a', { class: 'site-back', href: '/career#projects', 'data-link': '' }, '← All projects'),
      h('header', { class: 'site-hero' }, h('span', { class: 'pj-tag pj-tag-' + (p.tag_class || 'platform') }, p.tag),
        h('h1', { class: 'site-title' }, p.title), p.subtitle ? h('p', { class: 'site-lede' }, p.subtitle) : null,
        r ? h('p', { class: 'cr-from-line' }, 'From my time as ', h('strong', {}, r.title), ' at ',
          h('a', { href: '/career#experience', 'data-link': '' }, r.organization), ` (${month(r.started_on)} – ${month(r.ended_on)})`) : null,
        h('div', { class: 'pj-chips' }, p.chips.map(c => h('span', { class: 'pj-chip' }, c)))),
      p.metrics?.length ? h('div', { class: 'pj-metric-row' }, p.metrics.map(m => h('div', { class: 'pj-metric' },
        h('div', { class: 'pj-metric-value' }, m.value), h('div', { class: 'pj-metric-label' }, m.label)))) : null,
      p.overview || p.description ? h('section', { class: 'site-section' }, h('h2', {}, 'Overview'),
        ...(p.overview || p.description).split(/\n{2,}/).map(t => h('p', { class: 'site-prose' }, t))) : null,
      p.timeline?.length ? h('section', { class: 'site-section' }, h('h2', {}, 'How it came together'),
        h('ol', { class: 'pj-timeline' }, p.timeline.map(t => h('li', {}, h('div', { class: 'pj-period' }, t.period),
          h('div', {}, h('strong', {}, t.phase), t.description ? h('p', { class: 'site-prose' }, t.description) : null))))) : null);
  }

  // ── Section tabs follow the scroll ─────────────────────────────────────────
  function jump(id) {
    document.getElementById('cr-' + id)?.scrollIntoView({ behavior: 'smooth' });
    history.replaceState({}, '', '/career#' + id);
  }

  function watchSections(root) {
    observer?.disconnect();
    const links = [...root.querySelectorAll('.cr-tabs a')];
    const mark = id => links.forEach(a => {
      const on = a.dataset.section === id;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
    mark('ethos');
    observer = new IntersectionObserver(entries => {
      const top = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (top) mark(top.target.id.slice(3));
    }, { rootMargin: '-140px 0px -55% 0px' });
    root.querySelectorAll('.cr-section').forEach(s => observer.observe(s));
  }

  return { onEnter };
})();
