// ── Access control gate ─────────────────────────────────────────────────────
(function () {
  const ACCESS_TYPE = window.GEODEPLOY.accessType;
  const PASSWORD_SHA256 = window.GEODEPLOY.passwordSha256;
  const TITLE = window.GEODEPLOY.title;
  const gate = document.getElementById('access-gate');
  const sub  = document.getElementById('access-gate-sub');

  // Auth-gated tiers: 'organization' (any signed-in workspace member) and 'owner' (only the
  // creator + admins). 'private' is the LEGACY value for members-only — treat it as 'organization'
  // (the migration rewrites stored 'private' → 'organization'; this keeps a stale bundle working).
  const OWNER_ID = window.GEODEPLOY.ownerId;
  if (ACCESS_TYPE === 'owner' || ACCESS_TYPE === 'organization' || ACCESS_TYPE === 'private') {
    const ownerOnly = ACCESS_TYPE === 'owner';
    const token = localStorage.getItem('geodeploy_token');
    function showAuthGate() {
      gate.style.display = 'flex';
      document.getElementById('access-gate-input').style.display = 'none';
      document.getElementById('access-gate-btn').style.display = 'none';
      sub.innerHTML = (ownerOnly
        ? 'This portal is private to its owner. <a href="/" style="color:var(--accent)">Sign in</a> as the owner or an admin to view.'
        : 'This portal is restricted to your organization. <a href="/" style="color:var(--accent)">Sign in</a> to view.');
    }
    if (!token) { showAuthGate(); return; }
    fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + token } })
      .then(r => { if (!r.ok) throw new Error('unauthorized'); return r.json(); })
      .then(u => {
        // Members tier: any signed-in user passes. Owner tier: only the creator or an admin/owner.
        const allowed = ownerOnly
          ? (u.id === OWNER_ID || u.role === 'admin' || u.role === 'owner')
          : true;
        if (!allowed) showAuthGate();
      })
      .catch(showAuthGate);
    return;
  }

  // 'password' portals are enforced SERVER-SIDE now: nginx won't serve this bundle at all until the
  // visitor entered the password on /portal-gate (which set the per-portal unlock cookie). So by the
  // time this runs, access is already granted — nothing to do. (The old client-side sha256 gate was
  // bypassable via view-source and is gone; PASSWORD_SHA256 is retained only for older bundles.)
})();

// ──────────────────────────────────────────────────────────

(function () {
  'use strict';

  const STYLE = window.GEODEPLOY.style;
  const POPUP_CONFIG = window.GEODEPLOY.popupConfig;

  // ── V-11 Template Experiences: layout manifest ──────────────────────────
  // Mirror of portal_generator.resolve_layout (PARITY: also mirrored in PortalEditor.vue). The server
  // already bakes a resolved manifest into style.geodeploy.layout, so this is normally a pass-through;
  // resolveLayout stays defensive (older bundles / partial configs). Absent → webmap = pre-V-11 shell.
  const LAYOUT_ARCHETYPES = {
    webmap:   { regions: { layerList: { side: 'left', mode: 'docked', collapsed: false, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' } },     panels: { layerCatalog: true,  legend: true, basemap: true, about: true,  story: false } },
    storymap: { regions: { layerList: { side: 'left', mode: 'docked', collapsed: false, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'minimal' } }, panels: { layerCatalog: false, legend: true, basemap: true, about: false, story: true } },
  };
  const LAYOUT_ALIASES = { 'webmap+catalog': 'webmap', catalog: 'webmap' };  // dropped Phase-1 archetypes → webmap
  function resolveLayout(config) {
    let arch = (config && config.archetype) || 'webmap';
    arch = LAYOUT_ALIASES[arch] || arch;
    if (!LAYOUT_ARCHETYPES[arch]) arch = 'webmap';
    const base = LAYOUT_ARCHETYPES[arch];
    const out = { archetype: arch, regions: JSON.parse(JSON.stringify(base.regions)), panels: JSON.parse(JSON.stringify(base.panels)) };
    if (config) ['regions', 'panels'].forEach(function (g) {
      const src = config[g] || {};
      Object.keys(src).forEach(function (k) {
        if (src[k] && typeof src[k] === 'object' && out[g][k] && typeof out[g][k] === 'object') Object.assign(out[g][k], src[k]);
        else out[g][k] = src[k];
      });
    });
    return out;
  }
  function applyLayoutAttrs(L) {
    const b = document.body;
    const pos = L.regions.controls.position || 'top-right';
    const cside = pos.indexOf('left') >= 0 ? 'left' : 'right';
    b.dataset.archetype = L.archetype;
    b.dataset.layerlistSide = L.regions.layerList.side;   // → data-layerlist-side (L/R)
    b.dataset.layerlist = L.regions.layerList.mode;       // → data-layerlist (docked/floating)
    b.dataset.controlsSide = cside;                       // → data-controls-side (L/R, for flyout dir)
    b.dataset.controlsPos = pos;                          // → data-controls-pos (the full corner)
    b.dataset.header = L.regions.header.style;            // → data-header
    // collide: control cluster at the TOP corner on the list's side → push controls below the on-map
    // toggle. sameside: controls on the list's side at ANY corner → the floating list leaves the
    // control column free (so it never covers the controls, top OR bottom).
    b.dataset.collide = (pos === 'top-' + L.regions.layerList.side) ? '1' : '0';
    b.dataset.sameside = (cside === L.regions.layerList.side) ? '1' : '0';
  }
  const LAYOUT = resolveLayout(STYLE.geodeploy && STYLE.geodeploy.layout);
  applyLayoutAttrs(LAYOUT);
  // Corner for the map-control cluster (basemap/globe/zoom/tools/home/zoom-all/draw-zoom).
  const CTRL_CORNERS = ['top-left', 'top-right', 'bottom-left', 'bottom-right'];
  const CTRL_POS = CTRL_CORNERS.indexOf(LAYOUT.regions.controls.position) >= 0 ? LAYOUT.regions.controls.position : 'top-right';
  // True when this bundle is rendered inside the editor's preview iframe (?edit=1).
  const EDIT_MODE = new URLSearchParams(location.search).get('edit') === '1';

  // ── Header brand logo (R3/branding) ─────────────────────────────────────
  const LOGO_PRESETS = {
    layers:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
    globe:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/></svg>',
    pin:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>',
    compass: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>',
  };
  function buildHeaderLogo() {
    const header = document.getElementById('header');
    const title = document.getElementById('portal-title');
    if (!header || !title || document.getElementById('gd-header-logo')) return;
    const logo = (STYLE.geodeploy && STYLE.geodeploy.theme && STYLE.geodeploy.theme.logo) || { kind: 'preset', id: 'layers' };
    if (logo.kind === 'none') return;
    let el;
    if (logo.kind === 'custom' && logo.url) {
      el = document.createElement('img'); el.src = logo.url; el.alt = '';
    } else {
      el = document.createElement('span'); el.innerHTML = LOGO_PRESETS[logo.id] || LOGO_PRESETS.layers;
    }
    el.id = 'gd-header-logo';
    header.insertBefore(el, title);
  }
  buildHeaderLogo();

  // ── Theme (light/dark) ──────────────────────────────────
  // Dark = html[data-theme=dark] variable overrides in portal.css (template theme.css restyles
  // the LIGHT theme via :root and never clobbers dark). Default follows the visitor's OS color
  // scheme; an explicit toggle choice is persisted per browser.
  (function () {
    const saved = localStorage.getItem('gd-portal-theme');
    // R3: the admin's baked default mode (light/dark/auto); the visitor's own toggle still wins.
    const baked = (STYLE.geodeploy && STYLE.geodeploy.theme && STYLE.geodeploy.theme.mode) || 'auto';
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const wantDark = saved ? (saved === 'dark')
      : (baked === 'dark' ? true : baked === 'light' ? false : prefersDark);
    if (wantDark) {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
    const header = document.getElementById('header');
    if (!header) return;
    const btn = document.createElement('button');
    btn.id = 'gd-theme-toggle';
    btn.type = 'button';
    const sun = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
    const moon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
    function render() {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      btn.innerHTML = isDark ? sun : moon;
      btn.title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
      btn.setAttribute('aria-label', btn.title);
    }
    btn.addEventListener('click', function () {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      if (isDark) document.documentElement.removeAttribute('data-theme');
      else document.documentElement.setAttribute('data-theme', 'dark');
      localStorage.setItem('gd-portal-theme', isDark ? 'light' : 'dark');
      render();
    });
    render();
    const badge = document.getElementById('header-badge');
    if (badge) header.insertBefore(btn, badge); else header.appendChild(btn);
  })();

  // Make tile URLs absolute so MapLibre's Web Worker can resolve them
  // (Workers can't resolve relative URLs against the page origin)
  ;(function absolutifyTileUrls(style) {
    const base = location.origin;
    Object.values(style.sources || {}).forEach(src => {
      if (Array.isArray(src.tiles)) {
        src.tiles = src.tiles.map(u => u.startsWith('/') ? base + u : u);
      }
      // External WFS sources point at our same-origin GeoJSON proxy (root-relative) —
      // absolutify so the worker can fetch it too.
      if (typeof src.data === 'string' && src.data.startsWith('/')) {
        src.data = base + src.data;
      }
      // GeoParquet PMTiles sources: "pmtiles:///api/..." → "pmtiles://<origin>/api/..."
      // (the pmtiles lib fetches the part after pmtiles://, which must be absolute for the worker).
      if (typeof src.url === 'string' && src.url.indexOf('pmtiles://') === 0) {
        const rest = src.url.slice('pmtiles://'.length);
        if (rest.charAt(0) === '/') src.url = 'pmtiles://' + base + rest;
      }
    });
  })(STYLE);

  // Register the pmtiles:// protocol (the lib is loaded via CDN in layout.html) before map init.
  if (window.pmtiles && maplibregl && !maplibregl.__pmtilesRegistered) {
    maplibregl.addProtocol('pmtiles', new window.pmtiles.Protocol().tile);
    maplibregl.__pmtilesRegistered = true;
  }

  // ── Sidebar toggle ──────────────────────────────────────
  const sidebar = document.getElementById('sidebar');
  // V-11: honour the manifest's start-collapsed region option.
  if (LAYOUT.regions.layerList.collapsed) sidebar.classList.add('collapsed');
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
  const savedView = STYLE.geodeploy?.view;
  if (savedView && Array.isArray(savedView.center) && savedView.center.length === 2) {
    // Admin pinned a specific extent/zoom during portal creation — honour it exactly.
    try {
      map.jumpTo({
        center: savedView.center,
        zoom: savedView.zoom != null ? savedView.zoom : 2,
        bearing: savedView.bearing || 0,
        pitch: savedView.pitch || 0,
      });
    } catch (e) { /* ignore — keep default view */ }
  } else if (validLonLatBounds(bounds)) {
    try {
      map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], {
        padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 },
        duration: 0,
      });
    } catch (e) { /* ignore — keep default view */ }
  }

  // ── deck.gl overlay for GeoParquet layers ───────────────
  // GeoParquet layers are too big for a MapLibre geojson source, so they render in a deck.gl
  // MapboxOverlay refetched on pan/zoom. PRIMARY data path: DuckDB-WASM in the browser reading the
  // layer's partitioned GeoParquet directly over HTTP Range requests (only the row groups under
  // the viewport; partition files are immutable → browser-cached hard). FALLBACK: the PUBLIC
  // features.geojson viewport query (non-prepped layers, non-4326 CRS, old browsers, or any wasm
  // failure). (PMTiles-tiled layers instead come through the normal vector path above.)
  // Overlay draws above all MapLibre layers (interleaved:false); deck layers get a basic switcher
  // row (show/hide + zoom) but not the full symbology popover yet.
  const DECK_LAYERS = (STYLE.geodeploy && STYLE.geodeploy.deckLayers) || [];
  const deckState = {};  // layer_id → { visible, data }
  DECK_LAYERS.forEach(function (d) { deckState[d.layer_id] = { visible: d.visible !== false, data: null }; });
  let deckOverlay = null;

  // ── GeoArrow binary transport (detail) ──────────────────
  // The server sends viewport detail as a GeoArrow Arrow IPC stream (geometry only, built
  // WKB→ragged-arrays→Arrow with no GeoJSON text); the browser hands the buffer zero-copy to
  // @geoarrow/deck.gl-layers — no JSON parse, no per-feature JS objects. If any module fails to
  // load (CDN/offline) or the transport errors, everything falls back to the GeoJSON path on the
  // classic UMD deck build — identical output, just the slower transport.
  const ARROW_DETAIL = true;
  const gdArrow = { broken: false };
  let DK = null;  // the ONE deck module set in use: {MapboxOverlay, GeoJsonLayer, geo?, tableFromIPC?}

  function loadDeckModules() {
    function umd() {
      return (window.deck && deck.MapboxOverlay)
        ? { MapboxOverlay: deck.MapboxOverlay, GeoJsonLayer: deck.GeoJsonLayer,
            geo: null, tableFromIPC: null }
        : null;
    }
    if (!ARROW_DETAIL) return Promise.resolve(umd());
    // Preferred: the SELF-CONTAINED vendored bundle published next to index.html (one file, one
    // deck core, same-origin — works offline and avoids cross-CDN ESM interop, which failed in
    // practice with the jsDelivr module set).
    const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
    return import(base + 'deck-arrow.esm.js').then(function (m) {
      return { MapboxOverlay: m.MapboxOverlay, GeoJsonLayer: m.GeoJsonLayer,
               geo: m.geoarrow, tableFromIPC: m.tableFromIPC };
    }).catch(function (e) {
      // Straight to the UMD GeoJSON path — no CDN module-set attempt: cross-CDN ESM resolution
      // produced duplicate luma.gl copies (hard version-check throw) and just wasted seconds
      // failing before the fallback (observed live 2026-07-10).
      console.warn('[geodeploy] vendored GeoArrow bundle unavailable; using GeoJSON transport', e);
      return umd();
    });
  }

  function deckHexToRgb(hex) {
    const h = String(hex || '#3b82f6').replace('#', '');
    const f = h.length === 3 ? h.split('').map(function (c) { return c + c; }).join('') : h;
    const n = parseInt(f, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function makeDeckLayer(d) {
    const st = deckState[d.layer_id];
    if (!st || !st.visible || !st.data) return null;
    const geom = (d.geometry || '').toLowerCase();
    const isPoly = geom.indexOf('polygon') !== -1, isLine = geom.indexOf('line') !== -1;
    const rgb = deckHexToRgb(d.color), outline = deckHexToRgb(d.outline_color || '#1d4ed8');
    const op = d.opacity != null ? d.opacity : 1;
    if (st.data.__arrowTable) {
      // GeoArrow detail: the Arrow table is consumed zero-copy by @geoarrow/deck.gl-layers —
      // never converted to GeoJSON. Styling mirrors the GeoJsonLayer branch below.
      const t = st.data.__arrowTable;
      if (isLine) {
        return new DK.geo.GeoArrowPathLayer({
          id: 'deck_' + d.layer_id, data: t, pickable: false,
          getColor: rgb.concat(Math.round(255 * op)),
          getWidth: d.line_width != null ? d.line_width : 2,
          widthUnits: 'pixels', widthMinPixels: d.line_width || 2,
        });
      }
      if (isPoly) {
        return new DK.geo.GeoArrowPolygonLayer({
          id: 'deck_' + d.layer_id, data: t, pickable: false,
          filled: true, stroked: true,
          getFillColor: rgb.concat(Math.round(255 * op * (d.fill_opacity != null ? d.fill_opacity : 0.45))),
          getLineColor: outline.concat(Math.round(255 * op)),
          lineWidthUnits: 'pixels',
          getLineWidth: d.line_width != null ? d.line_width : 1,
          lineWidthMinPixels: 1,
        });
      }
      return new DK.geo.GeoArrowScatterplotLayer({
        id: 'deck_' + d.layer_id, data: t, pickable: false,
        getFillColor: rgb.concat(Math.round(255 * op)),
        radiusUnits: 'pixels',
        getRadius: d.radius != null ? d.radius : 5,
        radiusMinPixels: 2,
      });
    }
    if (st.data.__overview) {
      // Large-scale representation: the manifest's partition grid shaded by feature density.
      return new DK.GeoJsonLayer({
        id: 'deck_' + d.layer_id,
        data: st.data,
        pickable: false,
        filled: true,
        stroked: true,
        getFillColor: function (f) { return rgb.concat(Math.round(200 * op * f.properties.density)); },
        getLineColor: rgb.concat(Math.round(60 * op)),
        lineWidthUnits: 'pixels',
        getLineWidth: 0.5,
      });
    }
    return new DK.GeoJsonLayer({
      id: 'deck_' + d.layer_id,
      data: st.data,
      pickable: false,
      filled: !isLine,
      stroked: true,
      getFillColor: rgb.concat(Math.round(255 * op * (isPoly ? (d.fill_opacity != null ? d.fill_opacity : 0.45) : 1))),
      getLineColor: (isPoly ? outline : rgb).concat(Math.round(255 * op)),
      lineWidthUnits: 'pixels',
      getLineWidth: d.line_width != null ? d.line_width : (isLine ? 2 : 1),
      lineWidthMinPixels: isLine ? (d.line_width || 2) : 1,
      pointType: 'circle',
      pointRadiusUnits: 'pixels',
      getPointRadius: d.radius != null ? d.radius : 5,
      pointRadiusMinPixels: 2,
    });
  }

  function rebuildDeck() {
    if (!deckOverlay) return;
    // DECK_LAYERS is in reversed-config order (config[0] last) → config[0] draws on top.
    deckOverlay.setProps({ layers: DECK_LAYERS.map(makeDeckLayer).filter(Boolean) });
  }

  // ── DuckDB-WASM client for prepped GeoParquet ───────────
  // A prepped layer is a prefix of __cell=N/*.parquet files plus a manifest.json (partition grid,
  // covering column, cell→file map — see api duckdb_engine.build_manifest). The browser cannot
  // LIST S3, so the manifest names the files; each is registered as a DuckDB file handle with
  // directIO=true so duckdb-wasm streams it via HTTP Range requests through the public
  // /parquet/{path} proxy (NOT the in-WASM httpfs extension, which is unreliable). No spatial
  // extension is loaded: the covering columns filter on plain numerics and the WKB geometry is
  // decoded in JS below — this dodges the GeoParquet-version check and the extension download.
  const DUCKDB_CDN = 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
  const gdWasm = {
    supported: typeof WebAssembly === 'object' && typeof Worker === 'function',
    broken: false,     // any real wasm failure → permanent server fallback (no per-pan retries)
    dbPromise: null, duckdb: null, conn: null,
    manifests: {},     // layer_id → manifest object | 'unsupported'
    registered: {},    // layer_id → { handleName: true }
    seq: {},           // layer_id → latest fetch token (stale responses are dropped)
  };

  function sqlIdent(name) { return '"' + String(name).replace(/"/g, '""') + '"'; }
  function sqlField(name) { return "'" + String(name).replace(/[^A-Za-z0-9_]/g, '') + "'"; }

  function getWasmDb() {
    if (!gdWasm.dbPromise) {
      gdWasm.dbPromise = (async function () {
        const duckdb = await import(DUCKDB_CDN);
        const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
        // CDN worker scripts can't be constructed cross-origin; the importScripts blob shim is
        // the documented duckdb-wasm CDN pattern.
        const workerUrl = URL.createObjectURL(new Blob(
          ['importScripts("' + bundle.mainWorker + '");'], { type: 'text/javascript' }));
        const worker = new Worker(workerUrl);
        const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
        URL.revokeObjectURL(workerUrl);
        await db.open({});  // initialises the runtime/filesystem config; remote reads fail without it
        gdWasm.duckdb = duckdb;
        gdWasm.conn = await db.connect();
        return db;
      })().catch(function (e) { gdWasm.broken = true; throw e; });
    }
    return gdWasm.dbPromise;
  }

  function getManifest(d) {
    const id = d.layer_id;
    if (gdWasm.manifests[id]) return Promise.resolve(gdWasm.manifests[id]);
    return fetch(location.origin + d.parquet.manifest)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (m) {
        // Client-side reprojection is deferred: a non-4326 dataset uses the server fallback
        // (which reprojects). Missing grid/covering also → fallback.
        const ok = m && m.grid && m.covering && m.cells && m.geometry_column &&
          (!m.crs || m.crs === 'EPSG:4326');
        gdWasm.manifests[id] = ok ? m : 'unsupported';
        return gdWasm.manifests[id];
      })
      .catch(function () { gdWasm.manifests[id] = 'unsupported'; return 'unsupported'; });
  }

  // Mirrors the server's partition pruning (duckdb_engine.query_features_geojson): grid cell =
  // ix*grid + iy, +1-cell pad for features straddling a boundary.
  function cellsForBbox(g, bbox) {
    const gsz = g.grid | 0, pad = 1;
    function ci(v, lo, span) { return Math.floor((v - lo) / (span || 1.0) * gsz); }
    const ix0 = Math.max(0, ci(bbox[0], g.minx, g.spanx) - pad);
    const ix1 = Math.min(gsz - 1, ci(bbox[2], g.minx, g.spanx) + pad);
    const iy0 = Math.max(0, ci(bbox[1], g.miny, g.spany) - pad);
    const iy1 = Math.min(gsz - 1, ci(bbox[3], g.miny, g.spany) + pad);
    const cells = [];
    if (ix0 <= ix1 && iy0 <= iy1)
      for (let ix = ix0; ix <= ix1; ix++)
        for (let iy = iy0; iy <= iy1; iy++) cells.push(ix * gsz + iy);
    return cells;
  }

  // Minimal WKB → GeoJSON geometry decoder (ISO WKB + EWKB; Z/M ordinates are dropped).
  function decodeWkb(bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const s = { o: 0 };
    function geom() {
      const little = dv.getUint8(s.o) === 1; s.o += 1;
      let t = dv.getUint32(s.o, little); s.o += 4;
      let extra = 0;
      if (t & 0x80000000) extra += 1;             // EWKB Z
      if (t & 0x40000000) extra += 1;             // EWKB M
      if (t & 0x20000000) { s.o += 4; }           // EWKB SRID → skip
      t = t & 0x0fffffff;
      const iso = Math.floor((t % 10000) / 1000); // ISO: 1000=Z, 2000=M, 3000=ZM
      if (iso === 1 || iso === 2) extra += 1; else if (iso === 3) extra += 2;
      const base = t % 1000;
      const dims = 2 + extra;
      function pt() {
        const x = dv.getFloat64(s.o, little), y = dv.getFloat64(s.o + 8, little);
        s.o += 8 * dims;
        return [x, y];
      }
      function ring() {
        const n = dv.getUint32(s.o, little); s.o += 4;
        const out = new Array(n);
        for (let i = 0; i < n; i++) out[i] = pt();
        return out;
      }
      function many(fn) {
        const n = dv.getUint32(s.o, little); s.o += 4;
        const out = new Array(n);
        for (let i = 0; i < n; i++) out[i] = fn();
        return out;
      }
      switch (base) {
        case 1: return { type: 'Point', coordinates: pt() };
        case 2: return { type: 'LineString', coordinates: ring() };
        case 3: return { type: 'Polygon', coordinates: many(ring) };
        case 4: return { type: 'MultiPoint', coordinates: many(geom).map(function (g) { return g.coordinates; }) };
        case 5: return { type: 'MultiLineString', coordinates: many(geom).map(function (g) { return g.coordinates; }) };
        case 6: return { type: 'MultiPolygon', coordinates: many(geom).map(function (g) { return g.coordinates; }) };
        case 7: return { type: 'GeometryCollection', geometries: many(geom) };
        default: return null;
      }
    }
    try { return geom(); } catch (e) { return null; }
  }

  // Detail/overview switch: above this many partition files under the viewport, per-feature
  // detail is never shown — the viewport spans too much data for ANY transport (duckdb-wasm
  // would fetch hundreds of footers; the server response is tens of MB). Instead the layer
  // renders as a density-shaded partition-grid overview built from the manifest's per-cell
  // counts — instant, zero data reads. Zooming in drops under the cap and details load.
  // Kept small: duckdb-wasm's range reads are SERIAL sync-XHRs from the worker, so per-pan
  // cost scales with file count × per-request latency. Keep equal to the editor's
  // DECK_MAX_FILES so both surfaces switch to detail at the same moment.
  const WASM_MAX_FILES = 16;

  // Partition files AND the ESTIMATED feature count under a viewport bbox (via the manifest
  // grid). Rows are weighted by how much of each cell the viewport actually covers — summing
  // whole cells was a bug: the ±1-cell pad means ≥9 candidate cells at ANY deep zoom, which in
  // dense regions summed to millions and locked the layer in overview mode forever (observed:
  // a street-level view showing one solid overview rectangle, never polygons). With area
  // weighting, a street-level view estimates a tiny fraction of the cell → detail; a mid-zoom
  // view covering whole dense cells still estimates high → overview.
  function viewportLoad(m, bbox) {
    const g = m.grid, gsz = g.grid | 0, dx = g.spanx / gsz, dy = g.spany / gsz;
    const files = [];
    let rows = 0;
    cellsForBbox(g, bbox).forEach(function (c) {
      const list = m.cells[String(c)] || [];
      if (!list.length) return;
      const ix = Math.floor(c / gsz), iy = c % gsz;
      const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy;
      const ox = Math.max(0, Math.min(bbox[2], x0 + dx) - Math.max(bbox[0], x0));
      const oy = Math.max(0, Math.min(bbox[3], y0 + dy) - Math.max(bbox[1], y0));
      const frac = Math.min(1, (ox * oy) / (dx * dy || 1));
      list.forEach(function (f) {
        files.push(String(f.key));  // pad cells still count as files to open…
        rows += (f.rows || 0) * frac;  // …but contribute rows only for the visible fraction
      });
    });
    return { files: files, rows: rows };
  }

  // Density-shaded grid rectangles from the manifest (per-cell feature counts) — the
  // large-scale representation of the layer. Built once and cached on the manifest.
  function overviewGeojson(m) {
    if (m.__overviewFc) return m.__overviewFc;
    const g = m.grid, gsz = g.grid | 0, dx = g.spanx / gsz, dy = g.spany / gsz;
    let max = 0;
    const counts = {};
    Object.keys(m.cells).forEach(function (k) {
      const n = (m.cells[k] || []).reduce(function (a, f) { return a + (f.rows || 0); }, 0);
      counts[k] = n;
      if (n > max) max = n;
    });
    const feats = Object.keys(m.cells).map(function (k) {
      const c = +k, ix = Math.floor(c / gsz), iy = c % gsz;
      const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy;
      return {
        type: 'Feature',
        // sqrt so sparse cells stay visible next to the densest ones
        properties: { count: counts[k], density: max ? Math.sqrt(counts[k] / max) : 0 },
        geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + dx, y0],
          [x0 + dx, y0 + dy], [x0, y0 + dy], [x0, y0]]] },
      };
    });
    const fc = { type: 'FeatureCollection', features: feats };
    fc.__overview = true;
    m.__overviewFc = fc;
    return fc;
  }

  function wasmQuery(d, m, files, bbox, limit) {
    const id = d.layer_id;
    return getWasmDb().then(function (db) {
        const reg = gdWasm.registered[id] || (gdWasm.registered[id] = {});
        const handles = [];
        let chain = Promise.resolve();
        files.forEach(function (key) {
          const handle = 'l' + id + '_' + key.replace(/[^A-Za-z0-9_.]/g, '_');
          handles.push(handle);
          if (!reg[handle]) {
            reg[handle] = true;
            chain = chain.then(function () {
              return db.registerFileURL(handle, location.origin + d.parquet.base + key,
                gdWasm.duckdb.DuckDBDataProtocol.HTTP, true);  // directIO → range requests
            });
          }
        });
        const cc = sqlIdent(m.covering.column), fl = m.covering.fields;
        function ce(k) { return 'struct_extract(' + cc + ', ' + sqlField(fl[k]) + ')'; }
        const nb = bbox.map(Number);
        if (!nb.every(isFinite)) return { type: 'FeatureCollection', features: [] };
        const sql = 'SELECT ' + sqlIdent(m.geometry_column) + ' AS __wkb FROM read_parquet([' +
          handles.map(function (h) { return "'" + h + "'"; }).join(',') + ']) WHERE ' +
          ce('xmin') + ' <= ' + nb[2] + ' AND ' + ce('xmax') + ' >= ' + nb[0] + ' AND ' +
          ce('ymin') + ' <= ' + nb[3] + ' AND ' + ce('ymax') + ' >= ' + nb[1] +
          ' LIMIT ' + (limit | 0);
        return chain.then(function () { return gdWasm.conn.query(sql); }).then(function (table) {
          const col = table.getChild('__wkb');
          const feats = [];
          for (let i = 0; i < table.numRows; i++) {
            const wkb = col.get(i);
            if (!wkb) continue;
            const g = decodeWkb(wkb instanceof Uint8Array ? wkb : new Uint8Array(wkb));
            if (g) feats.push({ type: 'Feature', geometry: g, properties: {} });
          }
          return { type: 'FeatureCollection', features: feats };
        });
    });
  }

  // Abort the layer's previous in-flight fetch: its result would be discarded by the sequence
  // token anyway, and rapid zoom-outs otherwise stack several heavy queries in the browser.
  function abortableFetch(layerId, url) {
    const prev = gdWasm.aborters && gdWasm.aborters[layerId];
    if (prev) { try { prev.abort(); } catch (e) { /* already settled */ } }
    const ctl = typeof AbortController === 'function' ? new AbortController() : null;
    (gdWasm.aborters || (gdWasm.aborters = {}))[layerId] = ctl;
    return fetch(url, ctl ? { signal: ctl.signal } : undefined);
  }

  function serverViewportGeojson(d, bbox, limit) {
    const url = location.origin + '/api/data/vector/' + d.layer_id +
      '/features.geojson?bbox=' + encodeURIComponent(bbox.join(',')) + '&limit=' + limit;
    return abortableFetch(d.layer_id, url)
      .then(function (r) { return r.ok ? r.json() : null; });
  }

  function arrowViewport(d, bbox, limit) {
    const url = location.origin + '/api/data/vector/' + d.layer_id +
      '/features.arrow?bbox=' + encodeURIComponent(bbox.join(',')) + '&limit=' + limit;
    return abortableFetch(d.layer_id, url).then(function (r) {
      if (r.status === 204) return { type: 'FeatureCollection', features: [] };
      if (!r.ok) throw new Error('features.arrow HTTP ' + r.status);
      return r.arrayBuffer().then(function (buf) {
        return { __arrowTable: DK.tableFromIPC(new Uint8Array(buf)) };
      });
    });
  }

  // Light layers (small TOTAL feature count — world countries, modest point sets) always show
  // full detail at every zoom; the grid overview is only for datasets too heavy to ship at
  // large scale.
  const DETAIL_MAX_FEATURES = 50000;
  // Detail is also gated by the candidate rows under the viewport (manifest per-cell counts):
  // a mid-zoom view over dense data can span few files but ~1M features — the covering scan
  // alone takes 10-25 s server-side. Above this, show the overview instead.
  const DETAIL_MAX_ROWS = 400000;

  // DuckDB-WASM direct range reads are DISABLED pending faster range serving: through the
  // FastAPI proxy each serial sync-XHR costs ~50-70 ms and ONE detail query issues hundreds
  // (parquet footers + bbox-column pages + geometry pages across up to 16 partition files),
  // so detail loads took far longer than the server query they replaced and queued up behind
  // pans (observed live 2026-07-10: "requests forever, never displays"). The server viewport
  // query answers the same request in one response (~1.5-5 s). Flip this back on to experiment
  // once ranges are served by nginx directly from MinIO (~5 ms/request — see notes
  // §0h-addendum-2); the manifest/overview/grid pipeline stays live either way.
  const WASM_DETAIL_READS = false;

  // Whether a viewport is small enough to load per-feature DETAIL (vs the density overview).
  // Detail is fetched from the SERVER in ONE request (GeoArrow/GeoJSON), so the partition-FILE count
  // only matters for the (currently disabled) duckdb-wasm serial-read path — otherwise gate on the
  // frac-weighted ROW estimate alone. Gating on files locked dense cells (split into many partition
  // files because they're dense) into overview at EVERY zoom, so cities never showed individual
  // features however far you zoomed in.
  function fitsDetail(m, load) {
    if ((m.feature_count || 0) <= DETAIL_MAX_FEATURES) return true;   // light layer → always detail
    if (load.rows > DETAIL_MAX_ROWS) return false;                     // too much data in view → overview
    if (WASM_DETAIL_READS && load.files.length > WASM_MAX_FILES) return false;
    return true;
  }

  function fetchDeckLayer(d, bbox, limit) {
    if (!(d.parquet && d.parquet.manifest)) return serverViewportGeojson(d, bbox, limit);
    return getManifest(d).then(function (m) {
      if (m === 'unsupported') return serverViewportGeojson(d, bbox, limit);
      const light = (m.feature_count || 0) <= DETAIL_MAX_FEATURES;
      const load = viewportLoad(m, bbox);
      const files = load.files;
      if (!files.length) return { type: 'FeatureCollection', features: [] };
      // Heavy layer over too much DATA under the viewport → density grid, never details. (File count
      // no longer gates this — detail is one server request; see fitsDetail.)
      if (!fitsDetail(m, load)) {
        return overviewGeojson(m);
      }
      // This is a DETAIL fetch: if the previous view left the coarse overview grid cached, clear
      // it NOW — a zoomed-in view must never keep showing the whole-extent grid while features
      // load (brief blank is better than a misleading grid).
      const st = deckState[d.layer_id];
      if (st && st.data && st.data.__overview) { st.data = null; rebuildDeck(); }
      let p;
      // Preferred detail transport: GeoArrow binary (one request, zero JSON on either side).
      if (ARROW_DETAIL && DK && DK.geo && DK.tableFromIPC && !gdArrow.broken) {
        p = arrowViewport(d, bbox, limit).catch(function (e) {
          if (e && e.name === 'AbortError') throw e;  // superseded, not broken
          gdArrow.broken = true;  // hard failure → GeoJSON transport for the session
          console.warn('[geodeploy] GeoArrow transport failed; using GeoJSON fallback', e);
          return serverViewportGeojson(d, bbox, limit);
        });
      } else if (WASM_DETAIL_READS && gdWasm.supported && !gdWasm.broken &&
                 files.length <= WASM_MAX_FILES) {
        p = wasmQuery(d, m, files, bbox, limit).catch(function (e) {
          gdWasm.broken = true;  // one hard failure → stay on the server path for the session
          console.warn('[geodeploy] duckdb-wasm read failed; using server fallback', e);
          return serverViewportGeojson(d, bbox, limit);
        });
      } else {
        // Light layer spread over many small partitions, or wasm unavailable: one server call.
        p = serverViewportGeojson(d, bbox, limit);
      }
      // Only DETAIL fetches show the loading pill — overview responses are instant.
      deckLoading(1);
      return p.then(function (x) { deckLoading(-1); return x; },
                    function (e) { deckLoading(-1); throw e; });
    });
  }

  // Small "Loading features…" pill over the map while any detail fetch is in flight — visible
  // feedback that something is happening (user request 2026-07-10). Counter-based so overlapping
  // per-layer fetches keep it up until the last one settles.
  let gdLoadingCount = 0, gdLoaderEl = null;
  function deckLoading(delta) {
    gdLoadingCount = Math.max(0, gdLoadingCount + delta);
    if (!gdLoaderEl) {
      const host = document.getElementById('map') || document.body;
      gdLoaderEl = document.createElement('div');
      gdLoaderEl.id = 'gd-deck-loading';
      gdLoaderEl.innerHTML = '<span class="gd-spin"></span>Loading features…';
      gdLoaderEl.style.display = 'none';
      host.appendChild(gdLoaderEl);
    }
    gdLoaderEl.style.display = gdLoadingCount > 0 ? 'flex' : 'none';
  }

  // Fewer features when zoomed out: a country-wide view is a capped subset either way, and the
  // full 50k at low zoom is what made portal-open take a 67 MB response.
  function deckLimitForZoom() {
    const z = map.getZoom();
    return z < 7 ? 10000 : z < 10 ? 25000 : 50000;
  }

  // Incremental viewport loading: fetch a BUFFERED bbox (bigger than the screen) and skip refetching
  // while the viewport stays inside the region we already loaded at this zoom. Without this, every
  // pan reloaded the whole viewport — including the part already on screen — so panning stuttered and
  // returning to a loaded area re-ran "Loading features…". DECK_FETCH_PAD is the buffer added on each
  // side; the row limit is scaled to the buffer's area so on-screen density is preserved.
  const DECK_FETCH_PAD = 0.35;
  const DECK_PAD_AREA = (1 + 2 * DECK_FETCH_PAD) * (1 + 2 * DECK_FETCH_PAD);
  const DECK_FETCH_MAX = 150000;   // server /features caps at 200k; leave headroom
  function bboxContains(outer, inner) {
    return !!outer && inner[0] >= outer[0] && inner[1] >= outer[1] &&
           inner[2] <= outer[2] && inner[3] <= outer[3];
  }
  function padBbox(b, f) {
    const dx = (b[2] - b[0]) * f, dy = (b[3] - b[1]) * f;
    return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy];
  }

  function fetchDeck(refetch) {
    if (!deckOverlay) return;
    const b = map.getBounds();
    const vb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    const zb = Math.round(map.getZoom());
    const pending = DECK_LAYERS.filter(function (d) {
      const st = deckState[d.layer_id];
      if (!st || !st.visible) return false;
      if (!st.data) return true;                       // never loaded → fetch
      if (!refetch) return false;                      // style-only refresh → keep cache
      // Already loaded a buffered region covering this viewport at this zoom → nothing to do.
      if (st.loaded && st.loaded.band === zb && bboxContains(st.loaded.bbox, vb)) return false;
      return true;
    }).map(function (d) {
      const fb = padBbox(vb, DECK_FETCH_PAD);
      const limit = Math.min(DECK_FETCH_MAX, Math.round(deckLimitForZoom() * DECK_PAD_AREA));
      const token = (gdWasm.seq[d.layer_id] = (gdWasm.seq[d.layer_id] || 0) + 1);
      return fetchDeckLayer(d, fb, limit)
        .then(function (fc) {
          // Drop stale responses: a later pan may already have resolved.
          if (fc && gdWasm.seq[d.layer_id] === token) {
            deckState[d.layer_id].data = fc;
            // Remember what we covered so the next pan can skip. The overview grid already spans the
            // whole extent, so mark it world-wide (only a zoom-band change reloads it).
            deckState[d.layer_id].loaded = fc.__overview
              ? { bbox: [-180, -90, 180, 90], band: zb }
              : { bbox: fb, band: zb };
          }
        })
        .catch(function () {});
    });
    Promise.all(pending).then(rebuildDeck);
  }

  function initDeck() {
    if (!DECK_LAYERS.length) return;
    loadDeckModules().then(function (dk) {
      if (!dk) return;  // neither ESM nor UMD deck available — deck layers simply don't render
      DK = dk;
      initDeckWithModules();
    });
  }

  function initDeckWithModules() {
    deckOverlay = new DK.MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(deckOverlay);
    // Deck-only portal without an admin-pinned view: the merged bounds can be stretched by
    // far-flung outlier features (e.g. overseas territories on a mainland dataset), so the
    // fitBounds above opens on a huge, mostly-empty area AND makes the first fetch
    // near-full-extent. The manifest's partition grid extent is the percentile CORE of the
    // data (PREP_EXTENT_QUANTILE at prep) — refit to it before the first fetch. Benefits the
    // server-fallback path too (smaller first bbox), so it is not gated on wasm support.
    const userMapLayers = (STYLE.layers || []).filter(function (l) {
      return l.metadata && l.metadata['geodeploy:name'];
    });
    const manifested = DECK_LAYERS.filter(function (d) { return d.parquet && d.parquet.manifest; });
    // When the server baked the core extent into geodeploy.bounds, the initial fitBounds ABOVE already
    // opened here — skip the refit entirely (no on-load snap). Only older/unbaked bundles reach the
    // client-side refit below, and it now glides instead of snapping.
    const coreFitted = !!(STYLE.geodeploy && STYLE.geodeploy.coreFitted);
    const refit = (!savedView && !userMapLayers.length && manifested.length && !coreFitted)
      ? Promise.all(manifested.map(getManifest)).then(function (ms) {
          let u = null;
          ms.forEach(function (m) {
            if (!m || m === 'unsupported' || !m.grid) return;
            const g = m.grid, e = [g.minx, g.miny, g.minx + g.spanx, g.miny + g.spany];
            u = u ? [Math.min(u[0], e[0]), Math.min(u[1], e[1]),
                     Math.max(u[2], e[2]), Math.max(u[3], e[3])] : e;
          });
          if (!(u && validLonLatBounds(u))) return;
          // Glide (not a hard snap) to the core extent, and resolve only once the camera SETTLES so the
          // moveend/first-fetch below aren't armed mid-animation (which would fire a second fetch).
          return new Promise(function (resolve) {
            let done = false;
            const finish = function () { if (done) return; done = true; resolve(); };
            map.once('moveend', finish);
            map.fitBounds([[u[0], u[1]], [u[2], u[3]]], {
              padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 },
              duration: 650,
            });
            setTimeout(finish, 900);  // safety: a barely-moving camera may not emit moveend
          });
        }).catch(function () {})
      : Promise.resolve();
    refit.then(function () {
      // moveend attached AFTER the refit so the refit itself doesn't double-fetch.
      map.on('moveend', function () { fetchDeck(true); });
      // Mid-gesture: the moment the viewport qualifies for DETAIL, hide the coarse grid — don't
      // wait for moveend + the fetch (the grid lingering at zoomed-in views reads as wrong data).
      let gdMoveRaf = false;
      map.on('move', function () {
        if (gdMoveRaf) return;
        gdMoveRaf = true;
        requestAnimationFrame(function () {
          gdMoveRaf = false;
          let changed = false;
          const b = map.getBounds();
          const vb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
          DECK_LAYERS.forEach(function (d) {
            const st = deckState[d.layer_id];
            if (!st || !st.visible || !st.data || !st.data.__overview) return;
            const m = gdWasm.manifests[d.layer_id];
            if (!m || m === 'unsupported' || !m.grid) return;
            const load = viewportLoad(m, padBbox(vb, DECK_FETCH_PAD));  // same padded bbox as the fetch
            if (fitsDetail(m, load)) {   // now fits detail → drop the overview so a detail fetch runs
              st.data = null;
              changed = true;
            }
          });
          if (changed) rebuildDeck();
        });
      });
      fetchDeck(true);
    });
  }

  // Append a basic switcher row per deck layer (the MapLibre switcher only knows STYLE.layers).
  function appendDeckRows() {
    if (!DECK_LAYERS.length) return;
    const container = document.getElementById('layer-list');
    if (!container) return;
    const empty = container.querySelector('p');  // "No layers" placeholder when no MapLibre layers
    if (empty) container.removeChild(empty);
    const zoomSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
      '<circle cx="12" cy="12" r="7"/><line x1="12" y1="1" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="23"/>' +
      '<line x1="1" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="23" y2="12"/></svg>';
    // Show config[0] at the top → iterate reversed (DECK_LAYERS holds config[0] last).
    DECK_LAYERS.slice().reverse().forEach(function (d) {
      const st = deckState[d.layer_id];
      const canZoom = validLonLatBounds(d.bbox);
      const card = document.createElement('div');
      card.className = 'layer-card';
      card.dataset.ref = 'vector:' + d.layer_id;  // V-13: match to the folder tree (deck layers are vector)
      if (canZoom) card.dataset.bbox = JSON.stringify(d.bbox);  // V-13: for folder zoom-to-extent
      card.innerHTML =
        '<div class="layer-row">' +
          '<span class="layer-drag" style="visibility:hidden">' + dragIcon() + '</span>' +
          '<button class="layer-eye' + (st.visible ? '' : ' off') + '" title="Hide / show" aria-label="Toggle visibility">' + eyeIcon(st.visible) + '</button>' +
          '<span class="layer-swatch-btn" title="' + escHtml(d.name) + '">' + legendSwatch(d.geometry || 'point', d.color, null, 'circle') + '</span>' +
          '<span class="layer-name" title="' + escHtml(d.name) + '">' + escHtml(d.name) + '</span>' +
          '<button class="layer-zoom" title="Zoom to layer" aria-label="Zoom to layer"' + (canZoom ? '' : ' disabled') + '>' + zoomSvg + '</button>' +
        '</div>';
      const eye = card.querySelector('.layer-eye');
      eye.addEventListener('click', function () {
        st.visible = !st.visible;
        eye.innerHTML = eyeIcon(st.visible);
        eye.classList.toggle('off', !st.visible);
        if (st.visible && !st.data) fetchDeck(false); else rebuildDeck();
      });
      const zoomBtn = card.querySelector('.layer-zoom');
      zoomBtn.addEventListener('click', function () {
        if (!validLonLatBounds(d.bbox)) return;
        try {
          map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]],
            { padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 } });
        } catch (e) { /* ignore */ }
      });
      container.appendChild(card);
    });
    const reset = document.getElementById('reset-styling');
    if (reset) reset.style.display = '';
  }

  // ── About page links (portals-as-documentation) ─────────
  // The documentation is a STANDALONE page (about.html), rendered server-side at publish by
  // portal_generator._about_page — GeoNode-style "full page that links to the map". Only the
  // entry points live here: an About pill in the header (always visible) and a sidebar link.
  function buildAboutPanel() {
    if (!(STYLE.geodeploy && STYLE.geodeploy.aboutPage)) return;
    const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
    const href = base + 'about.html';
    const header = document.getElementById('header');
    const badge = document.getElementById('header-badge');
    if (header) {
      const nav = document.createElement('a');
      nav.id = 'gd-about-nav';
      nav.href = href;
      nav.textContent = 'About';
      if (badge) header.insertBefore(nav, badge); else header.appendChild(nav);
    }
    // Prefer the layer-list actions row (built by setupLayerSearch, runs before this); fall back to
    // the sidebar body when there's no list (so the About link still appears).
    const row = document.querySelector('.layer-actions-row .la-right');
    const side = document.createElement('a');
    side.id = 'gd-about-btn';
    side.href = href;
    if (row) {
      side.classList.add('la-icon');
      side.title = 'About this portal';
      side.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="7.5" r="0.6" fill="currentColor"/></svg>';
      row.appendChild(side);
    } else {
      side.innerHTML = '&#9432; About this portal';
      const inner = document.getElementById('sidebar-inner') || sidebar;
      if (inner) inner.appendChild(side);
    }
  }

  // ── Layer switcher ──────────────────────────────────────
  map.on('load', function () {
    ensurePointImages();  // register canvas icons before the symbol layers paint
    // V-11: the layer catalog panel is mounted only when the archetype enables it (storymap hides it).
    // The map LAYERS themselves render from STYLE regardless — this only gates the sidebar UI.
    if (LAYOUT.panels.layerCatalog) {
      // Reverse so the list shows config[0] (drawn on top) at the top of the list.
      const userLayers = STYLE.layers.filter(l => l.metadata && l.metadata['geodeploy:name']).reverse();
      buildLayerSwitcher(userLayers);
      appendDeckRows();
      // V-13: if the portal has a folder tree, reorganize the flat cards into groups (after both
      // MapLibre + deck cards exist, so every card is available to move).
      if (STYLE.geodeploy && STYLE.geodeploy.layerTree) {
        try { applyLayerGroups(STYLE.geodeploy.layerTree); } catch (e) { console.warn('[geodeploy] layer groups failed', e); }
      }
      try { setupLayerSearch(); } catch (e) { console.warn('[geodeploy] layer search failed', e); }
      enableLayerDrag(document.getElementById('layer-list'));  // after cards + deck rows + groups exist
      try { setupListToggle(); } catch (e) { console.warn('[geodeploy] list toggle failed', e); }
      try { applyFloatingLayout(); } catch (e) { console.warn('[geodeploy] floating layout failed', e); }
    }
    initDeck();  // always — GeoParquet deck layers must paint even without the catalog panel
    if (LAYOUT.panels.about) { try { buildAboutPanel(); } catch (e) { console.warn('[geodeploy] About panel failed', e); } }
    if (LAYOUT.panels.basemap) setupBasemaps();  // adds the basemap/home/zoom-all/draw-zoom/tools cluster (CTRL_POS)
    // Globe/2D projection toggle (MapLibre v5 native — no Cesium, no token). Guarded so a
    // cached v4 script can't crash the portal.
    if (maplibregl.GlobeControl) map.addControl(new maplibregl.GlobeControl(), CTRL_POS);
    map.addControl(new maplibregl.NavigationControl({ showCompass: true }), CTRL_POS);  // zoom below them
    // V-11 storymap: build the scrollytelling narrative that drives the camera + layer state.
    if (LAYOUT.archetype === 'storymap') { try { setupStory(); } catch (e) { console.warn('[geodeploy] story failed', e); } }
    // R2: when rendered as the editor's preview (?edit=1), open the postMessage channel + click-to-place.
    try { setupEditMode(); } catch (e) { console.warn('[geodeploy] edit mode failed', e); }
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
    if (!LAYOUT.panels.layerCatalog) return;  // no sidebar catalog to rebuild (e.g. storymap)
    buildLayerSwitcher(STYLE.layers.filter(l => l.metadata && l.metadata['geodeploy:name']).reverse());
    appendDeckRows();  // re-add the GeoParquet deck-layer rows (not in STYLE.layers)
    // Rebuilding the cards flattens the list — restore the folder tree, then clear any active filter.
    if (STYLE.geodeploy && STYLE.geodeploy.layerTree) {
      try { applyLayerGroups(STYLE.geodeploy.layerTree); } catch (e) { /* ignore */ }
    }
    enableLayerDrag(document.getElementById('layer-list'));  // re-mark the rebuilt cards + headers
    _searchActive = false;
    const si = document.querySelector('.layer-search-input');
    if (si) si.value = '';
    showNoResults(document.getElementById('layer-list'), false);
  }

  // ── V-11 Story map: scrollytelling narrative that drives the camera + layers ──
  // A layer ref is 'type:layer_id' (matches card.dataset.ref). Resolve a layer's ref-type the same
  // way the switcher does so 'vector:5' never toggles a raster that happens to share id 5.
  function layerRefType(l) {
    const m = l.metadata || {};
    if (m['geodeploy:external']) return 'external';
    if (l.type === 'raster') return 'raster';
    return 'vector';
  }
  function setLayerVisByRef(ref, visible) {
    const parts = String(ref).split(':'), type = parts[0], lid = parts[1];
    // GeoParquet deck layers live in deckState (keyed by numeric layer_id; refs tag them 'vector').
    if (type === 'vector' && deckState[lid] !== undefined) {
      const st = deckState[lid];
      st.visible = !!visible;
      if (st.visible && !st.data) fetchDeck(false); else rebuildDeck();
      return;
    }
    (STYLE.layers || []).forEach(function (l) {
      const m = l.metadata || {};
      if (layerRefType(l) === type && String(m['geodeploy:layer_id']) === String(lid)) {
        try { map.setLayoutProperty(l.id, 'visibility', visible ? 'visible' : 'none'); } catch (e) {}
      }
    });
  }
  function applyStoryLayers(layerMap) {
    if (!layerMap) return;
    Object.keys(layerMap).forEach(function (ref) { setLayerVisByRef(ref, layerMap[ref]); });
  }
  // Section content is title + body (plain text, XSS-escaped here). s.html is reserved for a future
  // rich-text editor (V-15) and passed through when present.
  function renderStoryHtml(s) {
    if (s.html) return s.html;
    var out = '';
    if (s.title) out += '<h2>' + escHtml(s.title) + '</h2>';
    // R4: an optional per-section image (uploaded via the portal-assets endpoint; a same-origin URL).
    if (s.image) out += '<img class="story-img" src="' + escHtml(String(s.image)) + '" alt="">';
    if (s.body) String(s.body).split(/\n{2,}/).forEach(function (p) {
      if (p.trim()) out += '<p>' + escHtml(p.trim()).replace(/\n/g, '<br>') + '</p>';
    });
    return out;
  }
  function setupStory() {
    const data = STYLE.geodeploy && STYLE.geodeploy.story;
    const panel = document.getElementById('story-panel');
    if (!panel || !data || !Array.isArray(data.sections) || !data.sections.length) return;
    panel.style.display = '';
    panel.innerHTML = '';
    data.sections.forEach(function (s, i) {
      const sec = document.createElement('section');
      sec.className = 'story-section';
      sec.dataset.idx = i;
      sec.innerHTML = renderStoryHtml(s);
      panel.appendChild(sec);
    });
    const sections = Array.prototype.slice.call(panel.querySelectorAll('.story-section'));
    let current = -1;
    function activate(i, fly) {
      if (i === current) return;
      current = i;
      sections.forEach(function (el, j) { el.classList.toggle('active', j === i); });
      const s = data.sections[i];
      if (fly && s && s.view && Array.isArray(s.view.center) && s.view.center.length === 2) {
        try {
          map.flyTo({ center: s.view.center, zoom: s.view.zoom != null ? s.view.zoom : map.getZoom(),
            bearing: s.view.bearing || 0, pitch: s.view.pitch || 0, duration: 1200, essential: true });
        } catch (e) {}
      }
      if (s && s.layers) applyStoryLayers(s.layers);
    }
    const io = new IntersectionObserver(function (entries) {
      // Pick the most-centered intersecting section (rootMargin narrows the trigger band to mid-screen).
      let best = null;
      entries.forEach(function (en) { if (en.isIntersecting && (!best || en.intersectionRatio > best.intersectionRatio)) best = en; });
      if (best) activate(parseInt(best.target.dataset.idx, 10), true);
    }, { rootMargin: '-45% 0px -45% 0px', threshold: [0, 0.5, 1] });
    sections.forEach(function (el) { io.observe(el); });
    // E4: in the editor preview each edit reloads the iframe — DON'T fly to section 0 (it yanks the
    // author's map away). Just mark it active; the baked initial_view keeps the author's camera.
    activate(0, !EDIT_MODE);

    // E2: a story must not zoom the map on wheel — repurpose wheel-over-map to scroll the narrative.
    try { map.scrollZoom.disable(); } catch (e) {}
    const mw = document.getElementById('map-wrap');
    if (mw && !mw._storyWheel) {
      mw._storyWheel = true;
      mw.addEventListener('wheel', function (e) {
        if (panel.contains(e.target)) return;  // already over the column → native scroll
        panel.scrollTop += e.deltaY;
        e.preventDefault();
      }, { passive: false });
    }

    // E1: hidden scrollbar (CSS) + up/down "more" chevrons that appear when content is off-screen.
    if (mw) {
      const up = document.createElement('div'); up.className = 'gd-story-more gd-story-up'; up.innerHTML = chevron('up');
      const down = document.createElement('div'); down.className = 'gd-story-more gd-story-down'; down.innerHTML = chevron('down');
      mw.appendChild(up); mw.appendChild(down);
      function updateArrows() {
        up.classList.toggle('show', panel.scrollTop > 6);
        down.classList.toggle('show', panel.scrollTop + panel.clientHeight < panel.scrollHeight - 6);
      }
      panel.addEventListener('scroll', updateArrows);
      up.addEventListener('click', function () { panel.scrollBy({ top: -panel.clientHeight * 0.8, behavior: 'smooth' }); });
      down.addEventListener('click', function () { panel.scrollBy({ top: panel.clientHeight * 0.8, behavior: 'smooth' }); });
      setTimeout(updateArrows, 120);
    }
  }
  function chevron(dir) {
    const pts = dir === 'up' ? '6 15 12 9 18 15' : '6 9 12 15 18 9';
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="' + pts + '"/></svg>';
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
      card.dataset.ref = type + ':' + meta['geodeploy:layer_id'];  // V-13: match to the folder tree
      if (canZoom) card.dataset.bbox = JSON.stringify(bbox);       // V-13: for folder zoom-to-extent
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
        (type === 'raster' && !meta['geodeploy:external']
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
    // Drag is wired at the end of the load/reset sequence (after deck rows + groups exist).
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

  // ── Tree-aware drag to reorder (changes map draw order; session only) ──
  // Drag a layer card to reorder it, or onto a folder header to move it in; drag a folder header to
  // reorder the whole folder. Delegated on the container so it survives group re-org / reset. After a
  // drop, applyLayerOrder re-reads the cards in DOM order (recursive) and reapplies map z-order.
  let _dragEl = null, _treeDragWired = false;
  function markDraggables(container) {
    container.querySelectorAll('.layer-card').forEach(function (c) { c.setAttribute('draggable', 'true'); });
    container.querySelectorAll('.layer-group > .layer-group-header').forEach(function (h) { h.setAttribute('draggable', 'true'); });
  }
  function enableLayerDrag(container) {
    markDraggables(container);
    if (_treeDragWired) return;   // delegated listeners attach once; re-marking is idempotent
    _treeDragWired = true;
    container.addEventListener('dragstart', function (e) {
      const card = e.target.closest ? e.target.closest('.layer-card') : null;
      const header = e.target.closest ? e.target.closest('.layer-group-header') : null;
      _dragEl = card || (header ? header.parentNode : null);
      if (!_dragEl) return;
      _dragEl.classList.add('dragging');
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', ''); } catch (_) {}
    });
    container.addEventListener('dragend', function () {
      if (_dragEl) _dragEl.classList.remove('dragging');
      clearDropMarks(container);
      _dragEl = null;
      applyLayerOrder(container);
    });
    container.addEventListener('dragover', function (e) {
      if (!_dragEl) return;
      e.preventDefault();
      paintDrop(container, dropTarget(container, _dragEl, e));
    });
    container.addEventListener('drop', function (e) {
      if (!_dragEl) return;
      e.preventDefault();
      performDrop(_dragEl, dropTarget(container, _dragEl, e));
      clearDropMarks(container);
    });
  }
  function dropTarget(container, dragEl, e) {
    const under = document.elementFromPoint(e.clientX, e.clientY);
    if (!under || !under.closest) return null;
    const header = under.closest('.layer-group-header');
    if (header && !dragEl.contains(header)) {   // over a folder header (not our own / an ancestor)
      const grp = header.parentNode, r = header.getBoundingClientRect(), y = (e.clientY - r.top) / (r.height || 1);
      if (y < 0.3) return { el: grp, pos: 'before' };
      if (y > 0.7) return { el: grp, pos: 'after' };
      return { el: grp, pos: 'into' };
    }
    const card = under.closest('.layer-card');
    if (card && card !== dragEl && !dragEl.contains(card)) {
      const r = card.getBoundingClientRect(), y = (e.clientY - r.top) / (r.height || 1);
      return { el: card, pos: y < 0.5 ? 'before' : 'after' };
    }
    return null;
  }
  function performDrop(dragEl, t) {
    if (!t || !t.el) return;
    if (t.pos === 'into') {
      const body = t.el.querySelector(':scope > .layer-group-body');
      if (!body) return;
      body.appendChild(dragEl);
      body.style.display = '';
      const caret = t.el.querySelector(':scope > .layer-group-header .lg-caret');
      if (caret) caret.classList.remove('collapsed');
    } else if (t.pos === 'before') {
      t.el.parentNode.insertBefore(dragEl, t.el);
    } else {
      t.el.parentNode.insertBefore(dragEl, t.el.nextSibling);
    }
  }
  function clearDropMarks(container) {
    container.querySelectorAll('.dnd-before,.dnd-after,.dnd-into').forEach(function (el) {
      el.classList.remove('dnd-before', 'dnd-after', 'dnd-into');
    });
  }
  function paintDrop(container, t) {
    clearDropMarks(container);
    if (!t || !t.el) return;
    let mark = t.el;   // group targets show the indicator on their header, not the whole subtree
    if (t.el.classList.contains('layer-group')) mark = t.el.querySelector(':scope > .layer-group-header') || t.el;
    mark.classList.add('dnd-' + t.pos);
  }
  function applyLayerOrder(container) {
    // Top of the list = topmost on the map. moveLayer(id) with no beforeId moves to top,
    // so move from the bottom card up to the top card.
    const ids = Array.prototype.slice.call(container.querySelectorAll('.layer-card')).map(c => c.dataset.layerId);
    for (let i = ids.length - 1; i >= 0; i--) {
      try { if (map.getLayer(ids[i])) map.moveLayer(ids[i]); } catch (e) { /* ignore */ }
    }
  }

  // ── V-13: reorganize the flat cards into the folder tree (STYLE.geodeploy.layerTree) ──────────
  function lgCaret() {
    return '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>';
  }
  function lgZoomIcon() {
    return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
  }
  // Union the extents of every layer card inside a folder, then fit the map to it.
  function zoomToGroup(body) {
    let b = null;
    body.querySelectorAll('.layer-card').forEach(function (card) {
      if (!card.dataset.bbox) return;
      let x; try { x = JSON.parse(card.dataset.bbox); } catch (e) { return; }
      if (!validLonLatBounds(x)) return;
      b = b ? [Math.min(b[0], x[0]), Math.min(b[1], x[1]), Math.max(b[2], x[2]), Math.max(b[3], x[3])] : x.slice();
    });
    if (!b) return;
    try {
      map.fitBounds([[b[0], b[1]], [b[2], b[3]]],
        { padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 } });
    } catch (e) { /* ignore */ }
  }
  function applyLayerGroups(tree) {
    const container = document.getElementById('layer-list');
    if (!container || !tree || !tree.length) return;
    const cardByRef = {};
    container.querySelectorAll('.layer-card').forEach(function (c) {
      if (c.dataset.ref) cardByRef[c.dataset.ref] = c;
    });

    function render(nodes, parent) {
      nodes.forEach(function (node) {
        if (node.layer_id != null && node.layer_type) {
          const card = cardByRef[node.layer_type + ':' + node.layer_id];
          if (card) parent.appendChild(card);   // MOVE the existing card — its handlers stay intact
          return;
        }
        if (!node.children) return;
        const grp = document.createElement('div');
        grp.className = 'layer-group' + (node.exclusive ? ' exclusive' : '');
        const collapsed = !!node.collapsed;
        const header = document.createElement('div');
        header.className = 'layer-group-header';
        header.innerHTML =
          '<span class="lg-caret' + (collapsed ? ' collapsed' : '') + '">' + lgCaret() + '</span>' +
          '<span class="lg-name" title="' + escHtml(node.name || 'Group') + '">' + escHtml(node.name || 'Group') + '</span>' +
          '<button class="lg-zoom" title="Zoom to this folder" aria-label="Zoom to folder">' + lgZoomIcon() + '</button>' +
          (node.exclusive ? '' : '<button class="lg-toggle-all layer-eye" title="Show / hide all">' + eyeIcon(true) + '</button>');
        grp.appendChild(header);
        const body = document.createElement('div');
        body.className = 'layer-group-body';
        if (collapsed) body.style.display = 'none';
        if (node.description) {
          const desc = document.createElement('div');
          desc.className = 'lg-desc';
          desc.textContent = node.description;
          body.appendChild(desc);
        }
        grp.appendChild(body);
        parent.appendChild(grp);
        render(node.children, body);
        wireGroup(header, body, node);
      });
    }
    const frag = document.createDocumentFragment();
    render(tree, frag);
    container.innerHTML = '';
    container.appendChild(frag);
  }
  function wireGroup(header, body, node) {
    const caret = header.querySelector('.lg-caret');
    header.addEventListener('click', function (e) {
      if (e.target.closest('.lg-toggle-all') || e.target.closest('.lg-zoom')) return;   // those buttons handle their own click
      const hidden = body.style.display === 'none';
      body.style.display = hidden ? '' : 'none';
      caret.classList.toggle('collapsed', !hidden);
    });
    const zoomBtn = header.querySelector('.lg-zoom');
    if (zoomBtn) zoomBtn.addEventListener('click', function (e) { e.stopPropagation(); zoomToGroup(body); });
    const toggleAll = header.querySelector('.lg-toggle-all');
    if (toggleAll) {
      toggleAll.addEventListener('click', function (e) {
        e.stopPropagation();
        // Descendant LAYER eyes only (group toggle-all eyes live in .layer-group-header, not .layer-card).
        const eyes = Array.prototype.slice.call(body.querySelectorAll('.layer-card .layer-eye'));
        const anyOff = eyes.some(function (x) { return x.classList.contains('off'); });
        eyes.forEach(function (x) {
          const isOff = x.classList.contains('off');
          if (anyOff && isOff) x.click(); else if (!anyOff && !isOff) x.click();
        });
        toggleAll.innerHTML = eyeIcon(anyOff);
        toggleAll.classList.toggle('off', !anyOff);
      });
    }
    if (node.exclusive) {   // showing one direct-child layer hides its siblings (radio behavior)
      const directEyes = Array.prototype.slice.call(body.children)
        .filter(function (el) { return el.classList.contains('layer-card'); })
        .map(function (el) { return el.querySelector('.layer-eye'); })
        .filter(Boolean);
      directEyes.forEach(function (eye) {
        eye.addEventListener('click', function () {
          setTimeout(function () {
            if (!eye.classList.contains('off')) {
              directEyes.forEach(function (o) { if (o !== eye && !o.classList.contains('off')) o.click(); });
            }
          }, 0);
        });
      });
    }
  }

  // ── V-13: search / filter the layer list ─────────────────────────────────
  // A thin client-side filter over the rendered cards (name match). Hides
  // non-matching layers and any folder left with no visible layer; while a
  // query is active, matching folders are force-expanded so hits are visible.
  let _searchActive = false;
  function setupLayerSearch() {
    const container = document.getElementById('layer-list');
    if (!container) return;
    const parent = container.parentNode;
    if (!parent || parent.querySelector('.layer-actions-row')) return;   // already added
    const hasGroups = !!container.querySelector('.layer-group');
    const nCards = container.querySelectorAll('.layer-card').length;

    // Search box — only worth it for ≥2 layers (but keep it if there are folders).
    if (nCards >= 2 || hasGroups) {
      const wrap = document.createElement('div');
      wrap.className = 'layer-search';
      wrap.innerHTML =
        '<svg class="layer-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>' +
        '<input type="search" class="layer-search-input" placeholder="Search layers…" aria-label="Search layers" autocomplete="off">';
      parent.insertBefore(wrap, container);
      const input = wrap.querySelector('.layer-search-input');
      input.addEventListener('input', function () { filterLayers(input.value); });
    }

    // Actions row: [expand/collapse all (folders only)] … [Reset styling] [About] — always present so
    // Reset + About live here instead of dangling below the list.
    const acts = document.createElement('div');
    acts.className = 'layer-group-actions layer-actions-row';
    const left = document.createElement('div'); left.className = 'la-left';
    const right = document.createElement('div'); right.className = 'la-right';
    if (hasGroups) {
      left.innerHTML =
        '<button type="button" class="lg-expand-all la-icon" title="Expand all" aria-label="Expand all folders"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/></svg></button>' +
        '<button type="button" class="lg-collapse-all la-icon" title="Collapse all" aria-label="Collapse all folders"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 11 12 6 7 11"/><polyline points="17 18 12 13 7 18"/></svg></button>';
    }
    acts.appendChild(left);
    acts.appendChild(right);
    parent.insertBefore(acts, container);
    if (hasGroups) {
      acts.querySelector('.lg-expand-all').addEventListener('click', function () { setAllGroups(false); });
      acts.querySelector('.lg-collapse-all').addEventListener('click', function () { setAllGroups(true); });
    }
    // Relocate the Reset-styling button (from layout.html) into the row — moves the node + its handler.
    const reset = document.getElementById('reset-styling');
    if (reset) {
      reset.classList.add('la-icon');
      reset.title = 'Reset styling';
      reset.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10"/></svg>';
      right.appendChild(reset);
    }
    // The About link is appended into `.la-right` by buildAboutPanel (runs after this).
  }
  function setAllGroups(collapsed) {
    const container = document.getElementById('layer-list');
    if (!container) return;
    container.querySelectorAll('.layer-group').forEach(function (g) {
      const body = g.querySelector(':scope > .layer-group-body');
      const caret = g.querySelector(':scope > .layer-group-header .lg-caret');
      if (body) body.style.display = collapsed ? 'none' : '';
      if (caret) caret.classList.toggle('collapsed', collapsed);
    });
  }
  function filterLayers(raw) {
    const container = document.getElementById('layer-list');
    if (!container) return;
    const q = (raw || '').trim().toLowerCase();
    const groups = Array.prototype.slice.call(container.querySelectorAll('.layer-group'));
    if (!q) {   // cleared — restore everything to its pre-search state
      container.querySelectorAll('.layer-card').forEach(function (c) { c.style.display = ''; });
      groups.forEach(function (g) {
        g.style.display = '';
        if (g._savedBodyDisp !== undefined) {
          const body = g.querySelector(':scope > .layer-group-body');
          const caret = g.querySelector(':scope > .layer-group-header .lg-caret');
          if (body) body.style.display = g._savedBodyDisp;
          if (caret) caret.classList.toggle('collapsed', g._savedBodyDisp === 'none');
          delete g._savedBodyDisp;
        }
      });
      _searchActive = false;
      showNoResults(container, false);
      return;
    }
    if (!_searchActive) {   // entering search — remember collapse state to restore on clear
      groups.forEach(function (g) {
        const body = g.querySelector(':scope > .layer-group-body');
        g._savedBodyDisp = body ? body.style.display : '';
      });
      _searchActive = true;
    }
    let anyVisible = false;
    container.querySelectorAll('.layer-card').forEach(function (c) {
      const nameEl = c.querySelector('.layer-name');
      const match = (nameEl ? nameEl.textContent : '').toLowerCase().indexOf(q) !== -1;
      c.style.display = match ? '' : 'none';
      if (match) anyVisible = true;
    });
    groups.forEach(function (g) {   // show + expand a group iff it holds a match (querySelectorAll is recursive → parents stay open for nested hits)
      const hasMatch = Array.prototype.slice.call(g.querySelectorAll('.layer-card'))
        .some(function (c) { return c.style.display !== 'none'; });
      g.style.display = hasMatch ? '' : 'none';
      if (hasMatch) {
        const body = g.querySelector(':scope > .layer-group-body');
        const caret = g.querySelector(':scope > .layer-group-header .lg-caret');
        if (body) body.style.display = '';
        if (caret) caret.classList.remove('collapsed');
      }
    });
    showNoResults(container, !anyVisible);
  }
  function showNoResults(container, on) {
    let note = container.querySelector('.layer-search-empty');
    if (on && !note) {
      note = document.createElement('p');
      note.className = 'layer-search-empty';
      note.textContent = 'No matching layers';
      container.appendChild(note);
    } else if (!on && note) {
      note.remove();
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

    // External sources (WMS/XYZ/WFS) can't use the raster stretch/colormap or marker
    // controls — show opacity (header) + a colour picker for vector, attribution note.
    const body = layer.metadata['geodeploy:external']
      ? externalStyleRow(layer, geom, color)
      : (type === 'raster' ? rasterStyleRow(layer) : styleRow(layer, geom, color));

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

  // Minimal controls for an external source: opacity lives in the popover header;
  // vector (WFS) gets a colour picker; everything gets an attribution note.
  function externalStyleRow(layer, geom, color) {
    const attribution = layer.metadata['geodeploy:attribution'];
    let html = '';
    if (geom !== 'raster') {
      html += '<div class="layer-style-field"><label>Color</label>' +
        '<input class="layer-style-color" type="color" value="' + toHex(color) + '" ' +
        'data-layer-id="' + layer.id + '" data-layer-type="' + layer.type + '"></div>';
    }
    let note = '<div style="font-size:11px;color:var(--text-muted);margin-top:6px">External source — tiles/features served by the provider.';
    if (attribution) note += '<br>© ' + escHtml(String(attribution));
    note += '</div>';
    return '<div class="layer-style-row" data-style-for="' + layer.id + '">' + html + '</div>' + note;
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

    // ── GeoParquet (deck.gl) identify section ──
    // Deck layers ship geometry only (GeoArrow) or capped subsets, so attributes are fetched on
    // click from the server identify endpoint (covering-pruned point query). Only layers showing
    // real DETAIL are queried — the density-grid overview has no per-feature meaning.
    const deckQ = DECK_LAYERS.filter(d => {
      const st = deckState[d.layer_id];
      return st && st.visible && st.data && !st.data.__overview;
    });
    // Click tolerance = the same 5px pad, converted to degrees at the current view.
    const tp1 = map.unproject([e.point.x - pad, e.point.y]);
    const tp2 = map.unproject([e.point.x + pad, e.point.y]);
    const deckTol = Math.max(Math.abs(tp2.lng - tp1.lng) / 2, 1e-7);

    // ── Raster identify section ──
    const rasters = visibleRasterLayers();
    if (!vectorHtml && !rasters.length && !deckQ.length) return;

    const loading = (rasters.length || deckQ.length)
      ? '<div class="popup-raster-loading">Reading values…</div>' : '';
    popup.setLngLat(e.lngLat).setHTML(vectorHtml + loading).addTo(map);
    wireFullTableBtn(ftLayerId, ftLayerName);

    if (rasters.length || deckQ.length) {
      const [deckResults, rasterResults] = await Promise.all([
        Promise.all(deckQ.map(d => fetchDeckIdentify(d, e.lngLat, deckTol))),
        Promise.all(rasters.map(l => fetchRasterPoint(l, e.lngLat))),
      ]);
      popup.setHTML(vectorHtml + deckIdentifyHtml(deckResults)
        + (rasters.length ? rasterValuesHtml(rasterResults) : ''));
      wireFullTableBtn(ftLayerId, ftLayerName);
    }
  });

  async function fetchDeckIdentify(d, lngLat, tol) {
    try {
      const url = location.origin + '/api/data/vector/' + d.layer_id + '/identify?lng=' +
        encodeURIComponent(lngLat.lng) + '&lat=' + encodeURIComponent(lngLat.lat) +
        '&tol=' + encodeURIComponent(tol) + '&limit=5';
      const r = await fetch(url);
      if (!r.ok) return null;
      const j = await r.json();
      if (!j.features || !j.features.length) return null;
      return { layerId: d.layer_id, name: d.name || ('Layer ' + d.layer_id), feats: j.features };
    } catch (e) { return null; }
  }

  function deckIdentifyHtml(results) {
    return (results || []).filter(Boolean).map(r => {
      const fields = POPUP_CONFIG[r.layerId] || POPUP_CONFIG[String(r.layerId)];
      const props = r.feats[0] || {};
      const keys = fields && fields.length
        ? fields.filter(k => props[k] != null)
        : Object.keys(props).filter(k => props[k] != null).slice(0, 8);
      const body = keys.length
        ? '<table class="popup-table">' + keys.map(k =>
            '<tr><th>' + escHtml(k) + '</th><td>' + escHtml(String(props[k])) + '</td></tr>').join('') + '</table>'
        : '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted)">No attributes</div>';
      const more = r.feats.length > 1
        ? '<div style="padding:2px 12px 8px;font-size:11px;color:var(--text-muted)">+' +
          (r.feats.length - 1) + ' more feature' + (r.feats.length > 2 ? 's' : '') + ' here</div>'
        : '';
      return '<div class="popup-header">' + escHtml(r.name) + '</div>' + body + more;
    }).join('');
  }

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
  // The catalog is the ONE source of truth on the server (portal_generator.BASEMAP_CATALOG); it's
  // baked into this bundle as STYLE.geodeploy.basemaps, so there's nothing to keep in sync here.
  // The minimal fallback only covers portals published before basemaps were baked in.
  const BASEMAP_CATALOG = (((STYLE.geodeploy || {}).basemaps) || []).length
    ? STYLE.geodeploy.basemaps
    : [{ id: 'positron', name: 'Positron',
         tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png', 'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png', 'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'],
         attribution: '© OpenStreetMap © CARTO',
         thumb: 'https://a.basemaps.cartocdn.com/light_all/4/8/5.png' }];
  const BASEMAPS = BASEMAP_CATALOG;
  // The admin's chosen basemap, baked into the base layer at publish. Portals published BEFORE
  // basemap selection have no defaultBasemap → keep the template's own baked basemap (the '__default__'
  // sentinel) so their appearance is unchanged; only portals that explicitly chose one switch away.
  const RAW_DEFAULT = ((STYLE.geodeploy || {}).defaultBasemap) || null;
  const HAS_DEFAULT_ENTRY = !RAW_DEFAULT;           // show a "Default" (template) option for old portals
  const DEFAULT_BASEMAP = RAW_DEFAULT || '__default__';
  // When publish repointed the builtin base layer to the chosen basemap, it ALREADY shows it on load —
  // swapping to the catalog copy in setupBasemaps would just flash. Skip that initial swap then.
  const BASE_REPOINTED = !!((STYLE.geodeploy || {}).baseRepointed);
  // Switcher options: catalog entries, plus a leading "Default" (the template's baked base) when the
  // portal didn't pick a basemap.
  const BASEMAP_OPTS = HAS_DEFAULT_ENTRY
    ? [{ id: '__default__', name: 'Default', thumb: BASEMAP_CATALOG[0].thumb }].concat(BASEMAP_CATALOG)
    : BASEMAP_CATALOG;

  function builtinBasemapIds() {
    return STYLE.layers.filter(l => !(l.metadata && l.metadata['geodeploy:name'])).map(l => l.id);
  }

  function setupBasemaps() {
    const firstId = (map.getStyle().layers[0] || {}).id;
    BASEMAPS.forEach(bm => {
      const srcId = 'gd-basemap-' + bm.id;
      if (!map.getSource(srcId)) {
        map.addSource(srcId, { type: 'raster', tiles: bm.tiles, tileSize: 256, attribution: bm.attribution || '' });
      }
      if (!map.getLayer(srcId)) {
        map.addLayer({ id: srcId, type: 'raster', source: srcId, layout: { visibility: 'none' } }, firstId);
      }
    });
    // The builtin already shows the right basemap when publish repointed it — swapping to the catalog
    // copy here is a redundant, visible flash. Only drive selectBasemap when NOT repointed (a vector
    // template whose base couldn't be repointed, or the '__default__' no-op for pre-basemap portals).
    if (!BASE_REPOINTED) selectBasemap(DEFAULT_BASEMAP);
    map.addControl(new BasemapControl(), CTRL_POS);
    map.addControl(new HomeControl(), CTRL_POS);        // back to the published default extent
    map.addControl(new ZoomAllControl(), CTRL_POS);     // fit all layers
    map.addControl(new DrawZoomControl(), CTRL_POS);    // drag a box to zoom (toggle back to pan)
    map.addControl(new ToolsControl(), CTRL_POS);
  }

  // ── Navigation helpers reused by the Home / Zoom-to-all controls ──────────────
  function goHome() {
    // The published default extent: the admin-pinned view, else the fit-to-data bounds.
    if (savedView && Array.isArray(savedView.center) && savedView.center.length === 2) {
      try { map.flyTo({ center: savedView.center, zoom: savedView.zoom != null ? savedView.zoom : 2,
        bearing: savedView.bearing || 0, pitch: savedView.pitch || 0, duration: 800, essential: true }); } catch (e) {}
    } else if (validLonLatBounds(bounds)) {
      try { map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        { padding: fitPadding(), duration: 800 }); } catch (e) {}
    }
  }
  function unionBbox(a, b) {
    if (!validLonLatBounds(b)) return a;
    if (!a) return b.slice();
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
  }
  function zoomToAllLayers() {
    let bb = null;
    (STYLE.layers || []).forEach(function (l) {
      const m = l.metadata || {};
      if (m['geodeploy:name']) bb = unionBbox(bb, m['geodeploy:bbox']);
    });
    (DECK_LAYERS || []).forEach(function (d) { bb = unionBbox(bb, d.bbox); });
    if (validLonLatBounds(bb)) {
      try { map.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { padding: fitPadding(), duration: 800 }); } catch (e) {}
    } else { goHome(); }
  }
  // Padding that keeps the fit clear of a docked layer list on its side.
  function fitPadding() {
    const p = { top: 40, bottom: 40, left: 40, right: 40 };
    const sb = document.getElementById('sidebar');
    if (sb && LAYOUT.panels.layerCatalog && LAYOUT.regions.layerList.mode === 'docked' && !sb.classList.contains('collapsed')) {
      p[LAYOUT.regions.layerList.side] = (sb.offsetWidth || 260) + 40;
    }
    return p;
  }

  function homeIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9"/></svg>';
  }
  function zoomAllIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/></svg>';
  }
  function drawZoomIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="14" height="14" rx="1" stroke-dasharray="3 2"/><circle cx="16.5" cy="16.5" r="4.5"/><line x1="19.7" y1="19.7" x2="22" y2="22"/></svg>';
  }
  function ctrlButton(cls, title, icon, onClick) {
    const c = document.createElement('div');
    c.className = 'maplibregl-ctrl maplibregl-ctrl-group';
    c.innerHTML = '<button type="button" class="' + cls + '" title="' + title + '" aria-label="' + title + '">' + icon + '</button>';
    c.querySelector('button').addEventListener('click', function (ev) { ev.stopPropagation(); onClick(c); });
    return c;
  }
  class HomeControl {
    onAdd() { this._c = ctrlButton('gd-home-btn', 'Home (default view)', homeIcon(), goHome); return this._c; }
    onRemove() { if (this._c) this._c.remove(); }
  }
  class ZoomAllControl {
    onAdd() { this._c = ctrlButton('gd-zoomall-btn', 'Zoom to all layers', zoomAllIcon(), zoomToAllLayers); return this._c; }
    onRemove() { if (this._c) this._c.remove(); }
  }
  // Draw-a-box-to-zoom, as a TOGGLE: on → drag a box to zoom (repeatable); click again → back to pan.
  let dzActive = false, dzStart = null, dzBtn = null;
  function dzDown(e) { dzStart = e.lngLat; map.on('mousemove', dzMove); map.once('mouseup', dzUp); }
  function dzMove(e) { if (dzStart) { ensureDrawLayers(); map.getSource('gd-draw').setData(rectFC(dzStart, e.lngLat)); } }
  function dzUp(e) {
    map.off('mousemove', dzMove);
    const a = dzStart; dzStart = null; clearDraw();
    if (a) {
      const b = e.lngLat;
      if (Math.abs(a.lng - b.lng) > 1e-7 && Math.abs(a.lat - b.lat) > 1e-7) {  // a real box, not a click
        try { map.fitBounds([[Math.min(a.lng, b.lng), Math.min(a.lat, b.lat)], [Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)]],
          { padding: 20, duration: 600 }); } catch (err) {}
      }
    }
    if (dzActive) map.once('mousedown', dzDown);  // stay armed for another box (even after a stray click)
  }
  function toggleDrawZoom() {
    dzActive = !dzActive;
    if (dzBtn) dzBtn.classList.toggle('active', dzActive);
    if (dzActive) {
      cancelAreaSelect();  // C8: only one map mode active at a time
      map.dragPan.disable();
      map.getCanvas().style.cursor = 'crosshair';
      map.once('mousedown', dzDown);
    } else {
      map.off('mousemove', dzMove); map.off('mousedown', dzDown);
      dzStart = null; clearDraw();
      map.dragPan.enable();
      map.getCanvas().style.cursor = '';
    }
  }
  class DrawZoomControl {
    onAdd() {
      this._c = ctrlButton('gd-drawzoom-btn', 'Draw a box to zoom (click again for pan)', drawZoomIcon(), toggleDrawZoom);
      dzBtn = this._c.querySelector('button');
      return this._c;
    }
    onRemove() { if (this._c) this._c.remove(); }
  }

  // On-map layer-list toggle. It is a REAL MapLibre control added at the layer-list corner (top-left
  // when the list is on the left, top-right when right) so it inherits MapLibre's exact button size,
  // radius, shadow and stacking — perfectly aligned + evenly spaced with the other controls (which is
  // why the old absolute-positioned button never lined up). Added BEFORE setupBasemaps so, when the
  // list shares a side with the controls, it sits at the TOP of that stack.
  function layersStackIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>';
  }
  class ListToggleControl {
    onAdd() {
      const sb = document.getElementById('sidebar');
      const c = document.createElement('div');
      c.className = 'maplibregl-ctrl maplibregl-ctrl-group gd-list-toggle-ctrl';
      c.innerHTML = '<button type="button" id="gd-list-toggle" title="Show / hide layers" aria-label="Toggle layers panel">' + layersStackIcon() + '</button>';
      c.querySelector('button').addEventListener('click', function (ev) {
        ev.stopPropagation();
        if (sb) sb.classList.toggle('collapsed');
        setTimeout(function () { map.resize(); }, 220);
      });
      this._c = c;
      return c;
    }
    onRemove() { if (this._c) this._c.remove(); }
  }
  function setupListToggle() {
    if (document.getElementById('gd-list-toggle')) return;
    const side = (LAYOUT.regions.layerList.side === 'right') ? 'right' : 'left';
    map.addControl(new ListToggleControl(), 'top-' + side);
  }

  // Floating layer list: apply the manifest's box (width/x/y) and add move + resize handles so the
  // visitor can reposition it (session-only; the editor persists the box into the manifest).
  function applyFloatingLayout() {
    const sb = document.getElementById('sidebar');
    if (!sb || LAYOUT.regions.layerList.mode !== 'floating') return;
    const ll = LAYOUT.regions.layerList;
    if (ll.width) sb.style.width = ll.width + 'px';
    if (ll.x != null && ll.y != null) {
      sb.style.left = ll.x + 'px'; sb.style.right = 'auto';
      sb.style.top = ll.y + 'px'; sb.style.bottom = 'auto';
    }
    // Move handle (grip at the top of the panel).
    if (!sb.querySelector('.gd-float-move')) {
      const h = document.createElement('div');
      h.className = 'gd-float-move'; h.title = 'Drag to move';
      h.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="9" cy="6" r="1.3"/><circle cx="9" cy="12" r="1.3"/><circle cx="9" cy="18" r="1.3"/><circle cx="15" cy="6" r="1.3"/><circle cx="15" cy="12" r="1.3"/><circle cx="15" cy="18" r="1.3"/></svg>';
      sb.insertBefore(h, sb.firstChild);
      h.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        const par = sb.offsetParent ? sb.offsetParent.getBoundingClientRect() : { left: 0, top: 0 };
        const r = sb.getBoundingClientRect();
        const ox = r.left - par.left, oy = r.top - par.top, sx = e.clientX, sy = e.clientY;
        sb.style.right = 'auto'; sb.style.bottom = 'auto';
        function mv(ev) { sb.style.left = (ox + ev.clientX - sx) + 'px'; sb.style.top = (oy + ev.clientY - sy) + 'px'; }
        function up() { document.removeEventListener('pointermove', mv); document.removeEventListener('pointerup', up); }
        document.addEventListener('pointermove', mv); document.addEventListener('pointerup', up);
      });
    }
    // Resize handle — bottom-right for a LEFT list (grows right), bottom-left for a RIGHT list (grows
    // left), so the grip is always on the map side, not jammed against the screen edge (C3/C5).
    if (!sb.querySelector('.gd-float-resize')) {
      const h = document.createElement('div');
      h.className = 'gd-float-resize'; h.title = 'Drag to resize';
      sb.appendChild(h);
      h.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        const side = document.body.dataset.layerlistSide || 'left';
        const r0 = sb.getBoundingClientRect();
        const par = sb.offsetParent ? sb.offsetParent.getBoundingClientRect() : { left: 0, top: 0 };
        sb.style.maxHeight = 'none';
        function mv(ev) {
          if (side === 'right') {  // keep the right edge fixed; drag the left edge outward
            const w = Math.max(180, r0.right - ev.clientX);
            sb.style.right = 'auto';
            sb.style.left = (r0.right - w - par.left) + 'px';
            sb.style.width = w + 'px';
          } else {                 // keep the left edge fixed; grow to the right
            sb.style.width = Math.max(180, ev.clientX - r0.left) + 'px';
          }
          sb.style.height = Math.max(120, ev.clientY - r0.top) + 'px';
          updateCtrlOffset();
        }
        function up() { document.removeEventListener('pointermove', mv); document.removeEventListener('pointerup', up); }
        document.addEventListener('pointermove', mv); document.addEventListener('pointerup', up);
      });
    }

    // C11: click anywhere outside the floating list (except the on-map toggle) collapses it.
    if (!applyFloatingLayout._outside) {
      applyFloatingLayout._outside = true;
      document.addEventListener('click', function (e) {
        if (document.body.dataset.layerlist !== 'floating') return;
        const s = document.getElementById('sidebar');
        const t = document.getElementById('gd-list-toggle');
        if (!s || s.classList.contains('collapsed')) return;
        if (s.contains(e.target) || (t && t.contains(e.target))) return;
        s.classList.add('collapsed');
        updateCtrlOffset();
      });
    }
    updateCtrlOffset();
  }
  // C2 is now handled entirely in CSS (fixed control offset below the toggle + inward float), so this
  // is a no-op kept only so its existing call sites stay valid.
  function updateCtrlOffset() {}

  // Opening a control flyout (basemap/tools) collapses a FLOATING layer list, so the panel isn't
  // hidden behind it (a control click stops propagation, so the click-outside handler won't fire).
  function collapseFloatingList() {
    if (document.body.dataset.layerlist !== 'floating') return;
    const s = document.getElementById('sidebar');
    if (s && !s.classList.contains('collapsed')) s.classList.add('collapsed');
  }

  // ── R2: editor edit-mode shim (only when iframed as a preview with ?edit=1) ────
  // Same-origin postMessage channel with the editor: reports the live camera (for save / story capture),
  // runs "click a preset slot to place an element", and applies view/zoom commands. The published portal
  // never enters this (no ?edit=1), so it's inert there.
  function currentViewObj() {
    const c = map.getCenter();
    return { center: [c.lng, c.lat], zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() };
  }
  // B (incremental preview): apply a colour theme live in the preview — no full iframe reload. Mirrors
  // portal_generator.build_theme_css + resolve_theme (mode/logo). Only used in edit mode.
  const LIVE_FONTS = { sans: "system-ui,-apple-system,'Segoe UI',Roboto,sans-serif", serif: "Georgia,'Iowan Old Style','Times New Roman',serif", mono: "'SF Mono',ui-monospace,'Cascadia Code',Menlo,monospace" };
  function applyThemeLive(theme) {
    theme = theme || {};
    if (!localStorage.getItem('gd-portal-theme')) {  // the visitor's own toggle still wins
      const mode = theme.mode || 'auto';
      const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      const dark = mode === 'dark' ? true : mode === 'light' ? false : prefersDark;
      if (dark) document.documentElement.setAttribute('data-theme', 'dark');
      else document.documentElement.removeAttribute('data-theme');
    }
    let css = '';
    const accent = (typeof theme.accent === 'string') ? theme.accent.trim() : '';
    if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(accent)) {
      css += ':root{--accent:' + accent + ';--accent-light:color-mix(in srgb,' + accent + ' 22%,transparent);}';
    }
    if (LIVE_FONTS[theme.font]) css += 'body{font-family:' + LIVE_FONTS[theme.font] + ';}';
    let style = document.getElementById('gd-live-theme');
    if (!style) { style = document.createElement('style'); style.id = 'gd-live-theme'; document.head.appendChild(style); }
    style.textContent = css;
    // Logo (rebuild the header brand).
    if (STYLE.geodeploy) { STYLE.geodeploy.theme = STYLE.geodeploy.theme || {}; STYLE.geodeploy.theme.logo = theme.logo; }
    const old = document.getElementById('gd-header-logo'); if (old) old.remove();
    try { buildHeaderLogo(); } catch (e) {}
  }
  function setupEditMode() {
    if (new URLSearchParams(location.search).get('edit') !== '1') return;
    const parent = window.parent;
    if (!parent || parent === window) return;
    function post(msg) { try { parent.postMessage(Object.assign({ gd: 1 }, msg), location.origin); } catch (e) {} }
    document.body.classList.add('gd-edit');
    post({ type: 'ready' });
    post({ type: 'view', view: currentViewObj() });
    map.on('moveend', function () { post({ type: 'view', view: currentViewObj() }); });

    let placeOverlay = null;
    function clearPlace() { if (placeOverlay) { placeOverlay.remove(); placeOverlay = null; } }
    function showPlace(element) {
      clearPlace();
      const wrap = document.getElementById('map-wrap') || document.body;
      const label = element === 'controls' ? 'controls' : 'layer list';
      placeOverlay = document.createElement('div');
      placeOverlay.className = 'gd-place-overlay';
      placeOverlay.innerHTML =
        '<div class="gd-place-zone" data-side="left"><span>Place ' + label + ' — Left</span></div>' +
        '<div class="gd-place-zone" data-side="right"><span>Place ' + label + ' — Right</span></div>';
      placeOverlay.querySelectorAll('.gd-place-zone').forEach(function (z) {
        z.addEventListener('click', function () { post({ type: 'placed', element: element, side: z.dataset.side }); clearPlace(); });
      });
      wrap.appendChild(placeOverlay);
    }
    window.addEventListener('message', function (e) {
      if (e.origin !== location.origin || !e.data || e.data.gd == null) return;
      const d = e.data;
      if (d.type === 'place') showPlace(d.element);
      else if (d.type === 'cancelPlace') clearPlace();
      else if (d.type === 'theme') applyThemeLive(d.theme);   // B: live theme, no reload
      else if (d.type === 'zoomall') zoomToAllLayers();
      else if (d.type === 'home') goHome();
      else if (d.type === 'fitbbox' && Array.isArray(d.bbox) && d.bbox.length === 4) {
        try { map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]], { padding: 30, duration: 500 }); } catch (err) {}
      }
      else if (d.type === 'setview' && d.view && Array.isArray(d.view.center)) {
        try { map.jumpTo({ center: d.view.center, zoom: d.view.zoom, bearing: d.view.bearing || 0, pitch: d.view.pitch || 0 }); } catch (err) {}
      }
    });
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
      c.className = 'maplibregl-ctrl maplibregl-ctrl-group gd-tools-ctrl';
      const drawSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="14" height="12" rx="1" stroke-dasharray="3 2"/><circle cx="18" cy="18" r="3.5"/><line x1="20.5" y1="20.5" x2="23" y2="23"/></svg>';
      const numSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="4" x2="8" y2="20"/><line x1="16" y1="4" x2="14" y2="20"/></svg>';
      c.innerHTML =
        '<button type="button" class="gd-tools-btn" title="Download data by area" aria-label="Download data by area">' + toolsIcon() + '</button>' +
        '<div class="gd-tools-menu">' +
          '<div class="gd-tools-title">Download by area</div>' +
          '<div class="gd-tools-tabs">' +
            '<button type="button" class="gd-tools-tab is-active" data-tab="draw">' + drawSvg + '<span>Draw a box</span></button>' +
            '<button type="button" class="gd-tools-tab" data-tab="coords">' + numSvg + '<span>Coordinates</span></button>' +
          '</div>' +
          '<div class="gd-tools-pane" data-pane="draw">' +
            '<p class="gd-tools-hint">Drag a rectangle on the map to select the area to download.</p>' +
            '<button type="button" class="gd-coords-go" data-act="draw">Draw on the map</button>' +
          '</div>' +
          '<div class="gd-tools-pane" data-pane="coords" hidden>' +
            '<div class="gd-coords-cross">' +
              '<input type="number" step="any" class="gd-c-in gd-c-n" data-k="n" placeholder="max Y / N" aria-label="North (max Y)">' +
              '<input type="number" step="any" class="gd-c-in gd-c-w" data-k="w" placeholder="min X / W" aria-label="West (min X)">' +
              '<span class="gd-c-mid">' + numSvg + '</span>' +
              '<input type="number" step="any" class="gd-c-in gd-c-e" data-k="e" placeholder="max X / E" aria-label="East (max X)">' +
              '<input type="number" step="any" class="gd-c-in gd-c-s" data-k="s" placeholder="min Y / S" aria-label="South (min Y)">' +
            '</div>' +
            '<button type="button" class="gd-coords-go" data-act="coords">Download this area</button>' +
          '</div>' +
        '</div>';
      const btn = c.querySelector('.gd-tools-btn');
      const menu = c.querySelector('.gd-tools-menu');
      btn.addEventListener('click', ev => { ev.stopPropagation(); c.classList.toggle('open'); if (c.classList.contains('open')) collapseFloatingList(); });
      menu.addEventListener('click', ev => ev.stopPropagation());
      document.addEventListener('click', () => c.classList.remove('open'));
      // Tab switch (Draw ⟷ Coordinates)
      c.querySelectorAll('.gd-tools-tab').forEach(function (tab) {
        tab.addEventListener('click', function () {
          const which = tab.dataset.tab;
          c.querySelectorAll('.gd-tools-tab').forEach(t => t.classList.toggle('is-active', t === tab));
          c.querySelectorAll('.gd-tools-pane').forEach(p => { p.hidden = (p.dataset.pane !== which); });
        });
      });
      c.querySelector('[data-act="draw"]').addEventListener('click', () => { c.classList.remove('open'); startAreaSelect(); });
      c.querySelector('[data-act="coords"]').addEventListener('click', () => {
        const v = k => parseFloat(c.querySelector('.gd-c-' + k).value);
        const n = v('n'), w = v('w'), e = v('e'), s = v('s');
        if ([n, w, e, s].some(x => isNaN(x))) { showHint('Fill in all four edges (N, S, E, W).'); return; }
        // bbox = [minX, minY, maxX, maxY]; N/S are Y, E/W are X.
        const bbox = [Math.min(w, e), Math.min(n, s), Math.max(w, e), Math.max(n, s)];
        c.classList.remove('open');
        openDownloadForBbox(bbox);
      });
      this._c = c;
      return c;
    }
    onRemove() { if (this._c) this._c.remove(); }
  }

  // C7: open the download dialog for a TYPED bbox (fit the map to it first so the in-viewport vector
  // hit-test sees the features, then draw the box + open the dialog on moveend).
  function openDownloadForBbox(bbox) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return;
    function go() {
      try {
        const pa = map.project([bbox[0], bbox[1]]), pb = map.project([bbox[2], bbox[3]]);
        const pixBox = [[Math.min(pa.x, pb.x), Math.min(pa.y, pb.y)], [Math.max(pa.x, pb.x), Math.max(pa.y, pb.y)]];
        ensureDrawLayers();
        map.getSource('gd-draw').setData(rectFC({ lng: bbox[0], lat: bbox[1] }, { lng: bbox[2], lat: bbox[3] }));
        openDownloadDialog(bbox, pixBox);
      } catch (e) {}
    }
    try {
      map.once('moveend', go);
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, duration: 500 });
    } catch (e) { go(); }
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
    if (dzActive) toggleDrawZoom();  // C8: turn off draw-zoom before starting an area select
    drawing = true;
    ensureDrawLayers();
    clearDraw();
    map.getCanvas().style.cursor = 'crosshair';
    map.dragPan.disable();
    showHint('Drag a box on the map to select an area');
    map.on('mousedown', onDrawDown);
  }
  // Cancel a pending area-select (e.g. when another map tool is chosen) — mirror of onDrawUp cleanup.
  function cancelAreaSelect() {
    if (!drawing) return;
    map.off('mousemove', onDrawMove); map.off('mousedown', onDrawDown);
    drawStart = null; drawing = false; clearDraw();
    map.dragPan.enable(); map.getCanvas().style.cursor = ''; hideHint();
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
    // GeoParquet layers render via the deck.gl overlay (not STYLE.layers) but export like any
    // vector layer — the server clips the file with DuckDB. Hit-test on the layer bbox (like
    // rasters); no bbox recorded → offer it anyway and let the clip decide.
    DECK_LAYERS.forEach(d => {
      const key = 'vector-' + d.layer_id;
      if (seen.has(key)) return;
      const st = deckState[d.layer_id];
      if (st && !st.visible) return;
      const bb = d.bbox;
      const hit = !(Array.isArray(bb) && bb.length === 4) ||
        !(bb[2] < bbox[0] || bb[0] > bbox[2] || bb[3] < bbox[1] || bb[1] > bbox[3]);
      if (!hit) return;
      seen.add(key);
      items.push({ id: d.layer_id, type: 'vector', name: d.name || ('Layer ' + d.layer_id) });
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
        (items.length ?
          '<div class="gd-download-crs">' +
            '<label>Coordinate system</label>' +
            '<select class="gd-dl-crs">' +
              '<option value="4326">EPSG:4326 (lon/lat, uniform)</option>' +
              '<option value="native">Native — each layer\'s own CRS</option>' +
            '</select>' +
            '<span class="gd-dl-crs-note">GeoJSON is always EPSG:4326; GeoPackage/CSV carry the chosen CRS.</span>' +
          '</div>' +
          '<div class="gd-download-foot"><span class="gd-dl-status"></span>' +
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
      const crsSel = dlg.querySelector('.gd-dl-crs');
      const targetCrs = crsSel ? crsSel.value : '4326';
      const apiBase = '/api/portals/' + encodeURIComponent(slug);
      go.disabled = true; status.textContent = 'Queued…';
      try {
        const resp = await fetch(apiBase + '/export-bundle', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ bbox: bbox.join(','), items: picks, target_crs: targetCrs }),
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
    // '__default__' → show the template's baked base layer(s); any catalog id → hide the baked base
    // and show that catalog raster instead.
    const showBuiltin = id === '__default__';
    builtinBasemapIds().forEach(lid => { if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', showBuiltin ? 'visible' : 'none'); });
    BASEMAPS.forEach(bm => {
      const lid = 'gd-basemap-' + bm.id;
      if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', bm.id === id ? 'visible' : 'none');
    });
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
      var checkSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
        'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
      c.innerHTML =
        '<button type="button" class="gd-basemap-btn" title="Basemaps" aria-label="Choose basemap">' + basemapIcon() + '</button>' +
        '<div class="gd-basemap-menu">' +
          '<div class="gd-basemap-title">Basemap</div>' +
          BASEMAP_OPTS.map((bm) =>
            '<label class="gd-basemap-opt"><input type="radio" name="gd-basemap" value="' + bm.id + '"' +
            (bm.id === DEFAULT_BASEMAP ? ' checked' : '') + '>' +
            '<img class="gd-basemap-thumb" src="' + bm.thumb + '" alt="" loading="lazy">' +
            '<span class="gd-basemap-name">' + escHtml(bm.name) + '</span>' +
            '<span class="gd-basemap-check">' + checkSvg + '</span></label>').join('') +
        '</div>';
      const btn = c.querySelector('.gd-basemap-btn');
      const menu = c.querySelector('.gd-basemap-menu');
      btn.addEventListener('click', ev => { ev.stopPropagation(); c.classList.toggle('open'); if (c.classList.contains('open')) collapseFloatingList(); });
      // Collapse the flyout after a choice (C6) — and on any outside click (below).
      menu.addEventListener('change', ev => { selectBasemap(ev.target.value); c.classList.remove('open'); });
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
