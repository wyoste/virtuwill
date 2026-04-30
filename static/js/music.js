/**
 * music.js — VirtuWill Music v3
 * Artist: Will Yoste
 *
 * Real HTML5 Audio playback, floating mini-player that persists across pages,
 * admin upload with song metadata, photo gallery, streaming links, tabs/lyrics.
 */
'use strict';
window.VW = window.VW || {};

window.VW.Music = (() => {

  // ── External links ────────────────────────────────────────────────────────
  const LINKS = {
    spotify: 'https://open.spotify.com/artist/willyoste',
    youtube: 'https://www.youtube.com/@willyoste',
    apple:   'https://music.apple.com/artist/will-yoste',
  };

  // ── Default tracks (until admin uploads real ones) ────────────────────────
  const DEFAULT_TRACKS = [
    { id:'t1', title:'Morning Haze',  year:2024, genre:'Ambient',
      story:'Written at dawn after a sleepless night — light through the studio window felt like permission to rest.',
      location:'Home studio · Plano, TX', chords:'Cadd9 – G – Em7 – D',
      key:'G major', bpm:72, src:null, lyrics:'', tabs:'' },
    { id:'t2', title:'Still Water',   year:2024, genre:'Ambient',
      story:'Inspired by a late-evening walk around White Rock Lake.',
      location:'White Rock Lake · Dallas, TX', chords:'Em – Cmaj7 – G – D/F#',
      src:null, lyrics:'', tabs:'' },
    { id:'t3', title:'First Cup',     year:2023, genre:'Acoustic',
      story:'A simple fingerpicked piece for the ritual of the first cup of coffee.',
      location:'Home studio · Plano, TX', chords:'G – Cadd9 – Em – D',
      src:null, lyrics:'', tabs:'' },
    { id:'t4', title:'Open Road',     year:2023, genre:'Acoustic',
      story:'Driving back from Oxford, MS with nothing but flat Texas highway. The riff came somewhere around Greenville.',
      location:'On the road · Texas/Mississippi', chords:'A – E – F#m – D',
      src:null, lyrics:'', tabs:'' },
    { id:'t5', title:'Wanderer',      year:2025, genre:'Folk',
      story:"A song about not knowing where you're going but being okay with that.",
      location:'Home studio · Plano, TX', chords:'C – Am – F – G',
      src:null, lyrics:'', tabs:'' },
  ];

  // ── State ─────────────────────────────────────────────────────────────────
  let tracks        = [];
  let _library      = { albums:[], singles:[] };  // filesystem-scanned structure
  let _carouselIdx  = 0;    // current slide index in catalog carousel
  let currentTrack  = null;
  let activeSection = 'catalog';
  let audioEl       = null;
  let isPlaying     = false;
  let miniVisible   = false;
  let miniExpanded  = true;
  let galleryPhotos = [];
  let _editingId    = null;
  let _dragInited   = false;
  let _sections     = [];   // working copy of song sections in the modal

  // ── Chord identifier lookup ──────────────────────────────────────────────
  const CHORD_INTERVALS = {
    'major':          [0,4,7],
    'minor':          [0,3,7],
    'dim':            [0,3,6],
    'aug':            [0,4,8],
    'sus2':           [0,2,7],
    'sus4':           [0,5,7],
    'maj7':           [0,4,7,11],
    'min7':           [0,3,7,10],
    '7':              [0,4,7,10],
    'dim7':           [0,3,6,9],
    'half-dim7':      [0,3,6,10],
    'maj9':           [0,4,7,11,14],
    'add9':           [0,4,7,14],
    'min9':           [0,3,7,10,14],
    '9':              [0,4,7,10,14],
    '6':              [0,4,7,9],
    'min6':           [0,3,7,9],
    'maj7#11':        [0,4,7,11,18],
    '13':             [0,4,7,10,14,21],
  };
  const NOTE_MAP = {
    'C':0,'C#':1,'Db':1,'D':2,'D#':3,'Eb':3,'E':4,'F':5,'F#':6,
    'Gb':6,'G':7,'G#':8,'Ab':8,'A':9,'A#':10,'Bb':10,'B':11
  };
  const NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
  const SECTION_TYPES = ['intro','verse','pre-chorus','chorus','bridge','solo','outro','coda'];
  const _btmStore   = {};   // keyed by track id/url → BTM metadata object

  // ── Bootstrap ─────────────────────────────────────────────────────────────
  function init() {
    if (!audioEl) _buildAudio();
    _loadCatalog().then(() => _renderSection(activeSection));
    // Init drag only once
    if (!_dragInited) { _dragInited = true; setTimeout(_initMiniDrag, 100); }
  }

  function _buildAudio() {
    audioEl = new Audio();
    audioEl.volume = 0.75;
    audioEl.addEventListener('timeupdate', _onTimeUpdate);
    audioEl.addEventListener('ended',      _onEnded);
    audioEl.addEventListener('play',  () => { isPlaying = true;  _syncPlayBtns(); });
    audioEl.addEventListener('pause', () => { isPlaying = false; _syncPlayBtns(); });
    audioEl.addEventListener('error', () => window.toast?.('Audio unavailable for this track', 'error'));
  }

  async function _loadCatalog() {
    try {
      const r = await fetch('/api/music/catalog');
      const d = await r.json();
      tracks = d.tracks?.length ? d.tracks : DEFAULT_TRACKS;
    } catch { tracks = DEFAULT_TRACKS; }

    // Also scan filesystem for album/single structure
    try {
      const r2 = await fetch('/api/music/library');
      _library = await r2.json();
    } catch { _library = { albums:[], singles:[] }; }
  }

  function onPublishToggle(checked) {
    const lbl = document.getElementById('mu-publish-lbl');
    if (lbl) lbl.textContent = checked ? 'Published' : 'Draft';
  }

  async function togglePublish(trackId) {
    const t = tracks.find(t => t.id === trackId);
    if (!t) return;
    t.published = !t.published;
    await _saveCatalog();
    _renderSection(activeSection);
    // Re-render carousel too in case visibility changed
    const wrap = document.getElementById('mu-section-wrap');
    if (wrap) _renderCatalog(wrap);
  }

  async function _saveCatalog() {
    try {
      await fetch('/api/music/catalog', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tracks }),
      });
    } catch { /* non-admin — swallow */ }
  }

  // ── Section routing ───────────────────────────────────────────────────────
  function showSection(sec) {
    activeSection = sec;
    ['catalog','gallery','links','sheets'].forEach(s => {
      document.getElementById('mu-tab-' + s)?.classList.toggle('on', s === sec);
    });
    _renderSection(sec);
  }

  function _renderSection(sec) {
    const wrap = document.getElementById('mu-section-wrap');
    if (!wrap) return;
    const fn = { catalog:_renderCatalog, gallery:_renderGallery,
                 links:_renderLinks, sheets:_renderSheets }[sec];
    if (fn) fn(wrap);
  }

  // ── Catalog ───────────────────────────────────────────────────────────────

  // Build unified slide array: albums (from filesystem) then singles (from filesystem),
  // falling back to catalog JSON tracks if no filesystem library loaded.
  // ── Pure slide builder — no auth, no filtering ────────────────────────────
  function _mergeSlides() {
    const cards = [];

    (_library.albums || []).forEach(album => {
      cards.push({ kind:'album', ...album });
    });

    (_library.singles || []).forEach(s => {
      const meta = _matchMeta(s.name, s.url);
      cards.push({ kind:'single', ...s, meta });
    });

    tracks.forEach(t => {
      const inFs = cards.some(c =>
        (c.kind === 'single' && c.url === t.src) ||
        (c.kind === 'album'  && c.tracks?.some(at => at.url === t.src))
      );
      if (!inFs) cards.push({ kind:'single', name:t.title, url:t.src||null, art:null, meta:t });
    });

    return cards;
  }

  // ── Publish predicate — pure function, no side effects ────────────────────
  function _isPublished(slide) {
    if (slide.kind === 'single') {
      const m = slide.meta;
      return !m || m.published !== false;  // no catalog entry/legacy metadata -> show it
    }
    if (slide.kind === 'album') {
      const cats = (slide.tracks || [])
        .map(t => _matchTrackMeta(t, slide.name)).filter(Boolean);
      return cats.length === 0 || cats.some(t => t.published !== false);
    }
    return true;
  }

  function _isTrackPublished(track, albumName) {
    const meta = _matchTrackMeta(track, albumName);
    return !meta || meta.published !== false;
  }

  function _visibleAlbumTracks(slide) {
    const all = slide.tracks || [];
    if (window.VW?.Auth?.isAdmin?.()) return all;
    return all.filter(t => _isTrackPublished(t, slide.name));
  }

  // ── Public entry point — applies auth filter ───────────────────────────────
  function _buildSlides() {
    const admin = window.VW?.Auth?.isAdmin?.() || false;
    const all   = _mergeSlides();
    return admin ? all : all.filter(_isPublished);
  }

  function _matchMeta(name, url) {
    return tracks.find(t => t.src === url) ||
           tracks.find(t => (t.title||'').toLowerCase() === (name||'').toLowerCase()) ||
           null;
  }

  // Enrich album tracks with catalog metadata where titles match
  function _matchTrackMeta(track, albumName) {
    return tracks.find(t => t.src === track.url) ||
           tracks.find(t => (t.title||'').toLowerCase() === (track.title||'').toLowerCase()) ||
           null;
  }

  // Build a flat play queue matching the carousel order:
  // album1-track1, album1-track2, ..., album2-track1, ..., single1, single2, ...
  // Each entry: { url, title, album, slideIdx, trackIdx }
  function _buildQueue() {
    const queue  = [];
    const slides = _buildSlides();
    slides.forEach((slide, si) => {
      if (slide.kind === 'album') {
        _visibleAlbumTracks(slide).forEach((t, ti) => {
          if (!t.url) return;
          const meta  = _matchTrackMeta(t, slide.name);
          queue.push({
            url:      t.url,
            title:    meta?.title || t.title,
            album:    slide.name,
            slideIdx: si,
            trackIdx: ti,
          });
        });
      } else {
        if (!slide.url) return;
        const meta = slide.meta;
        queue.push({
          url:      slide.url,
          title:    meta?.title || slide.name,
          album:    '',
          slideIdx: si,
          trackIdx: 0,
        });
      }
    });
    return queue;
  }

  function _renderCatalog(wrap) {
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;


    const slides = _buildSlides();
    if (!slides.length) {
      wrap.innerHTML = `<div class="mu-empty-catalog">
        <div style="font-size:48px;margin-bottom:16px">🎸</div>
        <div style="color:rgba(255,255,255,.5);font-size:14px;margin-bottom:20px">No music uploaded yet.</div>
        ${isAdmin ? `<button class="mu-add-btn" onclick="VW.Music.openUploadModal()">+ Upload Music</button>` : ''}
      </div>`; return;
    }

    _carouselIdx = Math.max(0, Math.min(_carouselIdx, slides.length - 1));
    const slide  = slides[_carouselIdx];
    const n      = slides.length;

    const adminBar = isAdmin ? (() => {
      return `<div class="mu-admin-bar">
        <span style="font-size:12px;color:rgba(255,255,255,.5)">${n} release${n!==1?'s':''}</span>
        <button class="mu-add-btn" onclick="VW.Music.openUploadModal()">+ Upload</button>
      </div>`;
    })() : '';

    wrap.innerHTML = adminBar + `
      <div class="mu-carousel-shell">
        <button class="mu-car-arrow" onclick="VW.Music.carouselNav(-1)"
          ${n < 2 ? 'style="visibility:hidden"' : ''}>&#8249;</button>

        <div class="mu-carousel-card" id="mu-carousel-card">
          ${_carouselCard(slide, _carouselIdx, isAdmin)}
        </div>

        <button class="mu-car-arrow" onclick="VW.Music.carouselNav(1)"
          ${n < 2 ? 'style="visibility:hidden"' : ''}>&#8250;</button>
      </div>

      <div class="mu-car-dots">
        ${slides.map((_,i) => `<button class="mu-car-dot${i===_carouselIdx?' on':''}"
          onclick="VW.Music.carouselGo(${i})"></button>`).join('')}
      </div>`;

    // Touch swipe on carousel card
    _attachCarouselSwipe(document.getElementById('mu-carousel-card'));
  }

  function _attachCarouselSwipe(el) {
    if (!el) return;
    let startX = 0, startY = 0;
    el.addEventListener('touchstart', e => {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    }, { passive: true });
    el.addEventListener('touchend', e => {
      const dx = e.changedTouches[0].clientX - startX;
      const dy = e.changedTouches[0].clientY - startY;
      if (Math.abs(dx) < 40 || Math.abs(dy) > Math.abs(dx)) return; // not a horizontal swipe
      carouselNav(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  // Full-width card: art panel left, info+tracklist right
  function _carouselCard(slide, idx, isAdmin) {
    const artStyle = slide.art
      ? `background-image:url('${slide.art}');background-size:cover;background-position:center`
      : '';

    // Hover play overlay — plays first track for album, the track for single
    let overlayUrl = '', overlayTitle = '', overlayAlbum = '';
    if (slide.kind === 'single' && slide.url) {
      const meta = slide.meta;
      overlayUrl   = slide.url;
      overlayTitle = _esc(meta?.title || slide.name);
    } else if (slide.kind === 'album' && slide.tracks?.length) {
      const firstTrack = _visibleAlbumTracks(slide)[0];
      const firstMeta = firstTrack ? _matchTrackMeta(firstTrack, slide.name) : null;
      overlayUrl   = firstTrack?.url || '';
      overlayTitle = _esc(firstMeta?.title || firstTrack?.title || '');
      overlayAlbum = _esc(slide.name);
    }
    const overlayPlaying = overlayUrl && currentTrack?.url === overlayUrl && isPlaying;

    const artPanel = `
      <div class="mu-card-art" style="${artStyle}">
        ${!slide.art ? `<span class="mu-card-art-placeholder">${slide.kind==='album'?'💿':'🎵'}</span>` : ''}
        ${slide.kind === 'single' ? '<div class="mu-card-single-badge">Single</div>' : ''}
        ${overlayUrl ? `
          <div class="mu-card-art-overlay">
            <button class="mu-car-big-play"
              onclick="event.stopPropagation();VW.Music.playUrl('${overlayUrl}','${overlayTitle}','${overlayAlbum}')">
              ${overlayPlaying ? '⏸' : '▶'}
            </button>
          </div>` : ''}
        ${isAdmin && slide.kind === 'album' ? `
          <div class="mu-car-admin-btns">
            <button class="mu-edit-btn" onclick="VW.Music.openUploadModal()">+ Track</button>
          </div>` : ''}
      </div>`;

    const infoPanel = `<div class="mu-card-info">${_albumInfoPanel(slide, idx, isAdmin)}</div>`;

    return artPanel + infoPanel;
  }

  function _albumInfoPanel(slide, idx, isAdmin) {
    if (!slide) return '';

    if (slide.kind === 'album') {
      const visibleTracks = _visibleAlbumTracks(slide);
      const rows = visibleTracks.map((t, ti) => {
        const meta         = _matchTrackMeta(t, slide.name);
        const displayTitle = meta?.title || t.title;
        const hasBehind    = meta && (meta.story || meta.location || meta.chords || meta.tabs || meta.lyrics);
        // Store BTM data by stable key; pass only the key into onclick
        const btmKey = 'btm-' + idx + '-' + ti;
        if (hasBehind) {
          _btmStore[btmKey] = {
            title:    displayTitle,
            story:    meta.story    || '',
            location: meta.location || '',
            chords:   meta.chords   || '',
            tabs:     meta.tabs     || '',
            lyrics:   meta.lyrics   || '',
          };
        }
        return `
          <div class="mu-tl-row-wrap">
            <div class="mu-tl-row" onclick="VW.Music.playUrl('${t.url}','${_esc(displayTitle)}','${_esc(slide.name)}')">
              <span class="mu-tl-num">${ti+1}</span>
              <span class="mu-tl-title">${_esc(displayTitle)}</span>
              <div class="mu-tl-right">
                ${hasBehind ? `<button class="mu-tl-btm-pill"
                  onclick="event.stopPropagation();VW.Music.openBtmModal('${btmKey}')">
                  Behind the music
                </button>` : ''}
                <button class="mu-tl-play"
                  onclick="event.stopPropagation();VW.Music.playUrl('${t.url}','${_esc(displayTitle)}','${_esc(slide.name)}')">
                  ${currentTrack?.url === t.url && isPlaying ? '⏸' : '▶'}
                </button>
              </div>
            </div>
          </div>`;
      }).join('');

      return `
        <div class="mu-fan-info-hd">
          <div class="mu-car-kind">Album</div>
          <div class="mu-car-name">${_esc(slide.name)}</div>
          <div class="mu-car-track-count">${visibleTracks.length} track${visibleTracks.length!==1?'s':''}</div>
          ${isAdmin ? `<button class="mu-edit-btn" onclick="VW.Music.openUploadModal()" style="margin-top:8px">+ Add track</button>` : ''}
        </div>
        <div class="mu-tracklist">${rows}</div>`;
    }

    // ── Single ────────────────────────────────────────────────────────────────
    const meta      = slide.meta;
    const title     = meta?.title || slide.name;
    const playing   = (currentTrack?.url === slide.url) && isPlaying;
    const hasBehind = meta && (meta.story || meta.location || meta.chords || meta.tabs || meta.lyrics);
    const btmKey    = 'btm-single-' + idx;
    const metaPills = meta ? [
      meta.key ? `<span class="mu-meta-pill">${meta.key}</span>` : '',
      meta.bpm ? `<span class="mu-meta-pill">♩ ${meta.bpm} bpm</span>` : '',
    ].filter(Boolean).join('') : '';

    if (hasBehind) {
      _btmStore[btmKey] = {
        title,
        story:    meta.story    || '',
        location: meta.location || '',
        chords:   meta.chords   || '',
        tabs:     meta.tabs     || '',
        lyrics:   meta.lyrics   || '',
      };
    }

    return `
      <div class="mu-fan-info-hd">
        <div class="mu-car-kind">Single</div>
        <div class="mu-car-name">${_esc(title)}</div>
        ${meta?.genre || meta?.year ? `<div class="mu-car-track-count">${[meta?.genre, meta?.year].filter(Boolean).join(' · ')}</div>` : ''}
        ${metaPills ? `<div class="mu-meta-pills" style="margin-top:8px">${metaPills}</div>` : ''}
      </div>
      <div class="mu-tracklist">
        <div class="mu-tl-row-wrap">
          <div class="mu-tl-row" onclick="VW.Music.playUrl('${slide.url||''}','${_esc(title)}','')">
            <span class="mu-tl-num">1</span>
            <span class="mu-tl-title">${_esc(title)}</span>
            <div class="mu-tl-right">
              ${hasBehind ? `<button class="mu-tl-btm-pill"
                onclick="event.stopPropagation();VW.Music.openBtmModal('${btmKey}')">
                Behind the music
              </button>` : ''}
              ${slide.url
                ? `<button class="mu-tl-play" onclick="event.stopPropagation();VW.Music.playUrl('${slide.url}','${_esc(title)}','')">
                    ${playing ? '⏸' : '▶'}
                   </button>`
                : '<span class="mu-no-audio">No audio</span>'}
            </div>
          </div>
        </div>
      </div>`;
  }

  function toggleTlBtm(id) {
    const panel = document.getElementById(id);
    const btn   = document.getElementById('mu-tl-btm-btn-' + id);
    if (!panel) return;
    const opening = panel.style.display === 'none';
    panel.style.display = opening ? 'block' : 'none';
    if (btn) btn.classList.toggle('open', opening);
  }

  function openBtmModal(key) {
    const meta  = _btmStore[key];
    if (!meta) return;

    const modal   = document.getElementById('mu-btm-modal');
    const titleEl = document.getElementById('mu-btm-modal-title');
    const bodyEl  = document.getElementById('mu-btm-modal-body');
    if (!modal || !bodyEl) return;

    if (titleEl) titleEl.textContent = meta.title || '';

    // Render sections if present, otherwise fall back to flat strings
    if (meta.sections?.length) {
      bodyEl.innerHTML = [
        meta.story    ? `<p class="mu-btm-story">${_esc(meta.story)}</p>` : '',
        meta.location ? `<div class="mu-btm-location">📍 ${_esc(meta.location)}</div>` : '',
        ...meta.sections.map(s => `
          <div class="mu-btm-section">
            <div class="mu-btm-section-hd">
              <span class="mu-btm-section-badge mu-sec-${s.type}">${_esc(s.label)}</span>
            </div>
            ${s.chords ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Chords</div><pre class="mu-btm-pre">${_esc(s.chords)}</pre></div>` : ''}
            ${s.lyrics ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Lyrics</div><pre class="mu-btm-pre mu-btm-lyrics">${_esc(s.lyrics)}</pre></div>` : ''}
            ${s.tabs   ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Tab</div><pre class="mu-btm-pre">${_esc(s.tabs)}</pre></div>` : ''}
          </div>`)
      ].join('');
    } else {
      bodyEl.innerHTML = [
        meta.story    ? `<p class="mu-btm-story">${_esc(meta.story)}</p>` : '',
        meta.location ? `<div class="mu-btm-location">📍 ${_esc(meta.location)}</div>` : '',
        meta.chords   ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Chords</div><pre class="mu-btm-pre">${_esc(meta.chords)}</pre></div>` : '',
        meta.tabs     ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Tab</div><pre class="mu-btm-pre">${_esc(meta.tabs)}</pre></div>` : '',
        meta.lyrics   ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Lyrics</div><pre class="mu-btm-pre mu-btm-lyrics">${_esc(meta.lyrics)}</pre></div>` : '',
      ].join('');
    }

    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  function closeBtmModal() {
    const modal = document.getElementById('mu-btm-modal');
    if (modal) modal.classList.remove('open');
    document.body.style.overflow = '';
  }

  function carouselNav(dir) {
    const slides = _buildSlides();
    _carouselIdx = ((_carouselIdx + dir) % slides.length + slides.length) % slides.length;
    _renderSection('catalog');
  }

  function carouselGo(idx) {
    _carouselIdx = idx;
    _renderSection('catalog');
  }

  function _btmToggle(t) {
    const id = 'mu-btm-' + t.id;
    return `<div class="mu-btm-wrap" onclick="event.stopPropagation()">
      <button class="mu-btm-toggle" id="mu-btm-btn-${t.id}" onclick="VW.Music.toggleBtm('${t.id}')">
        Behind the music <span class="mu-btm-chev">›</span>
      </button>
      <div class="mu-btm-panel" id="${id}" style="display:none">
        ${_btmContent(t)}
      </div>
    </div>`;
  }

  function _btmFull(t) {
    return `<div class="mu-td-btm">
      <div class="mu-btm-lbl" style="margin-bottom:12px">Behind the music</div>
      ${_btmContent(t)}
    </div>`;
  }

  function _btmContent(t) {
    return [
      t.story    ? `<p class="mu-btm-story">${_esc(t.story)}</p>` : '',
      t.location ? `<div class="mu-btm-location">📍 ${_esc(t.location)}</div>` : '',
      t.chords   ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Chords</div><pre class="mu-btm-pre">${_esc(t.chords)}</pre></div>` : '',
      t.tabs     ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Tab</div><pre class="mu-btm-pre">${_esc(t.tabs)}</pre></div>` : '',
      t.lyrics   ? `<div class="mu-btm-block"><div class="mu-btm-lbl">Lyrics</div><pre class="mu-btm-pre mu-btm-lyrics">${_esc(t.lyrics)}</pre></div>` : '',
    ].join('');
  }


  function _trackCard(t, isAdmin) {
    const playing    = currentTrack?.id === t.id && isPlaying;
    const hasBehind  = t.story || t.location || t.chords || t.tabs || t.lyrics;
    const btmId      = 'mu-btm-' + t.id;

    const admin = isAdmin ? `
      <div class="mu-track-admin" onclick="event.stopPropagation()">
        <button class="mu-edit-btn" onclick="VW.Music.openEditModal('${t.id}')">✏ Edit</button>
        <button class="mu-del-btn"  onclick="VW.Music.deleteTrack('${t.id}')">🗑</button>
      </div>` : '';

    const anim = playing
      ? '<div class="mu-playing-anim"><span></span><span></span><span></span></div>' : '';

    // Key + BPM pills (always shown if present)
    const metaPills = [
      t.key ? `<span class="mu-meta-pill">${t.key}</span>` : '',
      t.bpm ? `<span class="mu-meta-pill">♩ ${t.bpm} bpm</span>` : '',
    ].filter(Boolean).join('');

    // Behind the Music collapsible
    const behind = hasBehind ? `
      <div class="mu-btm-wrap" onclick="event.stopPropagation()">
        <button class="mu-btm-toggle" id="mu-btm-btn-${t.id}"
          onclick="VW.Music.toggleBtm('${t.id}')">
          Behind the music <span class="mu-btm-chev">›</span>
        </button>
        <div class="mu-btm-panel" id="${btmId}" style="display:none">
          ${t.story    ? `<p class="mu-btm-story">${t.story}</p>` : ''}
          ${t.location ? `<div class="mu-btm-location">📍 ${t.location}</div>` : ''}
          ${t.chords   ? `<div class="mu-btm-block">
            <div class="mu-btm-lbl">Chords</div>
            <pre class="mu-btm-pre">${t.chords}</pre></div>` : ''}
          ${t.tabs     ? `<div class="mu-btm-block">
            <div class="mu-btm-lbl">Tab</div>
            <pre class="mu-btm-pre">${t.tabs}</pre></div>` : ''}
          ${t.lyrics   ? `<div class="mu-btm-block">
            <div class="mu-btm-lbl">Lyrics</div>
            <pre class="mu-btm-pre mu-btm-lyrics">${t.lyrics}</pre></div>` : ''}
        </div>
      </div>` : '';

    return `
      <div class="mu-track-card${currentTrack?.id===t.id?' playing':''}"
           onclick="VW.Music.playTrack('${t.id}')">
        ${admin}
        <div class="mu-track-art">
          <span style="font-size:22px">${t.src ? '🎵' : '🎸'}</span>
          ${anim}
        </div>
        <div class="mu-track-info">
          <div class="mu-track-title">${t.title}</div>
          <div class="mu-track-meta">${t.genre} · ${t.year}</div>
          ${metaPills ? `<div class="mu-meta-pills">${metaPills}</div>` : ''}
          ${behind}
        </div>
        <div class="mu-track-actions">
          <button class="mu-play-pill${playing?' active':''}"
            onclick="event.stopPropagation();VW.Music.playTrack('${t.id}')">
            ${playing ? '⏸' : '▶'}
          </button>
          ${t.src ? '' : '<span class="mu-no-audio">No audio</span>'}
        </div>
      </div>`;
  }

  function toggleBtm(id) {
    const panel  = document.getElementById('mu-btm-' + id);
    const btn    = document.getElementById('mu-btm-btn-' + id);
    if (!panel) return;
    const opening = panel.style.display === 'none';
    panel.style.display = opening ? 'block' : 'none';
    if (btn) btn.classList.toggle('open', opening);
  }

  // ── Gallery ───────────────────────────────────────────────────────────────
  function _renderGallery(wrap) {
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    const bar = isAdmin ? `
      <div class="mu-admin-bar">
        <span style="font-size:12px;color:rgba(255,255,255,.5)">Admin · Photo Gallery</span>
        <label class="mu-add-btn" style="cursor:pointer">
          + Add Photos
          <input type="file" accept="image/*" multiple style="display:none"
            onchange="VW.Music.addGalleryPhotos(this.files)"/>
        </label>
      </div>` : '';

    const photos = galleryPhotos.length
      ? galleryPhotos.map((p, i) => `
          <div class="mu-gallery-item" onclick="VW.Music.openLightbox(${i})">
            <img src="${p.url}" alt="${p.caption||''}" loading="lazy"/>
            ${p.caption ? `<div class="mu-gallery-caption">${p.caption}</div>` : ''}
          </div>`).join('')
      : `<div class="mu-gallery-empty">
           <div style="font-size:48px;margin-bottom:12px">📷</div>
           <div style="color:rgba(255,255,255,.35);font-size:14px">Gallery coming soon</div>
         </div>`;

    wrap.innerHTML = bar + '<div class="mu-gallery-grid">' + photos + '</div>';
  }

  function addGalleryPhotos(files) {
    Array.from(files).forEach(f =>
      galleryPhotos.push({ url: URL.createObjectURL(f), caption: '' }));
    _renderGallery(document.getElementById('mu-section-wrap'));
  }

  function openLightbox(i) {
    const lb = document.getElementById('mu-lightbox');
    if (lb) { lb.querySelector('img').src = galleryPhotos[i]?.url || ''; lb.style.display = 'flex'; }
  }

  // ── Links ─────────────────────────────────────────────────────────────────
  function _renderLinks(wrap) {
    wrap.innerHTML = `
      <div class="mu-links-hero">
        <div class="mu-links-artist">Will Yoste</div>
        <div class="mu-links-tagline">Find the music on your platform of choice</div>
      </div>
      <div class="mu-links-grid">
        <a class="mu-link-card spotify" href="${LINKS.spotify}" target="_blank" rel="noopener">
          <div class="mu-link-icon">🎧</div>
          <div><div class="mu-link-name">Spotify</div><div class="mu-link-handle">@willyoste</div></div>
          <div class="mu-link-arrow">›</div>
        </a>
        <a class="mu-link-card youtube" href="${LINKS.youtube}" target="_blank" rel="noopener">
          <div class="mu-link-icon">▶</div>
          <div><div class="mu-link-name">YouTube</div><div class="mu-link-handle">@willyoste</div></div>
          <div class="mu-link-arrow">›</div>
        </a>
        <a class="mu-link-card apple" href="${LINKS.apple}" target="_blank" rel="noopener">
          <div class="mu-link-icon">🍎</div>
          <div><div class="mu-link-name">Apple Music</div><div class="mu-link-handle">Will Yoste</div></div>
          <div class="mu-link-arrow">›</div>
        </a>
      </div>`;
  }

  // ── Sheets ────────────────────────────────────────────────────────────────
  function _renderSheets(wrap) {
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    const cards = tracks.map(t => `
      <div class="mu-sheet-card">
        <div class="mu-sheet-hd" onclick="VW.Music.toggleSheet('sheet-${t.id}','chev-${t.id}')">
          <div>
            <div class="mu-sheet-title">${t.title}</div>
            <div class="mu-sheet-meta">${t.genre} · ${t.year}</div>
          </div>
          <span class="mu-sheet-chevron" id="chev-${t.id}">›</span>
        </div>
        <div class="mu-sheet-body" id="sheet-${t.id}" style="display:none">
          <div class="mu-sheet-section">
            <div class="mu-sheet-lbl">Key</div>
            <div class="mu-sheet-val">${t.key || '—'}</div>
          </div>
          ${t.bpm ? `<div class="mu-sheet-section">
            <div class="mu-sheet-lbl">BPM</div>
            <div class="mu-sheet-val">${t.bpm}</div>
          </div>` : ''}
          ${t.chords ? `<div class="mu-sheet-section">
            <div class="mu-sheet-lbl">Chords &amp; Changes</div>
            <pre class="mu-tabs-pre" style="font-size:13px">${t.chords}</pre>
          </div>` : ''}
          ${t.tabs ? `<div class="mu-sheet-section">
            <div class="mu-sheet-lbl">Guitar Tab</div>
            <pre class="mu-tabs-pre">${t.tabs}</pre>
          </div>` : ''}
          ${t.lyrics ? `<div class="mu-sheet-section">
            <div class="mu-sheet-lbl">Lyrics</div>
            <pre class="mu-lyrics-pre">${t.lyrics}</pre>
          </div>` : ''}
          ${(!t.tabs && !t.lyrics) ? `<div class="mu-sheet-section">
            <div style="color:rgba(255,255,255,.3);font-size:12px;font-style:italic">
              No tabs or lyrics added yet${isAdmin ? ' — click Edit to add them' : ''}.
            </div>
          </div>` : ''}
          ${isAdmin ? `<button class="mu-sheet-edit" onclick="VW.Music.openEditModal('${t.id}')">✏ Edit sheet</button>` : ''}
        </div>
      </div>`).join('');

    wrap.innerHTML = `
      <div style="max-width:700px;margin:0 auto">
        <div class="mu-sheets-intro">Guitar tabs, chord charts, and lyrics for each song.</div>
        ${cards}
      </div>`;
  }

  function toggleSheet(bodyId, chevId) {
    const el   = document.getElementById(bodyId);
    const chev = document.getElementById(chevId);
    if (!el) return;
    const open = el.style.display !== 'none';
    el.style.display = open ? 'none' : 'block';
    if (chev) chev.style.transform = open ? '' : 'rotate(90deg)';
  }

  // ── Audio playback ────────────────────────────────────────────────────────
  // Play by URL (for filesystem tracks that may not be in catalog JSON)
  function playUrl(url, title, album) {
    if (!url) return;
    const meta    = tracks.find(t => t.src === url) || { id: url, title, album, src: url };
    const isSame  = currentTrack?._queueUrl === url;

    currentTrack = { ...meta, url, _queueUrl: url };

    if (audioEl) {
      if (isSame) {
        // Same track — toggle play/pause, preserve position
        if (isPlaying) audioEl.pause();
        else           audioEl.play().catch(() => window.toast?.('Audio unavailable', 'error'));
      } else {
        // New track — load and play from start
        audioEl.src = url;
        audioEl.currentTime = 0;
        audioEl.play().catch(() => window.toast?.('Audio unavailable', 'error'));
      }
    }

    _showMini();
    _syncMiniInfo();
    _renderSection(activeSection);
  }

  function playTrack(id) {
    const t = tracks.find(t => t.id === id);
    if (!t) return;
    const isSame = currentTrack?.id === id;
    currentTrack = { ...t, _queueUrl: t.src };

    if (!t.src) {
      _showMini();
      _syncMiniInfo();
      window.toast?.('No audio file — upload one in admin mode', 'error', 3000);
      _renderSection(activeSection);
      return;
    }

    if (!isSame) {
      audioEl.src = t.src;
      audioEl.currentTime = 0;
    }

    if (isPlaying && isSame) audioEl.pause();
    else audioEl.play().catch(() => window.toast?.('Could not play audio', 'error'));

    _showMini();
    _syncMiniInfo();
    _renderSection(activeSection);
  }

  function togglePlay() {
    if (!currentTrack) { if (tracks.length) playTrack(tracks[0].id); return; }
    if (!currentTrack.src) return;
    isPlaying ? audioEl.pause() : audioEl.play();
  }

  function _currentQueueIdx() {
    const url   = currentTrack?._queueUrl || currentTrack?.src || currentTrack?.url;
    if (!url) return -1;
    return _buildQueue().findIndex(q => q.url === url);
  }

  function prevTrack() {
    const queue = _buildQueue();
    if (!queue.length) return;
    const i = _currentQueueIdx();
    const prev = queue[Math.max(0, i - 1)];
    if (prev) playUrl(prev.url, prev.title, prev.album);
  }

  function nextTrack() {
    const queue = _buildQueue();
    if (!queue.length) return;
    const i = _currentQueueIdx();
    if (i < 0 || i >= queue.length - 1) return;
    const next = queue[i + 1];
    if (next) playUrl(next.url, next.title, next.album);
  }

  function _onTimeUpdate() {
    const pct = audioEl.duration ? (audioEl.currentTime / audioEl.duration) * 100 : 0;
    const fill = document.getElementById('mu-mini-fill');
    if (fill) fill.style.width = pct + '%';
    const time = document.getElementById('mu-mini-time');
    if (time) time.textContent = _fmt(audioEl.currentTime) + ' / ' + _fmt(audioEl.duration);
  }

  function _onEnded() {
    const queue = _buildQueue();
    const i     = _currentQueueIdx();
    if (i >= 0 && i < queue.length - 1) {
      const next = queue[i + 1];
      playUrl(next.url, next.title, next.album);
    } else {
      isPlaying = false;
      _syncPlayBtns();
    }
  }

  function _fmt(s) {
    if (!s || isNaN(s)) return '0:00';
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
  }

  function seekMini(e) {
    if (!audioEl?.duration) return;
    const bg = e.currentTarget;
    audioEl.currentTime = audioEl.duration * ((e.clientX - bg.getBoundingClientRect().left) / bg.offsetWidth);
  }

  function setVol(v) { if (audioEl) audioEl.volume = v / 100; }

  function _syncPlayBtns() {
    ['mu-mini-play'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = isPlaying ? '⏸' : '▶';
    });
    if (activeSection === 'catalog') _renderSection('catalog');
  }

  // ── Mini-player ───────────────────────────────────────────────────────────
  function _showMini() {
    const mp = document.getElementById('mu-mini-player');
    if (mp) { mp.style.display = 'flex'; miniVisible = true; }
  }

  function _syncMiniInfo() {
    const t = currentTrack; if (!t) return;
    const title = document.getElementById('mu-mini-title');
    if (title) title.textContent = t.title;
    const meta = document.getElementById('mu-mini-meta');
    if (meta) meta.textContent = `${t.genre} · ${t.year}`;
  }

  function toggleMini() {
    miniExpanded = !miniExpanded;
    const body = document.getElementById('mu-mini-body');
    const tog  = document.getElementById('mu-mini-tog');
    if (body) body.style.display = miniExpanded ? 'block' : 'none';
    if (tog)  tog.textContent    = miniExpanded ? '▾' : '▴';
  }

  function closeMini() {
    const mp = document.getElementById('mu-mini-player');
    if (mp) { mp.style.display = 'none'; miniVisible = false; }
    audioEl?.pause();
  }

  // ── Admin upload/edit modal ───────────────────────────────────────────────
  function openUploadModal() {
    _editingId = null;
    _clearForm();
    document.getElementById('mu-modal-title').textContent = 'Add Track';
    document.getElementById('mu-upload-modal')?.classList.add('open');
  }

  function openEditModal(id) {
    const t = tracks.find(t => t.id === id);
    if (!t) return;
    _editingId = id;
    _fillForm(t);
    document.getElementById('mu-modal-title').textContent = 'Edit Track';
    document.getElementById('mu-upload-modal')?.classList.add('open');
  }

  async function openAdminEditor(id = '', title = '', src = '', album = '') {
    await _loadCatalog();
    const wantedTitle = (title || '').toLowerCase();
    const t = tracks.find(t => t.id === id) ||
              tracks.find(t => src && t.src === src) ||
              tracks.find(t => wantedTitle && (t.title || '').toLowerCase() === wantedTitle);
    if (t) {
      openEditModal(t.id);
    } else {
      openEditForTrack(title, src, album);
    }
  }

  // Open modal pre-filled for a filesystem track with no catalog entry yet.
  // src is the audio URL already on disk — no re-upload needed.
  function openEditForTrack(title, src, album) {
    _editingId = null;   // treat as new — will create a catalog entry
    _clearForm();
    const titleEl = document.getElementById('mu-f-title');
    const albumEl = document.getElementById('mu-f-album');
    const audioLbl = document.getElementById('mu-f-audio-lbl');
    if (titleEl)  titleEl.value  = title  || '';
    if (albumEl)  albumEl.value  = album  || '';
    if (audioLbl) audioLbl.textContent = src ? '✓ Audio on file (no re-upload needed)' : 'No file chosen';
    // Store the existing src so saveTrackForm can use it without a new file upload
    document.getElementById('mu-upload-modal').dataset.existingSrc = src || '';
    document.getElementById('mu-modal-title').textContent = 'Add metadata — ' + (title || 'Track');
    document.getElementById('mu-upload-modal')?.classList.add('open');
  }

  function closeUploadModal() {
    const modal = document.getElementById('mu-upload-modal');
    if (modal) { modal.classList.remove('open'); modal.dataset.existingSrc = ''; }
    _editingId = null;
  }

  // ── Chord identifier ──────────────────────────────────────────────────────

  function identifyChord(input) {
    const resultEl = document.getElementById('mu-chord-id-result');
    if (!resultEl) return;
    // Parse note names from input
    const tokens = input.trim().split(/[\s,/]+/).filter(Boolean);
    if (tokens.length < 2) { resultEl.textContent = '—'; resultEl.style.color = ''; return; }
    const midi = tokens.map(n => {
      const norm = n.replace(/b/g, 'b').replace(/♭/g, 'b').replace(/♯/g, '#');
      // Try 2-char first, then 1-char
      for (const key of Object.keys(NOTE_MAP)) {
        if (norm.toUpperCase() === key.toUpperCase()) return NOTE_MAP[key];
      }
      return null;
    }).filter(v => v !== null);

    if (midi.length < 2) { resultEl.textContent = 'Unknown'; resultEl.style.color = '#e84235'; return; }

    // Normalize to 0-based and deduplicate
    const root = midi[0];
    const intervals = [...new Set(midi.map(n => ((n - root) % 12 + 12) % 12))].sort((a,b)=>a-b);

    // Find matching chord type
    let match = null;
    for (const [type, pattern] of Object.entries(CHORD_INTERVALS)) {
      const normPat = pattern.map(i => i % 12).sort((a,b)=>a-b);
      if (JSON.stringify(normPat) === JSON.stringify(intervals)) {
        match = type; break;
      }
    }

    const rootName = NOTE_NAMES[root % 12];
    if (match) {
      const label = match === 'major' ? rootName
                  : match === 'minor' ? rootName + 'm'
                  : rootName + match;
      resultEl.textContent = label;
      resultEl.style.color = '#5bc8f5';
    } else {
      resultEl.textContent = rootName + '?';
      resultEl.style.color = 'rgba(255,255,255,.45)';
    }
  }

  // ── Section manager ────────────────────────────────────────────────────────

  function _sectionTypeLabel(type) {
    return type.charAt(0).toUpperCase() + type.slice(1).replace('-', '-');
  }

  function _countSectionsOfType(type) {
    return _sections.filter(s => s.type === type).length;
  }

  function addSection() {
    const typeEl = document.getElementById('mu-new-section-type');
    const type   = typeEl?.value || 'verse';
    const count  = _countSectionsOfType(type);
    // Label: "Verse 1", "Verse 2", "Chorus", "Bridge" etc.
    const needsNum = ['verse','chorus','pre-chorus'].includes(type);
    const label  = needsNum ? _sectionTypeLabel(type) + ' ' + (count + 1)
                            : _sectionTypeLabel(type);
    const section = { id: 'sec-' + Date.now(), type, label, chords: '', lyrics: '', tabs: '' };
    _sections.push(section);
    _renderSectionsList();
  }

  function removeSection(id) {
    _sections = _sections.filter(s => s.id !== id);
    _renderSectionsList();
  }

  function moveSectionUp(id) {
    const i = _sections.findIndex(s => s.id === id);
    if (i > 0) { [_sections[i-1], _sections[i]] = [_sections[i], _sections[i-1]]; _renderSectionsList(); }
  }

  function moveSectionDown(id) {
    const i = _sections.findIndex(s => s.id === id);
    if (i < _sections.length - 1) { [_sections[i], _sections[i+1]] = [_sections[i+1], _sections[i]]; _renderSectionsList(); }
  }

  function _updateSection(id, field, val) {
    const s = _sections.find(s => s.id === id);
    if (s) s[field] = val;
  }

  function _renderSectionsList() {
    const el = document.getElementById('mu-sections-list');
    if (!el) return;
    if (!_sections.length) {
      el.innerHTML = '<div class="mu-sections-empty">No sections yet — add one above.</div>';
      return;
    }
    el.innerHTML = _sections.map((s, i) => `
      <div class="mu-section-card" id="mu-sec-${s.id}"
        draggable="true"
        ondragstart="VW.Music._secDragStart(event,'${s.id}')"
        ondragover="VW.Music._secDragOver(event,'${s.id}')"
        ondragleave="VW.Music._secDragLeave(event)"
        ondrop="VW.Music._secDrop(event,'${s.id}')"
        ondragend="VW.Music._secDragEnd(event)">
        <div class="mu-section-hd">
          <span class="mu-sec-drag-handle" title="Drag to reorder">⠿</span>
          <input class="mu-f-input mu-section-label-input" value="${_esc(s.label)}"
            onchange="VW.Music._updateSection('${s.id}','label',this.value)"
            title="Edit section name"/>
          <span class="mu-section-type-badge mu-sec-${s.type}">${_sectionTypeLabel(s.type)}</span>
          <div class="mu-section-actions">
            ${i > 0 ? `<button class="mu-sec-btn" onclick="VW.Music.moveSectionUp('${s.id}')" title="Move up">↑</button>` : ''}
            ${i < _sections.length-1 ? `<button class="mu-sec-btn" onclick="VW.Music.moveSectionDown('${s.id}')" title="Move down">↓</button>` : ''}
            <button class="mu-sec-btn mu-sec-del" onclick="VW.Music.removeSection('${s.id}')" title="Remove">✕</button>
          </div>
        </div>
        <div class="mu-section-body">
          <div class="mu-section-cols">

            <!-- LEFT: chord wheel + chords + tab -->
            <div class="mu-section-left">
              <div class="mu-f-group">
                <label class="mu-f-lbl" style="margin-bottom:6px">Chords</label>
                <div class="mu-chord-wheel" id="mu-cw-${s.id}">
                  <div class="mu-cw-notes">${NOTE_NAMES.map(n =>
                    `<button class="mu-cw-note" data-note="${n}"
                      onclick="VW.Music._toggleNote('${s.id}','${n}',this)">${n}</button>`
                  ).join('')}</div>
                  <div class="mu-cw-result-row">
                    <span class="mu-cw-result" id="mu-cwr-${s.id}">Select notes</span>
                    <button class="mu-cw-add" id="mu-cwa-${s.id}"
                      onclick="VW.Music._appendChordToSection('${s.id}')"
                      title="Add chord to progression" disabled>+ Add</button>
                    <button class="mu-cw-clear" onclick="VW.Music._clearNotes('${s.id}')">✕ Clear</button>
                  </div>
                </div>
                <textarea class="mu-f-input mu-f-ta mu-f-mono mu-section-chords"
                  rows="3" placeholder="e.g. G – Cadd9 – Em – D"
                  onchange="VW.Music._updateSection('${s.id}','chords',this.value)"
                  >${_esc(s.chords)}</textarea>
              </div>
              <div class="mu-f-group">
                <label class="mu-f-lbl" style="margin-bottom:3px">Tab <span style="font-weight:400;opacity:.5">(optional)</span></label>
                <textarea class="mu-f-input mu-f-ta mu-f-mono mu-section-tabs"
                  rows="3" placeholder="Guitar tab…"
                  onchange="VW.Music._updateSection('${s.id}','tabs',this.value)"
                  >${_esc(s.tabs)}</textarea>
              </div>
            </div>

            <!-- RIGHT: lyrics -->
            <div class="mu-section-right">
              <div class="mu-f-group" style="height:100%">
                <label class="mu-f-lbl" style="margin-bottom:3px">Lyrics</label>
                <textarea class="mu-f-input mu-f-ta mu-section-lyrics mu-section-lyrics-tall"
                  placeholder="Lyrics for this section…"
                  onchange="VW.Music._updateSection('${s.id}','lyrics',this.value)"
                  >${_esc(s.lyrics)}</textarea>
              </div>
            </div>

          </div>
        </div>
      </div>`).join('');
  }

  // ── Section drag-and-drop reordering ──────────────────────────────────────

  let _dragSectionId = null;

  function _secDragStart(e, id) {
    _dragSectionId = id;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
    // Slight delay so the card isn't hidden before drag image captures
    setTimeout(() => {
      const el = document.getElementById('mu-sec-' + id);
      if (el) el.classList.add('mu-sec-dragging');
    }, 0);
  }

  function _secDragOver(e, targetId) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (targetId === _dragSectionId) return;
    // Show drop indicator above or below target
    const el = document.getElementById('mu-sec-' + targetId);
    if (!el) return;
    const rect   = el.getBoundingClientRect();
    const midY   = rect.top + rect.height / 2;
    const before = e.clientY < midY;
    // Clear all indicators first
    document.querySelectorAll('.mu-sec-drop-above,.mu-sec-drop-below')
      .forEach(n => n.classList.remove('mu-sec-drop-above','mu-sec-drop-below'));
    el.classList.add(before ? 'mu-sec-drop-above' : 'mu-sec-drop-below');
  }

  function _secDragLeave(e) {
    // Only clear if truly leaving (not entering a child)
    if (!e.currentTarget.contains(e.relatedTarget)) {
      e.currentTarget.classList.remove('mu-sec-drop-above', 'mu-sec-drop-below');
    }
  }

  function _secDrop(e, targetId) {
    e.preventDefault();
    document.querySelectorAll('.mu-sec-drop-above,.mu-sec-drop-below')
      .forEach(n => n.classList.remove('mu-sec-drop-above','mu-sec-drop-below'));
    if (!_dragSectionId || _dragSectionId === targetId) return;

    const fromIdx = _sections.findIndex(s => s.id === _dragSectionId);
    const toEl    = document.getElementById('mu-sec-' + targetId);
    const toIdx   = _sections.findIndex(s => s.id === targetId);
    if (fromIdx < 0 || toIdx < 0) return;

    // Determine insert position (before or after target)
    const rect   = toEl.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    const insert = before ? toIdx : toIdx + 1;

    const [moved] = _sections.splice(fromIdx, 1);
    const adjusted = insert > fromIdx ? insert - 1 : insert;
    _sections.splice(adjusted, 0, moved);
    _renderSectionsList();
  }

  function _secDragEnd(e) {
    _dragSectionId = null;
    document.querySelectorAll('.mu-sec-dragging,.mu-sec-drop-above,.mu-sec-drop-below')
      .forEach(n => n.classList.remove('mu-sec-dragging','mu-sec-drop-above','mu-sec-drop-below'));
  }



  const _noteSelections = {};  // sectionId → Set of selected note names
  const _esc = window.VW?.esc || (s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')); // shared util

  function _toggleNote(sectionId, note, btn) {
    if (!_noteSelections[sectionId]) _noteSelections[sectionId] = new Set();
    const sel = _noteSelections[sectionId];
    if (sel.has(note)) { sel.delete(note); btn.classList.remove('active'); }
    else               { sel.add(note);    btn.classList.add('active'); }
    _updateChordResult(sectionId);
  }

  function _clearNotes(sectionId) {
    _noteSelections[sectionId] = new Set();
    const wheel = document.getElementById('mu-cw-' + sectionId);
    wheel?.querySelectorAll('.mu-cw-note').forEach(b => b.classList.remove('active'));
    const res = document.getElementById('mu-cwr-' + sectionId);
    if (res) { res.textContent = 'Select notes'; res.style.color = ''; }
    const add = document.getElementById('mu-cwa-' + sectionId);
    if (add) add.disabled = true;
  }

  function _updateChordResult(sectionId) {
    const sel = _noteSelections[sectionId];
    const res = document.getElementById('mu-cwr-' + sectionId);
    const add = document.getElementById('mu-cwa-' + sectionId);
    if (!res) return;
    if (!sel || sel.size < 2) {
      res.textContent = sel?.size === 1 ? 'Need more notes' : 'Select notes';
      res.style.color = '';
      if (add) add.disabled = true;
      return;
    }
    // Map selected notes to MIDI values
    const midi = [...sel].map(n => NOTE_MAP[n]).filter(v => v !== undefined);
    const root = midi[0];
    const intervals = [...new Set(midi.map(n => ((n - root) % 12 + 12) % 12))].sort((a,b)=>a-b);
    let match = null;
    for (const [type, pattern] of Object.entries(CHORD_INTERVALS)) {
      if (JSON.stringify(pattern.map(i=>i%12).sort((a,b)=>a-b)) === JSON.stringify(intervals)) {
        match = type; break;
      }
    }
    const rootName = NOTE_NAMES[root % 12];
    const label = match
      ? (match === 'major' ? rootName : match === 'minor' ? rootName + 'm' : rootName + match)
      : rootName + '?';
    res.textContent = label;
    res.style.color = match ? '#5bc8f5' : 'rgba(255,255,255,.4)';
    res.dataset.chord = match ? label : '';
    if (add) add.disabled = !match;
  }

  function _appendChordToSection(sectionId) {
    const res = document.getElementById('mu-cwr-' + sectionId);
    const chord = res?.dataset.chord;
    if (!chord) return;
    const s = _sections.find(s => s.id === sectionId);
    if (s) {
      s.chords = s.chords ? s.chords + ' – ' + chord : chord;
      // Update the textarea live
      const card = document.getElementById('mu-sec-' + sectionId);
      const ta   = card?.querySelector('.mu-section-chords');
      if (ta) ta.value = s.chords;
    }
    _clearNotes(sectionId);
  }

  function _identifyIntoSection(sectionId, input) {
    const resultEl = document.getElementById('mu-cid-' + sectionId);
    if (!resultEl) return;
    const tokens = input.trim().split(/[\s,/]+/).filter(Boolean);
    if (tokens.length < 2) { resultEl.textContent = '—'; resultEl.style.color = ''; return; }
    const midi = tokens.map(n => {
      for (const key of Object.keys(NOTE_MAP)) {
        if (n.toUpperCase() === key.toUpperCase()) return NOTE_MAP[key];
      }
      return null;
    }).filter(v => v !== null);
    if (midi.length < 2) { resultEl.textContent = '?'; return; }
    const root = midi[0];
    const intervals = [...new Set(midi.map(n => ((n - root) % 12 + 12) % 12))].sort((a,b)=>a-b);
    let match = null;
    for (const [type, pattern] of Object.entries(CHORD_INTERVALS)) {
      if (JSON.stringify(pattern.map(i=>i%12).sort((a,b)=>a-b)) === JSON.stringify(intervals)) {
        match = type; break;
      }
    }
    const rootName = NOTE_NAMES[root % 12];
    const label = match ? (match === 'major' ? rootName : match === 'minor' ? rootName + 'm' : rootName + match) : rootName + '?';
    resultEl.textContent = label;
    resultEl.style.color = match ? '#5bc8f5' : 'rgba(255,255,255,.4)';
    // Append identified chord to the section's chord textarea
    if (match) {
      const s = _sections.find(s => s.id === sectionId);
      if (s) {
        s.chords = (s.chords ? s.chords + ' – ' : '') + label;
        // Update the textarea
        const card = document.getElementById('mu-sec-' + sectionId);
        const ta   = card?.querySelector('.mu-section-chords');
        if (ta) ta.value = s.chords;
      }
    }
  }

  // ── Legacy flat-string → sections migration ────────────────────────────────

  function _trackToSections(t) {
    // If track already has sections array, use it
    if (t.sections?.length) return t.sections.map(s => ({ ...s }));
    // Otherwise convert legacy flat strings into sections
    const sections = [];
    // Try to parse "Verse: G-C-Em-D\nChorus: C-G-Am-F" style chords
    const chordText = t.chords || '';
    const lyricsText = t.lyrics || '';
    const tabsText   = t.tabs   || '';
    const hasSectionedChords = /^(intro|verse|pre-chorus|chorus|bridge|solo|outro|coda)\s*:/im.test(chordText);
    if (hasSectionedChords) {
      const lines = chordText.split('\n');
      let current = null;
      lines.forEach(line => {
        const m = line.match(/^(intro|verse|pre-chorus|chorus|bridge|solo|outro|coda)\s*:\s*(.*)/i);
        if (m) {
          if (current) sections.push(current);
          const type = m[1].toLowerCase();
          const label = _sectionTypeLabel(type);
          current = { id: 'sec-' + Date.now() + Math.random(), type, label, chords: m[2].trim(), lyrics: '', tabs: '' };
        } else if (current) {
          current.chords += (current.chords ? '\n' : '') + line;
        }
      });
      if (current) sections.push(current);
    } else if (chordText || lyricsText || tabsText) {
      // Put everything in a single generic verse
      sections.push({ id: 'sec-' + Date.now(), type: 'verse', label: 'Verse 1', chords: chordText, lyrics: lyricsText, tabs: tabsText });
    }
    return sections;
  }

  function _sectionsToFlat(sections) {
    // Build backward-compatible flat strings from sections (for BTM display)
    const chords  = sections.map(s => s.chords ? s.label + ':\n' + s.chords : '').filter(Boolean).join('\n\n');
    const lyrics  = sections.map(s => s.lyrics ? '[' + s.label + ']\n' + s.lyrics : '').filter(Boolean).join('\n\n');
    const tabs    = sections.map(s => s.tabs   ? s.label + ':\n' + s.tabs   : '').filter(Boolean).join('\n\n');
    return { chords, lyrics, tabs };
  }


  function _clearForm() {
    ['mu-f-title','mu-f-year','mu-f-genre','mu-f-story','mu-f-location',
     'mu-f-key','mu-f-bpm','mu-f-album'].forEach(id => {
      const el = document.getElementById(id); if (el) el.value = '';
    });
    const lbl = document.getElementById('mu-f-audio-lbl');
    if (lbl) lbl.textContent = 'No file chosen';
    const artLbl = document.getElementById('mu-f-art-lbl');
    if (artLbl) artLbl.textContent = 'No image chosen';
    _sections = [];
    _renderSectionsList();
  }

  function onArtFileChosen(files) {
    const lbl = document.getElementById('mu-f-art-lbl');
    if (lbl && files?.length) lbl.textContent = files[0].name;
  }

  function _fillForm(t) {
    const map = {
      'mu-f-title': t.title, 'mu-f-year': t.year, 'mu-f-genre': t.genre,
      'mu-f-story': t.story||'', 'mu-f-location': t.location||'',
      'mu-f-key': t.key||'', 'mu-f-bpm': t.bpm||'',
    };
    Object.entries(map).forEach(([id, val]) => {
      const el = document.getElementById(id); if (el) el.value = val;
    });
    const lbl = document.getElementById('mu-f-audio-lbl');
    if (lbl) lbl.textContent = t.src ? '✓ Audio on file' : 'No file chosen';
    // Load sections (migrating legacy flat strings if needed)
    _sections = _trackToSections(t);
    _renderSectionsList();
  }

  async function saveTrackForm() {
    const g = id => document.getElementById(id)?.value?.trim() || '';
    const title = g('mu-f-title');
    if (!title) { window.toast?.('Title is required', 'error'); return; }

    let src = _editingId ? (tracks.find(t => t.id === _editingId)?.src || null) : null;
    // If openEditForTrack set an existingSrc (filesystem track with no catalog entry),
    // use that as the src instead of requiring a new file upload
    if (!src) {
      const existingSrc = document.getElementById('mu-upload-modal')?.dataset.existingSrc;
      if (existingSrc) src = existingSrc;
    }

    const fileInput  = document.getElementById('mu-f-audio');
    const artInput   = document.getElementById('mu-f-art');
    const albumInput = document.getElementById('mu-f-album');
    const albumName  = albumInput?.value.trim() || '';

    if (fileInput?.files?.length) {
      const fd = new FormData();
      fd.append('file',  fileInput.files[0]);
      fd.append('album', albumName);
      if (artInput?.files?.length) fd.append('art', artInput.files[0]);
      try {
        const r = await fetch('/api/music/upload', { method:'POST', body: fd });
        const d = await r.json();
        if (d.ok) {
          src = d.url;
          // Reload the library scan after upload
          try {
            const r2 = await fetch('/api/music/library');
            _library = await r2.json();
          } catch {}
        } else {
          window.toast?.('Upload failed: ' + (d.error||''), 'error');
        }
      } catch { window.toast?.('Upload error', 'error'); }
    }

    // Read current sections values from DOM (textareas update via onchange)
    const sectionEls = document.querySelectorAll('#mu-sections-list .mu-section-card');
    sectionEls.forEach(card => {
      const secId = card.id.replace('mu-sec-', '');
      const s = _sections.find(s => s.id === secId);
      if (!s) return;
      const chordsEl = card.querySelector('.mu-section-chords');
      const lyricsEl = card.querySelector('.mu-section-lyrics');
      const tabsEl   = card.querySelector('.mu-section-tabs');
      if (chordsEl) s.chords = chordsEl.value;
      if (lyricsEl) s.lyrics = lyricsEl.value;
      if (tabsEl)   s.tabs   = tabsEl.value;
    });
    // Build backward-compat flat fields for BTM display
    const flatFields = _sectionsToFlat(_sections);

    const previous = _editingId ? tracks.find(t => t.id === _editingId) : null;
    const published = previous?.published !== false;

    const track = {
      id:          _editingId || ('t' + Date.now()),
      title,
      year:        parseInt(g('mu-f-year')) || new Date().getFullYear(),
      genre:       g('mu-f-genre') || 'Acoustic',
      story:       g('mu-f-story'),
      location:    g('mu-f-location'),
      key:         g('mu-f-key'),
      bpm:         parseInt(g('mu-f-bpm')) || null,
      sections:    _sections.map(s => ({ ...s })),
      chords:      flatFields.chords,
      lyrics:      flatFields.lyrics,
      tabs:        flatFields.tabs,
      published,
      src,
    };

    if (_editingId) {
      const i = tracks.findIndex(t => t.id === _editingId);
      if (i >= 0) tracks[i] = track;
    } else {
      tracks.push(track);
    }

    const wasEdit = !!_editingId;
    await _saveCatalog();
    closeUploadModal();
    _renderSection(activeSection);
    window.VW?.Admin?._loadMusicSection?.();
    window.toast?.(wasEdit ? 'Track updated ✓' : 'Track added ✓', 'success');
  }

  async function deleteTrack(id) {
    if (!confirm('Delete this track? This cannot be undone.')) return;
    tracks = tracks.filter(t => t.id !== id);
    if (currentTrack?.id === id) { audioEl?.pause(); currentTrack = null; }
    await _saveCatalog();
    _renderSection(activeSection);
    window.VW?.Admin?._loadMusicSection?.();
    window.toast?.('Track deleted', 'success');
  }

  function onAudioFileChosen(files) {
    const lbl = document.getElementById('mu-f-audio-lbl');
    if (lbl && files.length) lbl.textContent = files[0].name;
  }


  // ── Mini-player drag ───────────────────────────────────────────────────────
  // The player is fixed to the bottom. Dragging the handle repositions it
  // horizontally (left offset) while keeping it pinned to the bottom.
  function _initMiniDrag() {
    const player = document.getElementById('mu-mini-player');
    const handle = document.getElementById('mu-mini-drag');
    if (!player || !handle) return;

    let dragging = false, startX = 0, startLeft = 0;

    handle.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      dragging  = true;
      startX    = e.clientX;
      // Get current left — player uses right: by default, convert to left
      const rect = player.getBoundingClientRect();
      // Switch from right-anchored to left-anchored mid-drag
      player.style.right  = 'auto';
      player.style.left   = rect.left + 'px';
      startLeft = rect.left;
      player.style.transition = 'none';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });

    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const delta   = e.clientX - startX;
      const newLeft = Math.max(0, Math.min(startLeft + delta, window.innerWidth - player.offsetWidth));
      player.style.left = newLeft + 'px';
    });

    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = '';
      player.style.transition = '';
      // Snap to nearest edge (left half → left-anchor, right half → right-anchor)
      const rect   = player.getBoundingClientRect();
      const mid    = rect.left + rect.width / 2;
      const vw     = window.innerWidth;
      if (mid > vw / 2) {
        // Right side — convert back to right-anchored
        player.style.left  = 'auto';
        player.style.right = (vw - rect.right) + 'px';
      }
      // Persist position in sessionStorage
      sessionStorage.setItem('mu_mini_left', player.style.left);
      sessionStorage.setItem('mu_mini_right', player.style.right);
    });

    // Touch support
    handle.addEventListener('touchstart', e => {
      const t = e.touches[0];
      dragging = true; startX = t.clientX;
      const rect = player.getBoundingClientRect();
      player.style.right = 'auto'; player.style.left = rect.left + 'px';
      startLeft = rect.left;
      player.style.transition = 'none';
    }, { passive: true });

    document.addEventListener('touchmove', e => {
      if (!dragging) return;
      const t = e.touches[0];
      const delta   = t.clientX - startX;
      const newLeft = Math.max(0, Math.min(startLeft + delta, window.innerWidth - player.offsetWidth));
      player.style.left = newLeft + 'px';
    }, { passive: true });

    document.addEventListener('touchend', () => { dragging = false; player.style.transition = ''; });

    // Restore saved position
    const savedLeft  = sessionStorage.getItem('mu_mini_left');
    const savedRight = sessionStorage.getItem('mu_mini_right');
    if (savedLeft  && savedLeft  !== 'auto') { player.style.left = savedLeft;  player.style.right = 'auto'; }
    if (savedRight && savedRight !== 'auto') { player.style.right = savedRight; player.style.left  = 'auto'; }
  }

  // ── Public API ────────────────────────────────────────────────────────────
  return {
    init,
    showSection,
    playTrack,
    playUrl,
    carouselNav,
    carouselGo,
    toggleTlBtm,
    openBtmModal,
    closeBtmModal,
    togglePlay,
    prevTrack,
    nextTrack,
    seekMini,
    setVol,
    toggleMini,
    closeMini,
    openUploadModal,
    openEditModal,
    openAdminEditor,
    openEditForTrack,
    identifyChord,
    _identifyIntoSection,
    _toggleNote,
    _clearNotes,
    _appendChordToSection,
    _updateSection,
    onPublishToggle,
    addSection,
    removeSection,
    moveSectionUp,
    moveSectionDown,
    _secDragStart,
    _secDragOver,
    _secDragLeave,
    _secDrop,
    _secDragEnd,
    _renderSectionsList,
    _sectionTypeLabel,
    toggleBtm,
    closeUploadModal,
    saveTrackForm,
    deleteTrack,
    togglePublish,
    onAudioFileChosen,
    onArtFileChosen,
    addGalleryPhotos,
    openLightbox,
    toggleSheet,
  };

})();
