/**
 * travel.js — VirtuWill Travel Map v2
 *
 * Features:
 *   • Custom draggable pins (visited / recommend / wishlist)
 *   • Country shading via GeoJSON — marks visited countries in blue
 *   • US State shading via GeoJSON — marks visited states
 *   • Photo overlays on the map
 *   • Google My Maps iframe embed
 *
 * Data stored on the server (/api/data/travel_*), cached in localStorage:
 *   vw_travel_pins    → [{id,name,city,lat,lng,type,note,photos}]
 *   vw_travel_visited → { countries:['US','MX',...], states:['TX','MS',...] }
 *
 * GeoJSON sources (loaded once from CDN, cached by browser):
 *   Countries: https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson
 *   US States: https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json
 */
'use strict';
window.VW = window.VW || {};

window.VW.Travel = (() => {

  // ── Constants ──────────────────────────────────────────────────────────────
  const PIN_KEY     = 'vw_travel_pins';
  const VISITED_KEY = 'vw_travel_visited';

  const PIN_TYPES = {
    visited:   { label:'Visited',   icon:'📍', color:'#109ACC' },
    recommend: { label:'Recommend', icon:'⭐', color:'#1D9E75' },
    wishlist:  { label:'Wish list', icon:'🌟', color:'#EF9F27' },
  };

  // Shading colors
  const COUNTRY_FILL   = 'rgba(16,154,204,0.28)';
  const COUNTRY_BORDER = 'rgba(16,154,204,0.7)';
  const STATE_FILL     = 'rgba(29,158,117,0.32)';
  const STATE_BORDER   = 'rgba(29,158,117,0.8)';

  // GeoJSON CDN URLs
  const COUNTRY_GEOJSON_URL = 'https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson';
  const STATES_GEOJSON_URL  = 'https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json';

  // ── State ──────────────────────────────────────────────────────────────────
  let _map            = null;
  let _pins           = [];
  let _markers        = {};
  let _photos         = [];
  let _visited        = { countries: [], states: [] };
  let _countryLayer   = null;   // L.geoJSON layer for countries
  let _stateLayer     = null;   // L.geoJSON layer for US states
  let _countryGeo     = null;   // cached GeoJSON
  let _stateGeo       = null;   // cached GeoJSON
  let _inited         = false;
  let _sidebarTab     = 'pins'; // 'pins' | 'visited'

  // ── Init ───────────────────────────────────────────────────────────────────
  async function init() {
    if (_inited) { _map?.invalidateSize(); return; }
    _inited = true;
    await Promise.all([VW.Store?.pull(PIN_KEY), VW.Store?.pull(VISITED_KEY)]);
    _loadPins();
    _loadVisited();
    setTimeout(_buildMap, 120);
  }

  function _buildMap() {
    const el = document.getElementById('travel-map');
    if (!el || !window.L) return;

    _map = L.map('travel-map', { center:[20,0], zoom:2, zoomControl:true });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 18,
    }).addTo(_map);

    // Load GeoJSON and shade visited places
    _loadCountryGeo();
    _loadStateGeo();

    // Place existing pins
    _pins.forEach(_addMarker);

    // Admin click → capture coords for photo overlay
    _map.on('click', e => {
      if (!window.VW?.Auth?.isAdmin?.()) return;
      const {lat, lng} = e.latlng;
      const latEl = document.getElementById('tv-photo-lat');
      const lngEl = document.getElementById('tv-photo-lng');
      if (latEl) latEl.value = lat.toFixed(5);
      if (lngEl) lngEl.value = lng.toFixed(5);
    });

    _updateAdminUI();
    _renderPinList();
    _renderVisitedPanel();
  }

  // ── GeoJSON: countries ─────────────────────────────────────────────────────
  async function _loadCountryGeo() {
    try {
      const r = await fetch(COUNTRY_GEOJSON_URL);
      _countryGeo = await r.json();
      _drawCountries();
    } catch (e) {
      console.warn('Travel: could not load country GeoJSON', e);
    }
  }

  function _drawCountries() {
    if (!_map || !_countryGeo) return;
    if (_countryLayer) { _countryLayer.remove(); _countryLayer = null; }

    _countryLayer = L.geoJSON(_countryGeo, {
      style: feature => {
        const code = feature.properties.ISO_A2 || feature.properties.iso_a2 || '';
        const visited = _visited.countries.includes(code.toUpperCase());
        return visited
          ? { fillColor: COUNTRY_FILL, fillOpacity:1, color: COUNTRY_BORDER, weight:1.5, opacity:1 }
          : { fillColor: 'transparent', fillOpacity:0, color:'transparent', weight:0, opacity:0 };
      },
      onEachFeature: (feature, layer) => {
        const code = (feature.properties.ISO_A2 || feature.properties.iso_a2 || '').toUpperCase();
        const name = feature.properties.ADMIN || feature.properties.name || code;
        if (_visited.countries.includes(code)) {
          layer.bindTooltip(name, { sticky:true, className:'tv-tooltip' });
        }
        if (window.VW?.Auth?.isAdmin?.()) {
          layer.on('click', e => {
            L.DomEvent.stopPropagation(e);
            toggleCountry(code, name);
          });
          layer.on('mouseover', () => {
            if (!_visited.countries.includes(code)) {
              layer.setStyle({ fillColor: 'rgba(16,154,204,0.10)', fillOpacity:1, color:'rgba(16,154,204,0.3)', weight:1, opacity:1 });
            }
          });
          layer.on('mouseout', () => {
            _countryLayer.resetStyle(layer);
          });
        }
      },
    }).addTo(_map);
  }

  // ── GeoJSON: US states ─────────────────────────────────────────────────────
  async function _loadStateGeo() {
    try {
      const r = await fetch(STATES_GEOJSON_URL);
      _stateGeo = await r.json();
      _drawStates();
    } catch (e) {
      console.warn('Travel: could not load state GeoJSON', e);
    }
  }

  function _drawStates() {
    if (!_map || !_stateGeo) return;
    if (_stateLayer) { _stateLayer.remove(); _stateLayer = null; }

    _stateLayer = L.geoJSON(_stateGeo, {
      style: feature => {
        const name = feature.properties.name || '';
        const abbr = _stateNameToAbbr(name);
        const visited = _visited.states.includes(abbr);
        return visited
          ? { fillColor: STATE_FILL, fillOpacity:1, color: STATE_BORDER, weight:1.5, opacity:1 }
          : { fillColor: 'transparent', fillOpacity:0, color:'transparent', weight:0, opacity:0 };
      },
      onEachFeature: (feature, layer) => {
        const name = feature.properties.name || '';
        const abbr = _stateNameToAbbr(name);
        if (_visited.states.includes(abbr)) {
          layer.bindTooltip(name, { sticky:true, className:'tv-tooltip' });
        }
        if (window.VW?.Auth?.isAdmin?.()) {
          layer.on('click', e => {
            L.DomEvent.stopPropagation(e);
            toggleState(abbr, name);
          });
          layer.on('mouseover', () => {
            if (!_visited.states.includes(abbr)) {
              layer.setStyle({ fillColor:'rgba(29,158,117,0.12)', fillOpacity:1, color:'rgba(29,158,117,0.3)', weight:1, opacity:1 });
            }
          });
          layer.on('mouseout', () => _stateLayer.resetStyle(layer));
        }
      },
    }).addTo(_map);
  }

  // ── Visited data ───────────────────────────────────────────────────────────
  function _loadVisited() {
    try { _visited = JSON.parse(localStorage.getItem(VISITED_KEY) || '{"countries":[],"states":[]}'); }
    catch { _visited = { countries:[], states:[] }; }
  }

  function _saveVisited() {
    localStorage.setItem(VISITED_KEY, JSON.stringify(_visited));
    VW.Store?.push(VISITED_KEY);
  }

  function toggleCountry(code, name) {
    if (!code) return;
    const idx = _visited.countries.indexOf(code.toUpperCase());
    if (idx >= 0) {
      _visited.countries.splice(idx, 1);
      window.toast?.('Removed: ' + (name||code), 'success', 1500);
    } else {
      _visited.countries.push(code.toUpperCase());
      window.toast?.('Added: ' + (name||code), 'success', 1500);
    }
    _saveVisited();
    _drawCountries();     // redraw shading
    _renderVisitedPanel();
  }

  function toggleState(abbr, name) {
    if (!abbr) return;
    const idx = _visited.states.indexOf(abbr);
    if (idx >= 0) {
      _visited.states.splice(idx, 1);
      window.toast?.('Removed: ' + (name||abbr), 'success', 1500);
    } else {
      _visited.states.push(abbr);
      window.toast?.('Added: ' + (name||abbr), 'success', 1500);
    }
    _saveVisited();
    _drawStates();
    _renderVisitedPanel();
  }

  // Add by text search (sidebar form)
  function addVisitedPlace() {
    const input = document.getElementById('tv-visited-input');
    const type  = document.getElementById('tv-visited-type')?.value || 'country';
    const val   = input?.value.trim();
    if (!val) return;

    if (type === 'state') {
      // Try to match a US state
      const abbr  = _stateNameToAbbr(val) || val.toUpperCase().slice(0,2);
      const match = US_STATES.find(s => s.abbr === abbr.toUpperCase() || s.name.toLowerCase() === val.toLowerCase());
      if (!match) { window.toast?.('State not recognised — try full name or abbreviation', 'error'); return; }
      toggleState(match.abbr, match.name);
    } else {
      // Geocode + get ISO code via Nominatim
      fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(val)}&limit=1&addressdetails=1`)
        .then(r => r.json())
        .then(results => {
          if (!results.length) { window.toast?.('Country not found', 'error'); return; }
          const cc = (results[0].address?.country_code || '').toUpperCase();
          const name = results[0].address?.country || val;
          if (!cc) { window.toast?.('Could not determine country code', 'error'); return; }
          toggleCountry(cc, name);
          _map?.flyTo([parseFloat(results[0].lat), parseFloat(results[0].lon)], 4, {animate:true});
        })
        .catch(() => window.toast?.('Geocoding failed', 'error'));
    }

    if (input) input.value = '';
  }

  function removeVisited(type, code) {
    if (type === 'country') {
      _visited.countries = _visited.countries.filter(c => c !== code);
    } else {
      _visited.states = _visited.states.filter(s => s !== code);
    }
    _saveVisited();
    _drawCountries(); _drawStates();
    _renderVisitedPanel();
  }

  // ── Visited sidebar panel ──────────────────────────────────────────────────
  function _renderVisitedPanel() {
    const el = document.getElementById('tv-visited-list');
    if (!el) return;

    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    const cc = _visited.countries.length;
    const sc = _visited.states.length;

    // Stats bar
    const statsEl = document.getElementById('tv-visited-stats');
    if (statsEl) {
      statsEl.innerHTML =
        '<span class="tv-stat"><span class="tv-stat-num">' + cc + '</span> countr' + (cc===1?'y':'ies') + '</span>' +
        '<span class="tv-stat-sep">·</span>' +
        '<span class="tv-stat-num">' + sc + '</span> US state' + (sc===1?'':'s');
    }

    if (!cc && !sc) {
      el.innerHTML = '<div class="tv-empty">' + (isAdmin
        ? 'Click a country or state on the map to shade it, or use the form above.'
        : 'No places added yet.') + '</div>';
      return;
    }

    let html = '';
    if (_visited.countries.length) {
      html += '<div class="tv-visited-group-lbl">🌍 Countries (' + cc + ')</div>';
      html += _visited.countries.map(code => {
        const name = _countryCodeToName(code);
        return '<div class="tv-visited-item">' +
          '<span class="tv-visited-flag">' + _flag(code) + '</span>' +
          '<span class="tv-visited-name">' + (name||code) + '</span>' +
          (isAdmin ? '<button class="tv-del-btn" onclick="VW.Travel.removeVisited(\'country\',\'' + code + '\')">×</button>' : '') +
          '</div>';
      }).join('');
    }
    if (_visited.states.length) {
      html += '<div class="tv-visited-group-lbl" style="margin-top:10px">🇺🇸 US States (' + sc + ')</div>';
      html += _visited.states.map(abbr => {
        const match = US_STATES.find(s => s.abbr === abbr);
        return '<div class="tv-visited-item">' +
          '<span class="tv-visited-flag" style="font-size:11px;font-weight:700;color:var(--blue)">' + abbr + '</span>' +
          '<span class="tv-visited-name">' + (match?.name || abbr) + '</span>' +
          (isAdmin ? '<button class="tv-del-btn" onclick="VW.Travel.removeVisited(\'state\',\'' + abbr + '\')">×</button>' : '') +
          '</div>';
      }).join('');
    }
    el.innerHTML = html;
  }

  // ── Sidebar tab switching ──────────────────────────────────────────────────
  function showSidebarTab(tab) {
    _sidebarTab = tab;
    ['pins','visited'].forEach(t => {
      document.getElementById('tv-tab-' + t)?.classList.toggle('on', t === tab);
      const panel = document.getElementById('tv-panel-' + t);
      if (panel) panel.style.display = t === tab ? 'block' : 'none';
    });
  }

  // ── Admin UI ───────────────────────────────────────────────────────────────
  function _updateAdminUI() {
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    ['travel-admin-bar','travel-photo-bar','travel-gmaps-admin','travel-visited-admin']
      .forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = isAdmin ? 'block' : 'none';
      });
    if (isAdmin) {
      const hint = document.getElementById('tv-map-hint');
      if (hint) hint.style.display = 'block';
    }
  }

  // ── Pins ───────────────────────────────────────────────────────────────────
  function _loadPins() {
    try { _pins = JSON.parse(localStorage.getItem(PIN_KEY) || '[]'); }
    catch { _pins = []; }
  }

  function _savePins() {
    localStorage.setItem(PIN_KEY, JSON.stringify(_pins));
    VW.Store?.push(PIN_KEY);
  }

  function addPin() {
    const name = document.getElementById('tv-pin-name')?.value.trim();
    const city = document.getElementById('tv-pin-city')?.value.trim();
    const type = document.getElementById('tv-pin-type')?.value || 'visited';
    const note = document.getElementById('tv-pin-note')?.value.trim();

    if (!name) { window.toast?.('Enter a place name', 'error'); return; }
    if (!city)  { window.toast?.('Enter city, country', 'error'); return; }

    const query = encodeURIComponent(city + ' ' + name);
    fetch('https://nominatim.openstreetmap.org/search?format=json&q=' + query + '&limit=1')
      .then(r => r.json())
      .then(results => {
        if (!results.length) { window.toast?.('Location not found', 'error'); return; }
        const {lat, lon, display_name} = results[0];
        const pin = { id:Date.now(), name, city, type, note, lat:parseFloat(lat), lng:parseFloat(lon), display:display_name, photos:[] };
        _pins.unshift(pin);
        _savePins();
        _addMarker(pin);
        _renderPinList();
        _map.flyTo([pin.lat, pin.lng], 8, {animate:true, duration:1.5});
        ['tv-pin-name','tv-pin-city','tv-pin-note'].forEach(id => { const el=document.getElementById(id); if(el)el.value=''; });
        window.toast?.(PIN_TYPES[type].icon + ' ' + name + ' pinned', 'success');
      })
      .catch(() => window.toast?.('Geocoding failed', 'error'));
  }

  function _addMarker(pin) {
    if (!_map) return;
    const t = PIN_TYPES[pin.type] || PIN_TYPES.visited;
    const icon = L.divIcon({
      className: '',
      html: '<div class="tv-marker tv-marker-' + pin.type + '" title="' + _esc(pin.name) + '"><span class="tv-marker-icon">' + t.icon + '</span></div>',
      iconSize:[32,32], iconAnchor:[16,32], popupAnchor:[0,-34],
    });
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    const marker = L.marker([pin.lat, pin.lng], { icon, draggable:isAdmin }).addTo(_map);
    if (isAdmin) {
      marker.on('dragstart', () => { marker.closePopup(); const el=marker.getElement(); if(el)el.style.opacity='0.65'; });
      marker.on('dragend', ev => {
        const ll=ev.target.getLatLng();
        const stored=_pins.find(p=>p.id===pin.id);
        if(stored){ stored.lat=pin.lat=parseFloat(ll.lat.toFixed(6)); stored.lng=pin.lng=parseFloat(ll.lng.toFixed(6)); _savePins(); }
        const el=marker.getElement(); if(el)el.style.opacity='1';
        _bindPopup(marker, pin);
        window.toast?.(pin.name + ' moved', 'success', 1800);
      });
    }
    _bindPopup(marker, pin);
    _markers[pin.id] = marker;
  }

  function _bindPopup(marker, pin) {
    const t = PIN_TYPES[pin.type] || PIN_TYPES.visited;
    const admin = window.VW?.Auth?.isAdmin?.() || false;
    // Photos attached in the workspace, each with its caption.
    const items = pin.photoItems || (pin.photos || []).map(url => ({ url, caption: '' }));
    const photos = items.map(p => '<figure style="margin:6px 0 0"><img src="' + _esc(p.url) + '" alt="' + _esc(p.caption || pin.name) + '" loading="lazy" style="width:100%;border-radius:4px;object-fit:cover;max-height:140px;display:block"/>'
      + (p.caption ? '<figcaption style="font-size:11px;color:#555;margin-top:2px">' + _esc(p.caption) + '</figcaption>' : '') + '</figure>').join('');
    const adminRow = admin
      ? '<div style="margin-top:8px;display:flex;gap:12px;align-items:center"><a href="#" onclick="VW.Travel.removePin(' + pin.id + ');return false;" style="font-size:11px;color:#e84235">Remove</a><span style="font-size:10px;color:#aaa">Drag to reposition</span></div>'
      : '';
    marker.bindPopup(
      '<div style="min-width:180px;font-family:Inter,sans-serif">'
      + '<div style="font-weight:700;font-size:14px;margin-bottom:2px">' + _esc(pin.name) + '</div>'
      + '<div style="font-size:11px;color:#666;margin-bottom:6px">' + t.icon + ' ' + t.label + ' &middot; ' + _esc(pin.city) + '</div>'
      + (pin.note ? '<div style="font-size:12px;color:#444;margin-bottom:6px">' + _esc(pin.note) + '</div>' : '')
      + photos + adminRow + '</div>',
      { maxWidth:240 }
    );
  }

  function removePin(id) {
    if (!confirm('Remove this pin?')) return;
    _markers[id]?.remove(); delete _markers[id];
    _pins = _pins.filter(p => p.id !== id);
    _savePins(); _renderPinList(); _map?.closePopup();
  }

  function focusPin(id) {
    const pin = _pins.find(p => p.id === id);
    if (!pin || !_map) return;
    _map.flyTo([pin.lat, pin.lng], 10, {animate:true, duration:1});
    _markers[id]?.openPopup();
  }

  function _renderPinList() {
    const el = document.getElementById('travel-pin-list');
    if (!el) return;
    if (!_pins.length) { el.innerHTML = '<div class="tv-empty">No pins yet.</div>'; return; }
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    el.innerHTML = _pins.map(p => {
      const t = PIN_TYPES[p.type] || PIN_TYPES.visited;
      return '<div class="tv-pin-item" onclick="VW.Travel.focusPin(' + p.id + ')">'
        + '<span class="tv-pin-icon">' + t.icon + '</span>'
        + '<div class="tv-pin-info"><div class="tv-pin-name">' + _esc(p.name) + '</div><div class="tv-pin-city">' + _esc(p.city) + '</div></div>'
        + (isAdmin ? '<button class="tv-del-btn" onclick="event.stopPropagation();VW.Travel.removePin(' + p.id + ')">×</button>' : '')
        + '</div>';
    }).join('');
  }

  // ── Photo overlays ─────────────────────────────────────────────────────────
  function uploadPhoto(files) {
    if (!files?.length || !_map) return;
    const lat = parseFloat(document.getElementById('tv-photo-lat')?.value);
    const lng = parseFloat(document.getElementById('tv-photo-lng')?.value);
    const caption = document.getElementById('tv-photo-caption')?.value || '';
    if (isNaN(lat)||isNaN(lng)) { window.toast?.('Click the map to set coordinates', 'error'); return; }
    const url = URL.createObjectURL(files[0]);
    const icon = L.divIcon({
      className:'',
      html: '<div class="tv-photo-marker"><img src="' + url + '" style="width:48px;height:48px;border-radius:6px;object-fit:cover;border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.4)"/></div>',
      iconSize:[52,52], iconAnchor:[26,52], popupAnchor:[0,-54],
    });
    const marker = L.marker([lat,lng], {icon}).addTo(_map);
    marker.bindPopup('<img src="' + url + '" style="width:220px;border-radius:6px;display:block"/>' + (caption ? '<div style="font-size:12px;margin-top:6px;color:#444">' + _esc(caption) + '</div>' : ''), {maxWidth:240});
    _photos.push({url,lat,lng,caption,marker});
    _map.flyTo([lat,lng], 10, {animate:true});
    window.toast?.('Photo pinned to map', 'success');
  }

  // ── Google My Maps embed ───────────────────────────────────────────────────
  function embedGoogleMap() {
    const url = document.getElementById('tv-gmaps-url')?.value.trim();
    if (!url) return;
    const frame = document.getElementById('tv-gmaps-frame');
    const panel = document.getElementById('travel-maps-panel');
    if (frame) frame.src = url;
    if (panel) panel.style.display = 'block';
    window.toast?.('Google map embedded', 'success');
  }

  // ── Auth change ────────────────────────────────────────────────────────────
  function onAuthChange() {
    _updateAdminUI(); _renderPinList(); _renderVisitedPanel();
    _drawCountries(); _drawStates();
    _pins.forEach(p => { if(_markers[p.id]){ _markers[p.id].remove(); delete _markers[p.id]; _addMarker(p); } });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  const _esc = window.VW?.esc || (s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')); // shared util

  function _flag(code) {
    // Convert ISO-2 country code to flag emoji
    if (!code || code.length !== 2) return '🌐';
    return String.fromCodePoint(...[...code.toUpperCase()].map(c => c.charCodeAt(0) + 0x1F1A5));
  }

  function _countryCodeToName(code) {
    // Look up in GeoJSON features if loaded
    if (_countryGeo) {
      const f = _countryGeo.features.find(f => (f.properties.ISO_A2||f.properties.iso_a2||'').toUpperCase() === code);
      if (f) return f.properties.ADMIN || f.properties.name || code;
    }
    return code;
  }

  function _stateNameToAbbr(name) {
    const match = US_STATES.find(s =>
      s.name.toLowerCase() === name.toLowerCase() ||
      s.abbr.toLowerCase() === name.toLowerCase()
    );
    return match?.abbr || null;
  }

  // ── US States lookup ───────────────────────────────────────────────────────
  const US_STATES = [
    {abbr:'AL',name:'Alabama'},{abbr:'AK',name:'Alaska'},{abbr:'AZ',name:'Arizona'},
    {abbr:'AR',name:'Arkansas'},{abbr:'CA',name:'California'},{abbr:'CO',name:'Colorado'},
    {abbr:'CT',name:'Connecticut'},{abbr:'DE',name:'Delaware'},{abbr:'FL',name:'Florida'},
    {abbr:'GA',name:'Georgia'},{abbr:'HI',name:'Hawaii'},{abbr:'ID',name:'Idaho'},
    {abbr:'IL',name:'Illinois'},{abbr:'IN',name:'Indiana'},{abbr:'IA',name:'Iowa'},
    {abbr:'KS',name:'Kansas'},{abbr:'KY',name:'Kentucky'},{abbr:'LA',name:'Louisiana'},
    {abbr:'ME',name:'Maine'},{abbr:'MD',name:'Maryland'},{abbr:'MA',name:'Massachusetts'},
    {abbr:'MI',name:'Michigan'},{abbr:'MN',name:'Minnesota'},{abbr:'MS',name:'Mississippi'},
    {abbr:'MO',name:'Missouri'},{abbr:'MT',name:'Montana'},{abbr:'NE',name:'Nebraska'},
    {abbr:'NV',name:'Nevada'},{abbr:'NH',name:'New Hampshire'},{abbr:'NJ',name:'New Jersey'},
    {abbr:'NM',name:'New Mexico'},{abbr:'NY',name:'New York'},{abbr:'NC',name:'North Carolina'},
    {abbr:'ND',name:'North Dakota'},{abbr:'OH',name:'Ohio'},{abbr:'OK',name:'Oklahoma'},
    {abbr:'OR',name:'Oregon'},{abbr:'PA',name:'Pennsylvania'},{abbr:'RI',name:'Rhode Island'},
    {abbr:'SC',name:'South Carolina'},{abbr:'SD',name:'South Dakota'},{abbr:'TN',name:'Tennessee'},
    {abbr:'TX',name:'Texas'},{abbr:'UT',name:'Utah'},{abbr:'VT',name:'Vermont'},
    {abbr:'VA',name:'Virginia'},{abbr:'WA',name:'Washington'},{abbr:'WV',name:'West Virginia'},
    {abbr:'WI',name:'Wisconsin'},{abbr:'WY',name:'Wyoming'},{abbr:'DC',name:'D.C.'},
  ];

  // ── Public API ─────────────────────────────────────────────────────────────
  return {
    init, onAuthChange,
    addPin, removePin, focusPin,
    uploadPhoto, embedGoogleMap,
    addVisitedPlace, removeVisited,
    toggleCountry, toggleState,
    showSidebarTab,
  };

})();
