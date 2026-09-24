/**
 * music.js — the public Music pages and the site-wide player.
 *
 *   /music          every song on one page (search, album filter), albums, photos
 *   /music/<slug>   one song: its story, every version, lyrics and chords by section
 *
 * Songs are the compositions; each version is a recording. The player bar
 * keeps playing while visitors move around the site.
 * Exposes: window.VW.Music (onEnter), window.VW.Player
 */
'use strict';
window.VW = window.VW || {};

// ── Player ───────────────────────────────────────────────────────────────────
window.VW.Player = (() => {
  let queue = [], index = -1;
  const $ = id => document.getElementById(id);
  const audio = () => $('player-audio');

  function fmtTime(s) {
    if (!isFinite(s)) return '0:00';
    return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  }

  function show(track) {
    $('player').hidden = false;
    document.body.classList.add('has-player');
    $('player-title').textContent = track.title;
    $('player-title').setAttribute('href', track.slug ? '/music/' + track.slug : '/music');
    $('player-meta').textContent = [track.label, track.album].filter(Boolean).join(' · ') || 'Will Yoste';
    const art = $('player-art');
    if (track.art) { art.src = track.art; art.hidden = false; } else art.hidden = true;
  }

  function play(list, i = 0) {
    queue = list; index = i;
    const track = queue[index];
    if (!track) return;
    show(track);
    const a = audio();
    a.src = track.url;
    a.play().catch(() => {});
    document.dispatchEvent(new CustomEvent('vw:track', { detail: track }));
  }

  function toggle() { const a = audio(); if (!a.src) return; a.paused ? a.play() : a.pause(); }
  function step(delta) { if (queue.length) play(queue, (index + delta + queue.length) % queue.length); }
  function current() { return queue[index]; }
  function isPlaying(url) { const a = audio(); return !!a && !a.paused && a.src.endsWith(encodeURI(url)); }

  document.addEventListener('DOMContentLoaded', () => {
    const a = audio();
    if (!a) return;
    $('player-play').onclick = toggle;
    $('player-prev').onclick = () => step(-1);
    $('player-next').onclick = () => step(1);
    $('player-close').onclick = () => { a.pause(); $('player').hidden = true; document.body.classList.remove('has-player'); };
    a.addEventListener('play', () => { $('player-play').textContent = '⏸'; $('player-play').setAttribute('aria-label', 'Pause'); document.dispatchEvent(new Event('vw:playstate')); });
    a.addEventListener('pause', () => { $('player-play').textContent = '▶'; $('player-play').setAttribute('aria-label', 'Play'); document.dispatchEvent(new Event('vw:playstate')); });
    a.addEventListener('ended', () => { if (index < queue.length - 1) step(1); });
    a.addEventListener('timeupdate', () => {
      $('player-time').textContent = fmtTime(a.currentTime) + ' / ' + fmtTime(a.duration);
      if (a.duration) $('player-seek').value = Math.round((a.currentTime / a.duration) * 1000);
    });
    $('player-seek').oninput = e => { if (a.duration) a.currentTime = (e.target.value / 1000) * a.duration; };
  });

  return { play, toggle, current, isPlaying };
})();

// ── Pages ────────────────────────────────────────────────────────────────────
window.VW.Music = (() => {
  const h = (...a) => window.VW.h(...a);
  let data = null, photos = null;
  let filter = { q: '', album: '' };

  // "01 - Randolph, MS" → "Randolph, MS"; timestamps from phone recordings dropped.
  const tidy = t => (t || '').replace(/^\d+\s*[-.]\s*/, '').replace(/\s*-\s*\d+[\s:_]\d+.*$/, '').replace(/_+/g, ' ').trim();

  async function load() {
    if (!data) data = await VW.getJSON('/api/v1/music');
    if (!photos) photos = await VW.getJSON('/api/music/photos').catch(() => []);
  }

  function trackOf(r, song) {
    return { url: r.url, title: song ? song.title : tidy(r.title), label: r.version_label || (song ? '' : ''), album: r.album,
             art: r.art || song?.art, slug: song?.slug || r.song_slug };
  }

  function playButton(tracks, i, label) {
    const b = h('button', { class: 'mu-play', 'aria-label': 'Play ' + label, onclick: e => { e.preventDefault(); e.stopPropagation(); VW.Player.play(tracks, i); } }, '▶');
    return b;
  }

  function art(src, alt, cls = 'mu-art') {
    return src ? h('img', { class: cls, src, alt: alt || '', loading: 'lazy' }) : h('div', { class: cls + ' mu-art-empty', 'aria-hidden': 'true' }, '♪');
  }

  async function onEnter(slug) {
    const root = document.getElementById('music-root');
    const hero = document.querySelector('#page-music .site-hero');
    root.replaceChildren(h('p', { class: 'site-muted' }, 'Loading…'));
    try {
      await load();
      if (slug) { hero.hidden = true; await songPage(root, slug); }
      else { hero.hidden = false; listPage(root); document.title = 'Music · Will Yoste'; }
    } catch (e) {
      root.replaceChildren(h('p', { class: 'site-muted' }, e.status === 404 ? 'That song isn’t here.' : 'Music could not load. ' + e.message),
        h('a', { href: '/music', 'data-link': '' }, '← All songs'));
    }
  }

  // ── /music ─────────────────────────────────────────────────────────────────
  function listPage(root) {
    const songs = data.songs.filter(s => s.versions.length || s.has_lyrics || s.story);
    const playable = songs.filter(s => s.versions.length);
    const queue = playable.map(s => trackOf(s.versions[0], s));
    const albums = data.albums;

    const search = h('input', { class: 'site-input', type: 'search', placeholder: 'Search songs', value: filter.q, 'aria-label': 'Search songs' });
    const chips = h('div', { class: 'site-chips', role: 'group', 'aria-label': 'Filter by album' },
      [['', 'All'], ...albums.map(a => [a.title, a.title])].map(([v, t]) =>
        h('button', { class: 'site-chip', 'aria-pressed': String(filter.album === v), onclick: () => { filter.album = v; listPage(root); } }, t)));
    const list = h('ol', { class: 'mu-songs' });
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      filter.q = q;
      const shown = songs.filter(s => (!q || [s.title, s.genre, s.written_at, s.story].join(' ').toLowerCase().includes(q))
        && (!filter.album || s.versions.some(v => v.album === filter.album)));
      list.replaceChildren(...shown.map(s => {
        const qi = playable.indexOf(s);
        return h('li', { class: 'mu-song' },
          qi >= 0 ? playButton(queue, qi, s.title) : h('span', { class: 'mu-play mu-play-off', 'aria-hidden': 'true' }, '♪'),
          art(s.art, '', 'mu-thumb'),
          h('a', { class: 'mu-song-main', href: '/music/' + s.slug, 'data-link': '' },
            h('span', { class: 'mu-song-title' }, s.title),
            h('span', { class: 'mu-song-meta' }, [s.year_written, s.versions.length > 1 ? s.versions.length + ' versions' : s.versions[0]?.album || 'single',
              s.has_lyrics ? 'lyrics & chords' : null].filter(Boolean).join(' · '))),
          h('span', { class: 'mu-song-go', 'aria-hidden': 'true' }, '›'));
      }));
      if (!shown.length) list.replaceChildren(h('li', { class: 'site-muted' }, songs.length ? 'No songs match.' : 'Songs are coming soon — listen to the albums below.'));
    };
    search.oninput = draw;
    draw();

    const albumGrid = h('div', { class: 'mu-albums' }, albums.map(a => {
      const tracks = a.tracks.map(t => trackOf(t, t.song_slug ? { title: t.song_title, slug: t.song_slug, art: null } : null));
      return h('details', { class: 'mu-album' },
        h('summary', {}, art(a.art, a.title), h('div', { class: 'mu-album-info' }, h('strong', {}, a.title),
          h('span', {}, `${a.tracks.length} track${a.tracks.length === 1 ? '' : 's'}${a.year ? ' · ' + a.year : ''}`))),
        h('ol', { class: 'mu-tracklist' }, a.tracks.map((t, i) => h('li', {},
          playButton(tracks, i, tidy(t.title)),
          t.song_slug ? h('a', { href: '/music/' + t.song_slug, 'data-link': '' }, t.song_title || tidy(t.title)) : h('span', {}, tidy(t.title)),
          t.version_label ? h('span', { class: 'mu-version' }, t.version_label) : null))));
    }));

    const singles = data.singles.length ? h('section', { class: 'site-section' }, h('h2', {}, 'More recordings'),
      h('ol', { class: 'mu-tracklist' }, data.singles.map((t, i, all) => h('li', {}, playButton(all.map(x => trackOf(x)), i, tidy(t.title)), h('span', {}, tidy(t.title)))))) : null;

    const gallery = photos.length ? h('section', { class: 'site-section' }, h('h2', {}, 'Photos'),
      h('div', { class: 'mu-photos' }, photos.map(src => h('a', { href: src, target: '_blank', rel: 'noopener' }, h('img', { src, alt: 'Music photo', loading: 'lazy' }))))) : null;

    root.replaceChildren(
      h('section', { class: 'site-section' },
        h('div', { class: 'site-toolbar' }, search, playable.length ? h('button', { class: 'site-btn', onclick: () => VW.Player.play(queue, 0) }, '▶ Play all') : null),
        albums.length > 1 ? chips : null, list),
      albums.length ? h('section', { class: 'site-section' }, h('h2', {}, 'Albums'), albumGrid) : null,
      singles, gallery);
  }

  // ── /music/<slug> ──────────────────────────────────────────────────────────
  async function songPage(root, slug) {
    const s = await VW.getJSON('/api/v1/music/songs/' + encodeURIComponent(slug));
    document.title = s.title + ' · Music · Will Yoste';
    const versions = s.versions.map(v => trackOf(v, s));
    const sections = (s.sections || []).filter(x => x.lyrics || x.chords || x.tabs);
    root.replaceChildren(
      h('a', { class: 'site-back', href: '/music', 'data-link': '' }, '← All songs'),
      h('header', { class: 'mu-song-head' }, art(s.art, s.title, 'mu-cover'),
        h('div', {}, h('div', { class: 'site-eyebrow' }, 'Song'), h('h1', { class: 'site-title' }, s.title),
          h('p', { class: 'site-muted' }, [s.year_written ? 'Written ' + s.year_written : null, s.written_at, s.genre, s.musical_key ? 'Key of ' + s.musical_key : null,
            s.bpm ? Math.round(s.bpm) + ' bpm' : null].filter(Boolean).join(' · ')),
          versions.length ? h('button', { class: 'site-btn', onclick: () => VW.Player.play(versions, 0) }, '▶ Play') : null)),
      s.story ? h('section', { class: 'site-section' }, h('h2', {}, 'The story'), ...s.story.split(/\n{2,}/).map(p => h('p', { class: 'site-prose' }, p))) : null,
      versions.length ? h('section', { class: 'site-section' }, h('h2', {}, versions.length > 1 ? `Versions (${versions.length})` : 'Listen'),
        h('ol', { class: 'mu-tracklist' }, s.versions.map((v, i) => h('li', {}, playButton(versions, i, s.title + ' ' + (v.version_label || '')),
          h('span', {}, v.version_label || tidy(v.title)), h('span', { class: 'mu-version' }, v.album || 'single'))))) : null,
      sections.length ? h('section', { class: 'site-section' }, h('h2', {}, 'Lyrics & chords'),
        h('div', { class: 'mu-sheet' }, sections.map(x => h('div', { class: 'mu-sheet-part' },
          h('div', { class: 'mu-sheet-label' }, x.label || (x.section_type === 'full' ? '' : x.section_type)),
          x.chords ? h('pre', { class: 'mu-chords' }, x.chords) : null,
          x.lyrics ? h('pre', { class: 'mu-lyrics' }, x.lyrics) : null,
          x.tabs ? h('pre', { class: 'mu-tabs' }, x.tabs) : null)))) : null);
  }

  return { onEnter };
})();
