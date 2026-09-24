/**
 * writing.js — posts, each with its own page.
 *
 *   /writing        every post, newest first
 *   /writing/<id>   one post
 * Exposes: window.VW.Writing (onEnter)
 */
'use strict';
window.VW = window.VW || {};

window.VW.Writing = (() => {
  const h = (...a) => window.VW.h(...a);
  const day = iso => iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }) : '';
  let posts = null;

  async function onEnter(id) {
    const root = document.getElementById('writing-root');
    root.replaceChildren(h('p', { class: 'site-muted' }, 'Loading…'));
    try {
      posts = posts || await VW.getJSON('/api/v1/posts');
      if (id) return post(root, posts.find(p => p.id === id) || await VW.getJSON('/api/v1/posts/' + encodeURIComponent(id)));
      document.title = 'Writing · Will Yoste';
      root.replaceChildren(
        h('header', { class: 'site-hero' }, h('div', { class: 'site-eyebrow' }, 'From the desk of Will'), h('h1', { class: 'site-title' }, 'Writing')),
        posts.length ? h('ol', { class: 'wr-list' }, posts.map(p => h('li', {},
          h('a', { class: 'wr-item', href: '/writing/' + encodeURIComponent(p.id), 'data-link': '' },
            p.thumbnail ? h('img', { class: 'wr-thumb', src: p.thumbnail, alt: '', loading: 'lazy' }) : null,
            h('div', {}, h('div', { class: 'site-muted' }, day(p.date)), h('h2', { class: 'wr-title' }, p.title),
              h('p', { class: 'site-prose' }, p.excerpt || p.body.slice(0, 180) + (p.body.length > 180 ? '…' : ''))))))) :
          h('p', { class: 'site-muted' }, 'Nothing here yet.'));
    } catch (e) {
      root.replaceChildren(h('p', { class: 'site-muted' }, e.status === 404 ? 'That post isn’t here.' : 'Writing could not load. ' + e.message),
        h('a', { href: '/writing', 'data-link': '' }, '← All writing'));
    }
  }

  function post(root, p) {
    document.title = p.title + ' · Will Yoste';
    root.replaceChildren(
      h('a', { class: 'site-back', href: '/writing', 'data-link': '' }, '← All writing'),
      h('article', { class: 'wr-post' },
        h('div', { class: 'site-muted' }, day(p.date) + (p.author ? ' · ' + p.author : '')),
        h('h1', { class: 'site-title' }, p.title),
        p.thumbnail ? h('img', { class: 'wr-hero', src: p.thumbnail, alt: '' }) : null,
        ...p.body.split(/\n{2,}/).map(t => h('p', { class: 'site-prose' }, t))));
  }

  return { onEnter };
})();
