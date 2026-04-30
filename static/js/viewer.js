/**
 * viewer.js — VirtuWill Garden Viewer
 *
 * Read-only canvas render of the planner data.
 * Displayed at the top of page-garden for all visitors.
 * Supports pan, zoom, and hover tooltips.
 * No edit tools — uses getState() from garden.js via window.gdn.
 */
'use strict';
window.GDN = window.GDN || {};

window.GDN.Viewer = (() => {

  // ── State ──────────────────────────────────────────────────────────────────
  let _cam     = { x: 0, y: 0, z: 5 };
  let _bgImg   = null;
  let _bgReady = false;
  let _drag    = null;
  let _inited  = false;
  let _hovBedId    = null;
  let _hovPlantId  = null;
  let _tooltip     = { bedId: null, plantId: null };
  let _viewerState = { beds: [] };   // own copy — doesn't depend on planner being loaded

  const GRID = 0.25;
  const HEALTH = [
    { icon:'💀', col:'#6b2f2f' },
    { icon:'🥀', col:'#8b6a3a' },
    { icon:'🌱', col:'#3d7a2f' },
    { icon:'🌸', col:'#b05580' },
    { icon:'🌺', col:'#c43030' },
  ];

  // ── Geometry helpers (duplicated from garden.js for isolation) ─────────────
  function w2s(wx, wy) {
    return { x: (wx + _cam.x) * _cam.z, y: (wy + _cam.y) * _cam.z };
  }
  function s2w(sx, sy) {
    return { x: sx / _cam.z - _cam.x, y: sy / _cam.z - _cam.y };
  }
  function _rotate(x, y, cos, sin) {
    return { x: x*cos - y*sin, y: x*sin + y*cos };
  }
  function _bedVerts(bed) {
    const cos = Math.cos(bed.transform.rotation * Math.PI/180);
    const sin = Math.sin(bed.transform.rotation * Math.PI/180);
    const { type } = bed.shape;
    let locals = [];
    if (type === 'rectangle') {
      const hw = bed.shape.width/2, hh = bed.shape.height/2;
      locals = [[-hw,-hh],[hw,-hh],[hw,hh],[-hw,hh]].map(([x,y])=>({x,y}));
    } else if (type === 'circle') {
      const r = bed.shape.radius, n = 32;
      locals = Array.from({length:n},(_,i)=>({ x:r*Math.cos(2*Math.PI*i/n), y:r*Math.sin(2*Math.PI*i/n) }));
    } else {
      locals = bed.shape.vertices || [];
    }
    return locals.map(v => {
      const r = _rotate(v.x, v.y, cos, sin);
      return { x: r.x + bed.transform.x, y: r.y + bed.transform.y };
    });
  }
  function _plantWorldPos(plant, bed) {
    const origin = _bedGridOrigin(bed);
    const lx = origin.x + plant.gi * GRID;
    const ly = origin.y + plant.gj * GRID;
    const cos = Math.cos(bed.transform.rotation * Math.PI/180);
    const sin = Math.sin(bed.transform.rotation * Math.PI/180);
    const r = _rotate(lx, ly, cos, sin);
    return { x: r.x + bed.transform.x, y: r.y + bed.transform.y };
  }
  function _bedGridOrigin(bed) {
    const { type } = bed.shape;
    if (type === 'rectangle') return { x: -bed.shape.width/2, y: -bed.shape.height/2 };
    if (type === 'circle')    return { x: -bed.shape.radius,  y: -bed.shape.radius  };
    const vs = bed.shape.vertices || [];
    if (!vs.length) return { x:0, y:0 };
    const xs = vs.map(v=>v.x), ys = vs.map(v=>v.y);
    return { x: (Math.min(...xs)+Math.max(...xs))/2, y: (Math.min(...ys)+Math.max(...ys))/2 };
  }
  function _ptInPoly(px, py, verts) {
    let inside = false;
    for (let i=0,j=verts.length-1; i<verts.length; j=i++) {
      const xi=verts[i].x,yi=verts[i].y,xj=verts[j].x,yj=verts[j].y;
      if (((yi>py)!==(yj>py))&&(px<(xj-xi)*(py-yi)/(yj-yi)+xi)) inside=!inside;
    }
    return inside;
  }

  // ── Species lookup ─────────────────────────────────────────────────────────
  function _species(id) {
    return (window.gdn?.SPECIES || []).find(s => s.id === id) || { dia:1.5, emoji:'🌿', name:'Plant' };
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    // Always reload data so viewer reflects latest saved state
    _loadData();
    if (_inited) return;
    _inited = true;
    _bgImg = new Image();
    _bgImg.onload  = () => { _bgReady = true; render(); };
    _bgImg.onerror = () => { _bgReady = false; };
    _bgImg.src = '/static/garden_illustrated.png';
    _attachEvents();
    _initResizer();
  }

  async function _loadData() {
    try {
      const r = await fetch('/api/garden');
      const d = await r.json();
      if (d.beds?.length) {
        _viewerState = d;
      }
    } catch { /* keep empty state */ }
    _fitAll();
    render();
  }

  function _fitAll() {
    const cv   = document.getElementById('gdn-viewer-cv');
    const wrap = document.getElementById('gdn-viewer-frame');
    if (!cv || !wrap) return;
    const W = wrap.clientWidth, H = cv.clientHeight || 340;
    const scaleX = W / 80, scaleY = H / 120;
    _cam.z = Math.min(scaleX, scaleY) * 0.88;
    _cam.x = (W / _cam.z - 80) / 2;
    _cam.y = (H / _cam.z - 120) / 2;
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  function render() {
    const cv   = document.getElementById('gdn-viewer-cv');
    const wrap = document.getElementById('gdn-viewer-frame');
    if (!cv || !wrap) return;
    const W = wrap.clientWidth;
    const H = cv.clientHeight || 340;
    if (cv.width!==W || cv.height!==H) { cv.width=W; cv.height=H; }
    const ctx = cv.getContext('2d');
    ctx.clearRect(0,0,W,H);

    // Background
    if (_bgReady && _bgImg) {
      const s0=w2s(0,0), s1=w2s(80,120);
      ctx.drawImage(_bgImg,0,0,_bgImg.naturalWidth,_bgImg.naturalHeight,s0.x,s0.y,s1.x-s0.x,s1.y-s0.y);
    } else {
      const s0=w2s(0,0),s1=w2s(80,120);
      ctx.fillStyle='#2a4a2a'; ctx.fillRect(s0.x,s0.y,s1.x-s0.x,s1.y-s0.y);
    }

    // Use own fetched state; if planner is loaded and has beds, prefer its live state
    const gdnState = window.gdn?.getState?.();
    const state    = (gdnState?.beds?.length ? gdnState : _viewerState);
    const HPID  = _hovPlantId;

    state.beds.forEach(bed => {
      const verts = _bedVerts(bed);
      const isHov = bed.id === _hovBedId;
      _drawBed(ctx, bed, verts, isHov);

      // Plants — non-hovered first
      bed.plants.filter(p => p.id !== HPID).forEach(plant => {
        const wp = _plantWorldPos(plant, bed);
        const sp = w2s(wp.x, wp.y);
        _drawViewerPlant(ctx, plant, sp.x, sp.y, false);
      });
    });

    // Hovered plant drawn last so tooltip is always on top of everything
    if (HPID) {
      state.beds.forEach(bed => {
        const p = bed.plants.find(p => p.id === HPID);
        if (!p) return;
        const wp = _plantWorldPos(p, bed);
        const sp = w2s(wp.x, wp.y);
        _drawViewerPlant(ctx, p, sp.x, sp.y, true);
      });
    }
  }

  function _drawBed(ctx, bed, verts, isHov) {
    if (!verts.length) return;
    const sp = verts.map(v => w2s(v.x, v.y));
    ctx.beginPath(); ctx.moveTo(sp[0].x, sp[0].y);
    sp.slice(1).forEach(p => ctx.lineTo(p.x, p.y));
    ctx.closePath();
    ctx.fillStyle   = bed.color + (isHov ? 'aa' : '44');
    ctx.fill();
    ctx.strokeStyle = bed.color;
    ctx.lineWidth   = isHov ? 2.5 : 1.5;
    ctx.setLineDash([]);
    ctx.stroke();

    // Bed name label (always shown at viewer zoom levels)
    const cx = sp.reduce((s,p)=>s+p.x,0)/sp.length;
    const cy = sp.reduce((s,p)=>s+p.y,0)/sp.length;
    const fs = Math.max(9, Math.min(13, _cam.z * 1.6));
    ctx.font = `600 ${fs}px Inter,sans-serif`;
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.strokeStyle='rgba(0,0,0,.6)'; ctx.lineWidth=3;
    ctx.strokeText(bed.name, cx, cy);
    ctx.fillStyle='#fff'; ctx.fillText(bed.name, cx, cy);
  }

  function _drawViewerPlant(ctx, plant, sx, sy, isHov) {
    const sp  = _species(plant.speciesId);
    const r   = Math.min(28, Math.max(3, (sp.dia/2) * _cam.z));
    const h   = HEALTH[plant.health ?? 2];

    if (isHov) {
      ctx.beginPath(); ctx.arc(sx,sy,r+4,0,Math.PI*2);
      ctx.strokeStyle='rgba(255,255,255,0.7)'; ctx.lineWidth=1.5; ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(sx,sy,r,0,Math.PI*2);
    ctx.fillStyle  = h.col+'44'; ctx.fill();
    ctx.strokeStyle= h.col; ctx.lineWidth=1; ctx.stroke();
    const fs = Math.max(8, r*0.9);
    ctx.font=`${fs}px sans-serif`; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(sp.emoji, sx, sy);

    // Name pill — only on hover
    if (isHov && plant.displayName) {
      const lfs = Math.max(10, r*0.7);
      ctx.font=`600 ${lfs}px Inter,sans-serif`;
      const tw  = ctx.measureText(plant.displayName).width;
      const ph  = lfs+8, pw = tw+14;
      const px  = sx - pw/2, py = sy+r+5;
      ctx.beginPath();
      ctx.roundRect(px, py, pw, ph, 4);
      ctx.fillStyle='rgba(0,0,0,.78)'; ctx.fill();
      ctx.fillStyle='#fff'; ctx.textBaseline='top';
      ctx.fillText(plant.displayName, sx, py+4);
      ctx.textBaseline='middle';
    }
  }

  // ── Tooltip (HTML overlay for bed hover) ──────────────────────────────────
  function _showTooltip(text, sx, sy) {
    const el = document.getElementById('gdn-viewer-tooltip');
    const fr = document.getElementById('gdn-viewer-frame');
    if (!el || !fr) return;
    const frRect = fr.getBoundingClientRect();
    el.textContent = text;
    el.style.display = 'block';
    el.style.left = Math.min(sx + 10, fr.clientWidth - 180) + 'px';
    el.style.top  = Math.max(sy - 36, 8) + 'px';
  }
  function _hideTooltip() {
    const el = document.getElementById('gdn-viewer-tooltip');
    if (el) el.style.display = 'none';
  }

  // ── Events ──────────────────────────────────────────────────────────────────
  function _attachEvents() {
    const cv = document.getElementById('gdn-viewer-cv');
    if (!cv) return;
    cv.addEventListener('wheel',     _onWheel, { passive:false });
    cv.addEventListener('mousedown', _onDown);
    cv.addEventListener('mousemove', _onMove);
    cv.addEventListener('mouseup',   _onUp);
    cv.addEventListener('mouseleave',_onLeave);
    window.addEventListener('resize', () => { _fitAll(); render(); });

    // ── Touch: pan + pinch-zoom ───────────────────────────────────────────
    let _t0 = null, _t1 = null, _pinchDist0 = 0;

    cv.addEventListener('touchstart', e => {
      e.preventDefault();
      if (e.touches.length === 1) {
        const t = e.touches[0];
        _t0 = { x: t.clientX, y: t.clientY, cx: _cam.x, cy: _cam.y };
        _t1 = null;
      } else if (e.touches.length === 2) {
        const a = e.touches[0], b = e.touches[1];
        _pinchDist0 = Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
        _t0 = { midX: (a.clientX+b.clientX)/2, midY: (a.clientY+b.clientY)/2,
                cx: _cam.x, cy: _cam.y, z: _cam.z };
        _t1 = true;
      }
    }, { passive: false });

    cv.addEventListener('touchmove', e => {
      e.preventDefault();
      if (_t1 && e.touches.length === 2) {
        // Pinch zoom
        const a = e.touches[0], b = e.touches[1];
        const dist = Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
        const factor = dist / _pinchDist0;
        const rect = cv.getBoundingClientRect();
        const mx = _t0.midX - rect.left, my = _t0.midY - rect.top;
        const bef = s2w(mx, my);
        _cam.z = Math.min(40, Math.max(1, _t0.z * factor));
        const aft = s2w(mx, my);
        _cam.x = _t0.cx + (aft.x - bef.x);
        _cam.y = _t0.cy + (aft.y - bef.y);
        render();
      } else if (_t0 && !_t1 && e.touches.length === 1) {
        // Pan
        const t = e.touches[0];
        _cam.x = _t0.cx + (t.clientX - _t0.x) / _cam.z;
        _cam.y = _t0.cy + (t.clientY - _t0.y) / _cam.z;
        render();
      }
    }, { passive: false });

    cv.addEventListener('touchend', e => {
      if (e.touches.length === 0) { _t0 = null; _t1 = null; }
    }, { passive: true });
  }

  function _onWheel(e) {
    e.preventDefault();
    const cv  = document.getElementById('gdn-viewer-cv');
    const r   = cv.getBoundingClientRect();
    const sx  = e.clientX - r.left, sy = e.clientY - r.top;
    const f   = e.deltaY < 0 ? 1.12 : 0.89;
    const bef = s2w(sx, sy);
    _cam.z    = Math.min(40, Math.max(1, _cam.z * f));
    const aft = s2w(sx, sy);
    _cam.x   += aft.x - bef.x;
    _cam.y   += aft.y - bef.y;
    render();
  }

  function _onDown(e) {
    const cv = document.getElementById('gdn-viewer-cv');
    const r  = cv.getBoundingClientRect();
    _drag = { sx: e.clientX-r.left, sy: e.clientY-r.top, cx: _cam.x, cy: _cam.y, moved:false };
    cv.style.cursor = 'grabbing';
  }

  function _onMove(e) {
    const cv = document.getElementById('gdn-viewer-cv');
    const r  = cv.getBoundingClientRect();
    const sx = e.clientX-r.left, sy = e.clientY-r.top;
    const w  = s2w(sx, sy);

    if (_drag) {
      const dx = sx - _drag.sx, dy = sy - _drag.sy;
      if (Math.hypot(dx,dy) > 3) _drag.moved = true;
      if (_drag.moved) {
        _cam.x = _drag.cx + dx / _cam.z;
        _cam.y = _drag.cy + dy / _cam.z;
        render(); return;
      }
    }

    // Hit detection
    const gdnSt = window.gdn?.getState?.();
    const state = (gdnSt?.beds?.length ? gdnSt : _viewerState);
    let newBedId = null, newPlantId = null;

    for (const bed of state.beds) {
      const verts = _bedVerts(bed);
      if (verts.length >= 3 && _ptInPoly(w.x, w.y, verts)) {
        newBedId = bed.id;
        // Check plants within this bed
        for (const plant of bed.plants) {
          const wp = _plantWorldPos(plant, bed);
          const sp = _species(plant.speciesId);
          const pr = plant.radiusFt ?? (sp.dia/2);
          if (Math.hypot(wp.x-w.x, wp.y-w.y) <= Math.max(pr, 0.5)) {
            newPlantId = plant.id; break;
          }
        }
        break;
      }
    }

    const changed = newBedId !== _hovBedId || newPlantId !== _hovPlantId;
    _hovBedId   = newBedId;
    _hovPlantId = newPlantId;
    if (changed) render();

    // HTML tooltip for bed info (when not hovering a plant)
    if (newBedId && !newPlantId) {
      const bed = state.beds.find(b => b.id === newBedId);
      if (bed) {
        const pc = bed.plants.length;
        _showTooltip(`${bed.name} · ${pc} plant${pc!==1?'s':''}`, sx, sy);
        cv.style.cursor = 'pointer';
      }
    } else {
      _hideTooltip();
      cv.style.cursor = _drag ? 'grabbing' : (newBedId ? 'crosshair' : 'grab');
    }
  }

  function _onUp(e) {
    const cv = document.getElementById('gdn-viewer-cv');
    _drag = null;
    cv.style.cursor = 'grab';
  }

  function _onLeave() {
    _drag = null;
    _hovBedId = null; _hovPlantId = null;
    _hideTooltip();
    render();
  }

  // ── Resize handle ──────────────────────────────────────────────────────────
  function _initResizer() {
    const handle = document.getElementById('gdn-viewer-resizer');
    const frame  = document.getElementById('gdn-viewer-frame');
    if (!handle || !frame) return;

    let _resizing = false;
    let _startY   = 0;
    let _startH   = 0;
    const MIN_H   = 180;
    const MAX_H   = Math.round(window.innerHeight * 0.8);

    handle.addEventListener('mousedown', e => {
      e.preventDefault();
      e.stopPropagation();
      _resizing = true;
      _startY   = e.clientY;
      _startH   = frame.offsetHeight;
      document.body.style.cursor     = 'ns-resize';
      document.body.style.userSelect = 'none';
    });

    // Touch support
    handle.addEventListener('touchstart', e => {
      _resizing = true;
      _startY   = e.touches[0].clientY;
      _startH   = frame.offsetHeight;
    }, { passive: true });

    document.addEventListener('mousemove', e => {
      if (!_resizing) return;
      const dy  = e.clientY - _startY;
      const newH = Math.min(MAX_H, Math.max(MIN_H, _startH + dy));
      frame.style.height = newH + 'px';
      render();   // redraw canvas at new size
    });

    document.addEventListener('touchmove', e => {
      if (!_resizing) return;
      const dy   = e.touches[0].clientY - _startY;
      const newH = Math.min(MAX_H, Math.max(MIN_H, _startH + dy));
      frame.style.height = newH + 'px';
      render();
    }, { passive: true });

    document.addEventListener('mouseup', () => {
      if (!_resizing) return;
      _resizing = false;
      document.body.style.cursor     = '';
      document.body.style.userSelect = '';
    });

    document.addEventListener('touchend', () => { _resizing = false; });
  }


  function zoom(factor) {
    const cv = document.getElementById('gdn-viewer-cv');
    if (!cv) return;
    const cx = cv.clientWidth/2, cy = cv.clientHeight/2;
    const bef = s2w(cx, cy);
    _cam.z = Math.min(40, Math.max(1, _cam.z * factor));
    const aft = s2w(cx, cy);
    _cam.x += aft.x - bef.x;
    _cam.y += aft.y - bef.y;
    render();
  }

  function resetView() { _fitAll(); render(); }

  return { init, render, zoom, resetView };

})();
