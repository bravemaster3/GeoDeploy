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

  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

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
    const userLayers = STYLE.layers.filter(l => l.metadata && l.metadata['geodeploy:name']);
    buildLayerSwitcher(userLayers);
    setupBasemaps();
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
    buildLayerSwitcher(STYLE.layers.filter(l => l.metadata && l.metadata['geodeploy:name']));
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
      const opacity = meta['geodeploy:opacity'] ?? 1;
      const color = getLayerColor(layer);
      const bbox = meta['geodeploy:bbox'];
      bboxById[layer.id] = bbox;
      const canZoom = validLonLatBounds(bbox);

      const geom = meta['geodeploy:geometry'] || (type === 'raster' ? 'raster' : 'point');
      const isVector = type === 'vector';

      const card = document.createElement('div');
      card.className = 'layer-card';
      card.innerHTML = `
        <div class="layer-card-top">
          <input class="layer-visibility" type="checkbox" checked
            data-layer-id="${layer.id}" title="Toggle visibility">
          <span class="layer-swatch" data-swatch="${layer.id}"
            title="${geomLabel(geom)}">${legendSwatch(geom, color)}</span>
          <span class="layer-name" title="${name}">${name}</span>
          <button class="layer-zoom" data-layer-id="${layer.id}" title="Zoom to layer"
            aria-label="Zoom to layer" ${canZoom ? '' : 'disabled'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round">
              <circle cx="12" cy="12" r="7"/>
              <line x1="12" y1="1" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="23"/>
              <line x1="1" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="23" y2="12"/>
            </svg>
          </button>
          <button class="layer-style-toggle" data-layer-id="${layer.id}"
            title="Style this layer" aria-label="Style this layer">${slidersIcon()}</button>
        </div>
        <div class="layer-opacity-row">
          <span class="layer-opacity-label">${Math.round(opacity * 100)}%</span>
          <input class="layer-opacity-slider" type="range" min="0" max="1" step="0.01"
            value="${opacity}" data-layer-id="${layer.id}" data-layer-type="${layer.type}">
        </div>
        ${type === 'raster' ? `<div class="layer-legend" data-legend="${layer.id}">${rasterLegendHtml(layer)}</div>` : ''}
        ${styleRow(layer, geom, color)}
      `;
      container.appendChild(card);
    });

    // Viewer styling — color / size (session only)
    container.querySelectorAll('.layer-style-toggle').forEach(btn => {
      btn.addEventListener('click', e => {
        const id = e.currentTarget.dataset.layerId;
        const row = container.querySelector(`.layer-style-row[data-style-for="${id}"]`);
        if (row) e.currentTarget.classList.toggle('active', row.classList.toggle('open'));
      });
    });
    container.querySelectorAll('.layer-style-color').forEach(inp => {
      inp.addEventListener('input', e => {
        const id = e.target.dataset.layerId, t = e.target.dataset.layerType;
        const prop = t === 'fill' ? 'fill-color' : t === 'line' ? 'line-color' : 'circle-color';
        map.setPaintProperty(id, prop, e.target.value);
        const geomK = t === 'fill' ? 'polygon' : t === 'line' ? 'line' : 'point';
        const sw = container.querySelector(`.layer-swatch[data-swatch="${id}"]`);
        if (sw) sw.innerHTML = legendSwatch(geomK, e.target.value);
      });
    });
    container.querySelectorAll('.layer-style-size').forEach(inp => {
      inp.addEventListener('input', e => {
        const id = e.target.dataset.layerId, t = e.target.dataset.layerType;
        const prop = t === 'line' ? 'line-width' : 'circle-radius';
        const v = parseFloat(e.target.value);
        if (!isNaN(v)) map.setPaintProperty(id, prop, v);
      });
    });
    // Raster viewer styling (palette / hillshade / stretch)
    container.querySelectorAll('.rstyle-colormap').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { colormap: e.target.value || null });
      applyRaster(s); updateRasterLegend(s);
    }));
    container.querySelectorAll('.rstyle-hillshade').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { hillshade: e.target.checked });
      applyRaster(s); updateRasterLegend(s);
    }));
    container.querySelectorAll('.rstyle-min').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { min: e.target.value });
      applyRaster(s); updateRasterLegend(s);
    }));
    container.querySelectorAll('.rstyle-max').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { max: e.target.value });
      applyRaster(s); updateRasterLegend(s);
    }));
    container.querySelectorAll('.rstyle-zfactor').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { zfactor: e.target.value });
      applyRaster(s);
    }));
    container.querySelectorAll('.rstyle-auto').forEach(el => el.addEventListener('click', e => {
      const btn = e.currentTarget, s = btn.dataset.src, row = btn.closest('.layer-style-row');
      autoStretchRaster(s, row.querySelector('.rstyle-min'), row.querySelector('.rstyle-max'), btn);
    }));

    // Zoom to layer
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

    // Visibility toggle
    container.querySelectorAll('.layer-visibility').forEach(cb => {
      cb.addEventListener('change', e => {
        map.setLayoutProperty(
          e.target.dataset.layerId,
          'visibility',
          e.target.checked ? 'visible' : 'none'
        );
      });
    });

    // Opacity slider
    container.querySelectorAll('.layer-opacity-slider').forEach(slider => {
      slider.addEventListener('input', e => {
        const id = e.target.dataset.layerId;
        const mapType = e.target.dataset.layerType;
        const val = parseFloat(e.target.value);
        const label = e.target.closest('.layer-opacity-row').querySelector('.layer-opacity-label');
        label.textContent = Math.round(val * 100) + '%';
        const opacityProp = mapType === 'raster'    ? 'raster-opacity'
                          : mapType === 'fill'       ? 'fill-opacity'
                          : mapType === 'line'       ? 'line-opacity'
                          : mapType === 'circle'     ? 'circle-opacity'
                          : null;
        if (opacityProp) map.setPaintProperty(id, opacityProp, val);
      });
    });
  }

  function getLayerColor(layer) {
    const paint = layer.paint || {};
    return paint['fill-color'] || paint['line-color'] || paint['circle-color'] || '#64748b';
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

  // Legend swatch that mirrors the layer's actual symbol + colour
  function legendSwatch(geom, color) {
    const c = color || '#3b82f6';
    if (geom === 'line')
      return '<svg width="18" height="18" viewBox="0 0 18 18"><line x1="2" y1="9" x2="16" y2="9" stroke="' + c + '" stroke-width="3" stroke-linecap="round"/></svg>';
    if (geom === 'polygon')
      return '<svg width="18" height="18" viewBox="0 0 18 18"><rect x="2.5" y="4" width="13" height="10" fill="' + c + '" fill-opacity="0.45" stroke="' + c + '" stroke-width="1.5"/></svg>';
    if (geom === 'raster')
      return geomIcon('raster');
    return '<svg width="18" height="18" viewBox="0 0 18 18"><circle cx="9" cy="9" r="5" fill="' + c + '" stroke="#fff" stroke-width="1.5"/></svg>';
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

  function rasterLegendHtml(layer) {
    const srcId = layer.source;
    const st = (typeof rasterState !== 'undefined' && rasterState[srcId]) || {};
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
    let sizeField = '';
    if (geom === 'line') {
      const w = (layer.paint && layer.paint['line-width']) ?? 2;
      sizeField = `<div class="layer-style-field"><label>Width</label>` +
        `<input class="layer-style-size" type="number" min="0.5" max="20" step="0.5" value="${w}" ` +
        `data-layer-id="${layer.id}" data-layer-type="${t}"></div>`;
    } else if (geom === 'point') {
      const r = (layer.paint && layer.paint['circle-radius']) ?? 5;
      sizeField = `<div class="layer-style-field"><label>Size</label>` +
        `<input class="layer-style-size" type="number" min="1" max="30" step="1" value="${r}" ` +
        `data-layer-id="${layer.id}" data-layer-type="${t}"></div>`;
    }
    return `<div class="layer-style-row" data-style-for="${layer.id}">` +
        `<div class="layer-style-field"><label>Color</label>` +
        `<input class="layer-style-color" type="color" value="${toHex(color)}" ` +
        `data-layer-id="${layer.id}" data-layer-type="${t}"></div>${sizeField}</div>`;
  }

  function rasterStyleRow(layer) {
    const src = layer.source;
    const bands = layer.metadata && layer.metadata['geodeploy:bands'];
    let html = '';
    if (bands === 1) {
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
    if (st.min != null && st.min !== '' && st.max != null && st.max !== '') params.push('rescale=' + st.min + ',' + st.max);
    if (st.hillshade) {
      params.push('algorithm=hillshade');
      if (st.zfactor && Number(st.zfactor) !== 1) params.push('expression=b1*' + st.zfactor);
    } else if (st.colormap) {
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
    openDownloadDialog(bbox);
  }

  function showHint(text) {
    let h = document.getElementById('gd-hint');
    if (!h) { h = document.createElement('div'); h.id = 'gd-hint'; document.body.appendChild(h); }
    h.textContent = text; h.style.display = 'block';
  }
  function hideHint() { const h = document.getElementById('gd-hint'); if (h) h.style.display = 'none'; }

  function openDownloadDialog(bbox) {
    const slug = (window.GEODEPLOY && window.GEODEPLOY.slug) || (location.pathname.split('/').filter(Boolean)[1] || '');
    const seen = new Set(), items = [];
    (STYLE.layers || []).forEach(l => {
      if (!l.metadata || l.metadata['geodeploy:type'] !== 'vector') return;
      const id = l.metadata['geodeploy:layer_id'];
      if (seen.has(id)) return;
      seen.add(id);
      items.push({ id: id, name: l.metadata['geodeploy:name'] || ('Layer ' + id) });
    });
    const exURL = (id, fmt) => '/api/portals/' + encodeURIComponent(slug) + '/export?layer_id=' +
      encodeURIComponent(id) + '&format=' + fmt + '&bbox=' + bbox.join(',');

    const old = document.getElementById('gd-download');
    if (old) old.remove();
    const dlg = document.createElement('div');
    dlg.id = 'gd-download';
    dlg.innerHTML =
      '<div class="gd-download-box">' +
        '<div class="gd-download-head"><span>Download selected area</span>' +
        '<button class="gd-download-close" aria-label="Close">&times;</button></div>' +
        '<div class="gd-download-body">' +
          (items.length ? items.map(it =>
            '<div class="gd-download-row"><span class="gd-download-name" title="' + escHtml(it.name) + '">' + escHtml(it.name) + '</span>' +
            '<a class="gd-download-link" href="' + exURL(it.id, 'geojson') + '" download>GeoJSON</a>' +
            '<a class="gd-download-link" href="' + exURL(it.id, 'csv') + '" download>CSV</a></div>'
          ).join('') : '<p class="gd-download-empty">No vector layers to download.</p>') +
        '</div></div>';
    document.body.appendChild(dlg);
    const close = () => { dlg.remove(); clearDraw(); };
    dlg.querySelector('.gd-download-close').addEventListener('click', close);
    dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
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
