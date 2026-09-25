/**
 * music.js — the public Music pages and the site-wide player.
 *
 *   /music          every song on one page (search, album filter), albums, photos
 *   /music/<slug>   one song, to learn as well as hear: Meaning (what it's about, how it
 *                   came to be, line notes) · Lyrics & chords (transpose, chord
 *                   fingerings, notes on lines) · Play it (setup, diagrams, tabs) · Versions
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

  // What there is to explore behind a song, as small badges on the list.
  function extras(s) {
    const badges = [s.meaning || s.story ? 'Meaning' : null, s.has_lyrics ? 'Lyrics' : null, s.has_chords ? 'Chords' : null,
                    s.note_count ? `${s.note_count} line note${s.note_count === 1 ? '' : 's'}` : null].filter(Boolean);
    return badges.length ? h('span', { class: 'mu-badges' }, badges.map(b => h('span', { class: 'mu-badge' }, b))) : null;
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
    const songs = data.songs.filter(s => s.versions.length || s.has_lyrics || s.has_chords || s.story || s.meaning);
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
      const shown = songs.filter(s => (!q || [s.title, s.genre, s.written_at, s.story, s.meaning, ...(s.themes || [])].join(' ').toLowerCase().includes(q))
        && (!filter.album || s.versions.some(v => v.album === filter.album)));
      list.replaceChildren(...shown.map(s => {
        const qi = playable.indexOf(s);
        return h('li', { class: 'mu-song' },
          qi >= 0 ? playButton(queue, qi, s.title) : h('span', { class: 'mu-play mu-play-off', 'aria-hidden': 'true' }, '♪'),
          art(s.art, '', 'mu-thumb'),
          h('a', { class: 'mu-song-main', href: '/music/' + s.slug, 'data-link': '' },
            h('span', { class: 'mu-song-title' }, s.title),
            h('span', { class: 'mu-song-meta' }, [s.year_written, s.versions.length > 1 ? s.versions.length + ' versions' : s.versions[0]?.album || (s.versions.length ? 'single' : null)]
              .filter(Boolean).join(' · ')),
            extras(s)),
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
  // A song is somewhere to learn about it as well as hear it: what it means, how
  // it came to be, the lyrics with the chords and notes on particular lines, how
  // to play it, and what's different about each recorded version.
  const C = () => window.VW.Chords;
  const song = { tab: '', steps: 0, view: 'chords' };
  let popCloser = null;                 // closes the open chord fingering on the song page
  document.addEventListener('click', () => popCloser && popCloser());

  const norm = t => C().plain(t).toLowerCase().replace(/[^a-z0-9' ]+/g, ' ').replace(/\s+/g, ' ').trim();
  const paras = (text, cls = 'site-prose') => String(text || '').split(/\n{2,}/).map(p => p.trim()).filter(Boolean).map(p => h('p', { class: cls }, p));

  function noteFor(line, notes) {
    const words = norm(line);
    return words ? notes.find(n => { const w = norm(n.line_text); return w && (words === w || words.includes(w)); }) : null;
  }

  async function songPage(root, slug) {
    const s = await VW.getJSON('/api/v1/music/songs/' + encodeURIComponent(slug));
    document.title = s.title + ' · Music · Will Yoste';
    if (song.slug !== s.slug) Object.assign(song, { slug: s.slug, tab: '', steps: 0, view: 'chords' });
    const versions = s.versions.map(v => trackOf(v, s));
    const sections = (s.sections || []).filter(x => x.lyrics || x.chords || x.tabs);
    const notes = s.notes || [];
    const about = !!(s.meaning || s.story || s.influences || (s.themes || []).length || notes.length);
    const lyrics = sections.some(x => x.lyrics || x.chords);
    const howTo = !!(sections.some(x => x.chords || x.tabs || C().hasInline(x.lyrics)) || s.capo !== null || s.tuning || s.strumming || s.time_signature);
    const tabs = [['meaning', 'Meaning', about], ['lyrics', 'Lyrics & chords', lyrics], ['play', 'Play it', howTo],
                  ['versions', versions.length > 1 ? `Versions (${versions.length})` : 'Listen', versions.length > 0]].filter(t => t[2]);
    const wanted = (location.hash || '').slice(1);
    if (!tabs.some(t => t[0] === song.tab)) song.tab = tabs.some(t => t[0] === wanted) ? wanted : tabs[0]?.[0] || '';

    const panel = h('div', { class: 'mu-panel', role: 'tabpanel' });
    const bar = h('div', { class: 'mu-tabs', role: 'tablist', 'aria-label': 'About this song' });
    const show = (tab, focusLine) => {
      song.tab = tab;
      history.replaceState(history.state, '', location.pathname + (tab === tabs[0]?.[0] ? '' : '#' + tab));
      bar.replaceChildren(...tabs.map(([id, label]) => h('button', { role: 'tab', class: 'mu-tab', 'aria-selected': String(id === tab),
        onclick: () => show(id) }, label)));
      const draw = { meaning: meaningTab, lyrics: lyricsTab, play: playTab, versions: versionsTab }[tab];
      panel.replaceChildren(...[].concat(draw ? draw() : []).filter(Boolean));
      if (focusLine) {
        const line = [...panel.querySelectorAll('.mu-line-noted')].find(l => l.dataset.line === focusLine);
        if (line) { line.click(); line.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      }
    };

    // Meaning: what it's about, how it came to be, themes, influences, line notes.
    function meaningTab() {
      return [
        s.meaning ? h('section', { class: 'mu-block' }, h('h2', {}, 'What it’s about'), ...paras(s.meaning, 'site-prose mu-lede')) : null,
        s.story ? h('section', { class: 'mu-block' }, h('h2', {}, 'How it came to be'), ...paras(s.story)) : null,
        s.influences ? h('section', { class: 'mu-block' }, h('h2', {}, 'Influences'), ...paras(s.influences)) : null,
        notes.length ? h('section', { class: 'mu-block' }, h('h2', {}, 'Line by line'),
          h('ol', { class: 'mu-notes' }, notes.map(n => h('li', {},
            lyrics && sections.some(x => (x.lyrics || '').split('\n').some(l => noteFor(l, [n])))
              ? h('button', { class: 'mu-quote', title: 'Show in the lyrics', onclick: () => show('lyrics', norm(n.line_text)) }, '“' + n.line_text + '”')
              : h('blockquote', { class: 'mu-quote' }, '“' + n.line_text + '”'),
            ...paras(n.note, 'mu-note-text'))))) : null,
      ];
    }

    const pop = h('div', { class: 'mu-chord-pop', hidden: true, role: 'dialog' });
    const closePop = () => { pop.hidden = true; };
    function chip(name) {
      return h('button', { class: 'mu-chip', 'aria-label': name + ' chord — show fingering', onclick: e => {
        e.stopPropagation();
        const svg = C().diagram(name);
        if (!svg || (!pop.hidden && pop.dataset.chord === name)) { closePop(); return; }
        pop.dataset.chord = name;
        pop.replaceChildren(h('strong', {}, name), svg);
        pop.hidden = false;
        const box = e.currentTarget.getBoundingClientRect(), host = pop.parentElement.getBoundingClientRect();
        pop.style.left = Math.max(0, Math.min(box.left - host.left, host.width - 132)) + 'px';
        pop.style.top = (box.bottom - host.top + 6) + 'px';
      } }, name);
    }
    const moved = name => C().transpose(name, song.steps, song.steps ? C().key(s.musical_key, song.steps).flats ?? undefined : undefined);

    function progression(text) {
      return h('div', { class: 'mu-prog' }, String(text).split('\n').filter(l => l.trim()).map(line =>
        h('div', { class: 'mu-prog-line' }, C().tokens(line).map(t => t.chord ? chip(moved(t.chord))
          : /^[\s\-,]+$/.test(t.text) ? null : h('span', { class: 'mu-prog-text' }, t.text.replace(/^[\s-]+|[\s-]+$/g, ''))))));
    }

    function lyricLine(line) {
      const note = noteFor(line, notes);
      const withChords = song.view === 'chords' && C().hasInline(line);
      const body = withChords
        ? h('span', { class: 'mu-inline' }, C().inline(line).map(p => h('span', { class: 'mu-seg' },
            h('span', { class: 'mu-seg-chord' }, p.chord ? chip(moved(p.chord)) : ' '), h('span', {}, p.text || ' '))))
        : h('span', {}, C().plain(line) || ' ');
      if (!note) return h('div', { class: 'mu-line' + (line.trim() ? '' : ' mu-line-gap') }, body);
      const detail = h('div', { class: 'mu-line-note', hidden: true }, ...paras(note.note, 'mu-note-text'));
      const row = h('div', { class: 'mu-line mu-line-noted', role: 'button', tabindex: '0', 'aria-expanded': 'false', 'data-line': norm(note.line_text),
        title: 'What this line is about' }, body, h('span', { class: 'mu-line-mark', 'aria-hidden': 'true' }, '✎'));
      const toggle = e => {
        if (e.target.closest('.mu-chip')) return;
        detail.hidden = !detail.hidden;
        row.setAttribute('aria-expanded', String(!detail.hidden));
      };
      row.addEventListener('click', toggle);
      row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(e); } });
      return h('div', {}, row, detail);
    }

    // Lyrics & chords: each section's progression over its words; noted lines open their note.
    function lyricsTab() {
      const chords = sections.some(x => x.chords || C().hasInline(x.lyrics));
      const keyNow = s.musical_key ? C().key(s.musical_key, song.steps).name : '';
      const tools = h('div', { class: 'mu-tools' },
        chords ? h('div', { class: 'site-chips', role: 'group', 'aria-label': 'Show' },
          [['chords', 'Chords + lyrics'], ['words', 'Lyrics only']].map(([v, t]) =>
            h('button', { class: 'site-chip', 'aria-pressed': String(song.view === v), onclick: () => { song.view = v; show('lyrics'); } }, t))) : null,
        chords && song.view === 'chords' ? h('div', { class: 'mu-transpose', role: 'group', 'aria-label': 'Transpose' },
          h('button', { class: 'mu-step', 'aria-label': 'Down a semitone', onclick: () => { song.steps = (song.steps + 11) % 12; show('lyrics'); } }, '−'),
          h('span', {}, keyNow ? 'Key ' + keyNow : 'Transpose', song.steps ? ` (${song.steps > 6 ? song.steps - 12 : '+' + song.steps})` : ''),
          h('button', { class: 'mu-step', 'aria-label': 'Up a semitone', onclick: () => { song.steps = (song.steps + 1) % 12; show('lyrics'); } }, '+'),
          song.steps ? h('button', { class: 'mu-reset', onclick: () => { song.steps = 0; show('lyrics'); } }, 'Reset') : null) : null,
        notes.length ? h('span', { class: 'mu-hint' }, h('span', { class: 'mu-line-mark', 'aria-hidden': 'true' }, '✎'), ' Tap a marked line for the story behind it') : null);
      return [tools, h('div', { class: 'mu-sheet' }, pop, sections.filter(x => x.lyrics || x.chords).map(x => h('div', { class: 'mu-sheet-part' },
        h('div', { class: 'mu-sheet-label' }, x.label || (x.section_type === 'full' ? 'Whole song' : x.section_type)),
        song.view === 'chords' && x.chords ? progression(x.chords) : null,
        x.lyrics ? h('div', { class: 'mu-lyrics' }, x.lyrics.split('\n').map(lyricLine)) : null)))];
    }

    // Play it: the setup, a diagram for every chord, and any tabs.
    function playTab() {
      const used = [];
      for (const x of sections) {
        for (const line of (x.chords || '').split('\n')) C().tokens(line).forEach(t => t.chord && used.push(moved(t.chord)));
        for (const line of (x.lyrics || '').split('\n')) C().inline(line).forEach(p => p.chord && used.push(moved(p.chord)));
      }
      const unique = [...new Set(used)];
      const facts = [['Key', s.musical_key ? C().key(s.musical_key, song.steps).name + (song.steps ? ` (written in ${s.musical_key})` : '') : ''],
        ['Capo', s.capo ? 'Fret ' + s.capo : s.capo === 0 ? 'None' : ''], ['Tuning', s.tuning || (sections.length ? 'Standard (E A D G B E)' : '')],
        ['Time', s.time_signature], ['Tempo', s.bpm ? Math.round(s.bpm) + ' bpm' : ''], ['Strumming', s.strumming]].filter(f => f[1]);
      const tabbed = sections.filter(x => x.tabs);
      return [
        facts.length ? h('dl', { class: 'mu-facts' }, facts.map(([k, v]) => h('div', {}, h('dt', {}, k), h('dd', { class: k === 'Strumming' ? 'mu-mono' : null }, v)))) : null,
        s.capo ? h('p', { class: 'site-muted mu-small' }, `Chords are the shapes you play with the capo on fret ${s.capo}.`) : null,
        unique.length ? h('section', { class: 'mu-block' }, h('h2', {}, 'Chords'),
          song.steps ? h('p', { class: 'site-muted mu-small' }, 'Transposed as set on Lyrics & chords.') : null,
          h('div', { class: 'mu-diagrams' }, unique.map(name => h('figure', { class: 'mu-diagram' }, C().diagram(name) || h('div', { class: 'mu-nodiagram' }, '?'),
            h('figcaption', {}, name))))) : null,
        tabbed.length ? h('section', { class: 'mu-block' }, h('h2', {}, 'Tabs'), tabbed.map(x => h('div', { class: 'mu-sheet-part' },
          h('div', { class: 'mu-sheet-label' }, x.label || x.section_type), h('pre', { class: 'mu-tabs-pre' }, x.tabs)))) : null,
      ];
    }

    // Versions: each recording, what's different about it, and a play button.
    function versionsTab() {
      return h('ol', { class: 'mu-versions' }, s.versions.map((v, i) => h('li', { class: 'mu-version-card' },
        playButton(versions, i, s.title + ' ' + (v.version_label || '')),
        h('div', { class: 'mu-version-main' },
          h('strong', {}, v.version_label || tidy(v.title)),
          h('span', { class: 'mu-song-meta' }, [v.album || 'Single', v.release_year].filter(Boolean).join(' · ')),
          ...paras(v.notes, 'mu-note-text')))));
    }

    const facts = [s.year_written ? 'Written ' + s.year_written : null, s.written_at, s.genre, s.musical_key ? 'Key of ' + s.musical_key : null,
                   s.capo ? 'Capo ' + s.capo : null, s.bpm ? Math.round(s.bpm) + ' bpm' : null].filter(Boolean);
    root.replaceChildren(
      h('a', { class: 'site-back', href: '/music', 'data-link': '' }, '← All songs'),
      h('header', { class: 'mu-song-head' }, art(s.art, s.title, 'mu-cover'),
        h('div', { class: 'mu-song-head-main' }, h('div', { class: 'site-eyebrow' }, 'Song'), h('h1', { class: 'site-title' }, s.title),
          facts.length ? h('p', { class: 'site-muted' }, facts.join(' · ')) : null,
          (s.themes || []).length ? h('div', { class: 'mu-themes mu-head-themes' }, s.themes.map(t => h('span', { class: 'mu-theme' }, t))) : null,
          h('div', { class: 'mu-head-actions' },
            versions.length ? h('button', { class: 'site-btn', onclick: () => VW.Player.play(versions, 0) }, '▶ Play') : null,
            versions.length > 1 ? h('button', { class: 'site-btn site-btn-ghost', onclick: () => show('versions') }, `${versions.length} versions`) : null))),
      tabs.length ? h('div', { class: 'mu-tabs-wrap' }, bar) : h('p', { class: 'site-muted' }, 'More about this song is coming soon.'),
      panel);
    popCloser = closePop;
    if (song.tab) show(song.tab);
  }

  return { onEnter };
})();
