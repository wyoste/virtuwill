/**
 * garden.js — VirtuWill Garden Planner v6
 *
 * Key changes from v5:
 *   - Unified 0.25 ft plant grid (no separate node layer)
 *   - Plants stored as grid indices { gi, gj } in bed-local space
 *   - Free-draw tool: capture path → simplify → polygon
 *   - Grid recalculates automatically when bed geometry changes
 *
 * Data model:
 *   Bed: { id, name, color, transform:{x,y,rotation}, shape, plants[] }
 *   Plant: { id, speciesId, displayName, gi, gj, health, notes }
 *     gi,gj  = integer grid indices in bed-local space
 *     local coords = { x: gi*GRID, y: gj*GRID }
 *
 * Grid: GRID = 0.25 ft. Valid positions = grid points inside bed shape.
 *       Grid origin = centre of bed shape bounding box.
 */

'use strict';
window.VW = window.VW || {};

window.VW.Garden = (() => {

  // ════════════════════════════════════════════════════════════
  //  CONSTANTS
  // ════════════════════════════════════════════════════════════

  const GRID           = 0.25;   // ft — plant placement grid spacing
  const DRAG_THRESHOLD = 5;      // px — click vs drag
  const PLANT_HIT      = 0.5;    // ft — hit radius for plant selection
  const FREE_MIN_DIST  = 0.3;    // ft — min distance between free-draw sample pts
  const FREE_MIN_VERTS = 3;      // minimum polygon vertices after simplification
  const SIMPLIFY_TOL   = 0.4;    // ft — Ramer-Douglas-Peucker tolerance
  const SCALE_BAR_FT   = 10;

  const HEALTH = [
    { icon:'💀', label:'Dead',       col:'#6b2f2f' },
    { icon:'🥀', label:'Struggling', col:'#8b6a3a' },
    { icon:'🌱', label:'Growing',    col:'#3d7a2f' },
    { icon:'🌸', label:'Blooming',   col:'#b05580' },
    { icon:'🌺', label:'Full Bloom', col:'#c43030' },
  ];

  const SPECIES = [
    { id:'hybrid-tea-rose',       name:'Hybrid Tea Rose',       cat:'Rose',        dia:4,   emoji:'🌹' },
    { id:'floribunda-rose',       name:'Floribunda Rose',        cat:'Rose',        dia:4,   emoji:'🌹' },
    { id:'climbing-rose',         name:'Climbing Rose',          cat:'Rose',        dia:8,   emoji:'🌹' },
    { id:'salvia-farinacea',      name:'Salvia Farinacea',       cat:'Perennial',   dia:2,   emoji:'💜' },
    { id:'rudbeckia',             name:'Rudbeckia',              cat:'Perennial',   dia:3,   emoji:'🌻' },
    { id:'shasta-daisy',          name:'Shasta Daisy',           cat:'Perennial',   dia:2,   emoji:'🌼' },
    { id:'dahlia-baja-aztec',     name:'Dahlia Baja Aztec',      cat:'Bulb',        dia:2.5, emoji:'🌸' },
    { id:'day-lily',              name:'Day Lily',               cat:'Perennial',   dia:2,   emoji:'🌷' },
    { id:'cone-flower',           name:'Cone Flower',            cat:'Perennial',   dia:2,   emoji:'🌺' },
    { id:'marigold',              name:'Marigold',               cat:'Annual',      dia:1,   emoji:'🟡' },
    { id:'hollyhock',             name:'Holly Hock',             cat:'Biennial',    dia:2,   emoji:'🌸' },
    { id:'canna',                 name:'Canna',                  cat:'Tropical',    dia:3,   emoji:'🌺' },
    { id:'columbine',             name:'Columbine',              cat:'Perennial',   dia:1.5, emoji:'🔵' },
    { id:'daffodil',              name:'Daffodil',               cat:'Bulb',        dia:0.5, emoji:'🌼' },
    { id:'dutch-iris',            name:'Dutch Iris',             cat:'Bulb',        dia:0.5, emoji:'💜' },
    { id:'gladiolus',             name:'Gladiolus',              cat:'Bulb',        dia:0.5, emoji:'🌸' },
    { id:'mammoth-sunflower',     name:'Mammoth Sunflower',      cat:'Annual',      dia:2,   emoji:'🌻' },
    { id:'teddybear-sunflower',   name:'Teddybear Sunflower',    cat:'Annual',      dia:1.5, emoji:'🌻' },
    { id:'august-beauty-gardenia',name:'August Beauty Gardenia', cat:'Shrub',       dia:5,   emoji:'⚪' },
    { id:'petunia',               name:'Petunia',                cat:'Annual',      dia:1,   emoji:'🌸' },
    { id:'rocket-larkspur',       name:'Rocket Larkspur',        cat:'Annual',      dia:1,   emoji:'💙' },
    { id:'zinnia',                name:'Zinnia',                 cat:'Annual',      dia:1,   emoji:'🌸' },
    { id:'lantana',               name:'Lantana',                cat:'Perennial',   dia:4,   emoji:'🟠' },
    { id:'asiatic-lily',          name:'Asiatic Lily',           cat:'Bulb',        dia:1,   emoji:'🌷' },
    { id:'morning-glory',         name:'Morning Glory',          cat:'Annual Vine', dia:3,   emoji:'💙' },
    { id:'blanket-flower',        name:'Blanket Flower',         cat:'Perennial',   dia:2,   emoji:'🌺' },
    { id:'milkweed',              name:'MilkWeed',               cat:'Perennial',   dia:2,   emoji:'🟣' },
    { id:'plumeria',              name:'Plumeria',               cat:'Tropical',    dia:6,   emoji:'🌸' },
    { id:'hydrangea',             name:'Hydrangea',              cat:'Shrub',       dia:6,   emoji:'💠' },
    { id:'hibiscus',              name:'Hybiscus',               cat:'Shrub',       dia:6,   emoji:'🌺' },
  ];

  // ════════════════════════════════════════════════════════════
  //  GEOMETRY UTILITIES
  // ════════════════════════════════════════════════════════════

  function worldToScreen(wx, wy, cam) {
    return { x: (wx + cam.x) * cam.z, y: (wy + cam.y) * cam.z };
  }
  function screenToWorld(sx, sy, cam) {
    return { x: sx / cam.z - cam.x, y: sy / cam.z - cam.y };
  }
  function localToWorld(lx, ly, bed) {
    const cos = Math.cos(bed.transform.rotation);
    const sin = Math.sin(bed.transform.rotation);
    return { x: bed.transform.x + lx*cos - ly*sin, y: bed.transform.y + lx*sin + ly*cos };
  }
  function worldToLocal(wx, wy, bed) {
    const dx = wx - bed.transform.x, dy = wy - bed.transform.y;
    const cos = Math.cos(-bed.transform.rotation), sin = Math.sin(-bed.transform.rotation);
    return { x: dx*cos - dy*sin, y: dx*sin + dy*cos };
  }

  function shapeLocalVertices(shape) {
    switch (shape.type) {
      case 'rectangle': {
        const hw = shape.width/2, hh = shape.height/2;
        return [{x:-hw,y:-hh},{x:hw,y:-hh},{x:hw,y:hh},{x:-hw,y:hh}];
      }
      case 'circle': {
        const n = 32, r = shape.radius;
        return Array.from({length:n}, (_,i) => {
          const a = (i/n)*Math.PI*2 - Math.PI/2;
          return { x: Math.cos(a)*r, y: Math.sin(a)*r };
        });
      }
      case 'polygon':
        return shape.vertices;
      default: return [];
    }
  }

  function bedWorldVertices(bed) {
    return shapeLocalVertices(bed.shape).map(v => localToWorld(v.x, v.y, bed));
  }

  function polyBounds(verts) {
    const xs = verts.map(v=>v.x), ys = verts.map(v=>v.y);
    return { minX:Math.min(...xs), maxX:Math.max(...xs), minY:Math.min(...ys), maxY:Math.max(...ys) };
  }

  function ptInPoly(px, py, verts) {
    let inside = false;
    for (let i=0, j=verts.length-1; i<verts.length; j=i++) {
      const xi=verts[i].x, yi=verts[i].y, xj=verts[j].x, yj=verts[j].y;
      if (((yi>py)!==(yj>py)) && (px < (xj-xi)*(py-yi)/(yj-yi)+xi)) inside=!inside;
    }
    return inside;
  }

  function ptSegDist(px,py,ax,ay,bx,by) {
    const dx=bx-ax, dy=by-ay, l2=dx*dx+dy*dy;
    if(!l2) return Math.hypot(px-ax,py-ay);
    const t=Math.max(0,Math.min(1,((px-ax)*dx+(py-ay)*dy)/l2));
    return Math.hypot(px-(ax+t*dx),py-(ay+t*dy));
  }

  function polyArea(verts) {
    let a = 0;
    for (let i=0; i<verts.length; i++) {
      const j=(i+1)%verts.length;
      a += verts[i].x*verts[j].y - verts[j].x*verts[i].y;
    }
    return Math.abs(a/2);
  }

  // ── Grid utilities ─────────────────────────────────────────

  // Convert grid index → local coord
  function gridToLocal(gi, gj, origin) {
    return { x: origin.x + gi*GRID, y: origin.y + gj*GRID };
  }
  // Convert local coord → nearest grid index (may be outside shape)
  function localToGrid(lx, ly, origin) {
    return { gi: Math.round((lx - origin.x) / GRID), gj: Math.round((ly - origin.y) / GRID) };
  }

  // Compute the grid origin for a bed (top-left of bounding box, snapped to GRID)
  function bedGridOrigin(bed) {
    const verts = shapeLocalVertices(bed.shape);
    if (!verts.length) return { x:0, y:0 };
    const b = polyBounds(verts);
    // Align origin to grid so grid is consistent when shape moves
    return {
      x: Math.floor(b.minX / GRID) * GRID,
      y: Math.floor(b.minY / GRID) * GRID,
    };
  }

  // Get all valid grid points inside a bed (in local coords + indices)
  function bedGridPoints(bed) {
    const verts  = shapeLocalVertices(bed.shape);
    if (!verts.length) return [];
    const origin = bedGridOrigin(bed);
    const b      = polyBounds(verts);
    const giMin  = Math.floor((b.minX - origin.x) / GRID);
    const giMax  = Math.ceil ((b.maxX - origin.x) / GRID);
    const gjMin  = Math.floor((b.minY - origin.y) / GRID);
    const gjMax  = Math.ceil ((b.maxY - origin.y) / GRID);
    const pts    = [];
    for (let gj = gjMin; gj <= gjMax; gj++) {
      for (let gi = giMin; gi <= giMax; gi++) {
        const lx = origin.x + gi*GRID, ly = origin.y + gj*GRID;
        if (ptInPoly(lx, ly, verts)) pts.push({ gi, gj, lx, ly });
      }
    }
    return pts;
  }

  // Get local coords of a plant's grid position
  function plantLocalPos(plant, bed) {
    const origin = bedGridOrigin(bed);
    return gridToLocal(plant.gi, plant.gj, origin);
  }

  // ── Free-draw utilities ────────────────────────────────────

  // Ramer–Douglas–Peucker simplification
  function _rdp(pts, tol) {
    if (pts.length <= 2) return pts;
    let maxD = 0, idx = 0;
    const first = pts[0], last = pts[pts.length-1];
    for (let i=1; i<pts.length-1; i++) {
      const d = ptSegDist(pts[i].x, pts[i].y, first.x, first.y, last.x, last.y);
      if (d > maxD) { maxD = d; idx = i; }
    }
    if (maxD > tol) {
      return [..._rdp(pts.slice(0,idx+1), tol).slice(0,-1), ..._rdp(pts.slice(idx), tol)];
    }
    return [first, last];
  }

  // ════════════════════════════════════════════════════════════
  //  STATE
  // ════════════════════════════════════════════════════════════

  let state = { calibration:{feetPerPixel:null}, backgroundImageUrl:'/static/garden_illustrated.png', beds:[] };

  let _bgImg = null, _bgReady = false;
  let _cam   = { x:0, y:0, z:8 };

  // Mode: 'overview' | 'bed_edit'
  let _mode      = 'overview';
  let _editBedId = null;

  // Selection
  let _selectedBedId   = null;
  let _selectedPlantId = null;
  let _hoveredBedId    = null;
  let _hoveredPlantId  = null;   // plant under mouse — only one shown at a time
  let _hovTrash        = false;  // mouse is over the trash button
  let _trashBtn        = null;   // { x, y, r } screen-space hit zone, set during render
  let _plantDelBtn     = null;   // { x, y, r, plantId } — delete × on selected plant

  // Interaction mode (bed edit)
  // 'select' | 'place_plant' | 'erase' | 'reshape' | 'pan' | 'free_draw' | 'polygon'
  let _iMode = 'select';

  // Drag state
  let _drag = null;

  // Mouse world position (live)
  let _mouseW = { x:0, y:0 };

  // In-progress polygon / free-draw
  let _polyPts   = [];   // clicked world points (polygon mode)
  let _freePts   = [];   // raw world points (free-draw mode, while button held)
  let _freeDown  = false;

  // Active species
  let _activeSpeciesId = SPECIES[0].id;

  // ID counter
  let _nid = 1000;
  function _uid(p) { return (p||'x') + (++_nid); }

  let _eventsOk = false;

  // ════════════════════════════════════════════════════════════
  //  INIT / LOAD / SAVE
  // ════════════════════════════════════════════════════════════

  function init() {
    _loadBg();
    _loadData().then(() => { _savedSnapshot = _snapshot(); _fitAll(); _buildSpeciesDropdown(); _renderBedList(); render(); });
    if (!_eventsOk) { _attachEvents(); _eventsOk = true; }
  }

  function onAdminLogin() { init(); }

  function _loadBg() {
    _bgImg = new Image();
    _bgImg.onload  = () => { _bgReady = true; render(); };
    _bgImg.onerror = () => { _bgReady = false; };
    _bgImg.src = state.backgroundImageUrl;
  }

  async function _loadData() {
    try {
      const r = await fetch('/api/garden');
      const d = await r.json();
      if (d.beds?.length) {
        state = { ...state, ...d };
        state.beds.forEach(b => {
          if (typeof b.transform.rotation !== 'number') b.transform.rotation = 0;
          // Migrate old node-based plants: if plant has x,y but no gi,gj, convert
          b.plants = (b.plants||[]).map(p => {
            if (p.gi == null && p.x != null) {
              const origin = bedGridOrigin(b);
              const g = localToGrid(p.x, p.y, origin);
              return { ...p, gi: g.gi, gj: g.gj };
            }
            return p;
          });
          // Strip legacy nodes field (no longer used)
          delete b.nodes;
        });
        _nid = Math.max(_nid,
          ...state.beds.map(b => parseInt(b.id.replace(/\D/g,''))||0),
          ...state.beds.flatMap(b => b.plants.map(p => parseInt(p.id.replace(/\D/g,''))||0))
        ) + 1;
      }
    } catch { /* use empty state */ }
  }

  // What was last loaded or saved, to tell whether there are unsaved changes.
  let _savedSnapshot = null;
  const _snapshot = () => JSON.stringify(state.beds);
  function isDirty() { return _savedSnapshot !== null && _snapshot() !== _savedSnapshot; }

  async function save() {
    // Strip legacy fields before saving
    const clean = { ...state, beds: state.beds.map(b => {
      const { nodes: _, ...rest } = b;
      return rest;
    })};
    const saving = _snapshot();
    try {
      const r = await fetch('/api/garden', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(clean),
      });
      const d = await r.json();
      window.toast?.(d.ok ? '💾 Garden saved' : 'Save failed', d.ok?'success':'error');
      if (d.ok) { _savedSnapshot = saving; GDN?.Viewer?.init?.(); _renderBedList(); }
    } catch {
      window.toast?.('Save failed', 'error');
    }
  }

  // ════════════════════════════════════════════════════════════
  //  RENDERING
  // ════════════════════════════════════════════════════════════

  function render() {
    const cv   = document.getElementById('gdn-cv');
    const wrap = document.getElementById('gdn-cv-wrap');
    if (!cv || !wrap) return;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    if (cv.width!==W || cv.height!==H) { cv.width=W; cv.height=H; }
    const ctx = cv.getContext('2d');
    ctx.clearRect(0,0,W,H);
    _plantDelBtn = null;   // reset each frame

    // Background
    if (_bgReady && _bgImg) {
      const s0 = worldToScreen(0,0,_cam), s1 = worldToScreen(80,120,_cam);
      ctx.drawImage(_bgImg, 0,0,_bgImg.naturalWidth,_bgImg.naturalHeight, s0.x,s0.y, s1.x-s0.x, s1.y-s0.y);
    } else {
      const s0=worldToScreen(0,0,_cam), s1=worldToScreen(80,120,_cam);
      ctx.fillStyle='#1e3d1e'; ctx.fillRect(s0.x,s0.y,s1.x-s0.x,s1.y-s0.y);
    }

    if (_mode==='overview') _drawOverview(ctx,W,H);
    else                    _drawBedEdit(ctx,W,H);

    _drawScaleBar(ctx,W,H);
    _drawNorthArrow(ctx,W);
    if (_iMode==='polygon' && _polyPts.length) _drawPolyPreview(ctx);
    if (_iMode==='free_draw' && _freeDown && _freePts.length) _drawFreePreview(ctx);
  }

  // ── Overview ──────────────────────────────────────────────
  function _drawOverview(ctx) {
    state.beds.forEach(bed => {
      const verts = bedWorldVertices(bed);
      const isSel = bed.id === _selectedBedId;
      const isHov = bed.id === _hoveredBedId;
      _drawBedShape(ctx, bed, verts, isSel, isHov, false);

      // Draw individual plant icons in overview (scaled to zoom level)
      // Draw non-hovered plants first, hovered plant last (so label is on top)
      const hovP = _hoveredPlantId;
      bed.plants.filter(p => p.id !== hovP).forEach(plant => {
        const lp  = plantLocalPos(plant, bed);
        const wp2 = localToWorld(lp.x, lp.y, bed);
        const sp2 = worldToScreen(wp2.x, wp2.y, _cam);
        _drawPlant(ctx, plant, sp2.x, sp2.y, false, false, false);
      });
      // Hovered plant drawn last so tooltip is always on top
      const hovPlant = bed.plants.find(p => p.id === hovP);
      if (hovPlant) {
        const lp  = plantLocalPos(hovPlant, bed);
        const wp2 = localToWorld(lp.x, lp.y, bed);
        const sp2 = worldToScreen(wp2.x, wp2.y, _cam);
        _drawPlant(ctx, hovPlant, sp2.x, sp2.y, false, true, false);
      }
    });

    if (_hoveredBedId) {
      const bed   = _getBed(_hoveredBedId);
      const verts = bedWorldVertices(bed);
      const sp    = verts.map(v=>worldToScreen(v.x,v.y,_cam));
      const cx    = sp.reduce((s,p)=>s+p.x,0)/sp.length;
      const cy    = sp.reduce((s,p)=>s+p.y,0)/sp.length;
      const a     = polyArea(verts);
      _drawTooltip(ctx, `${bed.name} · ${Math.round(a)} sq ft · ${bed.plants.length} plants`, cx, cy-24);
    }
  }

  // ── Bed edit ──────────────────────────────────────────────
  function _drawBedEdit(ctx, W, H) {
    const bed = _getBed(_editBedId); if (!bed) return;
    const activeVerts = bedWorldVertices(bed);

    // 1. Other beds dimmed
    state.beds.forEach(b => {
      if (b.id === _editBedId) return;
      const v = bedWorldVertices(b);
      if (!v.length) return;
      const sp = v.map(p=>worldToScreen(p.x,p.y,_cam));
      ctx.beginPath(); ctx.moveTo(sp[0].x,sp[0].y);
      sp.slice(1).forEach(p=>ctx.lineTo(p.x,p.y)); ctx.closePath();
      ctx.fillStyle=b.color+'18'; ctx.fill();
      ctx.strokeStyle=b.color+'44'; ctx.lineWidth=1; ctx.stroke();
    });

    // 2. Vignette
    if (activeVerts.length>=3) {
      const sp=activeVerts.map(v=>worldToScreen(v.x,v.y,_cam));
      ctx.save();
      ctx.fillStyle='rgba(0,0,0,0.5)'; ctx.fillRect(0,0,W,H);
      ctx.globalCompositeOperation='destination-out';
      ctx.beginPath(); ctx.moveTo(sp[0].x,sp[0].y);
      sp.slice(1).forEach(p=>ctx.lineTo(p.x,p.y)); ctx.closePath(); ctx.fill();
      ctx.globalCompositeOperation='source-over';
      ctx.restore();
    }

    // 3. Active bed outline
    {
      const sp=activeVerts.map(v=>worldToScreen(v.x,v.y,_cam));
      if (sp.length) {
        ctx.beginPath(); ctx.moveTo(sp[0].x,sp[0].y);
        sp.slice(1).forEach(p=>ctx.lineTo(p.x,p.y)); ctx.closePath();
        ctx.strokeStyle=bed.color; ctx.lineWidth=2.5; ctx.setLineDash([]); ctx.stroke();
      }
    }

    // 3b. Trash icon at top-right corner of active bed bounding box
    {
      const sp = activeVerts.map(v => worldToScreen(v.x, v.y, _cam));
      const minX = Math.min(...sp.map(p=>p.x));
      const maxX = Math.max(...sp.map(p=>p.x));
      const minY = Math.min(...sp.map(p=>p.y));
      const TR   = { x: maxX + 6, y: minY - 6 };   // top-right, offset outward
      const BTN  = 22;  // button diameter px
      // Draw circle button
      const isHovTrash = _hovTrash;
      ctx.beginPath();
      ctx.arc(TR.x, TR.y, BTN/2, 0, Math.PI*2);
      ctx.fillStyle = isHovTrash ? '#c0392b' : 'rgba(200,50,50,0.85)';
      ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
      // Trash icon (simple unicode)
      ctx.font = '13px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#fff';
      ctx.fillText('🗑', TR.x, TR.y);
      // Store hit zone in screen space for mousedown/mousemove
      _trashBtn = { x: TR.x, y: TR.y, r: BTN/2 };
    }

    // 4. Grid points
    const gridPts = bedGridPoints(bed);
    const dotR    = Math.max(1.5, Math.min(3, _cam.z * GRID * 0.6));
    const isPlacing = _iMode === 'place_plant';

    // Find nearest grid point to mouse (for hover highlight when placing)
    let nearestGP = null;
    if (isPlacing) {
      const local = worldToLocal(_mouseW.x, _mouseW.y, bed);
      const origin = bedGridOrigin(bed);
      const { gi, gj } = localToGrid(local.x, local.y, origin);
      nearestGP = gridPts.find(p => p.gi===gi && p.gj===gj) || null;
    }

    gridPts.forEach(({ gi, gj, lx, ly }) => {
      const wp = localToWorld(lx, ly, bed);
      const sp = worldToScreen(wp.x, wp.y, _cam);
      const occupied = bed.plants.find(p => p.gi===gi && p.gj===gj);
      const isNearest = nearestGP && nearestGP.gi===gi && nearestGP.gj===gj;

      if (!occupied) {
        ctx.beginPath(); ctx.arc(sp.x, sp.y, isNearest ? dotR*2 : dotR, 0, Math.PI*2);
        ctx.fillStyle = isNearest ? 'rgba(47,122,75,0.7)' : 'rgba(255,255,255,0.22)';
        ctx.fill();
        if (isNearest) {
          ctx.strokeStyle='rgba(47,122,75,0.9)'; ctx.lineWidth=1.5; ctx.stroke();
        }
      }
    });

    // 5. Plants — non-hovered first, hovered last so label is always on top
    bed.plants.filter(p => p.id !== _hoveredPlantId && p.id !== _selectedPlantId).forEach(plant => {
      const lp  = plantLocalPos(plant, bed);
      const wp  = localToWorld(lp.x, lp.y, bed);
      const sp  = worldToScreen(wp.x, wp.y, _cam);
      const isDrg = _drag?.type==='plant' && _drag.plantId===plant.id;
      _drawPlant(ctx, plant, sp.x, sp.y, false, false, isDrg);
    });
    // Selected plant
    const selPlant = bed.plants.find(p => p.id === _selectedPlantId);
    if (selPlant) {
      const lp = plantLocalPos(selPlant, bed);
      const wp = localToWorld(lp.x, lp.y, bed);
      const sp = worldToScreen(wp.x, wp.y, _cam);
      _drawPlant(ctx, selPlant, sp.x, sp.y, true, false, _drag?.type==='plant' && _drag.plantId===selPlant.id);
    }
    // Hovered plant last (tooltip on top, don't double-draw if also selected)
    if (_hoveredPlantId && _hoveredPlantId !== _selectedPlantId) {
      const hovPlant = bed.plants.find(p => p.id === _hoveredPlantId);
      if (hovPlant) {
        const lp = plantLocalPos(hovPlant, bed);
        const wp = localToWorld(lp.x, lp.y, bed);
        const sp = worldToScreen(wp.x, wp.y, _cam);
        _drawPlant(ctx, hovPlant, sp.x, sp.y, false, true, false);
      }
    }

    // Drag preview for in-flight plant
    if (_drag?.type==='plant' && _drag.moved) {
      const sp = SPECIES.find(s=>s.id===_drag.speciesId);
      if (sp) {
        const r = Math.min(36, Math.max(4, (sp.dia/2)*_cam.z));
        const ms = worldToScreen(_mouseW.x,_mouseW.y,_cam);
        ctx.globalAlpha=0.45;
        ctx.beginPath(); ctx.arc(ms.x,ms.y,r,0,Math.PI*2);
        ctx.fillStyle='#2F7A4B'; ctx.fill();
        ctx.font=`${Math.max(10,r*0.9)}px sans-serif`;
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(sp.emoji, ms.x, ms.y);
        ctx.globalAlpha=1;
      }
    }

    // 6. Reshape handles
    if (_iMode==='reshape') _drawReshapeHandles(ctx, bed);
  }

  function _drawBedShape(ctx, bed, verts, isSel, isHov, isDrag) {
    if (!verts.length) return;
    const sp = verts.map(v=>worldToScreen(v.x,v.y,_cam));
    ctx.beginPath(); ctx.moveTo(sp[0].x,sp[0].y);
    sp.slice(1).forEach(p=>ctx.lineTo(p.x,p.y)); ctx.closePath();
    ctx.fillStyle = bed.color + (isDrag?'55':isSel?'aa':isHov?'66':'33'); ctx.fill();
    ctx.strokeStyle = bed.color; ctx.lineWidth = isSel||isHov ? 2.5 : 1.5;
    ctx.setLineDash([]); ctx.stroke();
    const cx=sp.reduce((s,p)=>s+p.x,0)/sp.length, cy=sp.reduce((s,p)=>s+p.y,0)/sp.length;
    const fs=Math.max(10,Math.min(14,_cam.z*1.8));
    ctx.font=`600 ${fs}px Inter,sans-serif`;
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.strokeStyle='rgba(0,0,0,.6)'; ctx.lineWidth=3;
    ctx.strokeText(bed.name,cx,cy);
    ctx.fillStyle='#fff'; ctx.fillText(bed.name,cx,cy);
  }

  function _drawPlant(ctx, plant, sx, sy, isSel, isHov, isDrag) {
    const sp      = SPECIES.find(s=>s.id===plant.speciesId)||{dia:1.5,emoji:'🌿'};
    const radiusFt = plant.radiusFt != null ? plant.radiusFt : sp.dia / 2;
    const r        = Math.min(36, Math.max(4, radiusFt * _cam.z));
    const h    = HEALTH[plant.health??2];
    ctx.globalAlpha = isDrag ? 0.4 : 1;
    if (isSel||isHov) {
      ctx.beginPath(); ctx.arc(sx,sy,r+5,0,Math.PI*2);
      ctx.strokeStyle=isSel?'#fff':'rgba(255,255,255,0.6)';
      ctx.lineWidth=isSel?2.5:1.5; ctx.stroke();
    }
    // Delete × button above selected plant
    if (isSel) {
      const bx = sx + r + 2, by = sy - r - 2, br = Math.max(8, r * 0.45);
      ctx.beginPath(); ctx.arc(bx, by, br, 0, Math.PI*2);
      ctx.fillStyle = '#c0392b'; ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
      ctx.font = `bold ${Math.max(9, br)}px sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#fff'; ctx.fillText('✕', bx, by);
      // Store delete hit zone for click handler
      _plantDelBtn = { x: bx, y: by, r: br, plantId: plant.id };
    }
    ctx.beginPath(); ctx.arc(sx,sy,r,0,Math.PI*2);
    ctx.fillStyle=h.col+'44'; ctx.fill();
    ctx.strokeStyle=isSel?'#fff':h.col; ctx.lineWidth=isSel?2:1; ctx.stroke();
    const fs=Math.max(10,r*0.9);
    ctx.font=`${fs}px sans-serif`; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(sp.emoji,sx,sy);
    // Only show name on hover — one at a time, no clutter
    if (isHov && plant.displayName) {
      const lfs = Math.max(10, r*0.6);
      ctx.font=`600 ${lfs}px Inter,sans-serif`;
      // Pill background
      const tw = ctx.measureText(plant.displayName).width;
      const ph = lfs+8, pw = tw+14;
      const px = sx - pw/2, py = sy+r+6;
      ctx.beginPath();
      ctx.roundRect(px, py, pw, ph, 4);
      ctx.fillStyle='rgba(0,0,0,.72)'; ctx.fill();
      ctx.fillStyle='#fff';
      ctx.textAlign='center'; ctx.textBaseline='top';
      ctx.fillText(plant.displayName, sx, py+4);
      ctx.textBaseline='middle';
    }
    ctx.globalAlpha=1;
  }

  function _drawReshapeHandles(ctx, bed) {
    if (bed.shape.type==='polygon') {
      bed.shape.vertices.forEach((v,i) => {
        const wp=localToWorld(v.x,v.y,bed), sp=worldToScreen(wp.x,wp.y,_cam);
        const isSel=_drag?.type==='vertex'&&_drag.vertIdx===i;
        ctx.beginPath(); ctx.arc(sp.x,sp.y,isSel?8:5,0,Math.PI*2);
        ctx.fillStyle=isSel?'#fff':bed.color; ctx.fill();
        ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.stroke();
        // Edge length label
        const next=bed.shape.vertices[(i+1)%bed.shape.vertices.length];
        const wn=localToWorld(next.x,next.y,bed);
        const dist=Math.hypot(wn.x-wp.x,wn.y-wp.y);
        const midW={x:(wp.x+wn.x)/2,y:(wp.y+wn.y)/2};
        const midS=worldToScreen(midW.x,midW.y,_cam);
        ctx.font='10px Inter,sans-serif'; ctx.fillStyle='rgba(255,255,255,0.8)';
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(dist.toFixed(1)+"'",midS.x,midS.y-9);
      });
    } else if (bed.shape.type==='rectangle') {
      const hw=bed.shape.width/2, hh=bed.shape.height/2;
      [[-hw,-hh],[hw,-hh],[hw,hh],[-hw,hh]].forEach(([lx,ly]) => {
        const wp=localToWorld(lx,ly,bed), sp=worldToScreen(wp.x,wp.y,_cam);
        ctx.beginPath(); ctx.arc(sp.x,sp.y,5,0,Math.PI*2);
        ctx.fillStyle=bed.color; ctx.fill();
        ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.stroke();
      });
      const wb=worldToScreen(bed.transform.x+hw,bed.transform.y,_cam);
      const hb=worldToScreen(bed.transform.x,bed.transform.y+hh,_cam);
      ctx.font='11px Inter,sans-serif'; ctx.fillStyle='rgba(255,255,255,.85)';
      ctx.textAlign='center'; ctx.textBaseline='middle';
      ctx.fillText(`W: ${bed.shape.width.toFixed(1)}'`,wb.x+28,wb.y);
      ctx.fillText(`H: ${bed.shape.height.toFixed(1)}'`,hb.x,hb.y+20);
    } else if (bed.shape.type==='circle') {
      const sp=worldToScreen(bed.transform.x,bed.transform.y,_cam);
      const rPx=bed.shape.radius*_cam.z;
      ctx.beginPath(); ctx.arc(sp.x,sp.y,rPx,0,Math.PI*2);
      ctx.strokeStyle='rgba(255,255,255,0.5)'; ctx.lineWidth=1; ctx.setLineDash([4,3]); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font='11px Inter,sans-serif'; ctx.fillStyle='rgba(255,255,255,.85)';
      ctx.textAlign='center'; ctx.textBaseline='middle';
      ctx.fillText(`R: ${bed.shape.radius.toFixed(1)}'`,sp.x,sp.y-rPx-12);
    }
  }

  function _drawPolyPreview(ctx) {
    const pts = _polyPts.map(p=>worldToScreen(p.x,p.y,_cam));
    const ms  = worldToScreen(_mouseW.x,_mouseW.y,_cam);
    ctx.save();
    ctx.strokeStyle='rgba(255,255,255,0.85)'; ctx.lineWidth=1.5; ctx.setLineDash([6,4]);
    ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y);
    pts.forEach(p=>ctx.lineTo(p.x,p.y)); ctx.lineTo(ms.x,ms.y); ctx.stroke();
    ctx.setLineDash([]);
    pts.forEach((p,i)=>{
      ctx.beginPath(); ctx.arc(p.x,p.y,i===0?7:4,0,Math.PI*2);
      ctx.fillStyle=i===0?'#fff':'rgba(255,255,255,0.7)'; ctx.fill();
    });
    ctx.restore();
  }

  function _drawFreePreview(ctx) {
    if (_freePts.length < 2) return;
    const pts = _freePts.map(p=>worldToScreen(p.x,p.y,_cam));
    ctx.save();
    ctx.strokeStyle='rgba(47,122,75,0.9)'; ctx.lineWidth=2; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y);
    pts.slice(1).forEach(p=>ctx.lineTo(p.x,p.y)); ctx.stroke();
    // Close preview line back to first
    ctx.setLineDash([4,3]); ctx.strokeStyle='rgba(47,122,75,0.5)';
    ctx.beginPath(); ctx.moveTo(pts[pts.length-1].x,pts[pts.length-1].y);
    ctx.lineTo(pts[0].x,pts[0].y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }

  function _drawTooltip(ctx, text, sx, sy) {
    ctx.save();
    ctx.font='500 11px Inter,sans-serif';
    const tw=ctx.measureText(text).width, pad=8;
    ctx.fillStyle='rgba(10,18,30,0.88)';
    ctx.beginPath(); ctx.roundRect(sx-tw/2-pad,sy-13,tw+pad*2,22,5); ctx.fill();
    ctx.fillStyle='#fff'; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(text,sx,sy-2);
    ctx.restore();
  }

  function _drawScaleBar(ctx,W,H) {
    const barPx=SCALE_BAR_FT*_cam.z, x=16, y=H-28;
    ctx.save();
    ctx.strokeStyle='rgba(255,255,255,0.75)'; ctx.lineWidth=2;
    ctx.beginPath();
    ctx.moveTo(x,y); ctx.lineTo(x+barPx,y);
    ctx.moveTo(x,y-4); ctx.lineTo(x,y+4);
    ctx.moveTo(x+barPx,y-4); ctx.lineTo(x+barPx,y+4);
    ctx.stroke();
    ctx.font='500 11px Inter,sans-serif'; ctx.fillStyle='rgba(255,255,255,0.75)';
    ctx.textAlign='center'; ctx.textBaseline='bottom';
    ctx.fillText(`${SCALE_BAR_FT} ft`, x+barPx/2, y-5);
    ctx.restore();
  }

  function _drawNorthArrow(ctx,W) {
    const x=W-24,y=28;
    ctx.save();
    ctx.font='700 12px Inter,sans-serif'; ctx.fillStyle='rgba(255,255,200,0.75)';
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText('N',x,y-10);
    ctx.strokeStyle='rgba(255,255,200,0.5)'; ctx.lineWidth=1.2;
    ctx.beginPath();
    ctx.moveTo(x,y-4); ctx.lineTo(x,y+8);
    ctx.moveTo(x-6,y+2); ctx.lineTo(x+6,y+2);
    ctx.stroke();
    ctx.restore();
  }

  // ════════════════════════════════════════════════════════════
  //  HIT TESTING
  // ════════════════════════════════════════════════════════════

  function _hitBed(wx, wy) {
    return [...state.beds].reverse().find(bed => {
      const v=bedWorldVertices(bed);
      return v.length>=3 && ptInPoly(wx,wy,v);
    }) || null;
  }

  function _hitPlant(wx, wy, bed) {
    return bed.plants.find(plant => {
      const lp=plantLocalPos(plant,bed);
      const wp=localToWorld(lp.x,lp.y,bed);
      const sp = SPECIES.find(s=>s.id===plant.speciesId)||{dia:1.5};
      const pr = plant.radiusFt != null ? plant.radiusFt : sp.dia/2;
      return Math.hypot(wp.x-wx,wp.y-wy) <= Math.max(pr, PLANT_HIT);
    }) || null;
  }

  // Snap world point to nearest valid grid point inside bed
  function _snapToGrid(wx, wy, bed) {
    const local  = worldToLocal(wx, wy, bed);
    const origin = bedGridOrigin(bed);
    const { gi, gj } = localToGrid(local.x, local.y, origin);
    const verts = shapeLocalVertices(bed.shape);
    const lp    = gridToLocal(gi, gj, origin);
    if (!ptInPoly(lp.x, lp.y, verts)) return null;
    return { gi, gj, lp };
  }

  function _hitVertex(wx, wy, bed) {
    if (bed.shape.type!=='polygon') return -1;
    const hitR = 10 / _cam.z;  // 10 screen-px → world feet
    return bed.shape.vertices.findIndex(v => {
      const wp = localToWorld(v.x, v.y, bed);
      return Math.hypot(wp.x - wx, wp.y - wy) <= hitR;
    });
  }

  // ════════════════════════════════════════════════════════════
  //  EVENTS
  // ════════════════════════════════════════════════════════════

  function _attachEvents() {
    const cv = document.getElementById('gdn-cv');
    if (!cv) return;
    cv.addEventListener('mousemove', _onMove);
    cv.addEventListener('mousedown', _onDown);
    cv.addEventListener('mouseup',   _onUp);
    cv.addEventListener('dblclick',  _onDbl);
    cv.addEventListener('wheel',     _onWheel, { passive:false });
    document.addEventListener('keydown', _onKey);
    window.addEventListener('resize',    render);
  }

  function _onMove(e) {
    const cv=document.getElementById('gdn-cv');
    const r=cv.getBoundingClientRect();
    const sx=e.clientX-r.left, sy=e.clientY-r.top;
    const w=screenToWorld(sx,sy,_cam);
    _mouseW=w;

    // Free-draw: accumulate points while button held
    if (_iMode==='free_draw' && _freeDown) {
      const last=_freePts[_freePts.length-1];
      if (!last || Math.hypot(w.x-last.x,w.y-last.y) >= FREE_MIN_DIST) {
        _freePts.push({x:w.x,y:w.y});
      }
      render(); return;
    }

    // Polygon preview: just rerender for the trailing line
    if (_iMode==='polygon' && _polyPts.length) { render(); return; }

    // Trash button hover detection (bed edit mode, screen-space)
    if (_mode==='bed_edit' && _trashBtn) {
      const wasHov = _hovTrash;
      _hovTrash = Math.hypot(sx-_trashBtn.x, sy-_trashBtn.y) <= _trashBtn.r;
      if (_hovTrash !== wasHov) render();
      if (_hovTrash) { document.getElementById('gdn-cv').style.cursor='pointer'; }
    }

    if (_drag) {
      const px=Math.hypot(sx-_drag.startSx,sy-_drag.startSy);
      if (px>=DRAG_THRESHOLD) _drag.moved=true;

      if (_drag.moved) {
        if (_drag.type==='pan') {
          _cam.x=_drag.camStart.x+(sx-_drag.startSx)/_cam.z;
          _cam.y=_drag.camStart.y+(sy-_drag.startSy)/_cam.z;
        } else if (_drag.type==='bed') {
          const bed=_getBed(_drag.bedId);
          if (bed) {
            bed.transform.x=_drag.bedStart.x+(w.x-_drag.wStart.x);
            bed.transform.y=_drag.bedStart.y+(w.y-_drag.wStart.y);
          }
        } else if (_drag.type==='vertex') {
          const bed=_getBed(_editBedId);
          if (bed?.shape.type==='polygon') {
            const local=worldToLocal(w.x,w.y,bed);
            bed.shape.vertices[_drag.vertIdx]={x:local.x,y:local.y};
          }
        } else if (_drag.type==='plant') {
          // Preview handled in render
        }
        render(); return;
      }
    }

    // Hover
    if (_mode==='overview') {
      const hit    = _hitBed(w.x,w.y);
      const n      = hit?.id??null;
      let plantHit = null;
      if (hit) {
        plantHit = _hitPlant(w.x, w.y, hit);
      }
      const newPlantId = plantHit?.id ?? null;
      const changed = n!==_hoveredBedId || newPlantId!==_hoveredPlantId;
      _hoveredBedId   = n;
      _hoveredPlantId = newPlantId;
      if (changed) render();
      cv.style.cursor = hit ? 'grab' : 'default';
    } else {
      // In edit mode — track hovered plant for name display
      const bed = _getBed(_editBedId);
      const ph  = bed ? _hitPlant(w.x, w.y, bed) : null;
      const newPid = ph?.id ?? null;
      if (newPid !== _hoveredPlantId) { _hoveredPlantId = newPid; render(); }
      if (_iMode==='place_plant') render(); // redraw grid hover
    }
  }

  function _onDown(e) {
    if (e.button!==0&&e.button!==1) return;
    const cv=document.getElementById('gdn-cv');
    const r=cv.getBoundingClientRect();
    const sx=e.clientX-r.left, sy=e.clientY-r.top;
    const w=screenToWorld(sx,sy,_cam);

    // Middle mouse / pan mode → pan
    if (e.button===1||_iMode==='pan') {
      _drag={type:'pan',startSx:sx,startSy:sy,camStart:{x:_cam.x,y:_cam.y},moved:false};
      cv.style.cursor='grabbing'; return;
    }

    // Free-draw: start capture
    if (_iMode==='free_draw') {
      _freeDown=true; _freePts=[{x:w.x,y:w.y}]; render(); return;
    }

    // Polygon: add click point
    if (_iMode==='polygon') {
      if (_polyPts.length>=3) {
        const f=_polyPts[0];
        if (Math.hypot(w.x-f.x,w.y-f.y)<1.5/_cam.z*15) { _finishPolygon(); return; }
      }
      _polyPts.push({x:w.x,y:w.y});
      render(); return;
    }

    // Overview interactions
    if (_mode==='overview') {
      const hit=_hitBed(w.x,w.y);
      if (hit) {
        _selectedBedId=hit.id;
        _drag={type:'bed',bedId:hit.id,startSx:sx,startSy:sy,
               wStart:{x:w.x,y:w.y},bedStart:{...hit.transform},moved:false};
        cv.style.cursor='grabbing';
      } else {
        _selectedBedId=null;
        _drag={type:'pan',startSx:sx,startSy:sy,camStart:{x:_cam.x,y:_cam.y},moved:false};
      }
      _updateSidebar(); render(); return;
    }

    // Bed edit interactions
    const bed=_getBed(_editBedId); if (!bed) return;

    // Trash button click (screen-space, before world-space tests)
    if (_trashBtn && Math.hypot(sx-_trashBtn.x, sy-_trashBtn.y) <= _trashBtn.r) {
      _showDeleteConfirm(_editBedId);
      return;
    }

    // Plant delete × button click (screen-space)
    if (_plantDelBtn && Math.hypot(sx-_plantDelBtn.x, sy-_plantDelBtn.y) <= _plantDelBtn.r) {
      deletePlant(_plantDelBtn.plantId);
      return;
    }

    const verts=bedWorldVertices(bed);

    // Click outside bed → exit
    if (verts.length>=3&&!ptInPoly(w.x,w.y,verts)) { _exitEdit(); return; }

    // Reshape
    if (_iMode==='reshape') {
      const vi=_hitVertex(w.x,w.y,bed);
      if (vi>=0) { _drag={type:'vertex',vertIdx:vi,startSx:sx,startSy:sy,moved:false}; return; }
      _drag={type:'pan',startSx:sx,startSy:sy,camStart:{x:_cam.x,y:_cam.y},moved:false}; return;
    }

    // Erase plant or grid-snapped position
    if (_iMode==='erase') {
      const hp=_hitPlant(w.x,w.y,bed);
      if (hp) {
        bed.plants=bed.plants.filter(p=>p.id!==hp.id);
        if (_selectedPlantId===hp.id) _selectedPlantId=null;
        _updateSidebar(); render();
      }
      return;
    }

    // Place plant
    if (_iMode==='place_plant') {
      const snap=_snapToGrid(w.x,w.y,bed);
      if (!snap) return;
      const occupied=bed.plants.find(p=>p.gi===snap.gi&&p.gj===snap.gj);
      if (occupied) {
        // Select existing plant instead of placing
        _selectedPlantId=occupied.id;
        _updateSidebar(); render(); return;
      }
      const sp=SPECIES.find(s=>s.id===_activeSpeciesId)||SPECIES[0];
      const plant={id:_uid('p'),speciesId:_activeSpeciesId,displayName:sp.name,
                   gi:snap.gi, gj:snap.gj, health:2, notes:''};
      bed.plants.push(plant);
      _selectedPlantId=plant.id;
      _updateSidebar(); render(); return;
    }

    // Select mode: hit plant?
    const hp=_hitPlant(w.x,w.y,bed);
    if (hp) {
      _selectedPlantId=hp.id;
      _drag={type:'plant',plantId:hp.id,speciesId:hp.speciesId,
             startSx:sx,startSy:sy,wStart:{x:w.x,y:w.y},moved:false};
      _updateSidebar(); render(); return;
    }

    // Deselect
    _selectedPlantId=null; _updateSidebar(); render();
  }

  function _onUp(e) {
    const cv=document.getElementById('gdn-cv');
    const r=cv.getBoundingClientRect();
    const sx=e.clientX-r.left, sy=e.clientY-r.top;
    const w=screenToWorld(sx,sy,_cam);

    // Free-draw release → finish shape
    if (_iMode==='free_draw'&&_freeDown) {
      _freeDown=false;
      _finishFreeDraw();
      return;
    }

    if (!_drag) return;

    if (_drag.type==='pan') {
      cv.style.cursor=_mode==='overview'?(_hoveredBedId?'grab':'default'):'default';
    }

    if (_drag.type==='plant'&&_drag.moved) {
      const bed=_getBed(_editBedId);
      if (bed) {
        const snap=_snapToGrid(w.x,w.y,bed);
        if (snap) {
          const plant=bed.plants.find(p=>p.id===_drag.plantId);
          if (plant) {
            const occupant=bed.plants.find(p=>p.gi===snap.gi&&p.gj===snap.gj&&p.id!==plant.id);
            if (occupant) { const og=plant.gi,oj=plant.gj; plant.gi=snap.gi;plant.gj=snap.gj;occupant.gi=og;occupant.gj=oj; }
            else { plant.gi=snap.gi; plant.gj=snap.gj; }
            _updateSidebar();
          }
        }
      }
    }

    _drag=null;
    render();
  }

  function _onDbl(e) {
    const cv=document.getElementById('gdn-cv');
    const r=cv.getBoundingClientRect();
    const w=screenToWorld(e.clientX-r.left,e.clientY-r.top,_cam);

    if (_mode==='overview') {
      const hit=_hitBed(w.x,w.y);
      if (hit) _enterEdit(hit.id);
      return;
    }

    if (_mode==='bed_edit') {
      const bed=_getBed(_editBedId); if (!bed) return;
      const hp=_hitPlant(w.x,w.y,bed);
      if (hp) { _openPlantModal(hp); return; }
      if (_iMode==='reshape' && bed.shape.type==='polygon') {
          const local   = worldToLocal(w.x, w.y, bed);
          const verts   = bed.shape.vertices;
          const edgeHitR = 16 / _cam.z;  // 16 screen-px expressed in world ft
          let minD = Infinity, ins = 0;
          for (let i = 0; i < verts.length; i++) {
            const a = verts[i], b = verts[(i+1) % verts.length];
            const d = ptSegDist(local.x, local.y, a.x, a.y, b.x, b.y);
            if (d < minD) { minD = d; ins = i + 1; }
          }
          if (minD < edgeHitR) {
            verts.splice(ins, 0, { x: local.x, y: local.y });
            window.toast?.('Vertex added — drag to reposition', 'success', 1500);
          }
          render();
        }
    }
  }

  function _onWheel(e) {
    e.preventDefault();
    const cv=document.getElementById('gdn-cv');
    const r=cv.getBoundingClientRect();
    const sx=e.clientX-r.left, sy=e.clientY-r.top, f=e.deltaY<0?1.12:0.89;
    const before=screenToWorld(sx,sy,_cam);
    _cam.z=Math.min(120,Math.max(0.5,_cam.z*f));
    const after=screenToWorld(sx,sy,_cam);
    _cam.x+=after.x-before.x; _cam.y+=after.y-before.y;
    render();
  }

  function _onKey(e) {
    if (e.key==='Escape') {
      if (_iMode==='polygon'&&_polyPts.length){_polyPts=[];render();return;}
      if (_iMode==='free_draw'){_freeDown=false;_freePts=[];render();return;}
      if (_mode==='bed_edit'){_exitEdit();return;}
    }
    if (e.key==='Enter'&&_iMode==='polygon'&&_polyPts.length>=3) _finishPolygon();
    if ((e.key==='Delete'||e.key==='Backspace')&&_selectedPlantId) {
      const bed=_getBed(_editBedId);
      if (bed){bed.plants=bed.plants.filter(p=>p.id!==_selectedPlantId);_selectedPlantId=null;_updateSidebar();render();}
    }
  }

  // ════════════════════════════════════════════════════════════
  //  FREE DRAW
  // ════════════════════════════════════════════════════════════

  function _finishFreeDraw() {
    if (_freePts.length < 4) {
      _freePts=[]; render();
      window.toast?.('Draw a larger shape (hold and drag)', 'error', 2000); return;
    }
    // 1. Simplify with RDP
    const simplified = _rdp(_freePts, SIMPLIFY_TOL);
    if (simplified.length < FREE_MIN_VERTS) {
      _freePts=[]; render();
      window.toast?.('Shape too simple — draw a larger area', 'error', 2000); return;
    }
    // 2. Compute centroid → local coords
    const cx=simplified.reduce((s,p)=>s+p.x,0)/simplified.length;
    const cy=simplified.reduce((s,p)=>s+p.y,0)/simplified.length;
    const localVerts=simplified.map(p=>({x:p.x-cx,y:p.y-cy}));
    // 3. Create bed
    _createBed({type:'polygon',vertices:localVerts},cx,cy);
    _freePts=[]; render();
  }

  // ════════════════════════════════════════════════════════════
  //  ADD BED / POLYGON
  // ════════════════════════════════════════════════════════════

  function _finishPolygon() {
    if (_polyPts.length<3){window.toast?.('Need at least 3 points','error');return;}
    const cx=_polyPts.reduce((s,p)=>s+p.x,0)/_polyPts.length;
    const cy=_polyPts.reduce((s,p)=>s+p.y,0)/_polyPts.length;
    _createBed({type:'polygon',vertices:_polyPts.map(p=>({x:p.x-cx,y:p.y-cy}))},cx,cy);
    _polyPts=[]; render();
  }

  function _createBed(shape, wx, wy) {
    const n=state.beds.length+1;
    const COLORS=['#5DCAA5','#378ADD','#D4537E','#EF9F27','#7F77DD','#1D9E75','#E24B4A','#639922'];
    const bed={
      id:_uid('b'), name:`Bed ${n}`, color:COLORS[state.beds.length%COLORS.length],
      transform:{x:wx,y:wy,rotation:0}, shape, plants:[],
    };
    state.beds.push(bed);
    _selectedBedId=bed.id;
    _updateSidebar(); render();
    window.toast?.(`${bed.name} created — double-click to edit`,'success');
  }

  function addBed(shapeType) {
    const wrap=document.getElementById('gdn-cv-wrap');
    const W=wrap.clientWidth,H=wrap.clientHeight;
    const wc=screenToWorld(W/2,H/2,_cam);

    if (shapeType==='free_draw') { _setIMode('free_draw'); return; }
    if (shapeType==='polygon')   { _polyPts=[]; _setIMode('polygon'); return; }
    if (shapeType==='rectangle') { _createBed({type:'rectangle',width:8,height:6},wc.x-4,wc.y-3); }
    if (shapeType==='circle')    { _createBed({type:'circle',radius:5},wc.x,wc.y); }
  }

  // ════════════════════════════════════════════════════════════
  //  EDIT MODE
  // ════════════════════════════════════════════════════════════

  function _enterEdit(bedId) {
    _editBedId=bedId; _mode='bed_edit'; _iMode='select';
    _selectedPlantId=null; _drag=null;
    // Switch panel to edit view
    _showPanelView('edit');
    const title = document.getElementById('gdn-panel-edit-title');
    if (title) title.textContent = _getBed(bedId)?.name || 'Edit bed';
    _setIMode('select');
    _fitBed(bedId); _updateSidebar(); render();
  }

  function _exitEdit() {
    _mode='overview'; _editBedId=null; _iMode='select';
    _selectedPlantId=null; _drag=null; _polyPts=[]; _freePts=[]; _freeDown=false;
    _trashBtn=null; _hovTrash=false;
    _showPanelView('overview');
    _renderBedList();
    render();
  }

  function _showDeleteConfirm(bedId) {
    const bed  = _getBed(bedId); if (!bed) return;
    const modal = document.getElementById('gdn-delete-confirm');
    if (!modal) return;
    const nameEl = modal.querySelector('#gdn-delete-bed-name');
    if (nameEl) nameEl.textContent = bed.name;
    modal.dataset.bedId = bedId;
    modal.classList.add('open');
  }

  function _confirmDeleteBed() {
    const modal = document.getElementById('gdn-delete-confirm');
    const bedId = modal?.dataset.bedId;
    if (!bedId) return;
    state.beds = state.beds.filter(b => b.id !== bedId);
    if (_selectedBedId === bedId) _selectedBedId = null;
    modal.classList.remove('open');
    _exitEdit();
    _updateSidebar();
    window.toast?.('Bed deleted', 'success');
  }

  function _cancelDeleteBed() {
    const modal = document.getElementById('gdn-delete-confirm');
    if (modal) modal.classList.remove('open');
  }

  function _setIMode(m) {
    _iMode=m; _polyPts=[]; _freePts=[]; _freeDown=false;
    ['select','reshape','place-plant','erase','pan','free-draw','polygon'].forEach(n=>{
      document.getElementById('gdn-btn-'+n)?.classList.toggle('on',n===m||n===m.replace('_','-'));
    });
    const cv=document.getElementById('gdn-cv');
    const cursors={pan:'grab',reshape:'move',free_draw:'crosshair',polygon:'crosshair',erase:'not-allowed'};
    if(cv) cv.style.cursor=cursors[m]||'default';
    render();
  }

  function _fitBed(bedId) {
    const bed=_getBed(bedId),wrap=document.getElementById('gdn-cv-wrap');
    if(!bed||!wrap) return;
    const verts=bedWorldVertices(bed);if(!verts.length)return;
    const b=polyBounds(verts),W=wrap.clientWidth,H=wrap.clientHeight,pad=60;
    const z=Math.min((W-pad*2)/(b.maxX-b.minX),(H-pad*2)/(b.maxY-b.minY),80);
    const cx=(b.minX+b.maxX)/2,cy=(b.minY+b.maxY)/2;
    _cam={z,x:W/(2*z)-cx,y:H/(2*z)-cy};
  }

  function _fitAll() {
    const wrap=document.getElementById('gdn-cv-wrap');if(!wrap)return;
    const W=wrap.clientWidth,H=wrap.clientHeight;
    _cam={z:Math.min(W/90,H/130),x:5,y:5};
  }

  // ════════════════════════════════════════════════════════════
  //  SIDEBAR
  // ════════════════════════════════════════════════════════════

  function _buildSpeciesDropdown() {
    const el=document.getElementById('gdn-species-dropdown');
    if(!el||el.dataset.built) return;
    el.dataset.built='1';
    const cats={};
    SPECIES.forEach(sp=>{if(!cats[sp.cat])cats[sp.cat]=[];cats[sp.cat].push(sp);});
    el.innerHTML='<option value="">— Choose species —</option>'+
      Object.entries(cats).map(([cat,sps])=>
        `<optgroup label="${cat}">${sps.map(sp=>
          `<option value="${sp.id}">${sp.emoji} ${sp.name} (${sp.dia}ft)</option>`
        ).join('')}</optgroup>`
      ).join('');
    el.onchange=()=>{
      if(el.value){_activeSpeciesId=el.value;_setIMode('place_plant');}
    };
  }

  function _updateSidebar() {
    const bed=_mode==='bed_edit'?_getBed(_editBedId):_getBed(_selectedBedId);
    const el=document.getElementById('gdn-plant-list-panel'); if(!el) return;

    if(!bed){el.innerHTML='<div class="gdn-empty">Select a bed or double-click to edit.</div>';return;}

    // Update bed name input
    const nameEl=document.getElementById('gdn-bed-name');
    if(nameEl&&nameEl!==document.activeElement)nameEl.value=bed.name;

    // Dimension panel
    const dimEl=document.getElementById('gdn-dim-panel');
    if(dimEl) _renderDimPanel(dimEl,bed);

    // Plant list
    const plants=bed.plants;
    el.innerHTML = plants.length
      ? plants.map(p=>{
          const sp=SPECIES.find(s=>s.id===p.speciesId);
          const h=HEALTH[p.health??2];
          const isSel=p.id===_selectedPlantId;
          return `<div class="gdn-plant-card${isSel?' sel':''}"
              onclick="gdn.selectPlant('${p.id}')"
              ondblclick="gdn._openModalById('${p.id}')">
            <div class="gdn-plant-health" style="background:${h.col}22;border-color:${h.col}55">
              <span style="font-size:14px">${sp?.emoji||'🌿'}</span></div>
            <div style="flex:1;min-width:0">
              <div class="gdn-plant-name">${p.displayName}</div>
              <div class="gdn-plant-meta">${h.icon} ${h.label}</div>
            </div>
              <button class="gdn-plant-del-btn" title="Delete plant"
              onclick="event.stopPropagation();gdn.deletePlant('${p.id}')">✕</button>
            <button class="gdn-plant-edit-btn" onclick="event.stopPropagation();gdn._openModalById('${p.id}')">⋯</button>
          </div>`;
        }).join('')
      : '<div class="gdn-empty">No plants yet. Choose a species then click a grid point.</div>';
  }

  function _renderDimPanel(el, bed) {
    if(bed.shape.type==='rectangle'){
      el.innerHTML=`<div class="gdn-field-lbl" style="margin-top:10px">Dimensions</div>
        <div class="gdn-dim-row"><label class="gdn-dim-lbl">Width (ft)</label>
          <input class="gdn-input" type="number" step="0.5" min="0.5" value="${bed.shape.width.toFixed(1)}"
            onchange="gdn.setDim('width',parseFloat(this.value))"/></div>
        <div class="gdn-dim-row"><label class="gdn-dim-lbl">Height (ft)</label>
          <input class="gdn-input" type="number" step="0.5" min="0.5" value="${bed.shape.height.toFixed(1)}"
            onchange="gdn.setDim('height',parseFloat(this.value))"/></div>`;
    } else if(bed.shape.type==='circle'){
      el.innerHTML=`<div class="gdn-field-lbl" style="margin-top:10px">Dimensions</div>
        <div class="gdn-dim-row"><label class="gdn-dim-lbl">Radius (ft)</label>
          <input class="gdn-input" type="number" step="0.5" min="0.5" value="${bed.shape.radius.toFixed(1)}"
            onchange="gdn.setDim('radius',parseFloat(this.value))"/></div>`;
    } else {
      const a=polyArea(bed.shape.vertices);
      el.innerHTML=`<div class="gdn-field-lbl" style="margin-top:10px">Dimensions</div>
        <div style="font-size:11px;color:var(--text2);margin-top:4px">Polygon · ${bed.shape.vertices.length} vertices<br/>≈ ${a.toFixed(0)} sq ft</div>`;
    }
  }

  // ════════════════════════════════════════════════════════════
  //  PLANT MODAL
  // ════════════════════════════════════════════════════════════

  function _openPlantModal(plant) {
    const modal=document.getElementById('gdn-plant-modal'); if(!modal) return;
    const sp = SPECIES.find(s=>s.id===plant.speciesId)||{dia:1.5};
    modal.querySelector('#pm-name').value    = plant.displayName||'';
    modal.querySelector('#pm-species').value = plant.speciesId||'';
    modal.querySelector('#pm-notes').value   = plant.notes||'';
    modal.querySelector('#pm-age').value     = plant.age||0;
    modal.querySelector('#pm-health').value  = plant.health??2;
    // Radius: use override if set, else species default
    const r = plant.radiusFt != null ? plant.radiusFt : sp.dia/2;
    const rEl = modal.querySelector('#pm-radius');
    if (rEl) rEl.value = r.toFixed(2);
    // Show species default as placeholder hint
    const hint = modal.querySelector('#pm-radius-hint');
    if (hint) hint.textContent = `Species default: ${(sp.dia/2).toFixed(1)} ft`;
    _updateHealthLabel(plant.health??2);
    modal.dataset.plantId=plant.id;
    modal.style.display='flex';
  }

  function _openModalById(id) {
    const bed=_getBed(_editBedId)||_getBed(_selectedBedId);
    const p=bed?.plants.find(p=>p.id===id);
    if(p)_openPlantModal(p);
  }

  function deletePlant(plantId) {
    const bed = _getBed(_editBedId) || _getBed(_selectedBedId);
    if (!bed) return;
    bed.plants = bed.plants.filter(p => p.id !== plantId);
    if (_selectedPlantId === plantId) _selectedPlantId = null;
    _updateSidebar();
    render();
  }

  function savePlantDetail() {
    const modal=document.getElementById('gdn-plant-modal'); if(!modal)return;
    const bed=_getBed(_editBedId)||_getBed(_selectedBedId);
    const p=bed?.plants.find(p=>p.id===modal.dataset.plantId); if(!p)return;
    p.displayName = modal.querySelector('#pm-name').value;
    p.notes       = modal.querySelector('#pm-notes').value;
    p.age         = parseFloat(modal.querySelector('#pm-age').value)||0;
    p.health      = parseInt(modal.querySelector('#pm-health').value);
    // Radius override — null means use species default
    const rVal  = parseFloat(modal.querySelector('#pm-radius')?.value);
    const sp    = SPECIES.find(s=>s.id===p.speciesId)||{dia:1.5};
    const defR  = sp.dia/2;
    p.radiusFt  = (!isNaN(rVal) && Math.abs(rVal-defR)>0.01) ? Math.max(0.1,rVal) : null;
    modal.style.display='none';
    _updateSidebar(); render();
  }

  function closePlantModal() { document.getElementById('gdn-plant-modal')?.classList.remove('open'); }
  function _updateHealthLabel(v){const h=HEALTH[parseInt(v)]||HEALTH[2];const el=document.getElementById('pm-health-lbl');if(el)el.textContent=h.icon+' '+h.label;}

  // ════════════════════════════════════════════════════════════
  //  HELPERS
  // ════════════════════════════════════════════════════════════

  function _getBed(id) { return state.beds.find(b=>b.id===id)||null; }

  // ════════════════════════════════════════════════════════════
  //  PUBLIC API
  // ════════════════════════════════════════════════════════════

  // ── Panel view switching ──────────────────────────────────────────────────────
  function _showPanelView(view) {
    const overview = document.getElementById('gdn-panel-overview');
    const edit     = document.getElementById('gdn-panel-edit');
    if (overview) overview.style.display = view === 'overview' ? 'flex' : 'none';
    if (edit)     edit.style.display     = view === 'edit'     ? 'flex' : 'none';
  }

  function _renderBedList() {
    const list  = document.getElementById('gdn-bed-list');
    const count = document.getElementById('gdn-bed-count');
    if (!list) return;
    const beds = state.beds || [];
    if (count) count.textContent = beds.length ? `${beds.length} bed${beds.length!==1?'s':''}` : '';
    if (!beds.length) {
      list.innerHTML = '<div class="gdn-no-beds">No beds yet — draw one above</div>';
      return;
    }
    list.innerHTML = beds.map((b, i) => `
      <div class="gdn-bed-card" id="gdn-bed-card-${b.id}"
        draggable="true"
        ondragstart="gdn._bedDragStart(event,${i})"
        ondragover="gdn._bedDragOver(event,${i})"
        ondrop="gdn._bedDrop(event,${i})"
        ondragend="gdn._bedDragEnd()"
        onclick="gdn._clickBedCard('${b.id}')">
        <span class="gdn-bed-card-grip">⠿</span>
        <div class="gdn-bed-card-body">
          <div class="gdn-bed-card-name">${b.name || 'Unnamed bed'}</div>
          <div class="gdn-bed-card-meta">${b.plants?.length||0} plant${(b.plants?.length||0)!==1?'s':''}</div>
        </div>
        <button class="gdn-bed-card-edit" onclick="event.stopPropagation();gdn._clickBedCard('${b.id}')">&#9998;</button>
      </div>`).join('');
  }

  function _clickBedCard(bedId) { _enterEdit(bedId); }

  let _bedDragIdx = null;

  function _bedDragStart(e, idx) {
    _bedDragIdx = idx;
    e.dataTransfer.effectAllowed = 'move';
    setTimeout(() => {
      if (state.beds[idx]) document.getElementById('gdn-bed-card-' + state.beds[idx].id)?.classList.add('dragging');
    }, 0);
  }

  function _bedDragOver(e, idx) {
    e.preventDefault();
    if (_bedDragIdx === null || _bedDragIdx === idx) return;
    const card = state.beds[idx] ? document.getElementById('gdn-bed-card-' + state.beds[idx].id) : null;
    const rect = card?.getBoundingClientRect();
    document.querySelectorAll('.gdn-bed-drop-above,.gdn-bed-drop-below')
      .forEach(n => n.classList.remove('gdn-bed-drop-above','gdn-bed-drop-below'));
    card?.classList.add(e.clientY < rect?.top + rect?.height / 2 ? 'gdn-bed-drop-above' : 'gdn-bed-drop-below');
  }

  function _bedDrop(e, toIdx) {
    e.preventDefault();
    document.querySelectorAll('.gdn-bed-drop-above,.gdn-bed-drop-below')
      .forEach(n => n.classList.remove('gdn-bed-drop-above','gdn-bed-drop-below'));
    if (_bedDragIdx === null || _bedDragIdx === toIdx) return;
    const card = state.beds[toIdx] ? document.getElementById('gdn-bed-card-' + state.beds[toIdx].id) : null;
    const rect = card?.getBoundingClientRect();
    const before = e.clientY < rect?.top + rect?.height / 2;
    const insert = before ? toIdx : toIdx + 1;
    const [moved] = state.beds.splice(_bedDragIdx, 1);
    state.beds.splice(insert > _bedDragIdx ? insert - 1 : insert, 0, moved);
    _bedDragIdx = null;
    _renderBedList(); render();
  }

  function _bedDragEnd() {
    _bedDragIdx = null;
    document.querySelectorAll('.dragging,.gdn-bed-drop-above,.gdn-bed-drop-below')
      .forEach(n => n.classList.remove('dragging','gdn-bed-drop-above','gdn-bed-drop-below'));
  }

  return {
    init, onAdminLogin, save, isDirty,
    goBack()  { _exitEdit(); },
    _clickBedCard, _bedDragStart, _bedDragOver, _bedDrop, _bedDragEnd,
    _renderBedList,
    setMode(m){ _setIMode(m); },

    addBed,

    saveBedMeta() {
      const bed=_getBed(_editBedId); if(!bed)return;
      bed.name=document.getElementById('gdn-bed-name')?.value||bed.name;
      const bc=document.getElementById('gdn-bed-breadcrumb');if(bc)bc.textContent=bed.name;
      render();
    },
    setDim(field,val){
      const bed=_getBed(_editBedId);if(!bed)return;
      if(field==='width') bed.shape.width =Math.max(0.5,val);
      if(field==='height')bed.shape.height=Math.max(0.5,val);
      if(field==='radius')bed.shape.radius=Math.max(0.5,val);
      render(); _updateSidebar();
    },
    deleteBed(id){ _showDeleteConfirm(id); },
    deleteCurrentBed(){ if(_editBedId) _showDeleteConfirm(_editBedId); },
    deletePlant,
    confirmDeleteBed: _confirmDeleteBed,
    cancelDeleteBed:  _cancelDeleteBed,
    selectPlant(id){_selectedPlantId=id;_updateSidebar();render();},
    _openModalById,
    savePlantDetail, closePlantModal,
    updateHealthLabel(v){_updateHealthLabel(v);},
    showTab(tab){
      ['info','plants'].forEach(t=>{
        const p=document.getElementById('gdn-panel-'+t);if(p)p.style.display=t===tab?'block':'none';
        document.getElementById('gdn-tab-'+t)?.classList.toggle('on',t===tab);
      });
    },
    zoomBy(f){
      const wrap=document.getElementById('gdn-cv-wrap');
      const W=wrap.clientWidth,H=wrap.clientHeight;
      const before=screenToWorld(W/2,H/2,_cam);
      _cam.z=Math.min(120,Math.max(0.5,_cam.z*f));
      const after=screenToWorld(W/2,H/2,_cam);
      _cam.x+=after.x-before.x;_cam.y+=after.y-before.y;
      render();
    },
    resetView(){ if(_mode==='bed_edit'&&_editBedId)_fitBed(_editBedId); else _fitAll(); render(); },
    SPECIES,HEALTH,
    getBedList(){return state.beds;},
    getState(){return state;},
  };
})();

window.gdn=window.VW.Garden;
