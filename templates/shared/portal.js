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
    webmap:   { regions: { layerList: { side: 'left', mode: 'docked', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' } },     panels: { layerCatalog: true,  legend: true, basemap: true, about: true,  story: false } },
    storymap: { regions: { layerList: { side: 'left', mode: 'floating', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'minimal' } }, panels: { layerCatalog: true, legend: true, basemap: true, about: false, story: true } },
    // V-14 catalog: a BROWSE surface. The dataset list is the page and the map is a panel beside it,
    // so layerCatalog is off (the facet rail replaces the switcher) and `catalog` carries the split.
    catalog:  { regions: { layerList: { side: 'right', mode: 'floating', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' }, catalog: { scope: 'portal', mapSide: 'right', mapWidth: 50, railWidth: 20, perPage: 12 } }, panels: { catalog: true, layerCatalog: false, legend: true, basemap: true, about: false, story: false } },
    // V-16 dashboard: the MAP IS A WIDGET. #layout becomes the widget grid and #map-wrap takes the
    // map widget's cell by grid-area — never re-parented, same rule as the catalog. `layerCatalog`
    // is off by default because the widgets name their own data; an author can turn it back on.
    dashboard: { regions: { layerList: { side: 'left', mode: 'floating', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' }, dashboard: { density: 'comfortable', mapControls: true } }, panels: { dashboard: true, layerCatalog: false, legend: true, basemap: true, about: false, story: false } },
  };
  // `webmap+catalog` is still UNBUILT and degrades to a working map on purpose — a blank shell would
  // be worse. `catalog` used to be here too, which is why choosing it silently rendered a web map.
  const LAYOUT_ALIASES = { 'webmap+catalog': 'webmap' };
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
    // V-14 catalog: claim the panel's space NOW, before the map is constructed. This runs at parse
    // time; setupCatalog only runs on map 'load'. Revealing the panel there meant the map was built
    // against a full-width container, painted, and then jumped sideways when the panel appeared —
    // the visible "renders, blinks, moves" on load. With the grid correct up front MapLibre measures
    // the final width at construction, so there is nothing to correct and no resize to chase.
    if (L.archetype === 'catalog') {
      b.dataset.catalogMap = (L.regions.catalog && L.regions.catalog.mapSide) === 'left' ? 'left' : 'right';
      b.dataset.catalogView = 'list';
      // Author-chosen split. Clamped: below ~30% the map is a thumbnail, above ~60% the result cards
      // stop fitting a readable line length.
      var mw = Number((L.regions.catalog || {}).mapWidth);
      if (!isFinite(mw) || mw <= 0) mw = 40;
      b.style.setProperty('--cat-map-w', Math.min(60, Math.max(30, mw)) + '%');
      const cp = document.getElementById('catalog-panel');
      if (cp) cp.style.display = '';
    }
    // V-16 dashboard: reveal the widget host and stamp the density NOW, at parse time, for the same
    // reason the catalog claims its column here — MapLibre measures its container at construction,
    // so a grid that appears later would build the map at the wrong size and then jump.
    if (L.archetype === 'dashboard') {
      const dcfg = (L.regions && L.regions.dashboard) || {};
      b.dataset.dashDensity = dcfg.density === 'compact' ? 'compact' : 'comfortable';
      b.dataset.dashMapctrl = dcfg.mapControls === false ? '0' : '1';
      const dp = document.getElementById('dashboard-panel');
      if (dp) dp.style.display = '';
    }
    // The layer list is hidden by whether its PANEL is enabled, not by which archetype is in play.
    // It used to be hidden by a CSS rule keyed on the catalog archetype, which meant a catalog
    // portal that switched "Layer catalog" on got nothing: the list was built and then hidden by a
    // rule that could not see the choice. Keyed on the panel, every archetype behaves the same way
    // and the author's choice is the only thing that decides.
    // (The opening collapsed state is applied further down, where the sidebar handlers are wired.)
    const sb = document.getElementById('sidebar');
    if (sb && !L.panels.layerCatalog) sb.style.display = 'none';
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
      if (logo.tint) {
        // Tinted: the file becomes a MASK and the accent shows through it, so an uploaded mark
        // takes the theme colour the way the built-in presets do (those are inline SVG using
        // `currentColor`, which an <img> cannot inherit). A dark-on-transparent logo — the usual
        // export — is otherwise invisible against a dark header.
        //
        // Masking rather than inlining the SVG is deliberate. Inlining would give real
        // `currentColor`, but it puts an uploaded document in the page's DOM, where a <script> or
        // an `on*` attribute inside it would run with the portal's origin. A mask reads only the
        // alpha channel: nothing in the file is ever parsed as markup. It costs multi-colour —
        // which tinting was going to flatten anyway — and it works for a transparent PNG too.
        el = document.createElement('span');
        const u = 'url("' + String(logo.url).replace(/["\\]/g, '\\$&') + '")';
        el.style.webkitMaskImage = u; el.style.maskImage = u;
        el.className = 'gd-logo-tint';
      } else {
        el = document.createElement('img'); el.src = logo.url; el.alt = '';
      }
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
  // On a PHONE the list is an overlay drawer, so an expanded one covers the whole map — the visitor
  // would land on a list of names with no map behind it. Start collapsed there whatever the author
  // chose: the choice is about how a portal opens on a desktop, and the on-map toggle is one tap
  // away. Deliberately evaluated once at load, not on resize, so it never yanks a panel shut while
  // someone is using it (a phone rotating, or a desktop window being dragged narrow).
  const isPhone = window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
  if (LAYOUT.regions.layerList.collapsed || isPhone) sidebar.classList.add('collapsed');
  document.getElementById('sidebar-toggle').addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
    setTimeout(() => map.resize(), 220);
  });
  // On a phone the list is an overlay drawer covering the map, so tapping the map is the natural way
  // to dismiss it — and it is the SAFETY NET for the case that had no way out at all: an open drawer
  // hides the control cluster including its own toggle button. Capture phase, because the map's own
  // handlers stop propagation. Desktop is untouched: there the list sits beside the map, and closing
  // it on any stray click would be hostile.
  document.addEventListener('click', function (e) {
    if (!window.matchMedia || !window.matchMedia('(max-width: 640px)').matches) return;
    if (sidebar.classList.contains('collapsed')) return;
    if (sidebar.contains(e.target) || e.target.closest('#gd-list-toggle, #sidebar-toggle')) return;
    sidebar.classList.add('collapsed');
  }, true);

  /**
   * ── First-paint gate ──────────────────────────────────────────────────────────────────────────
   *
   * A portal used to assemble itself in front of the visitor: the map painted as soon as its tiles
   * arrived, and the catalog rail / story column / layer list appeared afterwards, in whatever order
   * they happened to finish. On a catalog that gap is the whole point of the page — a map with no
   * list beside it — and it is worst on exactly the connections where it is most visible.
   *
   * So `#gd-loading` (in the markup, painted before this script even parses) covers the window until
   * the pieces are READY. Readiness, not a timer: every gate is registered up front and cleared by
   * the thing it names, so the cover lifts when the last one reports in, however long that takes.
   *
   * Two rules that keep this from becoming its own bug:
   *   1. Register every gate SYNCHRONOUSLY, before any of them can be cleared. A set that empties
   *      because the next gate had not been added yet would lift the cover early.
   *   2. Clear a gate unconditionally — in a `finally`, or on the line after the try/catch. A gate
   *      that only clears on success turns any one broken panel into a portal that never appears.
   * The backstop timeout is for what neither rule can cover: a piece that never calls back at all.
   */
  const loading = (function () {
    const el = document.getElementById('gd-loading');
    const gates = {};
    let released = false;
    function hide() {
      if (released) return;
      released = true;
      if (!el) return;
      el.classList.add('gd-loading-out');
      // Removed rather than left transparent: it covers the viewport, and a stale overlay with
      // pointer-events still resolving would eat the first click on the map.
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 420);
    }
    function ready(name) {
      if (!gates[name]) return;
      delete gates[name];
      if (Object.keys(gates).length === 0) hide();
    }
    return {
      need: function (name) { if (!released) gates[name] = true; },
      ready: ready,
      hide: hide,
      waiting: function () { return Object.keys(gates); },
    };
  })();
  // The access gate is its OWN full-window surface and sits above this one; a visitor who has to
  // sign in should not be looking at a loader behind it.
  (function () {
    const g = document.getElementById('access-gate');
    if (g && g.style.display && g.style.display !== 'none') loading.hide();
  })();
  loading.need('map');       // the load handler ran to completion (panels mounted)
  loading.need('render');    // MapLibre has finished drawing the opening view
  if (LAYOUT.archetype === 'catalog') loading.need('catalog');
  if (LAYOUT.archetype === 'storymap') loading.need('story');
  if (LAYOUT.archetype === 'dashboard') loading.need('dashboard');
  // Backstop. NOT the mechanism — the gates above are — but a portal must never be held hostage by
  // one piece that fails in a way that skips its own clear (a hard error inside MapLibre, a style
  // that never finishes). Generous enough that a slow connection reaches readiness first.
  setTimeout(function () {
    if (loading.waiting().length) console.warn('[geodeploy] still waiting on', loading.waiting());
    loading.hide();
  }, 15000);

  // ── Map init ────────────────────────────────────────────
  const map = new maplibregl.Map({
    container: 'map',
    style: STYLE,
    center: [0, 20],
    zoom: 2,
    attributionControl: false,
    // Above MapLibre's default of 60. Extruded buildings and 3D bars are only legible from a low
    // angle, and 60 still looks down on them; 75 is a view along the ground without the horizon
    // filling the frame.
    maxPitch: 75,
    // Needed for getCanvas().toDataURL() to return pixels rather than a blank image: WebGL is free
    // to discard the drawing buffer after compositing unless asked not to. It costs real rendering
    // performance, so it is enabled ONLY in the editor preview, which is where the publish snapshot
    // is taken. Published portals are unaffected.
    preserveDrawingBuffer: EDIT_MODE,
  });

  // Zoom/compass added later (after the basemap + tools controls) so the basemap
  // icon sits above the zoom controls in the top-right stack.
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

  // Generate point-marker icons on demand (also covers the first render gap).
  map.on('styleimagemissing', function (e) {
    if (!e.id || e.id.indexOf('gd-pt-') !== 0 || map.hasImage(e.id)) return;
    // The id CARRIES its parameters (gd-pt-<shape>-<hex>-<size>), so any missing image can be built
    // from the id alone. It used to be looked up from the layer's metadata, which only worked while
    // a layer had exactly ONE icon — a classified point layer has one per class, and `icon-image`
    // is a data-driven expression selecting between them.
    const spec = parseMarkerImageId(e.id);
    if (spec) { setMarkerImage(e.id, spec.shape, spec.color, spec.size, spec.outline, spec.outlineWidth); return; }
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
  // The globe/2D PROJECTION is part of the pinned start view: an admin who arranges a portal on the
  // 3D globe expects visitors to open on the globe. center/zoom/bearing/pitch were already captured;
  // this was the only piece of the camera that was lost. Both guards matter — a cached MapLibre v4
  // bundle has no get/setProjection, and every portal saved before this has no `projection` key.
  // Either way we leave the map alone, which is the previous behaviour (mercator).
  function currentProjection() {
    try { return (map.getProjection() || {}).type || null; } catch (e) { return null; }
  }
  function applyProjection(name) {
    if (!name || typeof map.setProjection !== 'function') return;
    try { map.setProjection({ type: name }); } catch (e) {}
    // Caught SEPARATELY, and never allowed to escape. `applyProjection` is called from inside
    // `map.on('load')`, which is where the control cluster and every input handler are wired up —
    // so anything that throws here would abort the REST of that handler and leave the map half
    // built: some controls present, others missing, and interactions in whatever state they were
    // in. The sky is decoration; it must not be able to take navigation down with it.
    try { applySpace(name === 'globe'); } catch (e) { /* decoration only */ }
  }

  /**
   * The globe hangs in SPACE, not in a dark rectangle.
   *
   * Two independent pieces, because they cover different parts of the picture and either can be
   * unavailable:
   *
   *  1. `setSky` (MapLibre v5) paints the ATMOSPHERE — the luminous rim that makes the planet read
   *     as a sphere with air around it rather than a flat circle. Wrapped in try/catch and a
   *     capability check: a portal bundle published before this shipped runs an older maplibre from
   *     its own bundle, and must not throw here.
   *  2. A CSS starfield on the map CONTAINER, behind the canvas, which is what shows through
   *     wherever the canvas is transparent (everything beyond the atmosphere). Stars are not part of
   *     the MapLibre style spec at all, so this is the only way to have any.
   *
   * Toggled OFF for mercator: a flat map covering the whole viewport would never show it, and a
   * star texture behind a partially-transparent basemap would tint it.
   */
  /**
   * Apply the sky/starfield from the map's CURRENT projection, whatever set it.
   *
   * `applyProjection` is only one of the ways a portal reaches globe mode — MapLibre's own globe
   * control is another, and it changes the projection directly. Reading the map instead of tracking
   * our own intent means every route is covered, including ones added later.
   */
  function syncSpace() {
    let type = null;
    try {
      const p = map.getProjection && map.getProjection();
      type = p && (typeof p === 'string' ? p : p.type);
    } catch (e) { return; }        // older maplibre with no getProjection — leave it alone
    applySpace(type === 'globe');
  }

  function applySpace(on) {
    var el = map.getContainer && map.getContainer();
    if (el) el.classList.toggle('gd-space', !!on);
    if (typeof map.setSky !== 'function') return;
    try {
      map.setSky(on ? {
        // Brighter than the first pass, which sat so close to black that the planet read as a
        // cut-out. The limb is the whole effect — it is what makes a circle look like a lit sphere
        // with air around it — so it gets a strong, slightly cyan blue and a wider blend into space.
        'sky-color': '#0c1330',            // deep space, not pure black — black looks like a hole
        'sky-horizon-blend': 0.62,
        'horizon-color': '#a8d4ff',        // the atmospheric limb
        'horizon-fog-blend': 0.72,
        'fog-color': '#0c1330',
        'fog-ground-blend': 0.1,
        // Fade the atmosphere out as you zoom in: it belongs to the view of a PLANET, and at street
        // level it would just be a blue wash over the map.
        'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 4, 0.7, 7, 0],
      } : {
        'atmosphere-blend': 0,
      });
    } catch (e) { /* older maplibre in an existing bundle — the starfield still applies */ }
  }

  const bounds = STYLE.geodeploy?.bounds;
  const savedView = STYLE.geodeploy?.view;

  /**
   * A `fill-extrusion` layer is INVISIBLE on a flat map — straight down, an extruded polygon and a
   * plain fill are the same shape. So a portal whose author enabled 3D and pinned a top-down view
   * (which is what pinning does by default) would publish looking exactly like the 2D version, and
   * read as "the 3D feature is broken".
   *
   * Opening tilted only when the author did NOT choose a pitch: an explicit 0 that the author set
   * while looking at their 3D layer is a decision, and this must not overrule it. `pitch == null`
   * means "never pinned", which is the case that needs the help.
   */
  const has3D = (STYLE.layers || []).some(function (l) { return l && l.type === 'fill-extrusion'; });
  const DEFAULT_3D_PITCH = 45;

  if (savedView && Array.isArray(savedView.center) && savedView.center.length === 2) {
    // Admin pinned a specific extent/zoom during portal creation — honour it exactly.
    try {
      map.jumpTo({
        center: savedView.center,
        zoom: savedView.zoom != null ? savedView.zoom : 2,
        bearing: savedView.bearing || 0,
        pitch: savedView.pitch != null ? savedView.pitch : (has3D ? DEFAULT_3D_PITCH : 0),
      });
      applyProjection(savedView.projection);
    } catch (e) { /* ignore — keep default view */ }
  } else if (validLonLatBounds(bounds)) {
    try {
      map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], {
        padding: { top: 40, bottom: 40, left: sidebar.offsetWidth + 40, right: 40 },
        duration: 0,
        pitch: has3D ? DEFAULT_3D_PITCH : 0,
      });
    } catch (e) { /* ignore — keep default view */ }
  } else if (has3D) {
    try { map.setPitch(DEFAULT_3D_PITCH); } catch (e) { /* ignore */ }
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
  // 3D-Z elevation layers carry their geojson inline; transform Z (scale·z+offset) once up front so
  // deck.gl's GeoJsonLayer draws them at altitude. They never viewport-fetch (data is static).
  function transformElevation(geojson, elev) {
    const scale = (elev && isFinite(elev.vertical_scale)) ? elev.vertical_scale : 1;
    const offset = (elev && isFinite(elev.offset)) ? elev.offset : 0;
    function tz(c) {
      if (Array.isArray(c) && c.length && typeof c[0] === 'number') {
        const z = (typeof c[2] === 'number' && isFinite(c[2])) ? c[2] : 0;
        return [c[0], c[1], z * scale + offset];
      }
      return Array.isArray(c) ? c.map(tz) : c;
    }
    return { type: 'FeatureCollection', features: ((geojson && geojson.features) || []).map(function (f) {
      const g = f && f.geometry;
      return g ? Object.assign({}, f, { geometry: Object.assign({}, g, { coordinates: tz(g.coordinates) }) }) : f;
    }) };
  }
  DECK_LAYERS.forEach(function (d) {
    deckState[d.layer_id] = {
      visible: d.visible !== false,
      data: (d.elevation && d.geojson) ? transformElevation(d.geojson, d.elevation) : null,
    };
  });
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
    // "none" is a SENTINEL, not a colour. Feeding it to the hex parser gave NaN components, which
    // deck renders as BLACK — so asking for no outline produced the most visible outline available.
    const noOutline = d.outline_color === 'none';
    const rgb = deckHexToRgb(d.color);
    const outline = deckHexToRgb(noOutline ? '#000000' : (d.outline_color || '#1d4ed8'));
    const op = d.opacity != null ? d.opacity : 1;
    if (st.data.__arrowTable) {
      // GeoArrow detail: the Arrow table is consumed zero-copy by @geoarrow/deck.gl-layers —
      // never converted to GeoJSON. Styling mirrors the GeoJsonLayer branch below.
      const t = st.data.__arrowTable;
      if (isLine) {
        return new DK.geo.GeoArrowPathLayer({
          id: 'deck_' + d.layer_id, data: t, pickable: true,
          getColor: rgb.concat(Math.round(255 * op)),
          getWidth: d.line_width != null ? d.line_width : 2,
          widthUnits: 'pixels', widthMinPixels: d.line_width || 2,
        });
      }
      if (isPoly) {
        // 3D through the ARROW path too. This is the transport an untiled GeoParquet layer actually
        // uses, so extruding only in the GeoJSON branch below meant "3D does nothing" for exactly
        // the layers the feature was added for.
        //
        // A GeoArrow accessor is an Arrow COLUMN, not a function — `getElevation` takes the vector
        // straight from the table. The × multiplier therefore rides on `elevationScale` rather than
        // being folded into the accessor, which is what the prop is for anyway. No column of that
        // name (renamed, or a re-prep that dropped it) → no extrusion, rather than a flat mesh.
        const aex = d.extrusion || {};
        const acol = (aex.enabled && aex.field && t.getChild) ? t.getChild(aex.field) : null;
        return new DK.geo.GeoArrowPolygonLayer({
          id: 'deck_' + d.layer_id, data: t, pickable: true,
          filled: true, stroked: !acol && !noOutline,   // walls plus an outline is a smudge at any pitch
          extruded: !!acol,
          getElevation: acol || undefined,
          elevationScale: Number(aex.scale) || 1,
          getFillColor: rgb.concat(Math.round(255 * op * (d.fill_opacity != null ? d.fill_opacity : 0.45))),
          getLineColor: outline.concat(Math.round(255 * op)),
          lineWidthUnits: 'pixels',
          getLineWidth: d.line_width != null ? d.line_width : 1,
          lineWidthMinPixels: 1,
        });
      }
      return new DK.geo.GeoArrowScatterplotLayer({
        id: 'deck_' + d.layer_id, data: t, pickable: true,
        getFillColor: rgb.concat(Math.round(255 * op)),
        radiusUnits: 'pixels',
        getRadius: d.radius != null ? d.radius : 5,
        radiusMinPixels: 2,
      });
    }
    if (st.data.__overview) {
      // Large-scale representation: the manifest's partition grid shaded by feature density.
      // Deliberately NOT pickable, unlike the detail layers: a density cell is not a feature, so a
      // pointer cursor over it would promise a click that does nothing.
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
    // 3D for a deck-rendered POLYGON layer. A GeoParquet layer emits no MapLibre layer at all
    // (portal_generator returns a deck descriptor instead), so `fill-extrusion` never reaches it —
    // extrusion has to be asked of deck directly. GeoJsonLayer does it natively for polygons:
    // `extruded` + `getElevation`, no geometry change and no extra layer type.
    //
    // POINTS are not handled here. deck extrudes polygons, not points, and the vendored bundle has
    // no ColumnLayer — so a point pillar needs the geometry buffered first, which is what the
    // PostGIS path does in the tile server. Until that is mirrored here, the editor hides 3D for
    // deck-rendered point layers rather than offering something that does nothing.
    const ex = d.extrusion || {};
    const extruded = isPoly && !!ex.enabled && !!ex.field;
    const exScale = Number(ex.scale) || 1;
    return new DK.GeoJsonLayer({
      id: 'deck_' + d.layer_id,
      data: st.data,
      // Picking is what lets the cursor become a pointer over a GeoParquet
      // feature: these layers have no MapLibre layer for queryRenderedFeatures to find.
      pickable: true,
      filled: !isLine,
      // Lines ARE their stroke, so `noOutline` must not erase a line layer — it is a POLYGON
      // outline setting. Extruded polygons drop it too (walls plus an outline is a smudge).
      stroked: isLine || (!extruded && !(isPoly && noOutline)),
      extruded: extruded,
      // A feature missing the property, or holding a non-numeric one, becomes 0 rather than NaN —
      // NaN propagates into the mesh and drops the whole layer, not just that feature.
      getElevation: extruded
        ? function (f) { const v = Number((f.properties || {})[ex.field]); return (isFinite(v) ? v : 0) * exScale; }
        : 0,
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
      if (d.elevation) return false;                   // 3D-Z: static inline data, never fetch
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
    if (maplibregl.GlobeControl) {
      map.addControl(new maplibregl.GlobeControl(), CTRL_POS);
      // MapLibre's own globe button flips the projection ITSELF — it never goes through
      // applyProjection, which is the only thing that was telling applySpace about it. So switching
      // to the globe with that button gave a planet on a flat black panel: the sky and the starfield
      // are ours, and nothing had told them the projection changed. Sync from the map's ACTUAL
      // projection rather than from whatever we last set, so any route into globe mode is covered.
      try { syncSpace(); } catch (e) { /* decoration only */ }
      try { map.on('projectiontransition', syncSpace); } catch (e) { /* not in older maplibre */ }
      // The event above is not in every v5 build, so also watch the button itself. Deferred a tick
      // because the control sets the projection after the click handler it registered first.
      setTimeout(function () {
        const btn = document.querySelector('.maplibregl-ctrl-globe, button.maplibregl-ctrl-globe-enabled');
        if (btn) btn.addEventListener('click', function () { setTimeout(syncSpace, 0); });
      }, 0);
    }
    // `visualizePitch` makes the compass a TILT handle as well as a rotate one (drag it up/down) and
    // shows the current pitch on the needle. Without it the only way to tilt is right-drag or
    // ctrl-drag, which nothing on the page advertises — so a 3D portal looked flat and unfixable.
    map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), CTRL_POS);
    try { map.addControl(new TiltControl(), CTRL_POS); } catch (e) { console.warn('[geodeploy] tilt control failed', e); }
    // V-11 storymap: build the scrollytelling narrative that drives the camera + layer state.
    if (LAYOUT.archetype === 'storymap') {
      try { setupStory(); } catch (e) { console.warn('[geodeploy] story failed', e); }
      loading.ready('story');   // outside the catch: a story that fails must still reveal the map
    }
    // The pinned projection is applied at construction too, but a style load can reset it — the style
    // spec carries its own projection and MapLibre applies that when the style becomes live, which
    // silently put a globe portal back on mercator. Re-applying here is the point where the map is
    // definitely ready, and it is a no-op when the projection already matches.
    if (savedView && savedView.projection) applyProjection(savedView.projection);
    // V-14 catalog: build the browse surface. AFTER initDeck() so GeoParquet layers already have a
    // deckState entry — the cards seed their on/off state from it.
    if (LAYOUT.archetype === 'catalog') {
      try { setupCatalog(); } catch (e) { console.warn('[geodeploy] catalog failed', e); }
      loading.ready('catalog');   // outside the catch, for the same reason as the story panel
    }
    // V-16 dashboard: hand the map to the dashboard runtime (templates/shared/dashboard.js), which
    // owns the widget grid, the shared filter state and the query layer. It lives in its own file
    // rather than in here so a 4.7k-line runtime does not become a 6k-line one, and so it can be
    // syntax-checked on its own. AFTER initDeck() for the same reason the catalog is: a map widget
    // over a GeoParquet layer needs deckState to already exist.
    if (LAYOUT.archetype === 'dashboard') {
      try {
        if (window.GD_DASHBOARD && typeof window.GD_DASHBOARD.setup === 'function') {
          window.GD_DASHBOARD.setup({
            map: map,
            maplibregl: maplibregl,
            style: STYLE,
            layout: LAYOUT,
            editMode: EDIT_MODE,
            // The runtime's own absolutifier, so a tile/data URL baked with the origin token
            // resolves identically in both files rather than being re-derived in one of them.
            absUrl: function (u) { return String(u || '').split('__GD_ORIGIN__').join(location.origin); },
            // Fitting the map to a selection is portal.js's job (it owns the camera + the
            // navigation history), so the dashboard asks rather than reaching for map.fitBounds.
            fitBbox: function (b) {
              try { map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 16, duration: 700 }); }
              catch (e) { /* an empty or malformed extent is not worth breaking a click over */ }
            },
          });
        } else {
          console.warn('[geodeploy] dashboard runtime missing');
        }
      } catch (e) { console.warn('[geodeploy] dashboard failed', e); }
      loading.ready('dashboard');   // outside the catch, for the same reason as the story panel
    }
    // R2: when rendered as the editor's preview (?edit=1), open the postMessage channel + click-to-place.
    try { setupEditMode(); } catch (e) { console.warn('[geodeploy] edit mode failed', e); }
    // Everything this handler mounts now exists. The cover still waits on 'render' — the map has to
    // have DRAWN the opening view, not merely been told what to draw.
    loading.ready('map');
  });
  // MapLibre is idle once it has finished rendering everything it can for the current view: tiles
  // fetched, layers painted. That is the earliest honest moment to call the map "shown".
  map.once('idle', function () {
    loading.ready('render');
    // 'idle' strictly follows the 'load' handlers, so by here that handler has either finished (and
    // already cleared this) or thrown partway through. Clearing it again costs nothing and means a
    // single unguarded step in that sequence degrades to "a portal missing one panel" rather than
    // "a portal stuck behind a loading screen until the backstop fires".
    loading.ready('map');
  });

  const resetBtn = document.getElementById('reset-styling');
  if (resetBtn) resetBtn.addEventListener('click', resetStyling);

  // Restore each user layer's original paint + visibility, then rebuild controls
  function resetStyling() {
    STYLE.layers.forEach(l => {
      // Restore primaries AND raw-paint "parts" (a GeoLibre-imported layer can render as several
      // sub-layers sharing a geodeploy:layer_id; only the first carries geodeploy:name).
      if (!l.metadata || (!l.metadata['geodeploy:name'] && !l.metadata['geodeploy:part'])) return;
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
          applyProjection(s.view.projection);
        } catch (e) {}
      }
      if (s && s.layers) applyStoryLayers(s.layers);
    }
    // Issue #27: on a phone in PORTRAIT the narrative is a bottom strip that scrolls SIDEWAYS (see
    // portal.css). Same media query on both sides, so the layout and the behaviour cannot disagree.
    const horizontal = window.matchMedia('(max-width: 640px) and (orientation: portrait)');
    function isSideways() { return document.body.dataset.archetype === 'storymap' && horizontal.matches; }

    let io = null;
    function observeSections() {
      if (io) io.disconnect();
      // Narrow the trigger band to the middle of whichever axis is scrolling.
      const margin = isSideways() ? '0px -45% 0px -45%' : '-45% 0px -45% 0px';
      io = new IntersectionObserver(function (entries) {
        // Pick the most-centered intersecting section.
        let best = null;
        entries.forEach(function (en) { if (en.isIntersecting && (!best || en.intersectionRatio > best.intersectionRatio)) best = en; });
        if (best) activate(parseInt(best.target.dataset.idx, 10), true);
      }, { rootMargin: margin, threshold: [0, 0.5, 1] });
      sections.forEach(function (el) { io.observe(el); });
    }
    observeSections();
    // Rotating the phone swaps the axis; without this the trigger band stays on the old one and
    // sections stop activating.
    try { horizontal.addEventListener('change', observeSections); }
    catch (e) { try { horizontal.addListener(observeSections); } catch (e2) {} }
    // E4: in the editor preview each edit reloads the iframe — DON'T fly to section 0 (it yanks the
    // author's map away). Just mark it active; the baked initial_view keeps the author's camera.
    // Sections default to opacity .35 and the ACTIVE one goes to 1 through a transition. On load that
    // played as a visible fade — the panel appeared dimmed, then brightened. Suppress the transition
    // for the first activation only, then hand it back for real scrolling.
    document.body.dataset.storyReady = '0';
    activate(0, !EDIT_MODE);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { document.body.dataset.storyReady = '1'; });
    });

    // E2: the wheel scrolls the narrative ONLY when the cursor is over the story column; over the open
    // map it zooms as normal. The panel is pointer-events:none (so the map drags behind it), so we can't
    // rely on e.target — we hit-test the pointer against the panel's box. A capture-phase listener lets
    // us stop the event before MapLibre's own wheel handler sees it (so the map doesn't also zoom).
    try { map.scrollZoom.enable(); } catch (e) {}
    const mw = document.getElementById('map-wrap');
    if (mw && !mw._storyWheel) {
      mw._storyWheel = true;
      mw.addEventListener('wheel', function (e) {
        const r = panel.getBoundingClientRect();
        // The WHOLE left (or right) narrative column scrolls the story; only over the open map does the
        // wheel zoom. The panel is pointer-events:none, so we hit-test the pointer against its box.
        const inX = e.clientX >= r.left && e.clientX <= r.right;
        if (!inX || e.clientY < r.top || e.clientY > r.bottom) return;  // over the map → MapLibre zooms
        if (isSideways()) panel.scrollLeft += (e.deltaY || e.deltaX);
        else panel.scrollTop += e.deltaY;
        e.preventDefault();
        e.stopPropagation();                    // don't let the map zoom too
      }, { passive: false, capture: true });
    }

    // E1: hidden scrollbar (CSS) + up/down "more" chevrons that appear when content is off-screen.
    if (mw) {
      const up = document.createElement('div'); up.className = 'gd-story-more gd-story-up'; up.innerHTML = chevron('up');
      const down = document.createElement('div'); down.className = 'gd-story-more gd-story-down'; down.innerHTML = chevron('down');
      mw.appendChild(up); mw.appendChild(down);
      function setArrowGlyphs() {
        // The CSS moves these to the sides in portrait; the SVG has to follow, or a "next section"
        // control points downwards while the strip scrolls sideways.
        const sideways = isSideways();
        up.innerHTML = chevron(sideways ? 'left' : 'up');
        down.innerHTML = chevron(sideways ? 'right' : 'down');
      }
      setArrowGlyphs();
      try { horizontal.addEventListener('change', setArrowGlyphs); } catch (e) {}
      function updateArrows() {
        if (isSideways()) {
          up.classList.toggle('show', panel.scrollLeft > 6);
          down.classList.toggle('show', panel.scrollLeft + panel.clientWidth < panel.scrollWidth - 6);
        } else {
          up.classList.toggle('show', panel.scrollTop > 6);
          down.classList.toggle('show', panel.scrollTop + panel.clientHeight < panel.scrollHeight - 6);
        }
      }
      panel.addEventListener('scroll', updateArrows);
      // One page = one section when snapping sideways, so a tap lands on a section rather than
      // between two.
      up.addEventListener('click', function () {
        if (isSideways()) panel.scrollBy({ left: -panel.clientWidth * 0.86, behavior: 'smooth' });
        else panel.scrollBy({ top: -panel.clientHeight * 0.8, behavior: 'smooth' });
      });
      down.addEventListener('click', function () {
        if (isSideways()) panel.scrollBy({ left: panel.clientWidth * 0.86, behavior: 'smooth' });
        else panel.scrollBy({ top: panel.clientHeight * 0.8, behavior: 'smooth' });
      });
      try { horizontal.addEventListener('change', updateArrows); } catch (e) {}
      setTimeout(updateArrows, 120);
    }
  }
  function chevron(dir) {
    const pts = dir === 'up' ? '6 15 12 9 18 15'
      : dir === 'down' ? '6 9 12 15 18 9'
      : dir === 'left' ? '15 6 9 12 15 18'
      : '9 6 15 12 9 18';
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
  function markerImage(shape, color, size, outline, outlineWidth) {
    const dpr = 2, r = Math.max(3, Number(size) || 5);
    // A RATIO of the radius, not pixels: a 3 px ring around a 4 px dot and around a 20 px dot are
    // different symbols, and resizing a layer should keep the outline in proportion. 0.28 is what
    // the old hard-coded stroke was, so an unstyled marker is pixel-identical to before.
    const ow = (outlineWidth == null ? 0.28 : Number(outlineWidth));
    const stroke = Math.max(0, r * (isFinite(ow) ? ow : 0.28));
    // Fixed canvas size (fits the max marker radius) so every icon for a layer shares
    // dimensions — that lets map.updateImage() work when only the SIZE changes.
    const dim = 80;
    const cv = document.createElement('canvas');
    cv.width = dim * dpr; cv.height = dim * dpr;
    const ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr); ctx.lineJoin = 'round';
    drawMarkerPath(ctx, shape, dim / 2, dim / 2, r);
    ctx.fillStyle = color || '#3b82f6'; ctx.fill();
    // No outline is a real choice — `outline === null` means draw none, which is different from
    // "not specified" (undefined → the white default every marker used to have).
    const oc = outline === undefined ? '#ffffff' : outline;
    if (oc && stroke > 0) { ctx.strokeStyle = oc; ctx.lineWidth = stroke; ctx.stroke(); }
    const d = ctx.getImageData(0, 0, dim * dpr, dim * dpr);
    return { width: dim * dpr, height: dim * dpr, data: d.data, pixelRatio: dpr };
  }
  // Twin of ui/src/lib/symbology.js::parseMarkerImageId — the id is the spec.
  function parseMarkerImageId(id) {
    // gd-pt-<shape>-<hex>-<size>-<outlineHex|none>-<widthRatio>. The trailing pair is optional so an
    // id baked into a portal published before outlines were configurable still parses — it then
    // draws with the old white stroke, which is what that portal looked like.
    const m = /^gd-pt-([a-z]+)-([0-9a-f]{3,8})-([0-9.]+)(?:-(none|[0-9a-f]{3,8})-([0-9.]+))?$/
      .exec(String(id || ''));
    if (!m) return null;
    return {
      shape: m[1], color: '#' + m[2], size: parseFloat(m[3]),
      outline: m[4] === undefined ? undefined : (m[4] === 'none' ? null : '#' + m[4]),
      outlineWidth: m[5] === undefined ? undefined : parseFloat(m[5]),
    };
  }
  function setMarkerImage(imgId, shape, color, size, outline, outlineWidth) {
    // The WHOLE body is guarded, not just the add/update. `markerImage()` builds a canvas — it can
    // fail (no 2D context in a restricted browser, an unknown shape, a zero-sized canvas) and it
    // used to sit OUTSIDE the try, so a single bad image threw out of `ensurePointImages`, which
    // runs seven lines into `map.on('load')` — aborting the rest of that handler, where the control
    // cluster and every input binding are set up. The map would then load, draw, and simply not
    // respond properly: a missing marker must never cost you navigation.
    //
    // This got sharper when a classified layer began registering one image PER CLASS: many more
    // chances to hit it, on a code path that had only ever created one.
    try {
      const im = markerImage(shape, color, size, outline, outlineWidth);
      try { if (map.hasImage(imgId)) map.updateImage(imgId, im); else map.addImage(imgId, im, { pixelRatio: im.pixelRatio }); }
      catch (e) { try { if (map.hasImage(imgId)) map.removeImage(imgId); map.addImage(imgId, im, { pixelRatio: im.pixelRatio }); } catch (e2) {} }
    } catch (e) { /* one marker is not worth the map */ }
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
    // Per-layer guard for the same reason as above: this runs inside map.on('load'), so anything
    // escaping it takes the control setup with it.
    (STYLE.layers || []).forEach(l => { try {
      if (l.type !== 'symbol' || !l.layout || !l.layout['icon-image'] || !l.metadata) return;
      // A classified point layer declares one image per class in geodeploy:markerImages. Create
      // them ALL now: leaving it to styleimagemissing means each class's markers pop in the first
      // time that class scrolls into view, which looks like the map is still loading.
      const all = l.metadata['geodeploy:markerImages'];
      if (Array.isArray(all) && all.length) {
        all.forEach(function (im) { setMarkerImage(im.id, im.shape, im.color, im.size, im.outline, im.outline_width); });
        return;
      }
      if (l.metadata['geodeploy:marker'] === undefined) return;
      setMarkerImage(l.layout['icon-image'], l.metadata['geodeploy:marker'] || 'circle',
        l.metadata['geodeploy:markerColor'] || '#3b82f6', l.metadata['geodeploy:markerSize'] || 5);
    } catch (e) { /* one layer's icons are not worth the rest of the load handler */ } });
  }

  // All MapLibre layer ids that make up ONE catalog layer. Usually just the primary, but a
  // GeoLibre-imported layer using the raw-paint passthrough renders as several sub-layers (fill +
  // outline, …) that share a geodeploy:layer_id — the primary carries geodeploy:name/type, the rest
  // carry geodeploy:part. Grouping them lets the eye toggle hide/show the whole layer at once.
  function groupLayerIds(primaryId) {
    const prim = STYLE.layers.find(function (l) { return l.id === primaryId; });
    const m = prim && prim.metadata;
    if (!m || m['geodeploy:layer_id'] == null) return [primaryId];
    const lid = String(m['geodeploy:layer_id']), type = m['geodeploy:type'];
    // `ext` is part of the identity for the SAME reason card refs use layerRefType(): an external
    // XYZ source bakes geodeploy:type = 'raster', so without this an external and a real raster
    // sharing an id (both 1 on a fresh install) group together and one eye toggles both.
    const ext = !!m['geodeploy:external'];
    const ids = STYLE.layers.filter(function (l) {
      const lm = l.metadata;
      return lm && String(lm['geodeploy:layer_id']) === lid
        && !!lm['geodeploy:external'] === ext
        && (lm['geodeploy:type'] === type || lm['geodeploy:part'] === true);
    }).map(function (l) { return l.id; });
    return ids.length ? ids : [primaryId];
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
      // V-13: match to the folder tree. MUST be layerRefType(), NOT geodeploy:type — an EXTERNAL
      // source bakes geodeploy:type = src.kind ('raster' for an XYZ layer), so an XYZ source and a
      // real raster layer that happen to share an id (both 1 on a fresh install) produced the SAME
      // ref. cardByRef is a plain object, so the external overwrote the raster's entry: the tree's
      // raster node moved the EXTERNAL's card into that folder, and the tree's 'external:<id>' node
      // matched nothing, leaving its folder empty. layerRefType() namespaces externals separately,
      // which is already what setLayerVisByRef() and the storymap layer refs use.
      card.dataset.ref = layerRefType(layer) + ':' + meta['geodeploy:layer_id'];
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
          '<button class="layer-swatch-btn" data-swatch="' + layer.id + '" data-layer-id="' + layer.id + '" title="Symbology" aria-label="Edit symbology">' + legendSwatch(geom, color, dash, shape, geom === 'polygon' ? bakedOutline(layer.id) : null) + '</button>' +
          '<span class="layer-name" title="' + escHtml(name) + '">' + escHtml(name) + '</span>' +
          '<button class="layer-zoom" data-layer-id="' + layer.id + '" title="Zoom to layer" aria-label="Zoom to layer"' + (canZoom ? '' : ' disabled') + '>' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<circle cx="12" cy="12" r="7"/><line x1="12" y1="1" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="23"/>' +
            '<line x1="1" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="23" y2="12"/></svg>' +
          '</button>' +
        '</div>' +
        (type === 'raster' && !meta['geodeploy:external']
          ? '<div class="layer-legend" data-legend="' + layer.id + '">' + rasterLegendHtml(layer) + '</div>'
          : vectorLegendHtml(meta['geodeploy:legend'], geom,
                             meta['geodeploy:legendField'], meta['geodeploy:sizeLegend'], color,
                             meta['geodeploy:lineType'], meta['geodeploy:marker'],
                             geom === 'polygon' ? bakedOutline(layer.id) : null));
      container.appendChild(card);
    });

    container.querySelectorAll('.legend-toggle').forEach(btn => {
      btn.addEventListener('click', e => setLegendCollapsed(
        e.currentTarget.closest('.layer-legend'),
        e.currentTarget.getAttribute('aria-expanded') === 'true'));
    });
    // Expanded for ONE classified layer, collapsed beyond that: with a single legend the
    // classification is usually the point of the map, and with several the layer NAMES — the thing
    // you click — get pushed off screen. Only the initial state; the buttons win afterwards.
    const legends = container.querySelectorAll('.legend-classes');
    if (legends.length > 1) legends.forEach(el => setLegendCollapsed(el, true));

    container.querySelectorAll('.layer-eye').forEach(btn => {
      btn.addEventListener('click', e => {
        const id = e.currentTarget.dataset.layerId;
        const vis = (map.getLayoutProperty(id, 'visibility') === 'none') ? 'visible' : 'none';
        // Toggle EVERY MapLibre sub-layer of this catalog layer together (a raw-paint import renders
        // as fill + outline + …, all sharing one geodeploy:layer_id), not just the primary.
        groupLayerIds(id).forEach(function (lid) {
          try { map.setLayoutProperty(lid, 'visibility', vis); } catch (err) {}
        });
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
    // "Show me less" belongs in ONE place, so the legend control sits in the same row as the folder
    // one. Offered only when there is a legend to hide.
    const hasLegends = !!container.querySelector('.legend-classes');
    if (hasLegends) {
      left.insertAdjacentHTML('beforeend',
        '<button type="button" class="lg-legends la-icon" title="Show / hide all legends" ' +
        'aria-label="Show or hide all legends" aria-pressed="false">' +
        '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/>' +
        '<line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>' +
        '<circle cx="3.5" cy="6" r="1.5"/><circle cx="3.5" cy="12" r="1.5"/>' +
        '<circle cx="3.5" cy="18" r="1.5"/></svg></button>');
    }
    acts.appendChild(left);
    acts.appendChild(right);
    parent.insertBefore(acts, container);
    if (hasGroups) {
      acts.querySelector('.lg-expand-all').addEventListener('click', function () { setAllGroups(false); });
      acts.querySelector('.lg-collapse-all').addEventListener('click', function () { setAllGroups(true); });
    }
    if (hasLegends) {
      const btn = acts.querySelector('.lg-legends');
      // One button that flips, rather than a pair: the state is knowable (are any expanded?), so a
      // second button would spend width on something the first can answer.
      btn.addEventListener('click', function () {
        const anyOpen = !!document.querySelector(
          '#layer-list .legend-toggle[aria-expanded="true"]');
        setAllLegends(anyOpen);
        btn.setAttribute('aria-pressed', anyOpen ? 'true' : 'false');
      });
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
  function setLegendCollapsed(legend, collapsed) {
    if (!legend) return;
    const body = legend.querySelector(':scope > .legend-body');
    const btn = legend.querySelector(':scope > .legend-toggle');
    if (body) body.style.display = collapsed ? 'none' : '';
    if (btn) {
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      btn.title = collapsed ? 'Show these classes' : 'Hide these classes';
      const caret = btn.querySelector('.legend-caret');
      if (caret) caret.textContent = collapsed ? '▸' : '▾';
    }
  }
  function setAllLegends(collapsed) {
    const container = document.getElementById('layer-list');
    if (!container) return;
    container.querySelectorAll('.legend-classes').forEach(el => setLegendCollapsed(el, collapsed));
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
    root.querySelectorAll('.layer-outline-width').forEach(inp => {
      inp.addEventListener('input', e => {
        const fillId = e.target.dataset.layerId;
        const w = parseFloat(e.target.value);
        if (isNaN(w)) return;
        const outId = ensureOutlineLayer(fillId);
        if (outId) map.setPaintProperty(outId, 'line-width', w);
      });
    });
    root.querySelectorAll('.layer-outline-color').forEach(inp => {
      inp.addEventListener('input', e => {
        const fillId = e.target.dataset.layerId;
        // Whichever layer is drawing the edge: the published outline layer if there is one, the
        // fill's own hairline if not — setting the wrong one silently does nothing.
        if (map.getLayer(fillId + '-outline')) {
          map.setPaintProperty(fillId + '-outline', 'line-color', e.target.value);
        } else {
          try { map.setPaintProperty(fillId, 'fill-outline-color', e.target.value); } catch (err) {}
        }
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
    root.querySelectorAll('.rstyle-algorithm').forEach(el => el.addEventListener('change', e => {
      const s = e.target.dataset.src;
      // The row is REBUILT, not just re-applied: the Z factor and the contour inputs belong to one
      // mode each, so the popover has to stop showing the controls of the mode being left.
      rasterState[s] = Object.assign({}, rasterState[s], { algorithm: e.target.value });
      applyRaster(s); refreshRasterRow(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-increment').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { increment: e.target.value });
      applyRaster(s); updateRasterLegend(s);
    }));
    root.querySelectorAll('.rstyle-thickness').forEach(el => el.addEventListener('input', e => {
      const s = e.target.dataset.src;
      rasterState[s] = Object.assign({}, rasterState[s], { thickness: e.target.value });
      applyRaster(s);
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
    sw.innerHTML = legendSwatch(geomK, color, dash, shape,
      geomK === 'polygon' ? { color: polygonOutlineColor(id), width: polygonOutlineWidth(id) } : null);
  }

  // Legend swatch that mirrors the layer's actual symbol + colour (+ line dash / marker shape)
  /** A polygon's outline as PUBLISHED — `{color, width}` — or null.
   *
   * Read from STYLE rather than from the live map, because the layer list is built before the map
   * has finished loading its layers and `getPaintProperty` would throw for every one of them. The
   * outline lives in a sibling `-outline` line layer when the author set a width, and in the fill's
   * own `fill-outline-color` when they did not. */
  function bakedOutline(fillId) {
    const layers = (STYLE && STYLE.layers) || [];
    const line = layers.find(function (l) { return l.id === fillId + '-outline'; });
    if (line && line.paint) {
      return { color: line.paint['line-color'], width: line.paint['line-width'] };
    }
    const fill = layers.find(function (l) { return l.id === fillId; });
    const c = fill && fill.paint && fill.paint['fill-outline-color'];
    return typeof c === 'string' ? { color: c, width: 1 } : null;
  }

  function legendSwatch(geom, color, dash, shape, outline) {
    const c = color || '#3b82f6';
    if (geom === 'line') {
      const da = dash === 'dashed' ? ' stroke-dasharray="3 2"' : dash === 'dotted' ? ' stroke-dasharray="0.6 3"' : '';
      return '<svg width="18" height="18" viewBox="0 0 18 18"><line x1="2" y1="9" x2="16" y2="9" stroke="' + c + '" stroke-width="3" stroke-linecap="round"' + da + '/></svg>';
    }
    if (geom === 'polygon') {
      // The outline's own colour and width, when the caller knows them. 1.5 is what this swatch has
      // always drawn, so a caller that does not keeps its legend pixel-identical; a real width is
      // COMPRESSED into the swatch rather than scaled, because a 12 px border on the map would
      // swallow a 13x10 rect whole and the useful signal is "thicker than default".
      const oc = (outline && outline.color) || c;
      const raw = outline && Number(outline.width);
      const ow = isFinite(raw) ? Math.max(0.5, Math.min(4, 1.5 + (raw - 1) * 0.6)) : 1.5;
      return '<svg width="18" height="18" viewBox="0 0 18 18"><rect x="2.5" y="4" width="13" ' +
        'height="10" fill="' + c + '" fill-opacity="0.45" stroke="' + oc + '" stroke-width="' + ow + '"/></svg>';
    }
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
  // ── What is ACTUALLY on the map, per control ─────────────────────────────────────────────────
  // Viewer's session choice first, else what the author baked into the tile URL. Only `bidx` did
  // this before, which caused two related faults: the popover OPENED showing defaults (hillshade
  // unchecked, Z 1, empty stretch, grayscale) no matter how the portal was published; and because
  // `applyRaster` rebuilt the URL from `rasterState` alone, touching any ONE control then discarded
  // every baked param the viewer had not touched — changing the palette silently dropped the
  // author's stretch. Both surfaces now read through these, so the popover, the map and the legend
  // cannot disagree.
  //
  // `undefined` means "viewer has not touched this" — distinct from a viewer's deliberate empty
  // value, which must NOT resurrect the baked one.
  /** '' | 'hillshade' | 'contours' — TiTiler takes ONE algorithm, so this is a choice, not a flag. */
  function effectiveAlgorithm(srcId) {
    const st = rasterState[srcId] || {};
    if (st.algorithm !== undefined) return st.algorithm || '';
    return parseRasterParams(srcId).algorithm || '';
  }
  function effectiveHillshade(srcId) {
    return effectiveAlgorithm(srcId) === 'hillshade';
  }
  /** {increment, thickness} for contours — the viewer's, else what the author baked in. */
  function effectiveContours(srcId) {
    const st = rasterState[srcId] || {};
    let baked = {};
    try { baked = JSON.parse(parseRasterParams(srcId).algorithm_params || '{}') || {}; } catch (e) { baked = {}; }
    const pick = (key, fallback) => {
      if (st[key] !== undefined && st[key] !== '') return Number(st[key]);
      return baked[key] != null ? Number(baked[key]) : fallback;
    };
    return { increment: pick('increment', 35), thickness: pick('thickness', 1) };
  }
  function effectiveZfactor(srcId) {
    const st = rasterState[srcId] || {};
    if (st.zfactor !== undefined && st.zfactor !== '') return st.zfactor;
    const m = /^b1\*([0-9.]+)$/.exec(parseRasterParams(srcId).expression || '');
    return m ? m[1] : 1;
  }
  function effectiveColormap(srcId) {
    const st = rasterState[srcId] || {};
    if (st.colormap !== undefined) return st.colormap || '';
    return parseRasterParams(srcId).colormap_name || '';
  }
  /** "min,max" or '' — as a STRING, since that is what the tile URL wants. */
  function effectiveRescale(srcId) {
    const st = rasterState[srcId] || {};
    if (st.min != null && st.min !== '' && st.max != null && st.max !== '') return st.min + ',' + st.max;
    if (st.min !== undefined || st.max !== undefined) return '';  // viewer cleared it — respect that
    return parseRasterParams(srcId).rescale || '';
  }
  function rasterBandCount(srcId) {
    const l = STYLE.layers.find(x => x.source === srcId && x.metadata && x.metadata['geodeploy:type'] === 'raster');
    return (l && l.metadata['geodeploy:bands']) || 1;
  }

  /**
   * The legend for a CLASSIFIED vector layer, from `geodeploy:legend` baked into the style.
   *
   * Deliberately a renderer and nothing more: it does not read `classes`/`categories` and build its
   * own labels. The entries come from `services/symbology.legend_entries`, the same call the editor
   * shows while you are styling — so the published legend cannot drift from the published map, and
   * neither can drift from what you saw when you made it.
   *
   * Empty for a single-symbol layer: the swatch beside the name already says everything there is
   * to say, and a one-row legend repeating it is noise.
   */
  // A size scale, drawn as the two ENDS of the ramp. The size expression interpolates linearly, so
  // the ends describe the whole scale; listing intermediate stops would imply steps the map does
  // not draw. Points show as circles of the real radius, lines as strokes of the real width — the
  // legend has to look like the map, not merely report numbers about it.
  function sizeLegendHtml(size, geom, color) {
    if (!size || !size.field) return '';
    const swatch = function (px) {
      const d = Math.max(2, Math.min(28, Number(px) || 2));
      if (geom === 'line')
        return '<span class="legend-size-swatch"><span style="display:block;width:26px;height:' +
          d + 'px;border-radius:' + (d / 2) + 'px;background:' + escHtml(color || '#999') + '"></span></span>';
      return '<span class="legend-size-swatch"><span style="display:block;width:' + (d * 2) +
        'px;height:' + (d * 2) + 'px;border-radius:50%;background:' + escHtml(color || '#999') +
        '"></span></span>';
    };
    return '<div class="legend-size">' +
      '<div class="legend-by">Size by <span class="legend-field">' + escHtml(size.field) + '</span></div>' +
      '<div class="legend-size-row">' +
        '<span class="legend-size-item">' + swatch(size.min_size) +
          '<span class="legend-label">' + escHtml(String(size.min_label)) + '</span></span>' +
        '<span class="legend-size-item">' + swatch(size.max_size) +
          '<span class="legend-label">' + escHtml(String(size.max_label)) + '</span></span>' +
      '</div></div>';
  }

  function vectorLegendHtml(entries, geom, field, size, color, dash, shape, outline) {
    const sizeHtml = sizeLegendHtml(size, geom, color);
    // Size can vary while colour does not — they are independent dimensions — so a layer with no
    // classes may still have a legend worth showing.
    if (!Array.isArray(entries) || !entries.length)
      return sizeHtml ? '<div class="layer-legend legend-classes">' + sizeHtml + '</div>' : '';
    // The SHAPE of the thing, not a square standing in for it. A line layer's classes are lines,
    // a point layer's are its marker — which is part of the symbology, so a square swatch actively
    // misreports a layer drawn with stars. `legendSwatch` is the same function the layer's own
    // swatch button uses; it was simply never reached from here.
    const rows = entries.map(function (e) {
      return '<div class="legend-class">' +
        legendSwatch(geom, e.color || '#999', dash, shape, outline) +
        '<span class="legend-label">' + escHtml(e.label == null ? '' : String(e.label)) + '</span>' +
        '</div>';
    }).join('');
    // Naming the COLUMN turns a row of colours into a statement: without it a reader can see that
    // something varies but not what.
    const by = field ? '<div class="legend-by">Colour by <span class="legend-field">' +
      escHtml(field) + '</span></div>' : '';
    // A count button, then the classes. Collapsing is a DISPLAY state and nothing else: the entries
    // come from `geodeploy:legend`, baked at publish from the same class list the map draws, and are
    // never rebuilt here — that is what stops a published legend drifting from its map.
    const head = '<button type="button" class="legend-toggle" aria-expanded="true" ' +
      'title="Hide these classes">' +
      '<span class="legend-caret" aria-hidden="true">▾</span>' +
      '<span class="legend-count">' + entries.length + ' classes</span></button>';
    return '<div class="layer-legend legend-classes">' + head +
      '<div class="legend-body">' + by + rows + sizeHtml + '</div></div>';
  }

  function rasterLegendHtml(layer) {
    const srcId = layer.source;
    const st = (typeof rasterState !== 'undefined' && rasterState[srcId]) || {};
    const bidx = Array.isArray(st.bidx) ? st.bidx : bakedBidx(srcId);
    if (bidx.length === 3)  // RGB composite — a colormap gradient would be misleading
      return '<div class="legend-range"><span>RGB composite</span><span>bands ' + escHtml(bidx.join(' / ')) + '</span></div>';
    // Through the same helpers as the popover and the tile URL — three surfaces that must agree
    // about what is on the map. Hillshade always reads as a grey ramp: it IS a grey relief image.
    // CONTOURS always reads as TERRAIN, for the same reason: the algorithm colours the background
    // with its own built-in terrain ramp and ignores the layer's colormap entirely, so showing the
    // layer's palette here would be a legend describing a map nobody is looking at.
    const algorithm = effectiveAlgorithm(srcId);
    // A raster classified by VALUE is a list of swatches, not a strip — interpolating between class
    // 3 and class 4 means nothing, and a gradient would claim it does. The mapping is baked into the
    // tile URL as `colormap={"3":[r,g,b,a]}`, which is the only place it exists on this page.
    if (!algorithm) {
      // THE AUTHOR'S CLASSES FIRST, because they are the only place the LABELS exist. The tile URL
      // carries `colormap={"11":[r,g,b,a]}` — enough to draw the map, with nowhere to put a name —
      // so a legend built from it alone prints "11" where the author wrote "Water".
      const baked = (layer.metadata && layer.metadata['geodeploy:classes']) || null;
      if (Array.isArray(baked) && baked.length) {
        return baked.map(function (c) {
          return '<div class="legend-class">' +
            '<span style="display:inline-block;width:14px;height:10px;border-radius:2px;' +
            'border:1px solid var(--border);background:' + escHtml(String(c.color || '#999')) + '"></span>' +
            '<span class="legend-label">' + escHtml(String(c.label == null ? c.value : c.label)) +
            '</span></div>';
        }).join('');
      }
      let mapping = null;
      try { mapping = JSON.parse(parseRasterParams(srcId).colormap || 'null'); } catch (e) { mapping = null; }
      if (mapping && typeof mapping === 'object' && Object.keys(mapping).length) {
        // `legend-class` / `legend-label` are the classes the VECTOR legend already uses and
        // portal.css already styles — a classified raster's legend is the same list, so it should
        // look like one rather than inventing a second set of names with no CSS behind them.
        return Object.keys(mapping)
          .sort(function (a, b) { return Number(a) - Number(b); })
          .map(function (key) {
            const c = mapping[key] || [];
            const css = 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' +
              ((c.length > 3 ? c[3] : 255) / 255) + ')';
            return '<div class="legend-class">' +
              '<span style="display:inline-block;width:14px;height:10px;border-radius:2px;' +
              'border:1px solid var(--border);background:' + css + '"></span>' +
              '<span class="legend-label">' + escHtml(String(key)) + '</span></div>';
          }).join('');
      }
    }
    const cmap = algorithm === 'hillshade' ? 'gray'
      : algorithm === 'contours' ? 'terrain'
      : effectiveColormap(srcId);
    const p = effectiveRescale(srcId).split(',');
    const mn = (p[0] !== undefined && p[0] !== '') ? p[0] : 'min';
    const mx = (p[1] !== undefined && p[1] !== '') ? p[1] : 'max';
    const grad = LEGEND_GRADIENTS[cmap] || LEGEND_GRADIENTS.gray;
    let html = '<div class="legend-bar" style="background:' + grad + '"></div>' +
      '<div class="legend-range"><span>' + escHtml(String(mn)) + '</span><span>' + escHtml(String(mx)) + '</span></div>';
    if (algorithm === 'contours') {
      // The INTERVAL is the whole point of a contour map and it is nowhere else on the page — the
      // gradient above says what the colours mean, and this says what the lines mean.
      const c = effectiveContours(srcId);
      html += '<div class="legend-range"><span>contour lines</span><span>every ' +
        escHtml(String(c.increment)) + '</span></div>';
    }
    return html;
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
    // A POLYGON'S OUTLINE WIDTH. A `fill` strokes its own edge at a fixed hairline, so anything
    // wider is a separate `line` layer — published by portal_generator when the author set one, and
    // created here on demand when they did not, so a viewer can still thicken a border.
    let outlineField = '';
    if (t === 'fill') {
      outlineField = `<div class="layer-style-field"><label>Outline</label>` +
        `<input class="layer-outline-color" type="color" value="${toHex(polygonOutlineColor(layer.id))}" ` +
        `data-layer-id="${layer.id}">` +
        `<input class="layer-outline-width" type="number" min="0" max="20" step="0.5" title="Outline width, px" ` +
        `value="${polygonOutlineWidth(layer.id)}" data-layer-id="${layer.id}"></div>`;
    }
    return `<div class="layer-style-row" data-style-for="${layer.id}">` +
        `<div class="layer-style-field"><label>Color</label>` +
        `<input class="layer-style-color" type="color" value="${toHex(color)}" ` +
        `data-layer-id="${layer.id}" data-layer-type="${t}"></div>` +
        `${sizeField}${lineType}${outlineField}</div>`;
  }

  /** The id of a polygon's outline layer, creating it if the author published none. */
  function ensureOutlineLayer(fillId) {
    const outId = fillId + '-outline';
    if (map.getLayer(outId)) return outId;
    const layers = (map.getStyle() || {}).layers || [];
    const i = layers.findIndex(function (l) { return l.id === fillId; });
    if (i < 0 || layers[i].type !== 'fill') return null;
    let color = '#1d4ed8';
    try { color = map.getPaintProperty(fillId, 'fill-outline-color') || color; } catch (e) {}
    // Directly ABOVE the fill, not at the end of the style: appending would draw this polygon's
    // border over every layer sitting above it in the portal's own order.
    const before = layers[i + 1] && layers[i + 1].id;
    const def = { id: outId, type: 'line', source: layers[i].source,
                  paint: { 'line-color': color, 'line-width': 1 } };
    if (layers[i]['source-layer']) def['source-layer'] = layers[i]['source-layer'];
    try {
      map.addLayer(def, before);
      // Or the hairline is drawn underneath the new line — a hard inner edge on a soft fill.
      map.setPaintProperty(fillId, 'fill-antialias', false);
    } catch (e) { return null; }
    return outId;
  }

  function polygonOutlineWidth(fillId) {
    try {
      const w = map.getPaintProperty(fillId + '-outline', 'line-width');
      if (typeof w === 'number') return w;
    } catch (e) {}
    return 1;
  }

  function polygonOutlineColor(fillId) {
    for (const [id, prop] of [[fillId + '-outline', 'line-color'], [fillId, 'fill-outline-color']]) {
      try {
        const c = map.getPaintProperty(id, prop);
        if (typeof c === 'string') return c;
      } catch (e) {}
    }
    return '#1d4ed8';
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
    // Every control opens showing what is ON THE MAP (author's published styling, or the viewer's
    // own change). These used to render hard-coded defaults, so a portal published with hillshade
    // opened the popover with the box UNCHECKED and Z 1 — contradicting the map behind it.
    const cmapSel = effectiveColormap(src);
    if (bands === 1 || mode === 'single') {
      html += '<div class="layer-style-field"><label>Palette</label>' +
        '<select class="rstyle-colormap" data-src="' + src + '"><option value=""' + (cmapSel ? '' : ' selected') + '>Grayscale</option>' +
        PORTAL_COLORMAPS.map(c => '<option value="' + c + '"' + (c === cmapSel ? ' selected' : '') + '>' + c + '</option>').join('') +
        '</select></div>';
      // ONE CHOICE, not a checkbox: TiTiler takes a single `algorithm`, so hillshade and contours
      // are mutually exclusive and two ticks could ask for something that cannot be rendered.
      const alg = effectiveAlgorithm(src);
      html += '<div class="layer-style-field"><label>Terrain</label>' +
        '<select class="rstyle-algorithm" data-src="' + src + '">' +
        '<option value=""' + (alg ? '' : ' selected') + '>None</option>' +
        '<option value="hillshade"' + (alg === 'hillshade' ? ' selected' : '') + '>Hillshade</option>' +
        '<option value="contours"' + (alg === 'contours' ? ' selected' : '') + '>Contours</option>' +
        '</select></div>';
      if (alg === 'hillshade') {
        html += '<div class="layer-style-field" title="Hillshade vertical exaggeration"><label>Z</label>' +
          '<input class="rstyle-zfactor" data-src="' + src + '" type="number" min="0.1" max="10" step="0.1" value="' +
          escHtml(String(effectiveZfactor(src))) + '"></div>';
      }
      if (alg === 'contours') {
        const c = effectiveContours(src);
        html += '<div class="layer-style-field" title="Contour interval, in the raster\'s own units"><label>Interval</label>' +
          '<input class="rstyle-increment" data-src="' + src + '" type="number" min="0" step="any" value="' +
          escHtml(String(c.increment)) + '">' +
          '<input class="rstyle-thickness" data-src="' + src + '" type="number" min="1" max="10" step="1" title="Line width" value="' +
          escHtml(String(c.thickness)) + '"></div>';
      }
    }
    // Stretch does nothing under hillshade (the relief is already 0-255 and TiTiler applies rescale
    // AFTER the algorithm), so it is shown disabled rather than as an inviting empty box. Under
    // CONTOURS it is the opposite — the stretch is the range the relief behind the lines is
    // coloured over, so it stays enabled and says so.
    const hs = effectiveHillshade(src);
    const isCont = effectiveAlgorithm(src) === 'contours';
    const rs = effectiveRescale(src).split(',');
    const dis = hs ? ' disabled' : '';
    html += '<div class="layer-style-field"' +
      (hs ? ' title="Not used while Hillshade is on"'
          : isCont ? ' title="The elevation range the relief behind the contours is coloured over"' : '') +
      '><label>Stretch</label>' +
      '<input class="rstyle-min" data-src="' + src + '" type="number" placeholder="min" value="' + escHtml(String(rs[0] || '')) + '"' + dis + '>' +
      '<input class="rstyle-max" data-src="' + src + '" type="number" placeholder="max" value="' + escHtml(String(rs[1] || '')) + '"' + dis + '>' +
      '<button type="button" class="rstyle-auto" data-src="' + src + '" title="Auto stretch from raster statistics"' + dis + '>Auto</button></div>';
    return '<div class="layer-style-row" data-style-for="' + layer.id + '">' + html + '</div>';
  }

  // Rebuild a raster source's tile URL from the viewer's chosen params (session only)
  const rasterState = {};
  function applyRaster(srcId) {
    const baseFull = (STYLE.sources[srcId] && STYLE.sources[srcId].tiles && STYLE.sources[srcId].tiles[0]) || '';
    if (!baseFull) return;
    const base = baseFull.split('&')[0];  // keep up to ?url=s3://... (s3 key has no '&')
    const params = [];
    // EVERY param is rebuilt from the effective* helpers, not from `st` alone. Reading `st` directly
    // meant that changing one control dropped every baked param the viewer had not touched — pick a
    // palette and the author's stretch vanished, because it was never in `rasterState` to begin with.
    const bidx = effectiveBidx(srcId);
    bidx.forEach(b => params.push('bidx=' + b));
    const algorithm = effectiveAlgorithm(srcId);
    const rescale = effectiveRescale(srcId);
    // A CLASSIFIED raster's mapping is keyed on the RAW pixel values, so a stretch destroys it:
    // rescale maps the data into 0-255 before the lookup, and a classification of 0/1/2 arrives as
    // 0/127/255 where only one key still matches - the other classes fall through to transparent.
    // Mirrors services/titiler.py::get_tile_url.
    const bakedClasses = (!algorithm && parseRasterParams(srcId).colormap) || '';
    // Not when hillshading: TiTiler applies rescale AFTER the algorithm, and a hillshade is already
    // a finished 0-255 relief image, so a data-range stretch flattens it to one colour. Contours is
    // the same picture for a different reason — it returns finished RGB and CONSUMES the stretch as
    // the range its relief is coloured over. Mirrors services/titiler.py::get_tile_url.
    const usesClasses = !!bakedClasses && !effectiveColormap(srcId) && bidx.length !== 3;
    if (algorithm !== 'hillshade' && algorithm !== 'contours' && rescale && !usesClasses) {
      params.push('rescale=' + rescale);
    }
    if (algorithm === 'hillshade') {
      params.push('algorithm=hillshade');
      const z = effectiveZfactor(srcId);
      if (Number(z) !== 1) params.push('expression=b1*' + z);
    } else if (algorithm === 'contours') {
      params.push('algorithm=contours');
      const c = effectiveContours(srcId);
      const p = { increment: c.increment > 0 ? c.increment : 35,
                  thickness: c.thickness > 0 ? Math.trunc(c.thickness) : 1 };
      // minz/maxz come from the stretch, and MUST be integers — TiTiler types them as int and
      // rejects the whole tile request for a fractional one. Floored and ceiled so the coloured
      // band always contains the data rather than clipping its extremes flat.
      const parts = String(rescale || '').split(',');
      const lo = Number(parts[0]), hi = Number(parts[1]);
      if (isFinite(lo) && isFinite(hi) && hi > lo) { p.minz = Math.floor(lo); p.maxz = Math.ceil(hi); }
      params.push('algorithm_params=' + encodeURIComponent(JSON.stringify(p)));
    } else {
      const cmap = effectiveColormap(srcId);
      // A CLASSIFIED raster's colours live in a baked `colormap=` JSON mapping, which this used to
      // drop: touching any control rebuilt the URL without it and a land-cover layer fell back to
      // grayscale mid-session. Kept whenever the viewer has not chosen a named palette instead.
      if (cmap && bidx.length !== 3) params.push('colormap_name=' + cmap);  // ignored for RGB
      else if (usesClasses) params.push('colormap=' + encodeURIComponent(bakedClasses));
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
      // POPUP_CONFIG is keyed by vector-LAYER id and holds nothing for external sources, so an
      // external vector source sharing an id with a real layer would borrow that layer's field
      // list. No config → fall through to showing the feature's own properties.
      const fields = (f.layer.metadata && f.layer.metadata['geodeploy:external'])
        ? null : (POPUP_CONFIG[layerId] || POPUP_CONFIG[String(layerId)]);
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
    // Pass the click point: a raster that does not cover it has nothing to say, and asking anyway
    // costs a 500 per raster per click (TiTiler PointOutsideBounds).
    const rasters = visibleRasterLayers(e.lngLat);
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

  /**
   * Does this raster actually cover the clicked point?
   *
   * TiTiler's `/cog/point` raises `PointOutsideBounds` for a point off the edge of the data, and
   * that surfaces as a 500. So clicking anywhere on the map fired one request per visible raster and
   * every raster that does not cover that spot answered 500 — a wall of red in the console on every
   * single click, and real work asked of the tile server to be told what we already knew.
   *
   * The answer is not to swallow the 500 (`fetchRasterPoint` already does, which is exactly why this
   * stayed invisible) but to not ask: the extent is baked into the layer as `geodeploy:bbox`.
   *
   * No bbox → ASK. A layer whose extent was never recorded must still be identifiable; a 500 from
   * one of those is the old behaviour, not a regression.
   */
  function rasterCoversPoint(layer, lngLat) {
    const b = layer.metadata && layer.metadata['geodeploy:bbox'];
    if (!Array.isArray(b) || b.length < 4) return true;
    return lngLat.lng >= b[0] && lngLat.lng <= b[2] && lngLat.lat >= b[1] && lngLat.lat <= b[3];
  }

  function visibleRasterLayers(lngLat) {
    return (STYLE.layers || []).filter(l => {
      if (!l.metadata || l.metadata['geodeploy:type'] !== 'raster') return false;
      if (!map.getLayer(l.id)) return false;
      // Only when identifying at a point — a caller with no point wants every visible raster.
      if (lngLat && !rasterCoversPoint(l, lngLat)) return false;
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

  // ── Pointer cursor over anything clickable ────────────────────────────────────────────────────
  // A portal draws its vector layers through TWO renderers, and the cursor has to speak for both.
  // MapLibre layers answer `queryRenderedFeatures`; GeoParquet layers emit no MapLibre layer at all
  // (portal_generator returns a deck descriptor instead), so no query will ever find them and they
  // showed the pan cursor over every feature — which is most of what a modern portal displays.
  //
  // Deck features are hit-tested with deck's own picking, the only thing that knows where they are.
  // That is why the deck layers are `pickable` (see buildDeckLayer); the density OVERVIEW stays
  // unpickable on purpose — a grid cell is not a feature and clicking one does nothing.
  let pickTimer = null, deckHit = false;
  function setCursor(c) {
    // One writer. The draw-box and area-select modes own the cursor while they are active and set
    // it to crosshair themselves; this must not fight them.
    if (drawing || dzActive) return;
    map.getCanvas().style.cursor = c;
  }
  // Debounced, not throttled — this matters on the layers deck actually exists for. A pick is a
  // render pass over the pickable layers, and `mousemove` fires far faster than the screen updates;
  // running one per animation frame would mean ~60 picking passes a second over a multi-million-row
  // GeoArrow layer for the entire time the pointer is moving, which is most of the cost of the
  // interaction people notice. Nobody is asking "is there a feature here?" WHILE sweeping across the
  // map — they ask when they stop. So: pick once the pointer settles, and cancel outright if it
  // moves again. Panning and sweeping now cost nothing at all.
  const PICK_SETTLE_MS = 70;
  function deckHover(pt) {
    if (!deckOverlay) return;
    if (pickTimer) clearTimeout(pickTimer);
    pickTimer = setTimeout(function () {
      pickTimer = null;
      let hit = false;
      try {
        const info = deckOverlay.pickObject({ x: pt.x, y: pt.y, radius: 4 });
        hit = !!(info && info.object);
      } catch (e) { hit = false; }   // older bundle without pickObject, or a layer mid-update
      if (hit !== deckHit) { deckHit = hit; setCursor(hit ? 'pointer' : ''); }
    }, PICK_SETTLE_MS);
  }
  map.on('mousemove', e => {
    if (drawing) return;  // keep the crosshair while drawing a selection box
    const vectorLayerIds = (STYLE.layers || [])
      .filter(l => l.metadata && l.metadata['geodeploy:type'] === 'vector')
      // Only ids the LIVE style actually has. `queryRenderedFeatures` rejects the whole call when
      // one id is unknown — it does not skip that layer — so a single stale id from the baked style
      // would silently disable the pointer cursor for every layer in the portal.
      .filter(l => { try { return !!map.getLayer(l.id); } catch (err) { return false; } })
      .map(l => l.id);
    const f = vectorLayerIds.length
      ? map.queryRenderedFeatures(e.point, { layers: vectorLayerIds })
      : [];
    if (f.length) {
      // A MapLibre feature wins outright — and any pick already queued has to be dropped, or it
      // would land a moment later and overwrite this with its own (stale) answer.
      if (pickTimer) { clearTimeout(pickTimer); pickTimer = null; }
      deckHit = false;
      setCursor('pointer');
      return;
    }
    setCursor('');
    deckHover(e.point);
  });
  // Leaving the canvas cancels a queued pick: it would resolve against a pointer position that is
  // no longer on the map.
  map.on('mouseout', function () {
    if (pickTimer) { clearTimeout(pickTimer); pickTimer = null; }
    deckHit = false;
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
    map.addControl(new NavHistoryControl(), CTRL_POS);  // previous / next extent
    map.addControl(new DrawZoomControl(), CTRL_POS);    // drag a box to zoom (toggle back to pan)
    map.addControl(new ToolsControl(), CTRL_POS);
  }

  // ── Navigation helpers reused by the Home / Zoom-to-all controls ──────────────
  function goHome() {
    // The published default extent: the admin-pinned view, else the fit-to-data bounds.
    if (savedView && Array.isArray(savedView.center) && savedView.center.length === 2) {
      try { map.flyTo({ center: savedView.center, zoom: savedView.zoom != null ? savedView.zoom : 2,
        bearing: savedView.bearing || 0, pitch: savedView.pitch || 0, duration: 800, essential: true });
        applyProjection(savedView.projection); } catch (e) {}
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

  // ── Previous / next extent ────────────────────────────────────────────────
  // The navigation history every desktop GIS has: step back to where you just were after a zoom or
  // a "Zoom to" jump, and forward again. Applies to EVERY archetype — it is part of the map, not of
  // any one experience.
  const viewHist = [];
  let viewIdx = -1;
  let histSuppress = false;   // set while WE move the map, so our own move isn't recorded as a step
  let histBack = null, histFwd = null;

  function sameView(a, b) {
    if (!a || !b) return false;
    // Tolerances, not equality: a moveend fires for sub-pixel drift and for the tail of an eased
    // animation, and recording those would fill the history with steps that look identical.
    return Math.abs(a.zoom - b.zoom) < 0.01
      && Math.abs(a.center[0] - b.center[0]) < 1e-6 && Math.abs(a.center[1] - b.center[1]) < 1e-6
      && Math.abs((a.bearing || 0) - (b.bearing || 0)) < 0.5
      && Math.abs((a.pitch || 0) - (b.pitch || 0)) < 0.5;
  }
  function updateHistBtns() {
    if (histBack) histBack.disabled = viewIdx <= 0;
    if (histFwd) histFwd.disabled = viewIdx < 0 || viewIdx >= viewHist.length - 1;
  }
  function recordView() {
    if (histSuppress) { histSuppress = false; updateHistBtns(); return; }
    const v = currentViewObj();
    if (sameView(viewHist[viewIdx], v)) return;
    viewHist.splice(viewIdx + 1);   // a new move discards the forward branch, like a browser
    viewHist.push(v);
    if (viewHist.length > 60) viewHist.shift();   // bounded: a long panning session is not a leak
    viewIdx = viewHist.length - 1;
    updateHistBtns();
  }
  function histGo(delta) {
    const i = viewIdx + delta;
    if (i < 0 || i >= viewHist.length) return;
    viewIdx = i;
    histSuppress = true;
    const v = viewHist[i];
    try {
      map.easeTo({ center: v.center, zoom: v.zoom, bearing: v.bearing || 0, pitch: v.pitch || 0,
                   duration: 450, essential: true });
      applyProjection(v.projection);
    } catch (e) { histSuppress = false; }
    updateHistBtns();
  }
  function histBackIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>';
  }
  function histFwdIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>';
  }
  class NavHistoryControl {
    onAdd() {
      const c = document.createElement('div');
      c.className = 'maplibregl-ctrl maplibregl-ctrl-group';
      c.innerHTML =
        '<button type="button" class="gd-navback-btn" title="Previous extent" aria-label="Previous extent" disabled>' + histBackIcon() + '</button>' +
        '<button type="button" class="gd-navfwd-btn" title="Next extent" aria-label="Next extent" disabled>' + histFwdIcon() + '</button>';
      histBack = c.querySelector('.gd-navback-btn');
      histFwd = c.querySelector('.gd-navfwd-btn');
      histBack.addEventListener('click', function (e) { e.stopPropagation(); histGo(-1); });
      histFwd.addEventListener('click', function (e) { e.stopPropagation(); histGo(1); });
      // Seed with wherever the map settled after its opening fit, so the FIRST zoom already has
      // somewhere to go back to.
      map.once('idle', function () { recordView(); });
      map.on('moveend', recordView);
      this._c = c;
      return c;
    }
    onRemove() { map.off('moveend', recordView); if (this._c) this._c.remove(); }
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

  // ── Tilt ──────────────────────────────────────────────────────────────────────────────────────
  // A one-click way into (and out of) the tilted view. MapLibre can already pitch by right-drag,
  // ctrl-drag or dragging the compass, but none of those are discoverable — so a portal with 3D
  // buildings or bars in it presented no way to look at them from the side, which reads as the 3D
  // itself being broken. This is a TOGGLE rather than a slider: the two useful states are "flat" and
  // "in perspective", and anything between them is the compass's job.
  const TILT_PITCH = 60;      // enough for buildings to have visible sides, short of maxPitch (75)
  const TILT_ON_AT = 5;       // degrees below which the map counts as flat
  let tiltBtn = null;
  function tiltIcon() {
    // A plane seen in perspective, with a raised block standing on it.
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
      'stroke-linejoin="round" stroke-linecap="round">' +
      '<path d="M2 17l10 4 10-4-10-4-10 4z"/>' +
      '<path d="M9 15.8V9h6v4.4"/><path d="M9 9l3-2 3 2"/></svg>';
  }
  function syncTilt() {
    if (tiltBtn) tiltBtn.classList.toggle('active', map.getPitch() >= TILT_ON_AT);
  }
  function toggleTilt() {
    const flat = map.getPitch() < TILT_ON_AT;
    map.easeTo({ pitch: flat ? TILT_PITCH : 0, duration: 550 });
  }
  class TiltControl {
    onAdd() {
      this._c = ctrlButton('gd-tilt-btn', 'Tilt the map (3D view)', tiltIcon(), toggleTilt);
      tiltBtn = this._c.querySelector('button');
      // Reflect pitch reached ANY other way — the compass, a keyboard drag, a storymap section that
      // pins its own camera — so the button never contradicts the map.
      map.on('pitchend', syncTilt);
      syncTilt();
      return this._c;
    }
    onRemove() { map.off('pitchend', syncTilt); if (this._c) this._c.remove(); }
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
    // Put the toggle in the SAME corner as the other controls (CTRL_POS), added first so it sits at
    // the TOP of the cluster. MapLibre then gives it the exact button box/spacing of its siblings —
    // this is what keeps it pixel-aligned with the zoom/basemap/tools buttons (it used to live in the
    // list's corner alone, which drifted out of alignment).
    map.addControl(new ListToggleControl(), CTRL_POS);
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
    return { center: [c.lng, c.lat], zoom: map.getZoom(), bearing: map.getBearing(),
             pitch: map.getPitch(), projection: currentProjection() };
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
    // Mirrors build_theme_css: opacity is a colour-MIX, not CSS opacity, so the map shows through
    // the panel while the words stay solid. Kept in step with the server or the editor preview would
    // disagree with what gets published.
    const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
    const storyBg = (typeof theme.storyBg === 'string') ? theme.storyBg.trim() : '';
    if (HEX.test(storyBg)) {
      let pct = parseInt(theme.storyOpacity, 10);
      if (!isFinite(pct)) pct = 100;
      pct = Math.max(20, Math.min(100, pct));
      css += ':root{--story-bg:' + (pct >= 100 ? storyBg
        : 'color-mix(in srgb,' + storyBg + ' ' + pct + '%,transparent)') + ';}';
    }
    const storyFg = (typeof theme.storyFg === 'string') ? theme.storyFg.trim() : '';
    if (HEX.test(storyFg)) css += ':root{--story-fg:' + storyFg + ';}';
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
    // Card thumbnail: hand the editor a picture of the map as it currently renders. Waiting for
    // 'idle' matters — tiles and the deck.gl overlay load asynchronously, and capturing before then
    // yields a half-drawn map or bare basemap. The reply ALWAYS goes out, with dataUrl null on
    // failure, so the editor never waits on a message that will not arrive.
    /**
     * The space backdrop, painted into a 2D canvas.
     *
     * The starfield is a CSS background on the map CONTAINER, *behind* a transparent canvas (see
     * applySpace + portal.css `.gd-space`). `map.getCanvas().toDataURL()` reads the WebGL canvas
     * ALONE, so a globe thumbnail came out with the planet floating on transparency — and whatever
     * displayed it showed through, which is the green band behind the earth on the portal card.
     * CSS cannot be rasterised into the capture, so the backdrop is repainted here.
     *
     * Deliberately deterministic: a seeded PRNG places the stars, so re-publishing a portal does not
     * silently produce a different picture. Mirrors the colours in portal.css `.gd-space` — keep the
     * two in step, or the thumbnail stops looking like the portal it links to.
     */
    function paintSpaceBackdrop(ctx, w, h) {
      ctx.fillStyle = '#070b1a';
      ctx.fillRect(0, 0, w, h);
      // Nebula washes, in the CSS order (blue upper-left, violet lower-right, faint teal centre).
      [[0.26, 0.18, 1.15, 'rgba(72,116,215,.34)'],
       [0.80, 0.80, 0.85, 'rgba(150,84,196,.26)'],
       [0.62, 0.38, 0.60, 'rgba(38,190,190,.14)']].forEach(function (n) {
        const r = Math.max(w, h) * n[2] * 0.5;
        const g = ctx.createRadialGradient(w * n[0], h * n[1], 0, w * n[0], h * n[1], r);
        g.addColorStop(0, n[3]);
        g.addColorStop(1, 'transparent');
        ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      });
      // The Milky Way, on a diagonal — a horizontal band reads as a defect.
      ctx.save();
      ctx.translate(w / 2, h / 2); ctx.rotate(12 * Math.PI / 180); ctx.translate(-w / 2, -h / 2);
      const mw = ctx.createLinearGradient(0, h * 0.34, 0, h * 0.66);
      mw.addColorStop(0, 'transparent');
      mw.addColorStop(0.5, 'rgba(186,196,255,.26)');
      mw.addColorStop(1, 'transparent');
      ctx.fillStyle = mw; ctx.fillRect(-w, 0, w * 3, h);
      ctx.restore();
      // Stars. LCG rather than Math.random so the field is identical on every capture.
      let seed = 20260807;
      const rnd = function () { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
      const count = Math.round((w * h) / 2600);
      for (let i = 0; i < count; i++) {
        const x = rnd() * w, y = rnd() * h, t = rnd();
        const rad = t > 0.96 ? 1.6 : t > 0.82 ? 1.1 : 0.8;
        const alpha = t > 0.96 ? 1 : t > 0.82 ? 0.9 : 0.6;
        const tint = t > 0.96 ? '255,247,230' : t > 0.9 ? '210,228,255' : '255,255,255';
        if (t > 0.96) {  // brightest stars carry a halo, as in the CSS
          const halo = ctx.createRadialGradient(x, y, 0, x, y, rad * 3.5);
          halo.addColorStop(0, 'rgba(' + tint + ',.45)');
          halo.addColorStop(1, 'transparent');
          ctx.fillStyle = halo;
          ctx.beginPath(); ctx.arc(x, y, rad * 3.5, 0, Math.PI * 2); ctx.fill();
        }
        ctx.fillStyle = 'rgba(' + tint + ',' + alpha + ')';
        ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fill();
      }
    }

    /** The map canvas, flattened onto whatever is behind it. Returns the source canvas untouched
     *  when there is nothing behind it to lose (mercator paints its own opaque backdrop). */
    function snapshotCanvas() {
      const src = map.getCanvas();
      // Ask the SAME element applySpace writes to (`map.getContainer()`), not `#map-wrap` — the CSS
      // matches either, so guessing the wrong one here would silently skip the backdrop.
      const wrap = map.getContainer && map.getContainer();
      if (!wrap || !wrap.classList.contains('gd-space')) return src;
      const out = document.createElement('canvas');
      out.width = src.width; out.height = src.height;
      const ctx = out.getContext('2d');
      if (!ctx) return src;
      paintSpaceBackdrop(ctx, out.width, out.height);
      ctx.drawImage(src, 0, 0);
      return out;
    }

    function sendSnapshot(requestId) {
      // The reason travels WITH the reply. Discarding it made every failure look identical from the
      // dashboard — a tainted canvas (SecurityError, from a tile server that sent no CORS header),
      // a lost WebGL context and a plain bug all arrived as "no image", and diagnosing which meant
      // asking the operator to read their browser console.
      const reply = function (dataUrl, error) {
        post({ type: 'snapshot', requestId: requestId, dataUrl: dataUrl, error: error || null });
      };
      let done = false;
      const grab = function () {
        if (done) return;
        done = true;
        try {
          map.triggerRepaint();
          const url = snapshotCanvas().toDataURL('image/webp', 0.75);
          // toDataURL can succeed and still hand back a 1x1 placeholder if the drawing buffer was
          // already cleared — say so rather than letting it fail a size check three layers up.
          reply(url, url && url.length > 2048 ? null : 'canvas produced no image (' +
                (url ? url.length : 0) + ' chars) — preserveDrawingBuffer may be off');
        } catch (err) {
          console.warn('[geodeploy] snapshot failed', err);
          reply(null, (err && err.name ? err.name + ': ' : '') + (err && err.message || String(err)));
        }
      };
      // A map that is already idle fires no further 'idle', so race a timeout against it — and the
      // timeout doubles as the cap on how long publishing can be held up by a slow tile server.
      map.once('idle', grab);
      setTimeout(grab, 2500);
    }

    window.addEventListener('message', function (e) {
      if (e.origin !== location.origin || !e.data || e.data.gd == null) return;
      const d = e.data;
      if (d.type === 'place') showPlace(d.element);
      else if (d.type === 'cancelPlace') clearPlace();
      else if (d.type === 'theme') applyThemeLive(d.theme);   // B: live theme, no reload
      else if (d.type === 'zoomall') zoomToAllLayers();
      else if (d.type === 'snapshot') sendSnapshot(d.requestId);
      else if (d.type === 'home') goHome();
      // The editor's "Start in 3D globe" toggle. setProjection fires no moveend, so the camera is
      // reported explicitly — otherwise the editor's lastView would keep the OLD projection and the
      // pinned start view would not match what the preview is showing.
      else if (d.type === 'projection') {
        applyProjection(d.value === 'globe' ? 'globe' : 'mercator');
        post({ type: 'view', view: currentViewObj() });
      }
      // The editor's "Start tilted" toggle — the same two states as the on-map tilt control, so the
      // pinned view can carry a perspective the author chose rather than one they had to right-drag
      // into. easeTo fires moveend, so the camera reports itself back (no explicit post needed, and
      // syncTilt keeps the on-map button in step).
      else if (d.type === 'tilt') {
        try { map.easeTo({ pitch: d.value ? TILT_PITCH : 0, duration: 550 }); } catch (err) {}
      }
      else if (d.type === 'fitbbox' && Array.isArray(d.bbox) && d.bbox.length === 4) {
        try { map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]], { padding: 30, duration: 500 }); } catch (err) {}
      }
      else if (d.type === 'setview' && d.view && Array.isArray(d.view.center)) {
        try { map.jumpTo({ center: d.view.center, zoom: d.view.zoom, bearing: d.view.bearing || 0, pitch: d.view.pitch || 0 });
          applyProjection(d.view.projection); } catch (err) {}
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
      // Coordinates tab: a crosshair (position/coordinates), not the old "#" hash.
      const coordSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3.5"/><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>';
      // Centre of the N/W/E/S cross: a subtle dashed extent box (represents the bbox being defined).
      const bboxSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="6" width="14" height="12" rx="1.5" stroke-dasharray="3 2.5"/></svg>';
      c.innerHTML =
        '<button type="button" class="gd-tools-btn" title="Download data by area" aria-label="Download data by area">' + toolsIcon() + '</button>' +
        '<div class="gd-tools-menu">' +
          '<div class="gd-tools-title">Download by area</div>' +
          '<div class="gd-tools-tabs">' +
            '<button type="button" class="gd-tools-tab" data-tab="draw">' + drawSvg + '<span>Draw a box</span></button>' +
            '<button type="button" class="gd-tools-tab" data-tab="coords">' + coordSvg + '<span>Coordinates</span></button>' +
          '</div>' +
          '<p class="gd-tools-hint" data-pane="hint">Click <b>Draw a box</b> to select an area on the map, or enter coordinates.</p>' +
          '<div class="gd-tools-pane" data-pane="coords" hidden>' +
            '<div class="gd-coords-cross">' +
              '<input type="number" step="any" class="gd-c-in gd-c-n" data-k="n" placeholder="max Y / N" aria-label="North (max Y)">' +
              '<input type="number" step="any" class="gd-c-in gd-c-w" data-k="w" placeholder="min X / W" aria-label="West (min X)">' +
              '<span class="gd-c-mid">' + bboxSvg + '</span>' +
              '<input type="number" step="any" class="gd-c-in gd-c-e" data-k="e" placeholder="max X / E" aria-label="East (max X)">' +
              '<input type="number" step="any" class="gd-c-in gd-c-s" data-k="s" placeholder="min Y / S" aria-label="South (min Y)">' +
            '</div>' +
            '<button type="button" class="gd-coords-go" data-act="coords">Download this area</button>' +
          '</div>' +
        '</div>';
      const btn = c.querySelector('.gd-tools-btn');
      const menu = c.querySelector('.gd-tools-menu');
      const hint = c.querySelector('.gd-tools-hint');
      const coordsPane = c.querySelector('.gd-tools-pane[data-pane="coords"]');
      const coordsTab = c.querySelector('.gd-tools-tab[data-tab="coords"]');
      // Each open resets to the initial hint state (the cross only appears after "Coordinates").
      function resetPanes() { hint.hidden = false; coordsPane.hidden = true; coordsTab.classList.remove('is-active'); }
      btn.addEventListener('click', ev => { ev.stopPropagation(); c.classList.toggle('open'); if (c.classList.contains('open')) { collapseFloatingList(); resetPanes(); } });
      menu.addEventListener('click', ev => ev.stopPropagation());
      document.addEventListener('click', () => c.classList.remove('open'));
      // "Draw a box" starts drawing immediately (no second click); "Coordinates" reveals the cross.
      c.querySelector('.gd-tools-tab[data-tab="draw"]').addEventListener('click', () => { c.classList.remove('open'); startAreaSelect(); });
      coordsTab.addEventListener('click', () => { hint.hidden = true; coordsPane.hidden = false; coordsTab.classList.add('is-active'); });
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

  /** Do two [w, s, e, n] boxes overlap? Touching edges count — a layer ending exactly on the line
   *  the user drew is in the selection. One implementation, used by every layer kind, so vectors,
   *  rasters and GeoParquet cannot disagree about what "inside the box" means. */
  function bboxOverlaps(bb, box) {
    return !(bb[2] < box[0] || bb[0] > box[2] || bb[3] < box[1] || bb[1] > box[3]);
  }

  function openDownloadDialog(bbox, pixBox) {
    const slug = (window.GEODEPLOY && window.GEODEPLOY.slug) || (location.pathname.split('/').filter(Boolean)[1] || '');

    // Only offer layers that actually have data inside the box.
    const seen = new Set(), items = [];
    (STYLE.layers || []).forEach(l => {
      if (!l.metadata || !l.metadata['geodeploy:name']) return;
      const type = l.metadata['geodeploy:type'], id = l.metadata['geodeploy:layer_id'];
      // Namespaced like the card refs: an external XYZ source bakes geodeploy:type = 'raster', so
      // without the flag an external and a real raster sharing an id collide and `seen` silently
      // drops one of them from the download list.
      const key = (l.metadata['geodeploy:external'] ? 'ext' : type) + '-' + id;
      if (seen.has(key)) return;
      // GEOGRAPHIC overlap, not a screen-space query.
      //
      // This used to be `queryRenderedFeatures(pixBox, …)` for vectors, and it broke completely on
      // the globe: `pixBox` is an axis-aligned SCREEN rectangle between the two projected drag
      // corners, and on a globe the region between those corners is a curved quadrilateral — so the
      // query looked somewhere else entirely and reported "No layers intersect the selected area"
      // over an area full of features. It was also wrong in 2D in a quieter way: it asked what is
      // RENDERED, so a layer whose tiles had not arrived yet was silently left out of the download.
      //
      // The bbox is baked into every layer's metadata and is what the raster and GeoParquet branches
      // already use. It is coarser — a layer whose extent overlaps but has no features inside the
      // box will be offered and export nothing — but that matches how the other two behave, and the
      // SERVER does the real clip either way. Offering an empty download beats hiding a real one.
      const bb = l.metadata['geodeploy:bbox'];
      let hit;
      if (Array.isArray(bb) && bb.length === 4) {
        hit = bboxOverlaps(bb, bbox);
      } else if (type === 'vector') {
        // No bbox recorded — fall back to the rendered query rather than dropping the layer.
        try { hit = map.queryRenderedFeatures(pixBox, { layers: [l.id] }).length > 0; } catch (e) { hit = true; }
      } else {
        hit = false;
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
      const hit = !(Array.isArray(bb) && bb.length === 4) || bboxOverlaps(bb, bbox);
      if (!hit) return;
      seen.add(key);
      items.push({ id: d.layer_id, type: 'vector', backend: 'geoparquet',
                   name: d.name || ('Layer ' + d.layer_id) });
    });

    // GeoParquet is offered ONLY for GeoParquet-backed layers, where it is both the fastest export
    // (parquet-to-parquet, no conversion) and the only lossless one — GeoJSON is forced to 4326.
    // A PostGIS layer has no parquet to copy, so listing it there would be a broken choice.
    const fmtOptions = (it) => it.type === 'raster'
      ? '<option value="tif" selected>GeoTIFF</option>'
      : (it.backend === 'geoparquet' ? '<option value="geoparquet" selected>GeoParquet</option>' : '')
        + '<option value="geojson"' + (it.backend === 'geoparquet' ? '' : ' selected') + '>GeoJSON</option>'
        + '<option value="gpkg">GeoPackage</option><option value="csv">CSV</option>';
    const rowHtml = (it) =>
      '<label class="gd-download-row">' +
        '<input type="checkbox" class="gd-dl-check" data-id="' + it.id + '" data-type="' + it.type + '" checked>' +
        '<span class="gd-download-name" title="' + escHtml(it.name) + '">' + escHtml(it.name) + '</span>' +
        '<select class="gd-dl-format">' + fmtOptions(it) + '</select>' +
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

  // ── V-14 Catalog archetype: the dataset browse surface ──────────────────
  // Facet rail + result cards + a view-only map panel. Runs ONLY when the resolved archetype is
  // 'catalog'; webmap/storymap never reach here and #catalog-panel stays display:none, so their DOM
  // is untouched. Records come from style.geodeploy.catalog — the SAME `layers_info` the About page
  // renders, so the two surfaces cannot drift on what a dataset's metadata is.
  const CAT_ORIGIN_TOKEN = '__GD_ORIGIN__';
  function catAbs(url) { return String(url || '').split(CAT_ORIGIN_TOKEN).join(location.origin); }
  function catKindLabel(r) {
    if (r.kind === 'raster') return 'Raster';
    if (r.kind === 'external') return 'External';
    if (r.kind === 'elevation') return '3D terrain';
    return r.backend === 'geoparquet' ? 'GeoParquet' : 'Vector';
  }
  function catNum(n) {
    if (n == null) return '';
    const s = function (v, u) { return v.toFixed(1).replace(/\.0$/, '') + u; };
    return n >= 1e6 ? s(n / 1e6, 'M') : n >= 1e3 ? s(n / 1e3, 'k') : String(n);
  }
  function catKeywords(r) {
    return String(r.keywords || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function setupCatalog() {
    const panel = document.getElementById('catalog-panel');
    if (!panel) return;
    const cfg = (LAYOUT.regions && LAYOUT.regions.catalog) || {};
    const records = ((STYLE.geodeploy && STYLE.geodeploy.catalog) || [])
      .filter(function (r) { return r && r.name; });

    // The panel was revealed and the map side set in applyLayoutAttrs, BEFORE the map was built, so
    // the layout is already final here — nothing to reveal, nothing to resize. Only content follows.

    const perPage = Math.max(4, cfg.perPage || 12);
    const FACETS = [
      // Folder FIRST: it is the grouping the portal author already made in the editor (the V-13
      // layer tree), so it is the organisation a visitor is most likely to think in. Layers left at
      // the tree root carry no `folder` and simply do not appear here — the facet narrows, it never
      // becomes a required choice, so "everything" remains the default view.
      { key: 'folder',  title: 'Folder',   of: function (r) { return r.folder ? [String(r.folder)] : []; } },
      { key: 'kind',    title: 'Type',     of: function (r) { return [catKindLabel(r)]; } },
      { key: 'keyword', title: 'Keywords', of: catKeywords },
      { key: 'license', title: 'Licence',  of: function (r) { return r.license ? [String(r.license)] : []; } },
    ];
    const state = { q: '', page: 0, sort: 'name', folder: {}, kind: {}, keyword: {}, license: {}, open: {} };

    function selectedIn(key) {
      return Object.keys(state[key]).filter(function (k) { return state[key][k]; });
    }
    function textMatch(r) {
      if (!state.q) return true;
      const hay = [r.name, r.abstract, r.keywords, r.license, r.folder, catKindLabel(r)].join(' ').toLowerCase();
      return state.q.toLowerCase().split(/\s+/).filter(Boolean)
        .every(function (t) { return hay.indexOf(t) >= 0; });
    }
    // `skip` excludes one facet group from the test — that is what makes the COUNTS beside each facet
    // value correct: a value's count must show how many results picking it would give, so the group
    // being counted cannot filter itself.
    function facetMatch(r, skip) {
      return FACETS.every(function (f) {
        if (f.key === skip) return true;
        const sel = selectedIn(f.key);
        if (!sel.length) return true;
        const vals = f.of(r);
        return sel.some(function (s) { return vals.indexOf(s) >= 0; });
      });
    }
    function results() {
      const out = records.filter(function (r) { return textMatch(r) && facetMatch(r, null); });
      const by = state.sort;
      out.sort(function (a, b) {
        if (by === 'features') return (b.feature_count || 0) - (a.feature_count || 0);
        const c = String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' });
        return by === 'name-desc' ? -c : c;
      });
      return out;
    }

    // ── the extent highlight the map shows while a card is hovered ──
    function ensureHl() {
      if (map.getSource('gd-cat-hl')) return;
      map.addSource('gd-cat-hl', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'gd-cat-hl-fill', type: 'fill', source: 'gd-cat-hl',
        paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.10 } });
      map.addLayer({ id: 'gd-cat-hl-line', type: 'line', source: 'gd-cat-hl',
        paint: { 'line-color': '#f59e0b', 'line-width': 2, 'line-dasharray': [2, 1.5] } });
    }
    function showHl(bbox) {
      let data = { type: 'FeatureCollection', features: [] };
      if (bbox && bbox.length === 4) {
        const x0 = bbox[0], y0 = bbox[1], x1 = bbox[2], y1 = bbox[3];
        data = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: {
          type: 'Polygon', coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] } }] };
      }
      try { ensureHl(); map.getSource('gd-cat-hl').setData(data); } catch (e) {}
    }

    function refOf(r) {
      // Elevation is a terrain effect, not a toggleable layer.
      if (r.layer_id == null || r.kind === 'elevation') return null;
      // Namespaced exactly like layerRefType(), so an external source and a real layer sharing an
      // id stay distinct (they are both id 1 on a fresh install).
      return (r.kind === 'raster' ? 'raster' : r.kind === 'external' ? 'external' : 'vector')
        + ':' + r.layer_id;
    }
    // Seeded from what the admin PUBLISHED rather than forced on/off: a catalog that lit up every
    // layer at once would be unreadable, and one that hid them all would look broken.
    function publishedVisible(r) {
      const lid = String(r.layer_id), ext = r.kind === 'external';
      // deckState is keyed by numeric layer_id and only ever holds GeoParquet VECTOR layers, so it
      // must not be consulted for a raster or an external source that happens to share an id.
      if (!ext && r.kind !== 'raster' && deckState[lid] !== undefined) return !!deckState[lid].visible;
      const l = (STYLE.layers || []).find(function (x) {
        const m = x.metadata || {};
        return String(m['geodeploy:layer_id']) === lid && m['geodeploy:name']
          && !!m['geodeploy:external'] === ext;
      });
      return !!l && (l.layout || {}).visibility !== 'none';
    }
    const onMap = {};
    records.forEach(function (r) { if (refOf(r)) onMap[refOf(r)] = publishedVisible(r); });

    // ── shell ──
    panel.innerHTML =
      '<div class="cat-head">' +
        '<div class="cat-search">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.3-4.3"/></svg>' +
          '<input id="cat-q" type="search" placeholder="Search datasets, keywords, descriptions…" autocomplete="off">' +
        '</div>' +
        '<div class="cat-metabar">' +
          '<span id="cat-count" class="cat-count"></span>' +
          '<label class="cat-sortwrap">Sort' +
            '<select id="cat-sort">' +
              '<option value="name">Name A–Z</option>' +
              '<option value="name-desc">Name Z–A</option>' +
              '<option value="features">Most features</option>' +
            '</select></label>' +
        '</div>' +
        '<div id="cat-chips" class="cat-chips"></div>' +
      '</div>' +
      '<div class="cat-body">' +
        '<aside id="cat-rail" class="cat-rail"></aside>' +
        '<div class="cat-main"><div id="cat-results" class="cat-results"></div>' +
          '<div id="cat-pager" class="cat-pager"></div></div>' +
      '</div>';

    // Below the breakpoint the list and the map cannot share the viewport, so they become two views.
    const vt = document.createElement('div');
    vt.id = 'cat-viewtoggle';
    vt.innerHTML = '<button data-v="list" class="on">List</button><button data-v="map">Map</button>';
    document.body.appendChild(vt);
    vt.addEventListener('click', function (e) {
      const b = e.target.closest('button'); if (!b) return;
      document.body.dataset.catalogView = b.dataset.v;
      vt.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
      if (b.dataset.v === 'map') setTimeout(function () { try { map.resize(); } catch (e) {} }, 210);
    });

    // A catalog has no docked layer switcher — the facet rail owns that side, and listing every
    // baked-in layer would defeat the point. What a visitor DOES need is a list of what they have
    // switched on, which is a moving target: it starts empty and changes with every card they add.
    // So it is built from the live `onMap` state, overlays the map, and hides itself when empty.
    const byRef = {};
    records.forEach(function (r) { const ref = refOf(r); if (ref) byRef[ref] = r; });

    const $active = document.createElement('div');
    /**
     * The "on map" row's swatch: the layer's ACTUAL symbology, not a dot coloured by kind.
     *
     * This list is the only legend a catalog portal has — the layer switcher is a different panel —
     * and a row saying "vector" told you nothing about which of three point layers on screen was
     * which. It resolves through the same `legendSwatch` the layer list uses, so the two agree.
     *
     * RASTERS get a palette chip instead of `legendSwatch`'s raster icon. That icon is a grid, and
     * at this size in a 200px panel it is both the largest thing in the row and the least
     * informative — every raster gets an identical square. The colour ramp is what actually
     * distinguishes them, and it fits in the same space. Hillshade reads as grey, which it is.
     */
    function activeSwatch(ref, rec) {
      const parts = String(ref).split(':'), kind = parts[0], lid = parts[1];
      if (kind === 'raster') {
        const l = (STYLE.layers || []).find(function (x) {
          return x.metadata && x.metadata['geodeploy:type'] === 'raster'
            && String(x.metadata['geodeploy:layer_id']) === String(lid);
        });
        const src = l && l.source;
        // Same three-way rule as `rasterLegendHtml`: hillshade IS grey relief and contours draws
        // its own terrain ramp, so neither shows the layer's colormap.
        const alg = src ? effectiveAlgorithm(src) : '';
        const cmap = !src ? ''
          : alg === 'hillshade' ? 'gray'
          : alg === 'contours' ? 'terrain'
          : effectiveColormap(src);
        const grad = LEGEND_GRADIENTS[cmap] || LEGEND_GRADIENTS.gray;
        return '<span class="cat-active-sw cat-active-ramp" style="background:' + grad + '"></span>';
      }
      // GeoParquet layers live in deckState, not the style — check them before the MapLibre layers,
      // since a deck layer has no style layer to find.
      const d = (typeof DECK_LAYERS !== 'undefined' ? DECK_LAYERS : [])
        .find(function (x) { return String(x.layer_id) === String(lid); });
      if (d) return '<span class="cat-active-sw">' + legendSwatch(d.geometry || 'point', d.color, null, 'circle') + '</span>';
      const layer = (STYLE.layers || []).find(function (x) {
        const m = x.metadata || {};
        return layerRefType(x) === kind && String(m['geodeploy:layer_id']) === String(lid);
      });
      if (!layer) return '<span class="cat-active-sw"></span>';
      const m = layer.metadata || {};
      return '<span class="cat-active-sw">' +
        legendSwatch(m['geodeploy:geometry'] || 'point', getLayerColor(layer),
                     dashKind(layer.paint), m['geodeploy:marker'] || 'circle') + '</span>';
    }

    $active.id = 'cat-active';
    $active.style.display = 'none';
    (document.getElementById('map-wrap') || document.body).appendChild($active);

    function activeRefs() {
      return Object.keys(onMap).filter(function (ref) { return onMap[ref] && byRef[ref]; });
    }
    // Opens CLOSED. This sits ON the map, and on a catalog the map is already the smaller half of
    // the page — a list that grows with every dataset switched on eats the view it is describing.
    // The header still carries the count, so "3 layers on the map" is legible without opening it.
    // Session state, not persisted: it belongs to this visit's browsing, not to the portal.
    let activeOpen = false;
    function renderActive() {
      const refs = activeRefs();
      if (!refs.length) { $active.style.display = 'none'; $active.innerHTML = ''; return; }
      $active.style.display = '';
      // Re-rendered from scratch on every change, so the open/closed state has to be re-applied
      // here rather than left on the DOM — otherwise switching a layer on silently reopens it.
      $active.classList.toggle('collapsed', !activeOpen);
      $active.innerHTML =
        '<button type="button" class="cat-active-h" aria-expanded="' + (activeOpen ? 'true' : 'false') + '">' +
          '<svg class="cat-active-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>' +
          '<span>On map</span><span class="cat-active-n">' + refs.length + '</span>' +
        '</button>' +
        '<div class="cat-active-body">' +
        refs.map(function (ref) {
          const r = byRef[ref];
          const canZoom = !!(r.bbox && r.bbox.length === 4);
          return '<div class="cat-active-row" data-ref="' + ref + '">' +
            activeSwatch(ref, r) +
            '<span class="cat-active-t" title="' + escHtml(r.name) + '">' + escHtml(r.name) + '</span>' +
            (canZoom ? '<button class="cat-active-b" data-act="zoom" title="Zoom to this layer">&#9678;</button>' : '') +
            '<button class="cat-active-b" data-act="off" title="Remove from map">&times;</button>' +
            '</div>';
        }).join('') +
        '</div>';
    }
    $active.addEventListener('click', function (e) {
      // The header toggles the list. Checked before the row actions: it is the one control that is
      // not a [data-act], and it must work whether the list is open or closed.
      if (e.target.closest('.cat-active-h')) { activeOpen = !activeOpen; renderActive(); return; }
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const row = b.closest('.cat-active-row');
      const ref = row && row.dataset.ref;
      const r = ref && byRef[ref];
      if (!r) return;
      if (b.dataset.act === 'zoom') {
        if (r.bbox) map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]],
                                  { padding: 40, duration: 700 });
        return;
      }
      onMap[ref] = false;
      setLayerVisByRef(ref, false);
      renderActive();
      render();   // the card's button has to stop saying "On map"
    });

    const $q = panel.querySelector('#cat-q');
    const $rail = panel.querySelector('#cat-rail');
    const $res = panel.querySelector('#cat-results');
    const $pager = panel.querySelector('#cat-pager');
    const $count = panel.querySelector('#cat-count');
    const $chips = panel.querySelector('#cat-chips');

    function renderRail() {
      if (!$rail) return;
      let html = '';
      FACETS.forEach(function (f) {
        // Count against everything EXCEPT this group (see facetMatch) so the numbers stay honest.
        const pool = records.filter(function (r) { return textMatch(r) && facetMatch(r, f.key); });
        const counts = {};
        pool.forEach(function (r) {
          f.of(r).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
        });
        const vals = Object.keys(counts).sort(function (a, b) {
          return counts[b] - counts[a] || a.localeCompare(b);
        });
        // A one-value facet cannot narrow anything (ticking it changes nothing), so it is noise.
        // Keep it only when it is an ACTIVE selection the visitor needs in order to untick it.
        if (vals.length < 2 && !selectedIn(f.key).length) return;
        const collapsed = state.open[f.key] === false;
        html += '<section class="cat-facet' + (collapsed ? ' collapsed' : '') + '" data-facet="' + f.key + '">' +
          '<button class="cat-facet-h" data-toggle="' + f.key + '">' + escHtml(f.title) +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<path d="M6 9l6 6 6-6"/></svg></button><div class="cat-facet-b">';
        vals.slice(0, 12).forEach(function (v) {
          const on = !!state[f.key][v];
          html += '<label class="cat-fv' + (on ? ' on' : '') + '">' +
            '<input type="checkbox" data-g="' + f.key + '" value="' + escHtml(v) + '"' + (on ? ' checked' : '') + '>' +
            '<span class="cat-fv-t">' + escHtml(v) + '</span>' +
            '<span class="cat-fv-n">' + counts[v] + '</span></label>';
        });
        html += '</div></section>';
      });
      // Shown/hidden per RENDER, never gated on a one-time count. `scope: "public"` appends datasets
      // from the live feed AFTER the first render, so a rail decided once — from the baked records
      // only — stayed hidden even though the catalog had grown enough to need it. It also hides
      // itself when there is genuinely nothing to filter by (one dataset, or every facet single-
      // valued), which is the case the old threshold was really aiming at.
      $rail.innerHTML = html;
      $rail.style.display = html ? '' : 'none';
    }

    function renderChips() {
      let html = '';
      FACETS.forEach(function (f) {
        selectedIn(f.key).forEach(function (v) {
          html += '<button class="cat-chip" data-g="' + f.key + '" data-v="' + escHtml(v) + '">' +
            escHtml(v) + '<span aria-hidden="true">&times;</span></button>';
        });
      });
      if (html) html += '<button class="cat-chip cat-chip-clear" data-clear="1">Clear all</button>';
      $chips.innerHTML = html;
      $chips.hidden = !html;
    }

    function cardHtml(r) {
      const ref = refOf(r);
      const badges = [];
      badges.push('<span class="cat-b cat-b-' + (r.kind === 'raster' ? 'raster' : 'vector') + '">' +
        escHtml(catKindLabel(r)) + '</span>');
      if (r.private) badges.push('<span class="cat-b cat-b-lock">Restricted</span>');
      if (r.geometry_type) badges.push('<span class="cat-b">' + escHtml(String(r.geometry_type)) + '</span>');
      if (r.feature_count != null) badges.push('<span class="cat-b">' + catNum(r.feature_count) + ' features</span>');
      if (r.crs) badges.push('<span class="cat-b">' + escHtml(String(r.crs)) + '</span>');
      if (r.license) badges.push('<span class="cat-b cat-b-lic">' + escHtml(String(r.license)) + '</span>');

      let foot = '';
      if (ref) {
        foot += '<button class="cat-act' + (onMap[ref] ? ' on' : '') + '" data-act="map" data-ref="' + ref + '">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
          '<path d="M1 6l7-3 8 3 7-3v15l-7 3-8-3-7 3z"/><path d="M8 3v15M16 6v15"/></svg>' +
          '<span>' + (onMap[ref] ? 'On map' : 'Show on map') + '</span></button>';
        if (r.bbox) foot += '<button class="cat-act" data-act="zoom" data-ref="' + ref + '">Zoom to</button>';
      }
      const links = (r.share || []).filter(function (l) { return l && l.url; });
      if (links.length) foot += '<button class="cat-act" data-act="links">Access &amp; download</button>';
      if (!ref && !links.length) {
        foot += '<span class="cat-note">' + (r.private ? 'Metadata withheld' : 'No public access') + '</span>';
      }

      let linkHtml = '';
      if (links.length) {
        linkHtml = '<div class="cat-links" hidden>' + links.map(function (l) {
          const u = catAbs(l.url);
          return '<div class="cat-link' + (l.primary ? ' primary' : '') + '">' +
            '<div class="cat-link-h"><span class="cat-link-l">' + escHtml(l.label) +
              (l.primary ? ' <em>recommended</em>' : '') + '</span>' +
              '<span class="cat-link-f">' + escHtml(l.format || '') + '</span></div>' +
            '<div class="cat-link-u"><code>' + escHtml(u) + '</code>' +
              '<button class="cat-copy" data-copy="' + escHtml(u) + '">Copy</button>' +
              (l.download ? '<a class="cat-dl" href="' + escHtml(u) + '">Download</a>' : '') + '</div>' +
            (l.hint ? '<p class="cat-link-hint">' + escHtml(l.hint) + '</p>' : '') +
            ((l.tools || []).length ? '<p class="cat-link-tools">' +
              l.tools.map(function (t) { return '<span>' + escHtml(t) + '</span>'; }).join('') + '</p>' : '') +
            '</div>';
        }).join('') + '</div>';
      }

      const kw = catKeywords(r).slice(0, 6);
      return '<article class="cat-card" data-bbox="' + (r.bbox ? escHtml(JSON.stringify(r.bbox)) : '') + '">' +
        '<h3 class="cat-card-t">' + escHtml(r.name) + '</h3>' +
        '<div class="cat-badges">' + badges.join('') + '</div>' +
        (r.abstract ? '<p class="cat-abstract">' + escHtml(String(r.abstract)) + '</p>'
                    : '<p class="cat-abstract cat-abstract-none">No description provided.</p>') +
        (kw.length ? '<div class="cat-kw">' + kw.map(function (k) {
          return '<button class="cat-kwb" data-kw="' + escHtml(k) + '">' + escHtml(k) + '</button>';
        }).join('') + '</div>' : '') +
        '<div class="cat-foot">' + foot + '</div>' + linkHtml + '</article>';
    }

    function render() {
      const all = results();
      const pages = Math.max(1, Math.ceil(all.length / perPage));
      if (state.page >= pages) state.page = pages - 1;
      const page = all.slice(state.page * perPage, (state.page + 1) * perPage);

      $count.textContent = all.length === records.length
        ? all.length + (all.length === 1 ? ' dataset' : ' datasets')
        : all.length + ' of ' + records.length + ' datasets';

      $res.innerHTML = page.length ? page.map(cardHtml).join('')
        : '<div class="cat-empty"><strong>Nothing matches</strong>' +
          '<p>Try fewer filters or a broader search term.</p>' +
          '<button data-clear="1">Clear all filters</button></div>';

      // Paginated, not scrolled: the list only grows, and a page that gets taller forever pushes
      // everything below it out of reach (same reasoning as the Activity log).
      if (pages > 1) {
        let nums = '';
        for (let i = 0; i < pages; i++) {
          nums += '<button class="cat-pg' + (i === state.page ? ' on' : '') + '" data-pg="' + i + '">' + (i + 1) + '</button>';
        }
        $pager.innerHTML = '<button class="cat-pg" data-pg="' + (state.page - 1) + '"' +
            (state.page === 0 ? ' disabled' : '') + '>Prev</button>' + nums +
          '<button class="cat-pg" data-pg="' + (state.page + 1) + '"' +
            (state.page >= pages - 1 ? ' disabled' : '') + '>Next</button>';
      } else $pager.innerHTML = '';
      renderRail();
      renderChips();
    }

    // ── events (delegated: the cards are re-rendered on every change) ──
    let qt = null;
    $q.addEventListener('input', function () {
      clearTimeout(qt);
      qt = setTimeout(function () { state.q = $q.value.trim(); state.page = 0; render(); }, 140);
    });
    panel.querySelector('#cat-sort').addEventListener('change', function (e) {
      state.sort = e.target.value; render();
    });
    if ($rail) $rail.addEventListener('click', function (e) {
      const t = e.target.closest('[data-toggle]');
      if (t) {
        const k = t.dataset.toggle;
        state.open[k] = state.open[k] === false;
        renderRail();
        return;
      }
      const cb = e.target.closest('input[data-g]');
      if (cb) { state[cb.dataset.g][cb.value] = cb.checked; state.page = 0; render(); }
    });
    $chips.addEventListener('click', function (e) {
      if (e.target.closest('[data-clear]')) {
        FACETS.forEach(function (f) { state[f.key] = {}; });
        state.page = 0; render(); return;
      }
      const c = e.target.closest('.cat-chip');
      if (c) { state[c.dataset.g][c.dataset.v] = false; state.page = 0; render(); }
    });
    $pager.addEventListener('click', function (e) {
      const b = e.target.closest('[data-pg]');
      if (!b || b.disabled) return;
      state.page = Math.max(0, parseInt(b.dataset.pg, 10) || 0);
      render();
      // .cat-main is the scroller (.cat-body is overflow:hidden) — page 2 must start at the top,
      // otherwise clicking Next leaves you looking at the middle of the new page.
      const sc = panel.querySelector('.cat-main');
      if (sc) sc.scrollTo({ top: 0, behavior: 'smooth' });
    });

    $res.addEventListener('click', function (e) {
      if (e.target.closest('[data-clear]')) {
        FACETS.forEach(function (f) { state[f.key] = {}; });
        state.q = ''; $q.value = ''; state.page = 0; render(); return;
      }
      const kw = e.target.closest('[data-kw]');
      if (kw) { state.keyword[kw.dataset.kw] = true; state.page = 0; render(); return; }
      const copy = e.target.closest('[data-copy]');
      if (copy) {
        const done = function () { const o = copy.textContent; copy.textContent = 'Copied'; setTimeout(function () { copy.textContent = o; }, 1200); };
        if (navigator.clipboard) navigator.clipboard.writeText(copy.dataset.copy).then(done, function () {});
        else done();
        return;
      }
      const act = e.target.closest('[data-act]');
      if (!act) return;
      const card = act.closest('.cat-card');
      const bbox = card && card.dataset.bbox ? JSON.parse(card.dataset.bbox) : null;
      if (act.dataset.act === 'links') {
        const box = card.querySelector('.cat-links');
        if (box) { box.hidden = !box.hidden; act.classList.toggle('on', !box.hidden); }
        return;
      }
      if (act.dataset.act === 'zoom') {
        if (bbox) map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40, duration: 700 });
        if (window.matchMedia('(max-width: 1023px)').matches) {
          const mb = vt.querySelector('[data-v="map"]'); if (mb) mb.click();
        }
        return;
      }
      if (act.dataset.act === 'map') {
        const ref = act.dataset.ref;
        onMap[ref] = !onMap[ref];
        setLayerVisByRef(ref, onMap[ref]);
        act.classList.toggle('on', onMap[ref]);
        const lbl = act.querySelector('span');
        if (lbl) lbl.textContent = onMap[ref] ? 'On map' : 'Show on map';
        renderActive();
        if (onMap[ref] && bbox) {
          map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40, duration: 700 });
        }
      }
    });
    // Hover a card → flash its footprint. The spatial cue a catalog needs, without a tile request
    // per card (which is what a thumbnail grid would cost).
    $res.addEventListener('mouseover', function (e) {
      const card = e.target.closest('.cat-card');
      if (!card) return;
      showHl(card.dataset.bbox ? JSON.parse(card.dataset.bbox) : null);
    });
    $res.addEventListener('mouseleave', function () { showHl(null); });

    render();
    renderActive();
  }

  function escHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

})();
