// ── Access control gate ─────────────────────────────────────────────────────
(function () {
  const ACCESS_TYPE = window.GEODEPLOY.accessType;
  const PASSWORD_SHA256 = window.GEODEPLOY.passwordSha256;
  const TITLE = window.GEODEPLOY.title;
  const gate = document.getElementById('access-gate');
  const sub  = document.getElementById('access-gate-sub');

  if (ACCESS_TYPE === 'private') {
    const token = localStorage.getItem('geodeploy_token');
    if (token) {
      fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + token } })
        .then(r => {
          if (!r.ok) throw new Error('unauthorized');
          // Authenticated — portal stays visible, nothing to do
        })
        .catch(() => showPrivateGate());
    } else {
      showPrivateGate();
    }
    function showPrivateGate() {
      gate.style.display = 'flex';
      document.getElementById('access-gate-input').style.display = 'none';
      document.getElementById('access-gate-btn').style.display = 'none';
      sub.innerHTML = 'This portal is private. <a href="/" style="color:var(--accent)">Sign in</a> to view.';
    }
    return;
  }

  if (ACCESS_TYPE !== 'password' || !PASSWORD_SHA256) return;

  // Check session storage for already-verified session
  const CACHE_KEY = 'gd_auth_' + location.pathname;
  if (sessionStorage.getItem(CACHE_KEY) === PASSWORD_SHA256) return;

  gate.style.display = 'flex';
  sub.textContent = 'Enter the password to view this portal.';

  async function sha256hex(str) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  async function tryUnlock() {
    const val = document.getElementById('access-gate-input').value;
    if (!val) return;
    const hash = await sha256hex(val);
    if (hash === PASSWORD_SHA256) {
      sessionStorage.setItem(CACHE_KEY, hash);
      gate.style.display = 'none';
    } else {
      const err = document.getElementById('access-gate-error');
      err.textContent = 'Incorrect password.';
      setTimeout(() => { err.textContent = ''; }, 3000);
    }
  }

  document.getElementById('access-gate-btn').addEventListener('click', tryUnlock);
  document.getElementById('access-gate-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') tryUnlock();
  });
  // Auto-focus after next tick so the gate is visible
  setTimeout(() => document.getElementById('access-gate-input').focus(), 50);
})();

// ──────────────────────────────────────────────────────────

(function () {
  'use strict';

  const STYLE = window.GEODEPLOY.style;
  const POPUP_CONFIG = window.GEODEPLOY.popupConfig;

  // Make tile URLs absolute so MapLibre's Web Worker can resolve them
  // (Workers can't resolve relative URLs against the page origin)
  ;(function absolutifyTileUrls(style) {
    const base = location.origin;
    Object.values(style.sources || {}).forEach(src => {
      if (Array.isArray(src.tiles)) {
        src.tiles = src.tiles.map(u => u.startsWith('/') ? base + u : u);
      }
    });
  })(STYLE);

  // ── Sidebar toggle ──────────────────────────────────────
  const sidebar = document.getElementById('sidebar');
  document.getElementById('sidebar-toggle').addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
    setTimeout(() => map.resize(), 220);
  });

  // ── Map init ────────────────────────────────────────────
  const map = new maplibregl.Map({
    container: 'map',
    style: STYLE,
    center: [0, 20],
    zoom: 2,
    attributionControl: false,
  });

  // Zoom/compass added later (after the basemap + tools controls) so the basemap
  // icon sits above the zoom controls in the top-right stack.
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

  // Generate point-marker icons on demand (also covers the first render gap).
  map.on('styleimagemissing', function (e) {
    if (!e.id || e.id.indexOf('gd-pt-') !== 0 || map.hasImage(e.id)) return;
    const l = (STYLE.layers || []).find(x => x.layout && x.layout['icon-image'] === e.id);
    const m = (l && l.metadata) || {};
    setMarkerImage(e.id, m['geodeploy:marker'] || 'circle', m['geodeploy:markerColor'] || '#3b82f6', m['geodeploy:markerSize'] || 5);
  });

  // ── Auto-fit to data bounds ─────────────────────────────
  // Validate lon/lat ranges so one bad layer bbox can't throw and abort the
  // rest of this script (which would leave the layer switcher unbuilt).
  function validLonLatBounds(b) {
    return Array.isArray(b) && b.length === 4 &&
      b[0] >= -180 && b[2] <= 180 && b[0] < b[2] &&
      b[1] >= -90  && b[3] <= 90  && b[1] < b[3];
  }
  const bounds = STYLE.geodeploy?.bounds;
  if (validLonLatBounds(bounds)) {
    try {
      map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], {
        padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 },
        duration: 0,
      });
    } catch (e) { /* ignore — keep default view */ }
  }

  // ── Layer switcher ──────────────────────────────────────
  map.on('load', function () {
    ensurePointImages();  // register canvas icons before the symbol layers paint
    // Reverse so the list shows config[0] (drawn on top) at the top of the list.
    const userLayers = STYLE.layers.filter(l => l.metadata && l.metadata['geodeploy:name']).reverse();
    buildLayerSwitcher(userLayers);
    setupBasemaps();  // adds the basemap + tools controls (top-right)
    map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right');  // zoom below them
  });

  const resetBtn = document.getElementById('reset-styling');
  if (resetBtn) resetBtn.addEventListener('click', resetStyling);

  // Restore each user layer's original paint + visibility, then rebuild controls
  function resetStyling() {
    STYLE.layers.forEach(l => {
      if (!l.metadata || !l.metadata['geodeploy:name']) return;
      const paint = l.paint || {};
      Object.keys(paint).forEach(prop => {
        try { map.setPaintProperty(l.id, prop, paint[prop]); } catch (e) {}
      });
      try { map.setLayoutProperty(l.id, 'visibility', 'visible'); } catch (e) {}
    });
    // Revert any raster tile-URL restyling (palette / hillshade / stretch)
    Object.keys(rasterState).forEach(k => delete rasterState[k]);
    Object.keys(STYLE.sources || {}).forEach(srcId => {
      const s = STYLE.sources[srcId];
      if (s && s.type === 'raster' && s.tiles) {
        const src = map.getSource(srcId);
        if (src && src.setTiles) src.setTiles(s.tiles);
      }
    });
    ensurePointImages();  // restore original marker icons (shape/colour/size)
    buildLayerSwitcher(STYLE.layers.filter(l => l.metadata && l.metadata['geodeploy:name']).reverse());
  }

  // ---- Point marker shapes -------------------------------------------------
  // Points render as symbol layers with a canvas-drawn icon, so shapes work on
  // raster basemaps (no glyph dependency). Shape/colour/size = regenerate the icon.
  const MARKER_SHAPES = ['circle', 'square', 'triangle', 'diamond', 'star', 'cross'];
  function starPoints(cx, cy, r) {
    const p = [];
    for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = (i % 2) ? r * 0.45 : r; p.push((cx + Math.cos(a) * rr).toFixed(1) + ',' + (cy + Math.sin(a) * rr).toFixed(1)); }
    return p.join(' ');
  }
  function crossPoints(cx, cy, r) {
    const t = r * 0.38, pts = [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r], [-t, t], [-r, t], [-r, -t], [-t, -t]];
    return pts.map(d => (cx + d[0]).toFixed(1) + ',' + (cy + d[1]).toFixed(1)).join(' ');
  }
  function drawMarkerPath(ctx, shape, cx, cy, r) {
    ctx.beginPath();
    if (shape === 'square') { ctx.rect(cx - r, cy - r, r * 2, r * 2); }
    else if (shape === 'triangle') { ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r * 0.92, cy + r * 0.72); ctx.lineTo(cx - r * 0.92, cy + r * 0.72); ctx.closePath(); }
    else if (shape === 'diamond') { ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath(); }
    else if (shape === 'star') { const a = starPoints(cx, cy, r).split(' '); a.forEach((pt, i) => { const xy = pt.split(','); i ? ctx.lineTo(+xy[0], +xy[1]) : ctx.moveTo(+xy[0], +xy[1]); }); ctx.closePath(); }
    else if (shape === 'cross') { const a = crossPoints(cx, cy, r).split(' '); a.forEach((pt, i) => { const xy = pt.split(','); i ? ctx.lineTo(+xy[0], +xy[1]) : ctx.moveTo(+xy[0], +xy[1]); }); ctx.closePath(); }
    else { ctx.arc(cx, cy, r, 0, Math.PI * 2); }
  }
  function markerImage(shape, color, size) {
    const dpr = 2, r = Math.max(3, Number(size) || 5), stroke = Math.max(1, r * 0.28);
    // Fixed canvas size (fits the max marker radius) so every icon for a layer shares
    // dimensions — that lets map.updateImage() work when only the SIZE changes.
    const dim = 80;
    const cv = document.createElement('canvas');
    cv.width = dim * dpr; cv.height = dim * dpr;
    const ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr); ctx.lineJoin = 'round';
    drawMarkerPath(ctx, shape, dim / 2, dim / 2, r);
    ctx.fillStyle = color || '#3b82f6'; ctx.fill();
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = stroke; ctx.stroke();
    const d = ctx.getImageData(0, 0, dim * dpr, dim * dpr);
    return { width: dim * dpr, height: dim * dpr, data: d.data, pixelRatio: dpr };
  }
  function setMarkerImage(imgId, shape, color, size) {
    const im = markerImage(shape, color, size);
    try { if (map.hasImage(imgId)) map.updateImage(imgId, im); else map.addImage(imgId, im, { pixelRatio: im.pixelRatio }); }
    catch (e) { try { if (map.hasImage(imgId)) map.removeImage(imgId); map.addImage(imgId, im, { pixelRatio: im.pixelRatio }); } catch (e2) {} }
  }
  // SVG mirror of a marker shape, for the list/legend swatch.
  function markerSvg(shape, c) {
    const stroke = ' stroke="#fff" stroke-width="1.5" stroke-linejoin="round"';
    if (shape === 'square') return '<rect x="3" y="3" width="12" height="12" fill="' + c + '"' + stroke + '/>';
    if (shape === 'triangle') return '<polygon points="9,2.5 15.5,15 2.5,15" fill="' + c + '"' + stroke + '/>';
    if (shape === 'diamond') return '<polygon points="9,2 16,9 9,16 2,9" fill="' + c + '"' + stroke + '/>';
    if (shape === 'star') return '<polygon points="' + starPoints(9, 9, 6.5) + '" fill="' + c + '"' + stroke + '/>';
    if (shape === 'cross') return '<polygon points="' + crossPoints(9, 9, 6.5) + '" fill="' + c + '"' + stroke + '/>';
    return '<circle cx="9" cy="9" r="5.5" fill="' + c + '"' + stroke + '/>';
  }
  // Build/refresh icon images for every point (symbol) layer from its metadata.
  function ensurePointImages() {
    (STYLE.layers || []).forEach(l => {
      if (l.type !== 'symbol' || !l.layout || !l.layout['icon-image'] || !l.metadata) return;
      if (l.metadata['geodeploy:marker'] === undefined) return;
      setMarkerImage(l.layout['icon-image'], l.metadata['geodeploy:marker'] || 'circle',
        l.metadata['geodeploy:markerColor'] || '#3b82f6', l.metadata['geodeploy:markerSize'] || 5);
    });
  }

  function buildLayerSwitcher(layers) {
    const container = document.getElementById('layer-list');
    container.innerHTML = '';
    const resetBtn = document.getElementById('reset-styling');
    if (resetBtn) resetBtn.style.display = layers.length ? '' : 'none';
    if (!layers.length) {
      container.innerHTML = '<p style="font-size:13px;color:var(--text-muted)">No layers</p>';
      return;
    }

    const bboxById = {};

    layers.forEach(layer => {
      const meta = layer.metadata;
      const name = meta['geodeploy:name'];
      const type = meta['geodeploy:type'];
      const color = getLayerColor(layer);
      const bbox = meta['geodeploy:bbox'];
      bboxById[layer.id] = bbox;
      const canZoom = validLonLatBounds(bbox);
      const geom = meta['geodeploy:geometry'] || (type === 'raster' ? 'raster' : 'point');

      const card = document.createElement('div');
      card.className = 'layer-card';
      card.dataset.layerId = layer.id;
      card.setAttribute('draggable', 'true');
      const dash = dashKind(layer.paint);
      const shape = meta['geodeploy:marker'] || 'circle';
      let visOn = true;
      try { visOn = map.getLayoutProperty(layer.id, 'visibility') !== 'none'; } catch (e) {}
      card.innerHTML =
        '<div class="layer-row">' +
          '<span class="layer-drag" title="Drag to reorder">' + dragIcon() + '</span>' +
          '<button class="layer-eye' + (visOn ? '' : ' off') + '" data-layer-id="' + layer.id + '" title="Hide / show" aria-label="Toggle visibility">' + eyeIcon(visOn) + '</button>' +
          '<button class="layer-swatch-btn" data-swatch="' + layer.id + '" data-layer-id="' + layer.id + '" title="Symbology" aria-label="Edit symbology">' + legendSwatch(geom, color, dash, shape) + '</button>' +
          '<span class="layer-name" title="' + escHtml(name) + '">' + escHtml(name) + '</span>' +
          '<button class="layer-zoom" data-layer-id="' + layer.id + '" title="Zoom to layer" aria-label="Zoom to layer"' + (canZoom ? '' : ' disabled') + '>' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<circle cx="12" cy="12" r="7"/><line x1="12" y1="1" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="23"/>' +
            '<line x1="1" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="23" y2="12"/></svg>' +
          '</button>' +
        '</div>' +
        (type === 'raster'
          ? '<div class="layer-legend" data-legend="' + layer.id + '">' + rasterLegendHtml(layer) + '</div>'
          : '');
      container.appendChild(card);
    });

    container.querySelectorAll('.layer-eye').forEach(btn => {
      btn.addEventListener('click', e => {
        const id = e.currentTarget.dataset.layerId;
        const vis = (map.getLayoutProperty(id, 'visibility') === 'none') ? 'visible' : 'none';
        map.setLayoutProperty(id, 'visibility', vis);
        e.currentTarget.innerHTML = eyeIcon(vis === 'visible');
        e.currentTarget.classList.toggle('off', vis === 'none');
      });
    });
    container.querySelectorAll('.layer-zoom').forEach(btn => {
      btn.addEventListener('click', e => {
        const b = bboxById[e.currentTarget.dataset.layerId];
        if (!validLonLatBounds(b)) return;
        try {
          map.fitBounds([[b[0], b[1]], [b[2], b[3]]], {
            padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 },
          });
        } catch (err) { /* ignore */ }
      });
    });
    container.querySelectorAll('.layer-swatch-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const layer = STYLE.layers.find(l => l.id === e.currentTarget.dataset.layerId);
        if (layer) openSymbology(layer, e.currentTarget);
      });
    });
    enableLayerDrag(container);
  }

  function dragIcon() {
    return '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/>' +
      '<circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';
  }
  function eyeIcon(on) {
    const a = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
    return on
      ? '<svg ' + a + '><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>'
      : '<svg ' + a + '><path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a18.5 18.5 0 0 1 5.06-5.94M9.9 4.24A11 11 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22"/></svg>';
  }

  // ── Drag to reorder (changes map draw order; session only) ──
  function enableLayerDrag(container) {
    container.querySelectorAll('.layer-card').forEach(card => {
      card.addEventListener('dragstart', () => card.classList.add('dragging'));
      card.addEventListener('dragend', () => { card.classList.remove('dragging'); applyLayerOrder(container); });
    });
    container.addEventListener('dragover', e => {
      e.preventDefault();
      const cur = container.querySelector('.dragging');
      if (!cur) return;
      const after = dragAfter(container, e.clientY);
      if (after == null) container.appendChild(cur);
      else container.insertBefore(cur, after);
    });
  }
  function dragAfter(container, y) {
    const els = Array.prototype.slice.call(container.querySelectorAll('.layer-card:not(.dragging)'));
    let best = null, bestOff = -Infinity;
    els.forEach(child => {
      const box = child.getBoundingClientRect();
      const off = y - box.top - box.height / 2;
      if (off < 0 && off > bestOff) { bestOff = off; best = child; }
    });
    return best;
  }
  function applyLayerOrder(container) {
    // Top of the list = topmost on the map. moveLayer(id) with no beforeId moves to top,
    // so move from the bottom card up to the top card.
    const ids = Array.prototype.slice.call(container.querySelectorAll('.layer-card')).map(c => c.dataset.layerId);
    for (let i = ids.length - 1; i >= 0; i--) {
      try { if (map.getLayer(ids[i])) map.moveLayer(ids[i]); } catch (e) { /* ignore */ }
    }
  }

  // ── Symbology popover (opens from the swatch) ──
  let symbolPop = null;
  function closeSymbology() {
    if (symbolPop) { symbolPop.remove(); symbolPop = null; document.removeEventListener('mousedown', symbolOutside); }
  }
  function symbolOutside(e) {
    if (symbolPop && !symbolPop.contains(e.target) && !e.target.closest('.layer-swatch-btn')) closeSymbology();
  }
  function openSymbology(layer, anchorEl) {
    closeSymbology();
    const id = layer.id;
    const type = layer.metadata['geodeploy:type'];
    const geom = layer.metadata['geodeploy:geometry'] || (type === 'raster' ? 'raster' : 'point');
    const color = getLayerColor(layer);
    const opacity = layer.metadata['geodeploy:opacity'] != null ? layer.metadata['geodeploy:opacity'] : 1;

    const body = type === 'raster' ? rasterStyleRow(layer) : styleRow(layer, geom, color);

    const pop = document.createElement('div');
    pop.className = 'gd-symbology';
    pop.innerHTML =
      '<div class="gd-sym-head"><span>' + escHtml(layer.metadata['geodeploy:name']) + '</span>' +
      '<button class="gd-sym-close" aria-label="Close">&times;</button></div>' +
      '<div class="gd-sym-body">' +
        '<div class="layer-opacity-row"><span class="layer-opacity-label">' + Math.round(opacity * 100) + '%</span>' +
        '<input class="layer-opacity-slider" type="range" min="0" max="1" step="0.01" value="' + opacity +
        '" data-layer-id="' + id + '" data-layer-type="' + layer.type + '"></div>' +
        body +
      '</div>';
    document.body.appendChild(pop);
    symbolPop = pop;
    positionPopover(pop, anchorEl);
    pop.querySelector('.gd-sym-close').addEventListener('click', closeSymbology);
    const row = pop.querySelector('.layer-style-row');
    if (row) row.classList.add('open');
    attachStyleHandlers(pop, layer);
    setTimeout(() => document.addEventListener('mousedown', symbolOutside), 0);
  }
  function positionPopover(pop, anchorEl) {
    const r = anchorEl.getBoundingClientRect();
    const w = 240;
    let left = r.right + 8;
    if (left + w > window.innerWidth) left = Math.max(8, r.left - w - 8);
    pop.style.left = left + 'px';
    pop.style.top = Math.min(r.top, window.innerHeight - 300) + 'px';
    pop.style.width = w + 'px';
  }

  // Attach the styling control handlers to a root element (the popover).
  function attachStyleHandlers(root, layer) {
    root.querySelectorAll('.layer-style-color').forEach(inp => {
      inp.addEventListener('input', e => {
        const id = e.target.dataset.layerId, t = e.target.dataset.layerType;
        const prop = t === 'fill' ? 'fill-color' : t === 'line' ? 'line-color' : 'circle-color';
        map.setPaintProperty(id, prop, e.target.value);
        const geomK = t === 'fill' ? 'polygon' : t === 'line' ? 'line' : 'point';
        updateSwatch(id, geomK, e.target.value);
      });
    });
    root.querySelectorAll('.layer-style-size').forEach(inp => {
      inp.addEventListener('input', e => {
        const id = e.target.dataset.layerId, t = e.target.dataset.layerType;
        const prop = t === 'line' ? 'line-width' : 'circle-radius';
        const v = parseFloat(e.target.value);
        if (!isNaN(v)) map.setPaintProperty(id, prop, v);
      });
    });
    root.querySelectorAll('.layer-linetype').forEach(sel => {
      sel.addEventListener('change', e => {
        const id = e.target.dataset.layerId;
        const dash = e.target.value === 'dashed' ? [2, 1.5] : e.target.value === 'dotted' ? [0.4, 1.8] : null;
        try { map.setPaintProperty(id, 'line-dasharray', dash); } catch (err) { /* ignore */ }
        let color = '#3b82f6';
        try { color = map.getPaintProperty(id, 'line-color') || color; } catch (e2) {}
        updateSwatch(id, 'line', color);
      });
    });
    root.querySelectorAll('.layer-opacity-slider').forEach(slider => {
      slider.addEventListener('input', e => {
        const id = e.target.dataset.layerId, mapType = e.target.dataset.layerType;
        const val = parseFloat(e.target.value);
        const label = e.target.closest('.layer-opacity-row').querySelector('.layer-opacity-label');
        if (label) label.textContent = Math.round(val * 100) + '%';
        const prop = mapType === 'raster' ? 'raster-opacity' : mapType === 'fill' ? 'fill-opacity'
                   : mapType === 'line' ? 'line-opacity' : mapType === 'symbol' ? 'icon-opacity'
                   : mapType === 'circle' ? 'circle-opacity' : null;
        if (prop) map.setPaintProperty(id, prop, val);
      });
    });
    // Point marker controls (colour / shape / size all regenerate the icon image).
    function applyMarkerFrom(el) {
      const row = el.closest('.layer-style-row'); if (!row) return;
      const colorEl = row.querySelector('.layer-marker-color');
      const shapeEl = row.querySelector('.layer-marker-shape');
      const sizeEl = row.querySelector('.layer-marker-size');
      const color = colorEl ? colorEl.value : '#3b82f6';
      const shape = shapeEl ? shapeEl.value : 'circle';
      const size = sizeEl ? (parseFloat(sizeEl.value) || 5) : 5;
      setMarkerImage(el.dataset.imgId, shape, color, size);
      map.triggerRepaint && map.triggerRepaint();
      updateSwatch(el.dataset.layerId, 'point', color, shape);
    }
    root.querySelectorAll('.layer-marker-color, .layer-marker-size').forEach(el =>
      el.addEventListener('input', e => applyMarkerFrom(e.target)));
    root.querySelectorAll('.layer-marker-shape').forEach(el =>
      el.addEventListener('change', e => applyMarkerFrom(e.target)));
    root.querySelectorAll('.rstyle-colormap').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { colormap: e.target.value || null });
      applyRaster(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-hillshade').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { hillshade: e.target.checked });
      applyRaster(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-min').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { min: e.target.value });
      applyRaster(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-max').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { max: e.target.value });
      applyRaster(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-zfactor').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { zfactor: e.target.value });
      applyRaster(s);
    }));
    root.querySelectorAll('.rstyle-auto').forEach(el => el.addEventListener('click', e => {
      const btn = e.currentTarget, s = btn.dataset.src, r = btn.closest('.layer-style-row');
      autoStretchRaster(s, r.querySelector('.rstyle-min'), r.querySelector('.rstyle-max'), btn);
    }));
    // Multiband band selection (RGB composite ↔ single band).
    root.querySelectorAll('.rstyle-bandmode').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src, n = rasterBandCount(s), cur = effectiveBidx(s);
      let bidx;
      if (e.target.value === 'rgb') bidx = (cur.length === 3) ? cur : [1, Math.min(2, n), Math.min(3, n)];
      else bidx = [cur.length === 1 ? cur[0] : 1];
      const patch = { bidx: bidx };
      if (bidx.length === 3) patch.colormap = null;  // colormap is meaningless for RGB
      rasterState[s] = Object.assign({}, rasterState[s], patch);
      applyRaster(s); refreshRasterRow(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-rgb').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src, n = rasterBandCount(s), chan = parseInt(e.target.dataset.chan), cur = effectiveBidx(s);
      const rgb = (cur.length === 3) ? cur.slice() : [1, Math.min(2, n), Math.min(3, n)];
      rgb[chan] = parseInt(e.target.value);
      rasterState[s] = Object.assign({}, rasterState[s], { bidx: rgb, colormap: null });
      applyRaster(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-band').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { bidx: [parseInt(e.target.value)] });
      applyRaster(s); updateRasterLegend(s);
    }));
  }

  // Re-render just the raster style row in the open popover (used when the band mode
  // switches between RGB and single, which changes which controls are shown).
  function refreshRasterRow(srcId) {
    if (!symbolPop) return;
    const layer = STYLE.layers.find(l => l.source === srcId && l.metadata && l.metadata['geodeploy:type'] === 'raster');
    if (!layer) return;
    const oldRow = symbolPop.querySelector('.layer-style-row');
    if (!oldRow) return;
    const tmp = document.createElement('div');
    tmp.innerHTML = rasterStyleRow(layer);
    const newRow = tmp.firstElementChild;
    newRow.classList.add('open');
    oldRow.replaceWith(newRow);
    attachStyleHandlers(newRow, layer);  // scoped to the new row — won't double-bind the opacity slider (a sibling)
  }

  function getLayerColor(layer) {
    const paint = layer.paint || {};
    return paint['fill-color'] || paint['line-color'] || paint['circle-color'] ||
      (layer.metadata && layer.metadata['geodeploy:markerColor']) || '#64748b';
  }

  function geomIcon(kind) {
    const s = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"';
    if (kind === 'polygon') return `<svg ${s}><path d="M12 3l8 6-3 11H7L4 9z"/></svg>`;
    if (kind === 'line')    return `<svg ${s}><polyline points="3 17 9 11 14 15 21 5"/></svg>`;
    if (kind === 'raster')  return `<svg ${s}><rect x="3" y="3" width="18" height="18" rx="1"/>` +
      `<line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/>` +
      `<line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>`;
    return `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5" fill="currentColor"/></svg>`;
  }
  function geomLabel(kind) {
    return kind === 'polygon' ? 'Polygons' : kind === 'line' ? 'Lines'
         : kind === 'raster'  ? 'Raster'   : 'Points';
  }

  function dashKind(paint) {
    const d = paint && paint['line-dasharray'];
    if (!Array.isArray(d) || !d.length) return 'solid';
    return d[0] < 1 ? 'dotted' : 'dashed';
  }
  function updateSwatch(id, geomK, color, shape) {
    const sw = document.querySelector('.layer-swatch-btn[data-swatch="' + id + '"]');
    if (!sw) return;
    let dash = 'solid';
    if (geomK === 'line') { try { dash = dashKind({ 'line-dasharray': map.getPaintProperty(id, 'line-dasharray') }); } catch (e) {} }
    sw.innerHTML = legendSwatch(geomK, color, dash, shape);
  }

  // Legend swatch that mirrors the layer's actual symbol + colour (+ line dash / marker shape)
  function legendSwatch(geom, color, dash, shape) {
    const c = color || '#3b82f6';
    if (geom === 'line') {
      const da = dash === 'dashed' ? ' stroke-dasharray="3 2"' : dash === 'dotted' ? ' stroke-dasharray="0.6 3"' : '';
      return '<svg width="18" height="18" viewBox="0 0 18 18"><line x1="2" y1="9" x2="16" y2="9" stroke="' + c + '" stroke-width="3" stroke-linecap="round"' + da + '/></svg>';
    }
    if (geom === 'polygon')
      return '<svg width="18" height="18" viewBox="0 0 18 18"><rect x="2.5" y="4" width="13" height="10" fill="' + c + '" fill-opacity="0.45" stroke="' + c + '" stroke-width="1.5"/></svg>';
    if (geom === 'raster')
      return geomIcon('raster');
    return '<svg width="18" height="18" viewBox="0 0 18 18">' + markerSvg(shape || 'circle', c) + '</svg>';
  }

  // Approximate CSS gradients for the TiTiler palettes (for the raster legend bar)
  const LEGEND_GRADIENTS = {
    '':        'linear-gradient(to right,#000,#fff)',
    gray:      'linear-gradient(to right,#000,#fff)',
    viridis:   'linear-gradient(to right,#440154,#3b528b,#21918c,#5ec962,#fde725)',
    plasma:    'linear-gradient(to right,#0d0887,#6a00a8,#b12a90,#e16462,#fca636,#f0f921)',
    inferno:   'linear-gradient(to right,#000004,#420a68,#932667,#dd513a,#fca50a,#fcffa4)',
    magma:     'linear-gradient(to right,#000004,#3b0f70,#8c2981,#de4968,#fe9f6d,#fcfdbf)',
    cividis:   'linear-gradient(to right,#00204d,#31446b,#666970,#958f78,#cbba69,#ffea46)',
    terrain:   'linear-gradient(to right,#333399,#00b3b3,#99e699,#f2f2b3,#cc9966,#fff)',
    rdylgn:    'linear-gradient(to right,#a50026,#f46d43,#fee08b,#a6d96a,#006837)',
    spectral:  'linear-gradient(to right,#9e0142,#f46d43,#fee08b,#abdda4,#5e4fa2)',
    rdbu:      'linear-gradient(to right,#67001f,#f7f7f7,#053061)',
  };

  function parseRasterParams(srcId) {
    const t = (STYLE.sources[srcId] && STYLE.sources[srcId].tiles && STYLE.sources[srcId].tiles[0]) || '';
    const q = t.indexOf('?') >= 0 ? t.slice(t.indexOf('?') + 1) : '';
    const out = {};
    q.split('&').forEach(kv => { const i = kv.indexOf('='); if (i > 0) out[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1)); });
    return out;
  }

  // Band selection (bidx) helpers — bidx can repeat in the URL, so parseRasterParams
  // (last-wins) can't read it. Pull all bidx values from the baked tile URL.
  function bakedBidx(srcId) {
    const t = (STYLE.sources[srcId] && STYLE.sources[srcId].tiles && STYLE.sources[srcId].tiles[0]) || '';
    const out = []; const re = /[?&]bidx=(\d+)/g; let m;
    while ((m = re.exec(t))) out.push(parseInt(m[1]));
    return out;
  }
  function effectiveBidx(srcId) {
    const st = rasterState[srcId] || {};
    return Array.isArray(st.bidx) ? st.bidx : bakedBidx(srcId);
  }
  function rasterBandCount(srcId) {
    const l = STYLE.layers.find(x => x.source === srcId && x.metadata && x.metadata['geodeploy:type'] === 'raster');
    return (l && l.metadata['geodeploy:bands']) || 1;
  }

  function rasterLegendHtml(layer) {
    const srcId = layer.source;
    const st = (typeof rasterState !== 'undefined' && rasterState[srcId]) || {};
    const bidx = Array.isArray(st.bidx) ? st.bidx : bakedBidx(srcId);
    if (bidx.length === 3)  // RGB composite — a colormap gradient would be misleading
      return '<div class="legend-range"><span>RGB composite</span><span>bands ' + escHtml(bidx.join(' / ')) + '</span></div>';
    const baked = parseRasterParams(srcId);
    const cmap = st.hillshade ? 'gray' : (st.colormap !== undefined ? (st.colormap || '') : (baked.colormap_name || ''));
    const rescale = (st.min != null && st.min !== '' && st.max != null && st.max !== '')
      ? (st.min + ',' + st.max) : (baked.rescale || '');
    const p = rescale.split(',');
    const mn = (p[0] !== undefined && p[0] !== '') ? p[0] : 'min';
    const mx = (p[1] !== undefined && p[1] !== '') ? p[1] : 'max';
    const grad = LEGEND_GRADIENTS[cmap] || LEGEND_GRADIENTS.gray;
    return '<div class="legend-bar" style="background:' + grad + '"></div>' +
      '<div class="legend-range"><span>' + escHtml(String(mn)) + '</span><span>' + escHtml(String(mx)) + '</span></div>';
  }

  function updateRasterLegend(srcId) {
    const layer = STYLE.layers.find(l => l.source === srcId && l.metadata && l.metadata['geodeploy:type'] === 'raster');
    if (!layer) return;
    const el = document.querySelector('.layer-legend[data-legend="' + layer.id + '"]');
    if (el) el.innerHTML = rasterLegendHtml(layer);
  }
  function slidersIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">` +
      `<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>` +
      `<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>` +
      `<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>` +
      `<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>`;
  }
  function toHex(c) {
    return (typeof c === 'string' && /^#[0-9a-fA-F]{6}$/.test(c)) ? c : '#3b82f6';
  }
  const PORTAL_COLORMAPS = ['viridis','plasma','inferno','magma','cividis','terrain','gray','rdylgn','spectral','rdbu'];

  function styleRow(layer, geom, color) {
    if (layer.type === 'raster') return rasterStyleRow(layer);
    const t = layer.type;
    if (geom === 'point') {
      const m = layer.metadata || {};
      const imgId = (layer.layout && layer.layout['icon-image']) || ('gd-pt-' + m['geodeploy:layer_id']);
      const curShape = m['geodeploy:marker'] || 'circle';
      const curSize = m['geodeploy:markerSize'] || 5;
      const shapeOpts = MARKER_SHAPES.map(s =>
        `<option value="${s}"${s === curShape ? ' selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('');
      return `<div class="layer-style-row" data-style-for="${layer.id}">` +
        `<div class="layer-style-field"><label>Color</label>` +
        `<input class="layer-marker-color" type="color" value="${toHex(color)}" data-layer-id="${layer.id}" data-img-id="${imgId}"></div>` +
        `<div class="layer-style-field"><label>Shape</label>` +
        `<select class="layer-marker-shape" data-layer-id="${layer.id}" data-img-id="${imgId}">${shapeOpts}</select></div>` +
        `<div class="layer-style-field"><label>Size</label>` +
        `<input class="layer-marker-size" type="number" min="1" max="30" step="1" value="${curSize}" data-layer-id="${layer.id}" data-img-id="${imgId}"></div>` +
        `</div>`;
    }
    let sizeField = '', lineType = '';
    if (geom === 'line') {
      const w = (layer.paint && layer.paint['line-width']) ?? 2;
      sizeField = `<div class="layer-style-field"><label>Width</label>` +
        `<input class="layer-style-size" type="number" min="0.5" max="20" step="0.5" value="${w}" ` +
        `data-layer-id="${layer.id}" data-layer-type="${t}"></div>`;
    }
    if (geom === 'line') {
      const cur = dashKind(layer.paint);
      const opt = (v, l) => `<option value="${v}"${cur === v ? ' selected' : ''}>${l}</option>`;
      lineType = `<div class="layer-style-field"><label>Style</label>` +
        `<select class="layer-linetype" data-layer-id="${layer.id}">` +
        opt('solid', 'Solid') + opt('dashed', 'Dashed') + opt('dotted', 'Dotted') + `</select></div>`;
    }
    return `<div class="layer-style-row" data-style-for="${layer.id}">` +
        `<div class="layer-style-field"><label>Color</label>` +
        `<input class="layer-style-color" type="color" value="${toHex(color)}" ` +
        `data-layer-id="${layer.id}" data-layer-type="${t}"></div>${sizeField}${lineType}</div>`;
  }

  function rasterStyleRow(layer) {
    const src = layer.source;
    const bands = (layer.metadata && layer.metadata['geodeploy:bands']) || 1;
    const cur = effectiveBidx(src);
    const mode = (cur.length === 1) ? 'single' : 'rgb';
    let html = '';
    if (bands > 1) {
      const bandOpts = sel => { let o = ''; for (let b = 1; b <= bands; b++) o += '<option value="' + b + '"' + (b === sel ? ' selected' : '') + '>' + b + '</option>'; return o; };
      html += '<div class="layer-style-field"><label>Bands</label>' +
        '<select class="rstyle-bandmode" data-src="' + src + '">' +
        '<option value="rgb"' + (mode === 'rgb' ? ' selected' : '') + '>RGB composite</option>' +
        '<option value="single"' + (mode === 'single' ? ' selected' : '') + '>Single band</option>' +
        '</select></div>';
      if (mode === 'rgb') {
        const rgb = (cur.length === 3) ? cur : [1, Math.min(2, bands), Math.min(3, bands)];
        html += '<div class="layer-style-field"><label>R G B</label>' +
          '<select class="rstyle-rgb" data-src="' + src + '" data-chan="0">' + bandOpts(rgb[0]) + '</select>' +
          '<select class="rstyle-rgb" data-src="' + src + '" data-chan="1">' + bandOpts(rgb[1]) + '</select>' +
          '<select class="rstyle-rgb" data-src="' + src + '" data-chan="2">' + bandOpts(rgb[2]) + '</select></div>';
      } else {
        html += '<div class="layer-style-field"><label>Band</label>' +
          '<select class="rstyle-band" data-src="' + src + '">' + bandOpts(cur[0] || 1) + '</select></div>';
      }
    }
    if (bands === 1 || mode === 'single') {
      html += '<div class="layer-style-field"><label>Palette</label>' +
        '<select class="rstyle-colormap" data-src="' + src + '"><option value="">Grayscale</option>' +
        PORTAL_COLORMAPS.map(c => '<option value="' + c + '">' + c + '</option>').join('') +
        '</select></div>';
      html += '<label class="layer-style-field" style="cursor:pointer">' +
        '<input type="checkbox" class="rstyle-hillshade" data-src="' + src + '"> Hillshade</label>';
      html += '<div class="layer-style-field" title="Hillshade vertical exaggeration"><label>Z</label>' +
        '<input class="rstyle-zfactor" data-src="' + src + '" type="number" min="0.1" max="10" step="0.1" value="1"></div>';
    }
    html += '<div class="layer-style-field"><label>Stretch</label>' +
      '<input class="rstyle-min" data-src="' + src + '" type="number" placeholder="min">' +
      '<input class="rstyle-max" data-src="' + src + '" type="number" placeholder="max">' +
      '<button type="button" class="rstyle-auto" data-src="' + src + '" title="Auto stretch from raster statistics">Auto</button></div>';
    return '<div class="layer-style-row" data-style-for="' + layer.id + '">' + html + '</div>';
  }

  // Rebuild a raster source's tile URL from the viewer's chosen params (session only)
  const rasterState = {};
  function applyRaster(srcId) {
    const st = rasterState[srcId] || {};
    const baseFull = (STYLE.sources[srcId] && STYLE.sources[srcId].tiles && STYLE.sources[srcId].tiles[0]) || '';
    if (!baseFull) return;
    const base = baseFull.split('&')[0];  // keep up to ?url=s3://... (s3 key has no '&')
    const params = [];
    // Preserve the admin's baked band selection unless the viewer overrode it.
    const bidx = Array.isArray(st.bidx) ? st.bidx : bakedBidx(srcId);
    bidx.forEach(b => params.push('bidx=' + b));
    if (st.min != null && st.min !== '' && st.max != null && st.max !== '') params.push('rescale=' + st.min + ',' + st.max);
    if (st.hillshade) {
      params.push('algorithm=hillshade');
      if (st.zfactor && Number(st.zfactor) !== 1) params.push('expression=b1*' + st.zfactor);
    } else if (st.colormap && bidx.length !== 3) {  // colormap is ignored for an RGB composite
      params.push('colormap_name=' + st.colormap);
    }
    const url = base + (params.length ? '&' + params.join('&') : '');
    const src = map.getSource(srcId);
    if (src && src.setTiles) src.setTiles([url]);
  }

  // Auto-stretch: ask TiTiler statistics for the data range, fill min/max, apply
  async function autoStretchRaster(srcId, minInput, maxInput, btn) {
    const baseFull = (STYLE.sources[srcId] && STYLE.sources[srcId].tiles && STYLE.sources[srcId].tiles[0]) || '';
    const base = baseFull.split('&')[0];
    const qIdx = base.indexOf('?');
    if (qIdx < 0) return;
    const statsUrl = base.slice(0, qIdx).replace(/\/cog\/tiles\/[^/]+\/\{z\}\/\{x\}\/\{y\}/, '/cog/statistics') + base.slice(qIdx);
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.textContent = '…'; btn.disabled = true; }
    try {
      const r = await fetch(statsUrl);
      if (!r.ok) throw new Error('stats');
      const stats = await r.json();
      const mins = [], maxs = [];
      Object.values(stats).forEach(s => {
        if (!s || typeof s !== 'object') return;
        const lo = s.percentile_2 != null ? s.percentile_2 : s.min;
        const hi = s.percentile_98 != null ? s.percentile_98 : s.max;
        if (lo != null) mins.push(lo);
        if (hi != null) maxs.push(hi);
      });
      if (mins.length && maxs.length) {
        const lo = Math.min.apply(null, mins), hi = Math.max.apply(null, maxs);
        if (minInput) minInput.value = lo;
        if (maxInput) maxInput.value = hi;
        rasterState[srcId] = Object.assign({}, rasterState[srcId], { min: lo, max: hi });
        applyRaster(srcId);
        updateRasterLegend(srcId);
      }
    } catch (e) {
    } finally {
      if (btn) { btn.textContent = orig || 'Auto'; btn.disabled = false; }
    }
  }

  // ── Feature popup ───────────────────────────────────────
  const popup = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: false,
    className: 'gd-popup',
    maxWidth: '300px',
  });

  // Area-select (box draw) state — shared with the click/cursor handlers below.
  let drawing = false, suppressClick = false, drawStart = null;

  map.on('click', async e => {
    if (suppressClick) { suppressClick = false; return; }  // ignore the click that ends a box draw
    const vectorLayerIds = (STYLE.layers || [])
      .filter(l => l.metadata && l.metadata['geodeploy:type'] === 'vector')
      .map(l => l.id);

    // Query a small box around the click so thin lines / points are easy to hit.
    const pad = 5;
    const clickBox = [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]];
    const features = vectorLayerIds.length
      ? map.queryRenderedFeatures(clickBox, { layers: vectorLayerIds })
      : [];

    // ── Vector section ──
    let vectorHtml = '', ftLayerId = null, ftLayerName = '';
    if (features.length) {
      const f = features[0];
      ftLayerId = f.layer.id;
      const layerId = f.layer.metadata && f.layer.metadata['geodeploy:layer_id'];
      ftLayerName = (f.layer.metadata && f.layer.metadata['geodeploy:name']) || f.layer.id;
      const fields = POPUP_CONFIG[layerId] || POPUP_CONFIG[String(layerId)];
      const props = f.properties || {};
      const keys = fields && fields.length
        ? fields.filter(k => props[k] != null)
        : Object.keys(props).filter(k => props[k] != null).slice(0, 8);
      const body = keys.length
        ? '<table class="popup-table">' + keys.map(k =>
            '<tr><th>' + escHtml(k) + '</th><td>' + escHtml(String(props[k])) + '</td></tr>').join('') + '</table>'
        : '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted)">No attributes</div>';
      vectorHtml = '<div class="popup-header">' + escHtml(ftLayerName) + '</div>' + body +
        '<div class="popup-actions"><button class="popup-fulltable-btn" type="button">View full table ▸</button></div>';
    }

    // ── Raster identify section ──
    const rasters = visibleRasterLayers();
    if (!vectorHtml && !rasters.length) return;

    const loading = rasters.length ? '<div class="popup-raster-loading">Reading pixel value…</div>' : '';
    popup.setLngLat(e.lngLat).setHTML(vectorHtml + loading).addTo(map);
    wireFullTableBtn(ftLayerId, ftLayerName);

    if (rasters.length) {
      const results = await Promise.all(rasters.map(l => fetchRasterPoint(l, e.lngLat)));
      popup.setHTML(vectorHtml + rasterValuesHtml(results));
      wireFullTableBtn(ftLayerId, ftLayerName);
    }
  });

  function wireFullTableBtn(mapLayerId, layerName) {
    if (!mapLayerId) return;
    const el = popup.getElement();
    const btn = el && el.querySelector('.popup-fulltable-btn');
    if (btn) btn.addEventListener('click', () => openAttrPanel(mapLayerId, layerName));
  }

  function visibleRasterLayers() {
    return (STYLE.layers || []).filter(l => {
      if (!l.metadata || l.metadata['geodeploy:type'] !== 'raster') return false;
      if (!map.getLayer(l.id)) return false;
      try { return map.getLayoutProperty(l.id, 'visibility') !== 'none'; } catch (e) { return true; }
    });
  }

  async function fetchRasterPoint(layer, lngLat) {
    const baseFull = (STYLE.sources[layer.source] && STYLE.sources[layer.source].tiles && STYLE.sources[layer.source].tiles[0]) || '';
    const base = baseFull.split('&')[0];
    const qIdx = base.indexOf('?');
    if (qIdx < 0) return null;
    const url = base.slice(0, qIdx)
      .replace(/\/cog\/tiles\/[^/]+\/\{z\}\/\{x\}\/\{y\}/, '/cog/point/' + lngLat.lng + ',' + lngLat.lat) + base.slice(qIdx);
    try {
      const r = await fetch(url);
      if (!r.ok) return null;
      const j = await r.json();
      return { name: (layer.metadata && layer.metadata['geodeploy:name']) || 'Raster',
               values: j.values || [], bands: j.band_names || [] };
    } catch (e) { return null; }
  }

  function rasterValuesHtml(results) {
    const blocks = (results || []).filter(r => r && r.values && r.values.length).map(r => {
      const rows = r.values.map((v, i) => {
        const band = (r.bands && r.bands[i]) || ('Band ' + (i + 1));
        const val = (typeof v === 'number') ? (Math.round(v * 10000) / 10000) : v;
        return '<tr><th>' + escHtml(String(band)) + '</th><td>' + escHtml(String(val)) + '</td></tr>';
      }).join('');
      return '<div class="popup-header">' + escHtml(r.name) + '</div><table class="popup-table">' + rows + '</table>';
    });
    return blocks.length ? blocks.join('')
      : '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted)">No raster value at this point.</div>';
  }

  // ── Attribute table panel ───────────────────────────────
  function openAttrPanel(mapLayerId, layerName) {
    const panel = document.getElementById('attr-panel');
    const bodyEl = document.getElementById('attr-panel-body');
    const countEl = document.getElementById('attr-panel-count');
    document.getElementById('attr-panel-title').textContent = layerName;

    const feats = map.queryRenderedFeatures({ layers: [mapLayerId] });
    const seen = new Set(), rows = [];
    feats.forEach(ft => {
      const key = ft.id != null ? ft.id : JSON.stringify(ft.properties);
      if (seen.has(key)) return;
      seen.add(key);
      rows.push(ft.properties || {});
    });

    if (!rows.length) {
      countEl.textContent = '';
      bodyEl.innerHTML = '<p style="padding:12px;font-size:12px;color:var(--text-muted)">' +
        'No features in the current view. Zoom or pan to load features, then try again.</p>';
    } else {
      const cols = [];
      rows.forEach(p => Object.keys(p).forEach(k => { if (!cols.includes(k)) cols.push(k); }));
      countEl.textContent = rows.length + ' feature' + (rows.length === 1 ? '' : 's') + ' in view';
      const thead = '<thead><tr>' + cols.map(c => `<th>${escHtml(c)}</th>`).join('') + '</tr></thead>';
      const tbody = '<tbody>' + rows.map(p => '<tr>' + cols.map(c => {
        const v = p[c] == null ? '' : String(p[c]);
        return `<td title="${escHtml(v)}">${escHtml(v)}</td>`;
      }).join('') + '</tr>').join('') + '</tbody>';
      bodyEl.innerHTML = `<table class="attr-table">${thead}${tbody}</table>`;
    }
    panel.classList.add('open');
  }
  document.getElementById('attr-panel-close').addEventListener('click', () => {
    document.getElementById('attr-panel').classList.remove('open');
  });

  // Pointer cursor over interactive vector layers
  map.on('mousemove', e => {
    if (drawing) return;  // keep the crosshair while drawing a selection box
    const vectorLayerIds = (STYLE.layers || [])
      .filter(l => l.metadata && l.metadata['geodeploy:type'] === 'vector')
      .map(l => l.id);
    if (!vectorLayerIds.length) { map.getCanvas().style.cursor = ''; return; }
    const f = map.queryRenderedFeatures(e.point, { layers: vectorLayerIds });
    map.getCanvas().style.cursor = f.length ? 'pointer' : '';
  });

  // ── Coordinate readout (bottom-right) ───────────────────
  const coordsEl = document.getElementById('coords');
  if (coordsEl) {
    map.on('mousemove', e => {
      coordsEl.textContent = e.lngLat.lng.toFixed(5) + ', ' + e.lngLat.lat.toFixed(5);
    });
    map.on('mouseout', () => { coordsEl.textContent = ''; });
  }

  // ── Basemap switcher (top-right) ────────────────────────
  const BASEMAPS = [
    { id: 'default', name: 'Default', builtin: true,
      thumb: 'https://a.basemaps.cartocdn.com/light_all/4/8/5.png' },
    { id: 'osm', name: 'OpenStreetMap',
      tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png', 'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png'],
      attribution: '© OpenStreetMap contributors',
      thumb: 'https://a.tile.openstreetmap.org/4/8/5.png' },
    { id: 'dark', name: 'Dark',
      tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png', 'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
      attribution: '© OpenStreetMap © CARTO',
      thumb: 'https://a.basemaps.cartocdn.com/dark_all/4/8/5.png' },
    { id: 'satellite', name: 'Satellite',
      tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      attribution: 'Imagery © Esri',
      thumb: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/4/5/8' },
  ];

  function builtinBasemapIds() {
    return STYLE.layers.filter(l => !(l.metadata && l.metadata['geodeploy:name'])).map(l => l.id);
  }

  function setupBasemaps() {
    const firstId = (map.getStyle().layers[0] || {}).id;
    BASEMAPS.forEach(bm => {
      if (bm.builtin) return;
      const srcId = 'gd-basemap-' + bm.id;
      if (!map.getSource(srcId)) {
        map.addSource(srcId, { type: 'raster', tiles: bm.tiles, tileSize: 256, attribution: bm.attribution || '' });
      }
      if (!map.getLayer(srcId)) {
        map.addLayer({ id: srcId, type: 'raster', source: srcId, layout: { visibility: 'none' } }, firstId);
      }
    });
    map.addControl(new BasemapControl(), 'top-right');
    map.addControl(new ToolsControl(), 'top-right');
  }

  // ── Tools: select an area and download the vector data inside it ──────────────
  function toolsIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="3" y="3" width="18" height="18" rx="1" stroke-dasharray="4 3"/><path d="M12 8v8M8 12h8"/></svg>';
  }

  class ToolsControl {
    onAdd(m) {
      this._map = m;
      const c = document.createElement('div');
      c.className = 'maplibregl-ctrl maplibregl-ctrl-group';
      c.innerHTML = '<button type="button" class="gd-tools-btn" title="Select area & download" aria-label="Select area and download">' + toolsIcon() + '</button>';
      c.querySelector('.gd-tools-btn').addEventListener('click', ev => { ev.stopPropagation(); startAreaSelect(); });
      this._c = c;
      return c;
    }
    onRemove() { if (this._c) this._c.remove(); }
  }

  function emptyFC() { return { type: 'FeatureCollection', features: [] }; }

  function ensureDrawLayers() {
    if (!map.getSource('gd-draw')) {
      map.addSource('gd-draw', { type: 'geojson', data: emptyFC() });
      map.addLayer({ id: 'gd-draw-fill', type: 'fill', source: 'gd-draw',
        paint: { 'fill-color': '#2563eb', 'fill-opacity': 0.12 } });
      map.addLayer({ id: 'gd-draw-line', type: 'line', source: 'gd-draw',
        paint: { 'line-color': '#2563eb', 'line-width': 2, 'line-dasharray': [2, 1] } });
    }
  }

  function rectFC(a, b) {
    const x1 = Math.min(a.lng, b.lng), x2 = Math.max(a.lng, b.lng);
    const y1 = Math.min(a.lat, b.lat), y2 = Math.max(a.lat, b.lat);
    return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {},
      geometry: { type: 'Polygon', coordinates: [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]] } }] };
  }

  function clearDraw() { if (map.getSource('gd-draw')) map.getSource('gd-draw').setData(emptyFC()); }

  function startAreaSelect() {
    if (drawing) return;
    drawing = true;
    ensureDrawLayers();
    clearDraw();
    map.getCanvas().style.cursor = 'crosshair';
    map.dragPan.disable();
    showHint('Drag a box on the map to select an area');
    map.on('mousedown', onDrawDown);
  }

  function onDrawDown(e) {
    drawStart = e.lngLat;
    map.on('mousemove', onDrawMove);
    map.once('mouseup', onDrawUp);
  }
  function onDrawMove(e) {
    if (drawStart) map.getSource('gd-draw').setData(rectFC(drawStart, e.lngLat));
  }
  function onDrawUp(e) {
    map.off('mousemove', onDrawMove);
    map.off('mousedown', onDrawDown);
    map.dragPan.enable();
    map.getCanvas().style.cursor = '';
    hideHint();
    drawing = false;
    suppressClick = true;  // swallow the click event that follows this mouseup
    const a = drawStart, b = e.lngLat;
    drawStart = null;
    if (!a) return;
    const bbox = [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)];
    if (Math.abs(bbox[2] - bbox[0]) < 1e-7 || Math.abs(bbox[3] - bbox[1]) < 1e-7) { clearDraw(); return; }
    const pa = map.project(a), pb = map.project(b);
    const pixBox = [[Math.min(pa.x, pb.x), Math.min(pa.y, pb.y)], [Math.max(pa.x, pb.x), Math.max(pa.y, pb.y)]];
    openDownloadDialog(bbox, pixBox);
  }

  function showHint(text) {
    let h = document.getElementById('gd-hint');
    if (!h) { h = document.createElement('div'); h.id = 'gd-hint'; document.body.appendChild(h); }
    h.textContent = text; h.style.display = 'block';
  }
  function hideHint() { const h = document.getElementById('gd-hint'); if (h) h.style.display = 'none'; }

  function openDownloadDialog(bbox, pixBox) {
    const slug = (window.GEODEPLOY && window.GEODEPLOY.slug) || (location.pathname.split('/').filter(Boolean)[1] || '');

    // Only offer layers that actually have data inside the box.
    const seen = new Set(), items = [];
    (STYLE.layers || []).forEach(l => {
      if (!l.metadata || !l.metadata['geodeploy:name']) return;
      const type = l.metadata['geodeploy:type'], id = l.metadata['geodeploy:layer_id'];
      const key = type + '-' + id;
      if (seen.has(key)) return;
      let hit = false;
      if (type === 'vector') {
        try { hit = map.queryRenderedFeatures(pixBox, { layers: [l.id] }).length > 0; } catch (e) { hit = true; }
      } else {
        const bb = l.metadata['geodeploy:bbox'];
        hit = Array.isArray(bb) && bb.length === 4 &&
          !(bb[2] < bbox[0] || bb[0] > bbox[2] || bb[3] < bbox[1] || bb[1] > bbox[3]);
      }
      if (!hit) return;
      seen.add(key);
      items.push({ id: id, type: type, name: l.metadata['geodeploy:name'] || ('Layer ' + id) });
    });

    const fmtOptions = (type) => type === 'raster'
      ? '<option value="tif" selected>GeoTIFF</option>'
      : '<option value="geojson" selected>GeoJSON</option><option value="gpkg">GeoPackage</option><option value="csv">CSV</option>';
    const rowHtml = (it) =>
      '<label class="gd-download-row">' +
        '<input type="checkbox" class="gd-dl-check" data-id="' + it.id + '" data-type="' + it.type + '" checked>' +
        '<span class="gd-download-name" title="' + escHtml(it.name) + '">' + escHtml(it.name) + '</span>' +
        '<select class="gd-dl-format">' + fmtOptions(it.type) + '</select>' +
      '</label>';

    const old = document.getElementById('gd-download');
    if (old) old.remove();
    const dlg = document.createElement('div');
    dlg.id = 'gd-download';
    dlg.innerHTML =
      '<div class="gd-download-box">' +
        '<div class="gd-download-head"><span>Download selected area</span>' +
        '<button class="gd-download-close" aria-label="Close">&times;</button></div>' +
        '<div class="gd-download-body">' +
          (items.length ? items.map(rowHtml).join('') : '<p class="gd-download-empty">No layers intersect the selected area.</p>') +
        '</div>' +
        (items.length ? '<div class="gd-download-foot"><span class="gd-dl-status"></span>' +
          '<button class="gd-dl-go">Download</button></div>' : '') +
      '</div>';
    document.body.appendChild(dlg);

    const close = () => { dlg.remove(); clearDraw(); };
    dlg.querySelector('.gd-download-close').addEventListener('click', close);
    dlg.addEventListener('click', e => { if (e.target === dlg) close(); });

    const go = dlg.querySelector('.gd-dl-go');
    if (go) go.addEventListener('click', async () => {
      const picks = [];
      dlg.querySelectorAll('.gd-download-row').forEach(row => {
        const chk = row.querySelector('.gd-dl-check');
        if (!chk || !chk.checked) return;
        const sel = row.querySelector('.gd-dl-format');
        picks.push({ layer_id: Number(chk.dataset.id), layer_type: chk.dataset.type, format: sel ? sel.value : 'geojson' });
      });
      if (!picks.length) return;
      const status = dlg.querySelector('.gd-dl-status');
      const apiBase = '/api/portals/' + encodeURIComponent(slug);
      go.disabled = true; status.textContent = 'Queued…';
      try {
        const resp = await fetch(apiBase + '/export-bundle', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ bbox: bbox.join(','), items: picks }),
        });
        if (!resp.ok) throw new Error('start failed');
        const { job_id } = await resp.json();
        const ok = await pollExport(apiBase, job_id, status);
        if (!ok) { status.textContent = 'Failed — try again'; go.disabled = false; return; }
        const a = document.createElement('a');
        a.href = apiBase + '/export-download/' + encodeURIComponent(job_id);
        a.download = 'selection.zip';
        document.body.appendChild(a); a.click(); a.remove();
        status.textContent = 'Downloaded';
        setTimeout(close, 900);
      } catch (e) {
        status.textContent = 'Failed — try again';
        go.disabled = false;
      }
    });
  }

  // Poll the export job until the ZIP is ready (or it fails / times out ~4 min).
  async function pollExport(apiBase, jobId, statusEl) {
    for (let i = 0; i < 160; i++) {
      await new Promise(r => setTimeout(r, 1500));
      let s;
      try { const r = await fetch(apiBase + '/export-status/' + encodeURIComponent(jobId)); s = await r.json(); }
      catch (e) { continue; }
      if (s.status === 'ready') return true;
      if (s.status === 'error') return false;
      if (statusEl) statusEl.textContent = (s.status === 'processing') ? 'Processing…' : 'Queued…';
    }
    return false;
  }

  function selectBasemap(id) {
    const builtin = builtinBasemapIds();
    BASEMAPS.forEach(bm => {
      if (bm.builtin) return;
      const lid = 'gd-basemap-' + bm.id;
      if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', 'none');
    });
    if (id === 'default') {
      builtin.forEach(lid => { if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', 'visible'); });
    } else {
      builtin.forEach(lid => { if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', 'none'); });
      const lid = 'gd-basemap-' + id;
      if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', 'visible');
    }
  }

  function basemapIcon() {
    // 2x2 grid — the ArcGIS-style "basemap gallery" glyph (distinct from the layer-list icon)
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">' +
      '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>' +
      '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>';
  }

  class BasemapControl {
    onAdd(m) {
      this._map = m;
      const c = document.createElement('div');
      c.className = 'maplibregl-ctrl maplibregl-ctrl-group gd-basemap-ctrl';
      c.innerHTML =
        '<button type="button" class="gd-basemap-btn" title="Basemaps" aria-label="Choose basemap">' + basemapIcon() + '</button>' +
        '<div class="gd-basemap-menu">' +
          BASEMAPS.map((bm, i) =>
            '<label class="gd-basemap-opt"><input type="radio" name="gd-basemap" value="' + bm.id + '"' +
            (i === 0 ? ' checked' : '') + '>' +
            '<img class="gd-basemap-thumb" src="' + bm.thumb + '" alt="" loading="lazy">' +
            '<span>' + escHtml(bm.name) + '</span></label>').join('') +
        '</div>';
      const btn = c.querySelector('.gd-basemap-btn');
      const menu = c.querySelector('.gd-basemap-menu');
      btn.addEventListener('click', ev => { ev.stopPropagation(); c.classList.toggle('open'); });
      menu.addEventListener('change', ev => selectBasemap(ev.target.value));
      menu.addEventListener('click', ev => ev.stopPropagation());
      document.addEventListener('click', () => c.classList.remove('open'));
      this._c = c;
      return c;
    }
    onRemove() { if (this._c) this._c.remove(); }
  }

  function escHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

})();
