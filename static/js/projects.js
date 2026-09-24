/**
 * projects.js — explore technology projects.
 *
 *   /projects        every project, filterable by technology and searchable
 *   /projects/<id>   one project: overview, outcomes and timeline, or the
 *                    uploaded walkthrough in a sandboxed frame
 * Exposes: window.VW.Projects (onEnter)
 */
'use strict';
window.VW = window.VW || {};

window.VW.Projects = (() => {
  const h = (...a) => window.VW.h(...a);
  let projects = null;
  let tech = '', query = '';

  async function onEnter(id) {
    const root = document.getElementById('projects-root');
    const frameWrap = document.getElementById('project-frame-wrap');
    const frame = document.getElementById('project-iframe');
    frameWrap.hidden = true;
    frame.src = 'about:blank';
    root.hidden = false;
    root.replaceChildren(h('p', { class: 'site-muted' }, 'Loading…'));
    try {
      projects = projects || await VW.getJSON('/api/v1/projects');
      if (!id) return list(root);
      const p = projects.find(x => x.id === id) || await VW.getJSON('/api/v1/projects/' + encodeURIComponent(id));
      document.title = p.title + ' · Projects · Will Yoste';
      if (p.url && !p.builtin) {
        // Uploaded walkthroughs run in a sandbox with no access to this site.
        root.hidden = true;
        frameWrap.hidden = false;
        document.getElementById('project-frame-title').textContent = p.title;
        frame.src = p.url;
        return;
      }
      detail(root, p);
    } catch (e) {
      root.replaceChildren(h('p', { class: 'site-muted' }, e.status === 404 ? 'That project isn’t here.' : 'Projects could not load. ' + e.message),
        h('a', { href: '/projects', 'data-link': '' }, '← All projects'));
    }
  }

  function list(root) {
    document.title = 'Projects · Will Yoste';
    const techs = [...new Set(projects.flatMap(p => p.chips))].sort((a, b) => a.localeCompare(b));
    const search = h('input', { class: 'site-input', type: 'search', placeholder: 'Search projects', value: query, 'aria-label': 'Search projects' });
    const grid = h('div', { class: 'pj-grid' });
    const chips = h('div', { class: 'site-chips', role: 'group', 'aria-label': 'Filter by technology' },
      [['', 'All'], ...techs.map(t => [t, t])].map(([v, t]) => h('button', { class: 'site-chip', 'aria-pressed': String(tech === v),
        onclick: () => { tech = v; list(root); } }, t)));
    const draw = () => {
      query = search.value.trim().toLowerCase();
      const shown = projects.filter(p => (!tech || p.chips.includes(tech))
        && (!query || [p.title, p.description, p.tag, p.subtitle, ...p.chips].join(' ').toLowerCase().includes(query)));
      grid.replaceChildren(...shown.map(card));
      if (!shown.length) grid.replaceChildren(h('p', { class: 'site-muted' }, 'No projects match.'));
    };
    search.oninput = draw;
    draw();
    root.replaceChildren(
      h('header', { class: 'site-hero' }, h('div', { class: 'site-eyebrow' }, 'Data platforms · analytics · tools'),
        h('h1', { class: 'site-title' }, 'Projects'),
        h('p', { class: 'site-lede' }, 'Technology I’ve designed and built. Filter by what interests you, then open one for the problem, the approach and the outcome.')),
      h('div', { class: 'site-toolbar' }, search), techs.length ? chips : null, grid,
      h('p', { class: 'site-muted', style: { marginTop: '28px' } }, 'Looking for the full work history? ', h('a', { href: '/resume', 'data-link': '' }, 'Read my resume →')));
  }

  function card(p) {
    return h('a', { class: 'pj-card', href: '/projects/' + encodeURIComponent(p.id), 'data-link': '' },
      h('span', { class: 'pj-tag pj-tag-' + (p.tag_class || 'platform') }, p.tag || 'Project'),
      h('h2', { class: 'pj-title' }, p.title),
      h('p', { class: 'pj-desc' }, p.description || p.subtitle || ''),
      p.metrics?.length ? h('div', { class: 'pj-metrics' }, p.metrics.slice(0, 2).map(m => h('span', {}, h('strong', {}, m.value), ' ', m.label))) : null,
      h('div', { class: 'pj-chips' }, p.chips.slice(0, 6).map(c => h('span', { class: 'pj-chip' }, c))),
      h('span', { class: 'pj-open' }, p.url && !p.builtin ? 'Open walkthrough →' : 'Read more →'));
  }

  function detail(root, p) {
    root.replaceChildren(
      h('a', { class: 'site-back', href: '/projects', 'data-link': '' }, '← All projects'),
      h('header', { class: 'site-hero' }, h('span', { class: 'pj-tag pj-tag-' + (p.tag_class || 'platform') }, p.tag),
        h('h1', { class: 'site-title' }, p.title), p.subtitle ? h('p', { class: 'site-lede' }, p.subtitle) : null,
        h('div', { class: 'pj-chips' }, p.chips.map(c => h('span', { class: 'pj-chip' }, c)))),
      p.metrics?.length ? h('div', { class: 'pj-metric-row' }, p.metrics.map(m => h('div', { class: 'pj-metric' },
        h('div', { class: 'pj-metric-value' }, m.value), h('div', { class: 'pj-metric-label' }, m.label)))) : null,
      p.overview || p.description ? h('section', { class: 'site-section' }, h('h2', {}, 'Overview'),
        ...(p.overview || p.description).split(/\n{2,}/).map(t => h('p', { class: 'site-prose' }, t))) : null,
      p.timeline?.length ? h('section', { class: 'site-section' }, h('h2', {}, 'How it came together'),
        h('ol', { class: 'pj-timeline' }, p.timeline.map(t => h('li', {}, h('div', { class: 'pj-period' }, t.period),
          h('div', {}, h('strong', {}, t.phase), t.description ? h('p', { class: 'site-prose' }, t.description) : null))))) : null);
  }

  return { onEnter };
})();
