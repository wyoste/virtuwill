/**
 * gallery.js — VirtuWill Garden Gallery
 *
 * Public: photo gallery grouped by Bed or Plant.
 * Admin:  upload photos tagged with beds/plants/category from planner data.
 * Storage: server API (/api/garden/photos) not localStorage.
 * Tags: bed names and plant names come live from /api/garden (planner data).
 */
'use strict';
window.GDN = window.GDN || {};

window.GDN.Gallery = (() => {
  const _esc = window.VW?.esc || (s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'));

  // ── State ───────────────────────────────────────────────────────────────────
  let _photos    = [];   // [{id, url, beds[], plants[], caption, category, date}]
  let _groupBy   = 'bed';
  let _lbPhotos  = [];
  let _lbIdx     = 0;
  let _gardenData = { beds: [] };  // live planner data for tag options
  let _selectedBeds   = new Set();
  let _selectedPlants = new Set();
  let _selectedCat    = '';
  let _chosenFiles    = [];

  // ── Note / quote ─────────────────────────────────────────────────────────────
  const NOTE_KEY    = 'vw_garden_note';
  const DEFAULT_NOTE = 'A garden is a living thing — it grows, changes, and surprises you every season.';

  function init() {
    _loadNote();
    Promise.all([VW.Store?.pull(NOTE_KEY), VW.Store?.pull(HERO_KEY)]).then(([note, hero]) => {
      if (note) _loadNote();
      if (hero) _renderPhilosophy();
    });
    _loadGardenData().then(() => _loadPhotos()).then(() => {
      _renderGallery();
      _renderPhilosophy();
    });
    // Show edit hint for admin
    const hint = document.getElementById('gal-quote-hint');
    if (hint) hint.style.display = window.VW?.Auth?.isAdmin?.() ? '' : 'none';
    const uploadBtn = document.getElementById('gal-upload-btn');
    if (uploadBtn) uploadBtn.style.display = window.VW?.Auth?.isAdmin?.() ? '' : 'none';
  }

  function onAuthChange() {
    const hint = document.getElementById('gal-quote-hint');
    if (hint) hint.style.display = window.VW?.Auth?.isAdmin?.() ? '' : 'none';
    const uploadBtn = document.getElementById('gal-upload-btn');
    if (uploadBtn) uploadBtn.style.display = window.VW?.Auth?.isAdmin?.() ? '' : 'none';
    const editBtn = document.getElementById('gdn-hero-edit-btn');
    if (editBtn) editBtn.style.display = window.VW?.Auth?.isAdmin?.() ? '' : 'none';
    _renderPhilosophy();
  }

  // ── Note/quote ──────────────────────────────────────────────────────────────
  function _loadNote() {
    const note = localStorage.getItem(NOTE_KEY) || DEFAULT_NOTE;
    const el   = document.getElementById('gal-quote-text');
    if (el) el.textContent = note;
  }

  function editNote() {
    if (!window.VW?.Auth?.isAdmin?.()) return;
    document.getElementById('gal-quote-display').style.display = 'none';
    document.getElementById('gal-quote-editor').style.display  = 'block';
    const input = document.getElementById('gal-quote-input');
    if (input) input.value = localStorage.getItem(NOTE_KEY) || DEFAULT_NOTE;
  }

  function saveNote() {
    const val = document.getElementById('gal-quote-input')?.value?.trim();
    if (val) {
      localStorage.setItem(NOTE_KEY, val);
      VW.Store?.push(NOTE_KEY);
      const el = document.getElementById('gal-quote-text');
      if (el) el.textContent = val;
    }
    cancelNote();
  }

  function cancelNote() {
    document.getElementById('gal-quote-display').style.display = '';
    document.getElementById('gal-quote-editor').style.display  = 'none';
  }

  // ── Load data ───────────────────────────────────────────────────────────────
  async function _loadGardenData() {
    try {
      const r = await fetch('/api/garden');
      _gardenData = await r.json();
    } catch { _gardenData = { beds: [] }; }
  }

  async function _loadPhotos() {
    try {
      const r = await fetch('/api/garden/photos');
      _photos = await r.json();
    } catch { _photos = []; }
  }

  // ── Derive bed/plant tags from planner data ──────────────────────────────────
  function _getBeds() {
    return (_gardenData.beds || []).map(b => ({
      id:   b.id,
      name: b.name || `Bed ${b.id}`,
    }));
  }

  function _getPlants() {
    const plants = new Map();
    (_gardenData.beds || []).forEach(bed => {
      (bed.plants || []).forEach(p => {
        if (p.name && !plants.has(p.name)) {
          plants.set(p.name, { id: p.name, name: p.name });
        }
      });
    });
    // Add special categories
    ['Panorama', 'Yardscape', 'General'].forEach(cat => {
      if (!plants.has(cat)) plants.set(cat, { id: cat, name: cat });
    });
    return [...plants.values()];
  }

  function _bedName(id) {
    const b = _getBeds().find(b => b.id === id);
    return b?.name || id;
  }

  // ── Gallery rendering ────────────────────────────────────────────────────────
  function groupBy(mode) {
    _groupBy = mode;
    ['bed','plant','all'].forEach(m => {
      document.getElementById('gvt-' + m)?.classList.toggle('on', m === mode);
      document.getElementById('gxt-' + m)?.classList.toggle('on', m === mode);
    });
    _renderGallery();
  }

  function _renderGallery() {
    const body = document.getElementById('gal-body');
    if (!body) return;

    if (!_photos.length) {
      body.innerHTML = `
        <div class="gal-empty">
          <div style="font-size:52px;margin-bottom:14px">🌿</div>
          <div>No photos yet — check back soon.</div>
        </div>`;
      return;
    }

    if (_groupBy === 'all') {
      body.innerHTML = `<div class="gal-grid">${_photos.map(_photoCard).join('')}</div>`;
      return;
    }

    if (_groupBy === 'bed') {
      // Group by bed — each bed gets a section with its photos
      const byBed = new Map();
      _photos.forEach(p => {
        const beds = p.beds?.length ? p.beds : ['uncategorized'];
        beds.forEach(bid => {
          if (!byBed.has(bid)) byBed.set(bid, []);
          byBed.get(bid).push(p);
        });
      });

      // Ordered beds from planner first, then uncategorized
      const ordered = _getBeds().map(b => b.id).filter(id => byBed.has(id));
      if (byBed.has('uncategorized')) ordered.push('uncategorized');

      body.innerHTML = ordered.map(bid => {
        const photos = byBed.get(bid) || [];
        const label  = bid === 'uncategorized' ? 'Uncategorized' : _bedName(bid);
        return `
          <div class="gal-group">
            <div class="gal-group-hd">
              <span class="gal-group-label">🌱 ${_esc(label)}</span>
              <span class="gal-group-count">${photos.length} photo${photos.length!==1?'s':''}</span>
            </div>
            <div class="gal-grid">${photos.map(_photoCard).join('')}</div>
          </div>`;
      }).join('');
    }

    if (_groupBy === 'plant') {
      const byPlant = new Map();
      _photos.forEach(p => {
        const plants = p.plants?.length ? p.plants : (p.category ? [p.category] : ['uncategorized']);
        plants.forEach(pl => {
          if (!byPlant.has(pl)) byPlant.set(pl, []);
          byPlant.get(pl).push(p);
        });
      });

      const ordered = [...byPlant.keys()].sort((a,b) =>
        a === 'uncategorized' ? 1 : b === 'uncategorized' ? -1 : a.localeCompare(b));

      body.innerHTML = ordered.map(pl => {
        const photos = byPlant.get(pl) || [];
        const label  = pl === 'uncategorized' ? 'General' : pl;
        return `
          <div class="gal-group">
            <div class="gal-group-hd">
              <span class="gal-group-label">🌼 ${_esc(label)}</span>
              <span class="gal-group-count">${photos.length} photo${photos.length!==1?'s':''}</span>
            </div>
            <div class="gal-grid">${photos.map(_photoCard).join('')}</div>
          </div>`;
      }).join('');
    }
  }

  function _photoCard(p, i) {
    const admin = window.VW?.Auth?.isAdmin?.();
    const bedLabels   = (p.beds||[]).map(_bedName).join(', ');
    const plantLabels = (p.plants||[]).join(', ');
    const catLabel    = p.category || '';
    const sublabel    = [catLabel, bedLabels, plantLabels].filter(Boolean).join(' · ');
    return `
      <div class="gal-photo-card" onclick="GDN.Gallery.openLightbox('${p.id}')">
        <img class="gal-photo-img" src="${_esc(p.url)}" loading="lazy" alt="${_esc(p.caption||'')}"/>
        ${sublabel ? `<div class="gal-photo-meta">${_esc(sublabel)}</div>` : ''}
        ${p.caption ? `<div class="gal-photo-caption">${_esc(p.caption)}</div>` : ''}
        ${admin ? `<button class="gal-photo-del" onclick="event.stopPropagation();GDN.Gallery.deletePhoto('${p.id}')" title="Delete">✕</button>` : ''}
      </div>`;
  }

  // ── Philosophy / hero blurb ─────────────────────────────────────────────────
  const HERO_KEY = 'vw_garden_hero';

  function _renderPhilosophy() {
    const saved = localStorage.getItem(HERO_KEY);
    if (saved) {
      const el = document.getElementById('gdn-hero-text');
      if (el) el.textContent = saved;
    }
    const editBtn = document.getElementById('gdn-hero-edit-btn');
    if (editBtn) editBtn.style.display = window.VW?.Auth?.isAdmin?.() ? '' : 'none';
  }

  function editHero() {
    const current = document.getElementById('gdn-hero-text')?.textContent || '';
    document.getElementById('gdn-hero-blurb').style.display  = 'none';
    document.getElementById('gdn-hero-editor').style.display = 'block';
    const inp = document.getElementById('gdn-hero-input');
    if (inp) inp.value = current;
  }

  function saveHero() {
    const val = document.getElementById('gdn-hero-input')?.value?.trim();
    if (val) {
      localStorage.setItem(HERO_KEY, val);
      VW.Store?.push(HERO_KEY);
      const el = document.getElementById('gdn-hero-text');
      if (el) el.textContent = val;
    }
    cancelHero();
  }

  function cancelHero() {
    document.getElementById('gdn-hero-blurb').style.display  = '';
    document.getElementById('gdn-hero-editor').style.display = 'none';
  }

  // ── Lightbox ──────────────────────────────────────────────────────────────────
  function openLightbox(photoId) {
    _lbPhotos = _photos;
    _lbIdx    = _lbPhotos.findIndex(p => p.id === photoId);
    if (_lbIdx < 0) _lbIdx = 0;
    _showLightboxSlide();
    const lb = document.getElementById('gal-lightbox');
    if (lb) lb.style.display = 'flex';
  }

  function closeLightbox() {
    const lb = document.getElementById('gal-lightbox');
    if (lb) lb.style.display = 'none';
  }

  function lightboxNav(dir) {
    _lbIdx = ((_lbIdx + dir) % _lbPhotos.length + _lbPhotos.length) % _lbPhotos.length;
    _showLightboxSlide();
  }

  function _showLightboxSlide() {
    const p = _lbPhotos[_lbIdx];
    if (!p) return;
    const img = document.getElementById('gal-lb-img');
    const cap = document.getElementById('gal-lb-caption');
    const tags = document.getElementById('gal-lb-tags');
    if (img)  img.src = p.url;
    if (cap)  cap.textContent = p.caption || '';
    if (tags) {
      const bedL   = (p.beds||[]).map(_bedName).join(', ');
      const plantL = (p.plants||[]).join(', ');
      const cat    = p.category || '';
      tags.textContent = [cat, bedL, plantL].filter(Boolean).join(' · ');
    }
  }

  // ── Upload modal ──────────────────────────────────────────────────────────────
  function openUpload() {
    _loadGardenData().then(() => {
      _buildTagChips();
      _selectedBeds   = new Set();
      _selectedPlants = new Set();
      _selectedCat    = '';
      _chosenFiles    = [];
      document.getElementById('gal-files-chosen').textContent = '';
      document.getElementById('gal-caption').value = '';
      document.getElementById('gal-upload-modal')?.classList.add('open');
    });
  }

  function closeUpload() {
    document.getElementById('gal-upload-modal')?.classList.remove('open');
  }

  function _buildTagChips() {
    // Bed chips
    const bedWrap = document.getElementById('gal-bed-chips');
    if (bedWrap) {
      const beds = _getBeds();
      bedWrap.innerHTML = beds.length
        ? beds.map(b => `
            <button class="gal-chip" data-id="${_esc(b.id)}" data-type="bed"
              onclick="GDN.Gallery._toggleChip(this,'bed')">${_esc(b.name)}</button>`).join('')
        : '<span style="font-size:12px;color:var(--text3)">No beds in planner yet</span>';
    }

    // Category chips (Panorama, Yardscape, General)
    const catWrap = document.getElementById('gal-cat-chips');
    if (catWrap) {
      catWrap.innerHTML = ['Panorama','Yardscape','General'].map(c => `
        <button class="gal-chip" data-id="${c}" data-type="cat"
          onclick="GDN.Gallery._toggleChip(this,'cat')">${c}</button>`).join('');
    }

    // Plant chips
    const plantWrap = document.getElementById('gal-plant-chips');
    if (plantWrap) {
      const plants = _getPlants().filter(p => !['Panorama','Yardscape','General'].includes(p.id));
      plantWrap.innerHTML = plants.length
        ? plants.map(p => `
            <button class="gal-chip" data-id="${_esc(p.id)}" data-type="plant"
              onclick="GDN.Gallery._toggleChip(this,'plant')">${_esc(p.name)}</button>`).join('')
        : '<span style="font-size:12px;color:var(--text3)">Add plants in the planner to tag photos</span>';
    }
  }

  function _toggleChip(btn, type) {
    btn.classList.toggle('on');
    const id = btn.dataset.id;
    if (type === 'bed') {
      if (_selectedBeds.has(id)) _selectedBeds.delete(id);
      else _selectedBeds.add(id);
    } else if (type === 'plant') {
      if (_selectedPlants.has(id)) _selectedPlants.delete(id);
      else _selectedPlants.add(id);
    } else if (type === 'cat') {
      // Single-select category
      document.querySelectorAll('.gal-chip[data-type="cat"]').forEach(c => c.classList.remove('on'));
      btn.classList.add('on');
      _selectedCat = id;
    }
  }

  function handleDrop(e) {
    e.preventDefault();
    document.getElementById('gal-drop-zone')?.classList.remove('over');
    filesChosen(e.dataTransfer.files);
  }

  function filesChosen(files) {
    _chosenFiles = [...files];
    const el = document.getElementById('gal-files-chosen');
    if (el) el.textContent = `${_chosenFiles.length} file${_chosenFiles.length!==1?'s':''} selected`;
  }

  async function uploadPhotos() {
    if (!_chosenFiles.length) { window.toast?.('Select at least one photo', 'error'); return; }
    const fd = new FormData();
    _chosenFiles.forEach(f => fd.append('files', f));
    [..._selectedBeds].forEach(b   => fd.append('beds',   b));
    [..._selectedPlants].forEach(p => fd.append('plants', p));
    if (_selectedCat) fd.append('category', _selectedCat);
    fd.append('caption', document.getElementById('gal-caption')?.value?.trim() || '');
    try {
      const r = await fetch('/api/garden/photo', { method: 'POST', body: fd });
      const d = await r.json();
      if (d.ok) {
        await _loadPhotos();
        _renderGallery();
        closeUpload();
        window.toast?.('✓ Photos uploaded', 'success');
      } else {
        window.toast?.('Upload failed', 'error');
      }
    } catch { window.toast?.('Network error', 'error'); }
  }

  async function deletePhoto(id) {
    if (!confirm('Delete this photo?')) return;
    try {
      await fetch(`/api/garden/photo/${id}`, { method: 'DELETE' });
      await _loadPhotos();
      _renderGallery();
    } catch { window.toast?.('Delete failed', 'error'); }
  }

  return {
    init, onAuthChange,
    editNote, saveNote, cancelNote,
    editHero, saveHero, cancelHero,
    groupBy,
    openLightbox, closeLightbox, lightboxNav,
    openUpload, closeUpload, handleDrop, filesChosen, uploadPhotos,
    deletePhoto,
    _toggleChip,
  };
})();
