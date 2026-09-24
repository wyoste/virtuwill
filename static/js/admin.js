/**
 * admin.js — VirtuWill Admin Portal
 *
 * Centralised dashboard for all admin operations.
 * Sections: Overview · Garden · Music · Portfolio · Travel · Notes · Chat
 *
 * Calls existing module APIs (gdn, VW.Music, VW.Resume, VW.Travel, VW.Contact)
 * and the backend API endpoints directly. Never duplicates module logic.
 */
'use strict';
window.VW = window.VW || {};

window.VW.Admin = (() => {

  let _activeSection = 'overview';
  let _inited        = false;
  let _musicTrackCache = {};

  const SECTIONS = ['overview','garden','music','portfolio','travel','notes','blog','chat','accounts','finance','health'];

  // ── Init ────────────────────────────────────────────────────────────────────
  function init() {
    if (!window.VW?.Auth?.isAdmin?.()) return;
    _renderOverview();
    _renderGardenSection();
    _loadMusicSection();
    _loadPortfolioSection();
    _renderTravelSection();
    _loadNotes();
    _loadChatHistory();
    VW.Blog?.initAdmin?.();
    _inited = true;
  }

  function onAuthChange() {
    if (window.VW?.Auth?.isAdmin?.()) init();
  }

  // ── Nav ──────────────────────────────────────────────────────────────────────
  function showSection(id) {
    if (!window.VW?.Auth?.isAdmin?.() || !SECTIONS.includes(id)) return;
    _activeSection = id;
    SECTIONS.forEach(s => {
      document.getElementById('adm-sec-' + s)?.classList.toggle('hidden', s !== id);
      document.getElementById('adm-nav-' + s)?.classList.toggle('on', s === id);
    });
    if (id === 'finance' || id === 'health') VW.Trackers?.open(id);
    if (id === 'health') VW.HealthDashboard?.load();
    // Lazy-load on first visit
    if (id === 'notes')    _loadNotes();
    if (id === 'blog')     VW.Blog?.initAdmin?.();
    if (id === 'music')    _loadMusicSection();
    if (id === 'portfolio')_loadPortfolioSection();
    if (id === 'chat')     _loadChatHistory();
    if (id === 'overview') _renderOverview();
    if (id === 'travel')   _renderTravelSection();
    if (id === 'accounts') _loadTemplate();
    if (id === 'garden')   _renderGardenSection();
  }

  function _initPlannerEmbed() {
    const wrap = document.getElementById('adm-planner-embed');
    if (!wrap || wrap.dataset.loaded) return;
    wrap.dataset.loaded = '1';
    // Clone the planner page content into the embed
    const planner = document.getElementById('page-planner');
    if (!planner) { wrap.innerHTML = '<p style="color:var(--text3)">Planner not found.</p>'; return; }
    wrap.innerHTML = '';
    wrap.appendChild(planner.cloneNode(true));
    // Boot the garden planner module
    if (window.gdn?.init) window.gdn.init();
    else if (window.GDN?.Planner?.init) window.GDN.Planner.init();
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Overview
  // ════════════════════════════════════════════════════════════
  function _renderOverview() {
    const el = document.getElementById('adm-overview-body');
    if (!el) return;

    const beds   = gdn?.getBedList?.() || [];
    const plants = beds.reduce((t, b) => t + (b.plants?.length || 0), 0);

    el.innerHTML = `
      <div class="adm-stat-grid">
        <div class="adm-stat" onclick="VW.Admin.showSection('garden')" style="cursor:pointer">
          <div class="adm-stat-icon">🌿</div>
          <div class="adm-stat-num">${beds.length}</div>
          <div class="adm-stat-lbl">Garden beds</div>
          <div class="adm-stat-sub">${plants} plants placed</div>
        </div>
        <div class="adm-stat" onclick="VW.Admin.showSection('music')" style="cursor:pointer">
          <div class="adm-stat-icon">🎵</div>
          <div class="adm-stat-num" id="adm-music-count">—</div>
          <div class="adm-stat-lbl">Songs in catalog</div>
          <div class="adm-stat-sub">Click to manage</div>
        </div>
        <div class="adm-stat" onclick="VW.Admin.showSection('portfolio')" style="cursor:pointer">
          <div class="adm-stat-icon">📁</div>
          <div class="adm-stat-num" id="adm-portfolio-count">—</div>
          <div class="adm-stat-lbl">Portfolio projects</div>
          <div class="adm-stat-sub">Uploaded HTML files</div>
        </div>
        <div class="adm-stat" onclick="VW.Admin.showSection('notes')" style="cursor:pointer">
          <div class="adm-stat-icon">✉️</div>
          <div class="adm-stat-num" id="adm-notes-count">—</div>
          <div class="adm-stat-lbl">Notes received</div>
          <div class="adm-stat-sub" id="adm-notes-unread"></div>
        </div>
        <div class="adm-stat" onclick="VW.Admin.showSection('travel')" style="cursor:pointer">
          <div class="adm-stat-icon">✈️</div>
          <div class="adm-stat-num" id="adm-travel-count">—</div>
          <div class="adm-stat-lbl">Countries visited</div>
          <div class="adm-stat-sub" id="adm-states-count"></div>
        </div>
        <div class="adm-stat" onclick="VW.Admin.showSection('blog')" style="cursor:pointer">
          <div class="adm-stat-icon">&#128240;</div>
          <div class="adm-stat-num" id="adm-blog-count">&#8212;</div>
          <div class="adm-stat-lbl">Blog posts</div>
          <div class="adm-stat-sub" id="adm-blog-pub"></div>
        </div>
        <div class="adm-stat" onclick="go('planner')" style="cursor:pointer">
          <div class="adm-stat-icon">⚙️</div>
          <div class="adm-stat-num">→</div>
          <div class="adm-stat-lbl">Garden Planner</div>
          <div class="adm-stat-sub">Open canvas editor</div>
        </div>
      </div>

      <div class="adm-quicklinks">
        <div class="adm-ql-title">Quick actions</div>
        <div class="adm-ql-grid">
          <button class="adm-ql-btn" onclick="VW.Admin.showSection('finance')"><span>💰</span> Finances &amp; savings goals</button>
          <button class="adm-ql-btn" onclick="VW.Admin.showSection('health')"><span>♥</span> Health &amp; workout goals</button>
          <button class="adm-ql-btn" onclick="VW.Music.openUploadModal();go('music')">
            <span>🎵</span> Add song
          </button>
          <button class="adm-ql-btn" onclick="VW.Resume.openUpload();go('portfolio')">
            <span>📁</span> Upload project
          </button>
          <button class="adm-ql-btn" onclick="VW.Admin.showSection('notes')">
            <span>✉️</span> Check inbox
          </button>
          <button class="adm-ql-btn" onclick="GDN.Gallery.openUpload();go('garden')">
            <span>📷</span> Garden photos
          </button>
          <button class="adm-ql-btn" onclick="VW.Blog.openCompose();VW.Admin.showSection('blog')">
            <span>&#128240;</span> Write post
          </button>
          <button class="adm-ql-btn" onclick="VW.Admin.showSection('travel')">
            <span>🌍</span> Update travel
          </button>
          <button class="adm-ql-btn" onclick="go('planner')">
            <span>⚙️</span> Garden planner
          </button>
        </div>
      </div>`;

    // Fetch live counts
    _fetchNoteCount();
    _fetchMusicCount();
    _fetchPortfolioCount();
    _fetchTravelStats();
    _fetchBlogCount();
  }

  async function _fetchNoteCount() {
    try {
      const r = await fetch('/api/contact/messages');
      const msgs = await r.json();
      const unread = msgs.filter(m => !m.read).length;
      const numEl = document.getElementById('adm-notes-count');
      const subEl = document.getElementById('adm-notes-unread');
      if (numEl) numEl.textContent = msgs.length;
      if (subEl) subEl.textContent = unread ? `${unread} unread` : 'All read';
    } catch {}
  }
  async function _fetchMusicCount() {
    try {
      const r = await fetch('/api/music/catalog');
      const d = await r.json();
      const el = document.getElementById('adm-music-count');
      if (el) el.textContent = (d.tracks||d).length || 0;
    } catch {}
  }
  async function _fetchPortfolioCount() {
    try {
      const r = await fetch('/api/portfolio/uploads');
      const d = await r.json();
      const el = document.getElementById('adm-portfolio-count');
      if (el) el.textContent = d.length || 0;
    } catch {}
  }
  async function _fetchBlogCount() {
    try {
      const r = await fetch('/api/blog');
      const posts = await r.json();
      const pub = posts.filter(p=>p.published).length;
      const numEl = document.getElementById('adm-blog-count');
      const subEl = document.getElementById('adm-blog-pub');
      if (numEl) numEl.textContent = posts.length;
      if (subEl) subEl.textContent = pub + ' published';
    } catch {}
  }

  async function _fetchTravelStats() {
    await VW.Store?.pull('vw_travel_visited');
    try {
      const vis = JSON.parse(localStorage.getItem('vw_travel_visited') || '{"countries":[],"states":[]}');
      const ccEl = document.getElementById('adm-travel-count');
      const scEl = document.getElementById('adm-states-count');
      if (ccEl) ccEl.textContent = vis.countries.length;
      if (scEl) scEl.textContent = `${vis.states.length} US states`;
    } catch {}
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Garden
  // ════════════════════════════════════════════════════════════
  function _renderGardenSection() {
    const el = document.getElementById('adm-garden-body');
    if (!el) return;

    const HEALTH = gdn?.HEALTH || [{icon:'💀'},{icon:'🥀'},{icon:'🌱'},{icon:'🌸'},{icon:'🌺'}];
    const beds = gdn?.getBedList?.() || [];

    el.innerHTML = `
      <div class="adm-section-actions">
        <button class="adm-btn-primary" onclick="go('planner')">⚙️ Open Garden Planner</button>
        <button class="adm-btn-secondary" onclick="GDN.Gallery.openUpload();go('garden')">📷 Upload Garden Photos</button>
        <button class="adm-btn-secondary" onclick="gdn?.save?.();window.toast?.('Garden saved','success')">💾 Save Garden</button>
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">Beds overview <span class="adm-badge">${beds.length}</span></div>
          <div class="adm-subsec-hint">Double-click a bed name to open it in the planner</div>
        </div>
        ${beds.length ? `<div class="adm-bed-grid">
          ${beds.map(bed => {
            const pc = bed.plants?.length || 0;
            return `<div class="adm-bed-card" style="border-top:3px solid ${bed.color}" ondblclick="go('planner')">
              <div class="adm-bed-name">${_esc(bed.name)}</div>
              <div class="adm-bed-shape">${bed.shape?.type || 'polygon'}</div>
              <div class="adm-bed-plants">${pc} plant${pc!==1?'s':''}</div>
              <div class="adm-bed-plant-list">
                ${(bed.plants||[]).slice(0,4).map(p => {
                  const sp = gdn?.SPECIES?.find(s=>s.id===p.speciesId);
                  const h  = HEALTH[p.health??2];
                  return `<span class="adm-bed-plant-pill" title="${_esc(p.displayName)}">${sp?.emoji||'🌿'} ${_esc(p.displayName)}</span>`;
                }).join('')}
                ${pc>4?`<span class="adm-bed-plant-more">+${pc-4} more</span>`:''}
              </div>
            </div>`;
          }).join('')}
        </div>` : `<div class="adm-empty">No beds yet. <button class="adm-link" onclick="go('planner')">Open Planner →</button></div>`}
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">Garden gallery</div>
        </div>
        <div class="adm-subsec-hint" style="margin-bottom:12px">Photos live in the public garden gallery, tagged by bed and plant species.</div>
        <button class="adm-btn-secondary" onclick="go('garden')">View gallery →</button>
      </div>`;
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Music
  // ════════════════════════════════════════════════════════════
  async function _loadMusicSection() {
    const el = document.getElementById('adm-music-body');
    if (!el) return;

    el.innerHTML = `<div class="adm-loading">Loading catalog…</div>`;

    try {
      // Fetch both catalog JSON metadata and filesystem-scanned library
      const [catRes, libRes] = await Promise.all([
        fetch('/api/music/catalog'),
        fetch('/api/music/library'),
      ]);
      const catData = await catRes.json();
      const libData = await libRes.json();

      const catalogTracks = catData.tracks || catData || [];
      const libAlbums  = libData.albums  || [];
      const libSingles = libData.singles || [];

      // Merge: build unified track list
      // Albums from filesystem — each folder is an album
      // Catalog JSON enriches with story/chords/lyrics/etc.
      const tracks = [];

      libAlbums.forEach(album => {
        album.tracks.forEach(t => {
          const meta = catalogTracks.find(m =>
            m.src === t.url ||
            (m.title||'').toLowerCase() === (t.title||'').toLowerCase()
          );
          tracks.push({ ...t, ...(meta||{}), _album: album.name, _art: album.art,
                     id: meta?.id || t.url,
                     _hasCatalogEntry: !!meta });
        });
      });
      libSingles.forEach(s => {
        const meta = catalogTracks.find(m =>
          m.src === s.url ||
          (m.title||'').toLowerCase() === (s.name||'').toLowerCase()
        );
        tracks.push({ ...s, ...(meta||{}), title: meta?.title || s.name, _art: s.art,
                   id: meta?.id || s.url,
                   _hasCatalogEntry: !!meta });
      });
      // Also add catalog-only tracks that weren't found in filesystem
      catalogTracks.forEach(ct => {
        if (!tracks.find(t => t.id === ct.id || t.src === ct.src)) {
          tracks.push(ct);
        }
      });

      _musicTrackCache = {};

      el.innerHTML = `
        <div class="adm-section-actions">
          <button class="adm-btn-primary" onclick="VW.Music.openUploadModal()">🎵 Add new song</button>
          <button class="adm-btn-secondary" onclick="go('music')">View music page →</button>
        </div>

        <div class="adm-subsection">
          <div class="adm-subsec-hd">
            <div class="adm-subsec-title">Song catalog <span class="adm-badge">${tracks.length}</span></div>
          </div>
          ${tracks.length
            ? tracks.map((t, i) => _musicTrackCard(t, i)).join('')
            : `<div class="adm-empty">No songs yet. Click "Add new song" to get started.</div>`}
        </div>`;
    } catch {
      el.innerHTML = `<div class="adm-error">Failed to load music catalog.</div>`;
    }
  }

  function _musicTrackPayload(trackKey) {
    const t = _musicTrackCache[trackKey] || {};
    return {
      id: t.id || '',
      title: t.title || t.name || '',
      src: t.url || t.src || '',
      album: t._album || '',
    };
  }

  function editMusicTrack(trackKey) {
    const t = _musicTrackPayload(trackKey);
    window.VW?.Music?.openAdminEditor?.(t.id, t.title, t.src, t.album);
  }

  function publishMusicTrack(trackKey, published) {
    const t = _musicTrackPayload(trackKey);
    setMusicPublished(t.id, published, t.title, t.src, t.album);
  }

  function deleteMusicTrack(trackKey) {
    const t = _musicTrackPayload(trackKey);
    if (t.id) window.VW?.Music?.deleteTrack?.(t.id);
  }

  async function setMusicPublished(trackId, published, title = '', src = '', album = '') {
    try {
      const r = await fetch('/api/music/catalog');
      const data = await r.json();
      const catalogTracks = data.tracks || data || [];
      const id = trackId || src || title;
      const idx = catalogTracks.findIndex(t =>
        t.id === id ||
        (src && t.src === src) ||
        ((t.title || '').toLowerCase() === (title || '').toLowerCase())
      );

      if (idx >= 0) {
        catalogTracks[idx] = { ...catalogTracks[idx], published };
      } else {
        catalogTracks.push({
          id: id || ('t' + Date.now()),
          title: title || 'Untitled',
          year: new Date().getFullYear(),
          genre: 'Acoustic',
          story: '',
          location: '',
          key: '',
          bpm: null,
          sections: [],
          chords: '',
          lyrics: '',
          tabs: '',
          published,
          src: src || null,
          album: album || '',
        });
      }

      const save = await fetch('/api/music/catalog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tracks: catalogTracks }),
      });
      if (!save.ok) throw new Error('Save failed');

      _loadMusicSection();
      _fetchMusicCount();
      window.toast?.(published ? 'Song set live' : 'Song moved to draft', 'success', 1800);
    } catch {
      window.toast?.('Could not update publish status', 'error');
      _loadMusicSection();
    }
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Portfolio
  // ════════════════════════════════════════════════════════════
  async function _loadPortfolioSection() {
    const el = document.getElementById('adm-portfolio-body');
    if (!el) return;

    el.innerHTML = `<div class="adm-loading">Loading uploads…</div>`;

    try {
      const r = await fetch('/api/portfolio/uploads');
      const uploads = await r.json();

      el.innerHTML = `
        <div class="adm-section-actions">
          <button class="adm-btn-primary" onclick="VW.Resume.openUpload()">📁 Upload project HTML</button>
          <button class="adm-btn-secondary" onclick="go('portfolio')">View portfolio →</button>
        </div>

        <div class="adm-subsection">
          <div class="adm-subsec-hd">
            <div class="adm-subsec-title">Uploaded projects <span class="adm-badge">${uploads.length}</span></div>
            <div class="adm-subsec-hint">Built-in projects are managed via the Resume page</div>
          </div>
          ${uploads.length ? `<table class="adm-table">
            <thead><tr>
              <th>Title</th><th>Tag</th><th>Uploaded</th><th>File</th><th></th>
            </tr></thead>
            <tbody>
              ${uploads.map(u => `<tr>
                <td><strong>${_esc(u.title)}</strong>${u.desc?`<div class="adm-table-sub">${_esc(u.desc.slice(0,60))}</div>`:''}</td>
                <td><span class="adm-pill">${_esc(u.tag||'Project')}</span></td>
                <td>${u.uploaded||'—'}</td>
                <td><code>${_esc(u.filename)}</code></td>
                <td class="adm-table-actions">
                  <a class="adm-icon-btn" href="${_esc(u.url)}" target="_blank" title="Preview">🔗</a>
                  <button class="adm-icon-btn danger" onclick="VW.Admin.deletePortfolioItem('${_esc(u.filename)}')" title="Delete">🗑</button>
                </td>
              </tr>`).join('')}
            </tbody>
          </table>` : `<div class="adm-empty">No uploaded projects yet.</div>`}
        </div>`;
    } catch {
      el.innerHTML = `<div class="adm-error">Failed to load portfolio uploads.</div>`;
    }
  }

  async function deletePortfolioItem(filename) {
    if (!confirm(`Delete "${filename}"?`)) return;
    await fetch(`/api/portfolio/upload/${filename}`, { method:'DELETE' });
    _loadPortfolioSection();
    window.toast?.('Project deleted', 'success');
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Travel
  // ════════════════════════════════════════════════════════════
  async function _renderTravelSection() {
    const el = document.getElementById('adm-travel-body');
    if (!el) return;
    await Promise.all([VW.Store?.pull('vw_travel_visited'), VW.Store?.pull('vw_travel_pins')]);

    let vis = { countries:[], states:[] };
    let pins = [];
    try { vis  = JSON.parse(localStorage.getItem('vw_travel_visited') || '{"countries":[],"states":[]}'); } catch {}
    try { pins = JSON.parse(localStorage.getItem('vw_travel_pins')    || '[]'); } catch {}

    const FLAG_MAP = {};  // populated from GeoJSON if available

    const countryNames = vis.countries.map(code => {
      const flag = String.fromCodePoint(...[...code.toUpperCase()].map(c=>c.charCodeAt(0)+0x1F1A5));
      return `<span class="adm-travel-tag">${flag} ${code}</span>`;
    }).join('');

    const stateNames = vis.states.map(abbr =>
      `<span class="adm-travel-tag">🇺🇸 ${abbr}</span>`
    ).join('');

    const pinTypes = { visited:'📍', recommend:'⭐', wishlist:'🌟' };

    el.innerHTML = `
      <div class="adm-section-actions">
        <button class="adm-btn-primary" onclick="go('travel')">✈️ Open Travel Map</button>
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">Add visited place</div>
        </div>
        <div class="adm-travel-form">
          <select class="adm-select" id="adm-travel-type">
            <option value="country">🌍 Country</option>
            <option value="state">🇺🇸 US State</option>
          </select>
          <input class="adm-input" id="adm-travel-input" placeholder="Country or state name…" 
            onkeydown="if(event.key==='Enter')VW.Admin.addTravelPlace()"/>
          <button class="adm-btn-primary" onclick="VW.Admin.addTravelPlace()">+ Add</button>
        </div>
        <div class="adm-subsec-hint">Countries and states can also be toggled by clicking them on the map.</div>
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">Countries visited <span class="adm-badge">${vis.countries.length}</span></div>
        </div>
        <div class="adm-travel-tags" id="adm-country-tags">
          ${countryNames || '<span class="adm-empty-inline">None yet</span>'}
        </div>
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">US States visited <span class="adm-badge">${vis.states.length}</span></div>
        </div>
        <div class="adm-travel-tags" id="adm-state-tags">
          ${stateNames || '<span class="adm-empty-inline">None yet</span>'}
        </div>
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">Map pins <span class="adm-badge">${pins.length}</span></div>
        </div>
        ${pins.length ? `<table class="adm-table">
          <thead><tr><th>Place</th><th>Location</th><th>Type</th><th></th></tr></thead>
          <tbody>
            ${pins.map(p => `<tr>
              <td><strong>${_esc(p.name)}</strong></td>
              <td>${_esc(p.city)}</td>
              <td>${pinTypes[p.type]||'📍'} ${p.type}</td>
              <td class="adm-table-actions">
                <button class="adm-icon-btn danger" onclick="VW.Admin.deletePin(${p.id})" title="Remove">🗑</button>
              </td>
            </tr>`).join('')}
          </tbody>
        </table>` : `<div class="adm-empty">No pins yet. Open the travel map and add pins.</div>`}
      </div>

      <div class="adm-subsection">
        <div class="adm-subsec-hd">
          <div class="adm-subsec-title">Add map pin</div>
        </div>
        <div class="adm-travel-form" style="flex-wrap:wrap;gap:8px">
          <input class="adm-input" id="adm-pin-name" placeholder="Place name"/>
          <input class="adm-input" id="adm-pin-city" placeholder="City, Country"/>
          <select class="adm-select" id="adm-pin-type">
            <option value="visited">📍 Visited</option>
            <option value="recommend">⭐ Recommend</option>
            <option value="wishlist">🌟 Wish list</option>
          </select>
          <button class="adm-btn-primary" onclick="VW.Admin.addMapPin()">+ Add pin</button>
        </div>
        <div class="adm-subsec-hint">Geocoding via OpenStreetMap — pins also appear on the map.</div>
      </div>`;
  }

  function addTravelPlace() {
    const type  = document.getElementById('adm-travel-type')?.value || 'country';
    const input = document.getElementById('adm-travel-input');
    const val   = input?.value.trim();
    if (!val) return;
    // Delegate to the travel module
    const typeEl = document.getElementById('tv-visited-type');
    const inputEl = document.getElementById('tv-visited-input');
    if (typeEl)  typeEl.value  = type;
    if (inputEl) inputEl.value = val;
    VW.Travel?.addVisitedPlace?.();
    if (input) input.value = '';
    // Re-render after brief delay for async geocoding
    setTimeout(() => _renderTravelSection(), 1500);
  }

  function addMapPin() {
    const name = document.getElementById('adm-pin-name')?.value.trim();
    const city = document.getElementById('adm-pin-city')?.value.trim();
    const type = document.getElementById('adm-pin-type')?.value || 'visited';
    if (!name || !city) { window.toast?.('Enter both place name and city', 'error'); return; }
    // Delegate to travel module by filling its hidden fields
    const pnEl = document.getElementById('tv-pin-name');
    const pcEl = document.getElementById('tv-pin-city');
    const ptEl = document.getElementById('tv-pin-type');
    if (pnEl) pnEl.value = name;
    if (pcEl) pcEl.value = city;
    if (ptEl) ptEl.value = type;
    VW.Travel?.addPin?.();
    document.getElementById('adm-pin-name').value = '';
    document.getElementById('adm-pin-city').value = '';
    setTimeout(() => _renderTravelSection(), 2000);
  }

  function deletePin(id) {
    VW.Travel?.removePin?.(id);
    setTimeout(() => _renderTravelSection(), 300);
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Notes inbox
  // ════════════════════════════════════════════════════════════
  async function _loadNotes() {
    const el = document.getElementById('adm-notes-body');
    if (!el) return;

    el.innerHTML = `<div class="adm-loading">Loading messages…</div>`;

    try {
      const r = await fetch('/api/contact/messages');
      if (!r.ok) { el.innerHTML = `<div class="adm-error">Unauthorized</div>`; return; }
      const msgs = await r.json();
      const unread = msgs.filter(m => !m.read).length;

      el.innerHTML = `
        <div class="adm-section-actions">
          <div class="adm-notes-summary">
            <span class="adm-badge">${msgs.length}</span> total
            ${unread ? `<span class="adm-badge-red">${unread} unread</span>` : ''}
          </div>
          <button class="adm-btn-secondary" onclick="VW.Admin._loadNotes()">↻ Refresh</button>
        </div>
        ${msgs.length ? msgs.map(m => `
          <div class="adm-msg${m.read?'':' adm-msg-unread'}" id="adm-msg-${m.id}">
            <div class="adm-msg-hd">
              <span class="adm-msg-name">${_esc(m.name)}</span>
              ${m.contact ? `<span class="adm-msg-contact">${_esc(m.contact)}</span>` : ''}
              <span class="adm-msg-date">${m.date||''}</span>
              ${!m.read
                ? `<button class="adm-msg-read-btn" onclick="VW.Admin.markNoteRead('${m.id}')">Mark read</button>`
                : `<span class="adm-msg-read-badge">✓ read</span>`}
            </div>
            <div class="adm-msg-body">${_esc(m.message)}</div>
          </div>`).join('')
          : `<div class="adm-empty">No messages yet.</div>`}`;
    } catch {
      el.innerHTML = `<div class="adm-error">Failed to load messages.</div>`;
    }
  }

  async function markNoteRead(id) {
    await fetch(`/api/contact/read/${id}`, { method: 'POST' });
    const el = document.getElementById(`adm-msg-${id}`);
    if (el) {
      el.classList.remove('adm-msg-unread');
      const btn = el.querySelector('.adm-msg-read-btn');
      if (btn) { const badge = document.createElement('span'); badge.className='adm-msg-read-badge'; badge.textContent='✓ read'; btn.replaceWith(badge); }
    }
    // Also update the overview badge
    _fetchNoteCount();
  }

  // ════════════════════════════════════════════════════════════
  //  SECTION: Chat history
  // ════════════════════════════════════════════════════════════
  async function _loadChatHistory() {
    const el = document.getElementById('adm-chat-body');
    if (!el) return;

    el.innerHTML = `<div class="adm-loading">Loading chat history…</div>`;

    try {
      // Try the will_chat local FastAPI server (if running)
      const r = await fetch('http://localhost:8765/threads', {
        signal: AbortSignal.timeout(2000)
      });
      const d = await r.json();
      const threads = d.threads || [];

      if (!threads.length) {
        el.innerHTML = `<div class="adm-empty">No chat threads yet.</div>`;
        return;
      }

      el.innerHTML = `
        <div class="adm-section-actions">
          <div class="adm-subsec-hint">Chat history from the local LLaVA chat server</div>
        </div>
        <div class="adm-chat-list">
          ${threads.map(t => `
            <div class="adm-chat-thread" onclick="VW.Admin.loadThread('${t.id}')">
              <div class="adm-chat-thread-hd">
                <span class="adm-chat-thread-id">Thread ${t.id.slice(0,8)}…</span>
                <span class="adm-chat-thread-date">${t.created||''}</span>
                <span class="adm-chat-thread-count">${t.message_count||0} msgs</span>
              </div>
              ${t.preview ? `<div class="adm-chat-thread-preview">${_esc(t.preview)}</div>` : ''}
            </div>`).join('')}
        </div>
        <div id="adm-chat-thread-detail"></div>`;
    } catch {
      el.innerHTML = `
        <div class="adm-chat-offline">
          <div class="adm-chat-offline-icon">💬</div>
          <div class="adm-chat-offline-title">Chat server offline</div>
          <div class="adm-chat-offline-sub">The local LLaVA chat server isn't running. Start it with <code>npm run server</code> in the will_chat directory, then refresh.</div>
          <button class="adm-btn-secondary" onclick="VW.Admin._loadChatHistory()" style="margin-top:14px">↻ Retry</button>
        </div>`;
    }
  }

  async function loadThread(threadId) {
    const el = document.getElementById('adm-chat-thread-detail');
    if (!el) return;
    try {
      const r = await fetch(`http://localhost:8765/thread/${threadId}`, {
        signal: AbortSignal.timeout(2000)
      });
      const d = await r.json();
      const msgs = d.messages || [];
      el.innerHTML = `
        <div class="adm-chat-detail">
          <div class="adm-chat-detail-hd">Thread ${threadId.slice(0,8)}</div>
          ${msgs.map(m => `
            <div class="adm-chat-msg adm-chat-msg-${m.role}">
              <div class="adm-chat-msg-role">${m.role==='user'?'You':'Will (AI)'}</div>
              <div class="adm-chat-msg-text">${_esc(m.content)}</div>
            </div>`).join('')}
        </div>`;
    } catch {
      el.innerHTML = `<div class="adm-error">Could not load thread.</div>`;
    }
  }


  // ════════════════════════════════════════════════════════════
  //  Music track card with collapsible "Behind the Music"
  // ════════════════════════════════════════════════════════════

  function _musicTrackCard(t, idx) {
    const id      = t.id || '';
    const title   = _esc(t.title || t.name || 'Untitled');
    const year    = t.year  || '';
    const genre   = t.genre || '';
    const key     = t.key   || '';
    const bpm     = t.bpm   || '';
    const story   = t.story    || '';
    const location= t.location || '';
    const chords  = t.chords   || '';
    const tabs    = t.tabs     || '';
    const lyrics  = t.lyrics   || '';
    const published = t.published !== false;

    // "Behind the Music" only shown if at least one field has content
    const hasBehind = story || location || chords || tabs || lyrics;

    // Pill row for key metadata
    const pills = [
      year  ? `<span class="adm-mu-pill">${_esc(String(year))}</span>` : '',
      genre ? `<span class="adm-mu-pill">${_esc(genre)}</span>` : '',
      key   ? `<span class="adm-mu-pill adm-mu-pill-accent">Key: ${_esc(key)}</span>` : '',
      bpm   ? `<span class="adm-mu-pill">♩ ${_esc(String(bpm))} bpm</span>` : '',
    ].filter(Boolean).join('');

    const trackKey = 'track-' + idx;
    _musicTrackCache[trackKey] = t;
    const behindId = 'btm-' + trackKey;

    return `
      <div class="adm-mu-card">

        <!-- ── Card header ──────────────────────────── -->
        <div class="adm-mu-hd">
          ${t._art ? `<img src="${_esc(t._art)}" style="width:48px;height:48px;object-fit:cover;border-radius:6px;flex-shrink:0"/>` : '<span class="adm-mu-icon">🎵</span>'}
          <div class="adm-mu-hd-left">
            <div>
              <div class="adm-mu-title">${title}</div>
              ${t._album ? `<div style="font-size:11px;color:var(--blue);margin-bottom:4px">📀 ${_esc(t._album)}</div>` : ''}
              ${pills ? `<div class="adm-mu-pills">${pills}</div>` : ''}
            </div>
          </div>
          <div class="adm-mu-actions">
            <label class="adm-toggle-label adm-mu-publish-toggle"
              title="${published ? 'Live — visible to site visitors' : 'Draft — hidden from site visitors'}">
              <input type="checkbox" class="adm-toggle-cb" ${published ? 'checked' : ''}
                onchange="VW.Admin.publishMusicTrack('${trackKey}', this.checked)"/>
              <span class="adm-toggle-track"></span>
              <span class="adm-toggle-text">${published ? 'Live' : 'Draft'}</span>
            </label>
            ${hasBehind ? `
            <button class="adm-mu-btm-toggle" onclick="VW.Admin.toggleBtm('${behindId}')"
              id="toggle-${behindId}" aria-expanded="false">
              <span>Behind the music</span>
              <span class="adm-mu-chev">›</span>
            </button>` : ''}
            <button class="adm-icon-btn" title="Edit song"
              onclick="VW.Admin.editMusicTrack('${trackKey}')">✏️</button>
            ${t._hasCatalogEntry ? `<button class="adm-icon-btn danger" onclick="VW.Admin.deleteMusicTrack('${trackKey}')" title="Delete song">🗑</button>` : ''}
          </div>
        </div>

        <!-- ── Behind the Music (collapsible) ─────── -->
        ${hasBehind ? `
        <div class="adm-mu-btm" id="${behindId}" style="display:none">
          <div class="adm-mu-btm-inner">

            ${story ? `
            <div class="adm-mu-btm-block">
              <div class="adm-mu-btm-lbl">Background</div>
              <p class="adm-mu-btm-text">${_esc(story)}</p>
            </div>` : ''}

            ${location ? `
            <div class="adm-mu-btm-block">
              <div class="adm-mu-btm-lbl">Written</div>
              <p class="adm-mu-btm-text">📍 ${_esc(location)}</p>
            </div>` : ''}

            ${chords ? `
            <div class="adm-mu-btm-block">
              <div class="adm-mu-btm-lbl">Chords</div>
              <pre class="adm-mu-btm-pre">${_esc(chords)}</pre>
            </div>` : ''}

            ${tabs ? `
            <div class="adm-mu-btm-block">
              <div class="adm-mu-btm-lbl">Guitar tab</div>
              <pre class="adm-mu-btm-pre">${_esc(tabs)}</pre>
            </div>` : ''}

            ${lyrics ? `
            <div class="adm-mu-btm-block">
              <div class="adm-mu-btm-lbl">Lyrics</div>
              <pre class="adm-mu-btm-pre adm-mu-lyrics">${_esc(lyrics)}</pre>
            </div>` : ''}

          </div>
        </div>` : ''}

      </div>`;
  }

  function toggleBtm(id) {
    const panel  = document.getElementById(id);
    const toggle = document.getElementById('toggle-' + id);
    if (!panel) return;
    const open = panel.style.display === 'none';
    panel.style.display  = open ? 'block' : 'none';
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(open));
      toggle.classList.toggle('open', open);
    }
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  const _esc = window.VW?.esc || (s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'));


  // ── Account template management ──────────────────────────────────────────────

  let _tmpl = [];
  let _tmplDragIdx = null;

  async function _loadTemplate() {
    try {
      const r = await fetch('/api/accounts-template');
      _tmpl = await r.json();
    } catch { _tmpl = []; }
    _renderTemplate();
  }

  function _renderTemplate() {
    const tbody = document.getElementById('adm-tmpl-body');
    if (!tbody) return;
    if (!_tmpl.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="j-fin-empty">No accounts yet — click + Add account</td></tr>';
      return;
    }
    tbody.innerHTML = _tmpl.map((t, i) => `
      <tr class="j-fin-row" id="adm-tmpl-row-${i}"
        draggable="true"
        ondragstart="VW.Admin._tmplDragStart(event,${i})"
        ondragover="VW.Admin._tmplDragOver(event,${i})"
        ondrop="VW.Admin._tmplDrop(event,${i})"
        ondragend="VW.Admin._tmplDragEnd(event)">
        <td class="j-fin-td j-fin-td-drag">
          <span class="j-fin-drag-handle">⠿</span>
        </td>
        <td class="j-fin-td">
          <input class="j-fin-input" value="${_esc(t.institution || '')}"
            placeholder="e.g. Chase"
            oninput="VW.Admin._tmplUpdate(${i},'institution',this.value)"/>
        </td>
        <td class="j-fin-td">
          <input class="j-fin-input" value="${_esc(t.name || '')}"
            placeholder="e.g. Checking"
            oninput="VW.Admin._tmplUpdate(${i},'name',this.value)"/>
        </td>
        <td class="j-fin-td j-fin-td-del">
          <button class="j-fin-del-btn" onclick="VW.Admin._tmplDelete(${i})">✕</button>
        </td>
      </tr>`).join('');
  }

  function _syncTemplateFromDom() {
    document.querySelectorAll('#adm-tmpl-body .j-fin-row').forEach((row, i) => {
      const inputs = row.querySelectorAll('.j-fin-input');
      if (!_tmpl[i]) _tmpl[i] = { institution: '', name: '' };
      _tmpl[i].institution = inputs[0]?.value || '';
      _tmpl[i].name = inputs[1]?.value || '';
    });
  }

  function addTemplateAccount() {
    _syncTemplateFromDom();
    _tmpl.push({ institution: '', name: '' });
    _renderTemplate();
    const inputs = document.querySelectorAll('#adm-tmpl-body .j-fin-input');
    if (inputs.length >= 2) inputs[inputs.length - 2].focus();
    _markUnsaved();
  }

  function _tmplUpdate(idx, field, val) {
    if (_tmpl[idx]) _tmpl[idx][field] = val;
    _markUnsaved();
  }

  function _tmplDelete(idx) {
    _syncTemplateFromDom();
    _tmpl.splice(idx, 1);
    _renderTemplate();
    _markUnsaved();
  }

  let _tmplUnsaved = false;
  function _markUnsaved() {
    _tmplUnsaved = true;
    const btn = document.getElementById('adm-tmpl-save-btn');
    if (btn && !btn.textContent.includes('*')) btn.textContent = 'Save template *';
  }

  async function saveTemplate() {
    _syncTemplateFromDom();
    const btn = document.getElementById('adm-tmpl-save-btn');
    if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }
    try {
      const r = await fetch('/api/accounts-template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_tmpl),
      });
      const d = await r.json();
      if (btn) {
        btn.textContent = d.ok ? 'Saved ✓' : 'Error';
        btn.disabled = false;
        if (d.ok) { _tmplUnsaved = false; setTimeout(() => { btn.textContent = 'Save template'; }, 2000); }
      }
      // Invalidate journal's cached template
      if (window.VW?.Journal) window.VW.Journal._acctTemplate = null;
    } catch {
      if (btn) { btn.textContent = 'Error — retry'; btn.disabled = false; }
    }
  }

  function _tmplDragStart(e, idx) {
    _tmplDragIdx = idx;
    e.dataTransfer.effectAllowed = 'move';
    setTimeout(() => document.getElementById('adm-tmpl-row-' + idx)?.classList.add('j-fin-dragging'), 0);
  }

  function _tmplDragOver(e, idx) {
    e.preventDefault();
    if (_tmplDragIdx === null || _tmplDragIdx === idx) return;
    const el = document.getElementById('adm-tmpl-row-' + idx);
    const rect = el?.getBoundingClientRect();
    const before = rect && e.clientY < rect.top + rect.height / 2;
    document.querySelectorAll('.j-fin-drop-above,.j-fin-drop-below')
      .forEach(n => n.classList.remove('j-fin-drop-above','j-fin-drop-below'));
    el?.classList.add(before ? 'j-fin-drop-above' : 'j-fin-drop-below');
  }

  function _tmplDrop(e, toIdx) {
    e.preventDefault();
    _syncTemplateFromDom();
    document.querySelectorAll('.j-fin-drop-above,.j-fin-drop-below')
      .forEach(n => n.classList.remove('j-fin-drop-above','j-fin-drop-below'));
    if (_tmplDragIdx === null || _tmplDragIdx === toIdx) return;
    const el = document.getElementById('adm-tmpl-row-' + toIdx);
    const rect = el?.getBoundingClientRect();
    const before = rect && e.clientY < rect.top + rect.height / 2;
    const insert = before ? toIdx : toIdx + 1;
    const [moved] = _tmpl.splice(_tmplDragIdx, 1);
    _tmpl.splice(insert > _tmplDragIdx ? insert - 1 : insert, 0, moved);
    _tmplDragIdx = null;
    _renderTemplate();
    _markUnsaved();
  }

  function _tmplDragEnd() {
    _tmplDragIdx = null;
    document.querySelectorAll('.j-fin-dragging,.j-fin-drop-above,.j-fin-drop-below')
      .forEach(n => n.classList.remove('j-fin-dragging','j-fin-drop-above','j-fin-drop-below'));
  }

  window.VW?.Dirty?.register?.('admin', {
    label: 'Account template',
    isDirty: () => {
      const compose = document.getElementById('adm-blog-compose');
      if (compose?.style.display === 'flex') {
        return !!document.getElementById('adm-blog-title-input')?.value?.trim();
      }
      return !!_tmplUnsaved;
    },
    save: async () => {
      const compose = document.getElementById('adm-blog-compose');
      if (compose?.style.display === 'flex') {
        window.VW.Blog?.saveDraft?.();
      } else {
        await saveTemplate();
      }
    },
  });

  return {
    init, onAuthChange, showSection, toggleBtm,
    addTemplateAccount, saveTemplate,
    _tmplDragStart, _tmplDragOver, _tmplDrop, _tmplDragEnd,
    addTravelPlace, addMapPin, deletePin,
    deletePortfolioItem, markNoteRead,
    setMusicPublished, editMusicTrack, publishMusicTrack, deleteMusicTrack,
    loadThread,
    _loadNotes, _loadChatHistory, _loadMusicSection, _loadPortfolioSection,
  };

})();
