/** Shared, locally hosted botanical artwork. No saved garden data is changed. */
'use strict';
window.VW = window.VW || {};
VW.PlantArt = (() => {
  const ids = ['salvia-farinacea', 'canna', 'hybrid-tea-rose', 'rudbeckia',
    'day-lily', 'august-beauty-gardenia'];
  const assets = Object.freeze(Object.fromEntries(ids.map(id => [id, Object.freeze({
    map: `/static/plants/${id}/map.webp`,
    thumbnail: `/static/plants/${id}/thumbnail.webp`,
  })])));
  const images = new Map(), pending = new Map();
  function load(id) {
    if (!Object.hasOwn(assets, id)) return Promise.resolve(null);
    if (pending.has(id)) return pending.get(id);
    const promise = new Promise(resolve => {
      const image = new Image();
      image.onload = () => { images.set(id, image); resolve(image); };
      image.onerror = () => resolve(null);
      image.src = assets[id].map;
    });
    pending.set(id, promise);
    return promise;
  }
  function preload(ids) { return Promise.all([...new Set(ids)].map(load)); }
  function markerSize(zoom) { return Math.max(24, Math.min(48, zoom * 3)); }
  function draw(ctx, id, x, y, size) {
    const image = images.get(id);
    if (image) {
      const scale = size / Math.max(image.naturalWidth, image.naturalHeight);
      const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
      ctx.drawImage(image, x - w / 2, y - h / 2, w, h);
    } else {
      // Neutral marker for species awaiting artwork, or an unavailable image.
      ctx.beginPath(); ctx.arc(x, y, Math.min(6, size / 4), 0, Math.PI * 2);
      ctx.fillStyle = '#526c48'; ctx.fill();
      ctx.strokeStyle = '#faf6e9'; ctx.lineWidth = 1.5; ctx.stroke();
    }
  }
  function thumbnail(id) {
    const fallback = document.createElement('span');
    fallback.className = 'gdn-plant-thumbnail gdn-plant-placeholder';
    fallback.setAttribute('aria-hidden', 'true');
    fallback.textContent = '•';
    if (!Object.hasOwn(assets, id)) return fallback;
    const image = document.createElement('img');
    image.className = 'gdn-plant-thumbnail';
    image.alt = ''; // Adjacent plant name is the accessible label.
    image.width = 36; image.height = 36;
    image.loading = 'lazy'; image.decoding = 'async';
    image.onerror = () => image.replaceWith(fallback);
    image.src = assets[id].thumbnail;
    return image;
  }
  function canvasContext(canvas, width, height) {
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const w = Math.round(width * dpr), h = Math.round(height * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return ctx;
  }
  return { assets, preload, draw, thumbnail, markerSize, canvasContext };
})();
