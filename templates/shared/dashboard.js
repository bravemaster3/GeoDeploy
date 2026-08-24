/* ── V-16 Dashboard runtime ──────────────────────────────────────────────────────────────────────
   The published dashboard: the widget grid, the shared filter state that wires the widgets to each
   other, and the query layer that turns a filter into an HTTP request.

   WHY THIS IS ITS OWN FILE. portal.js is the map runtime and is already 4.7k lines; the dashboard
   is a different kind of surface (a grid of data widgets, a filter bus, a query client) that only
   needs the map as one of its widgets. Keeping it separate means it can be read, changed and
   syntax-checked on its own, and every other archetype pays only the bytes. layout.html loads this
   BEFORE portal.js, and portal.js calls `GD_DASHBOARD.setup(ctx)` from the map's load handler with
   everything this needs (`map`, `maplibregl`, the baked style, the resolved layout, an absolutifier
   and a fitBounds it does not have to own).

   THE FILTER BUS is the whole design. There is ONE filter state per dashboard, not one per widget:

     store.attr[sourceWidgetId] = { layerKey, targets:[ids], expr:{field, op, …}, label }
     store.geom                 = { sourceId, targets:[ids], geometry, bbox, label } | null
     store.selection            = { sourceId, targets:[ids], props, bbox, title } | null

   A SOURCE widget publishes into one of those channels when it is interacted with; the ids it may
   publish to are its own `actions.filters`, decided by the author in the builder (the ArcGIS
   "actions" model). A TARGET widget asks `store.filtersFor(widget)` for everything currently
   pointed at it, and re-queries. Three channels rather than one because they carry different
   things: `attr` is a field/value predicate, `geom` is a geometry with no field at all, and
   `select` is one feature's attributes for the details panel.

   Attribute filters apply only to targets reading the SAME layer — a filter on `region` means
   nothing to a widget over a different table, and applying it anyway would silently return zero.
   Geometry filters apply to every target regardless of layer, because a geometry is universal: that
   is exactly what lets a polygon drawn over parcels drive elevation statistics from a DEM.

   Multiple filters combine with AND. Clearing is per-source (each chip in the bar) or all at once
   (the reset), and clearing the geometry channel also clears any raster statistics it produced.

   VALIDATION HAPPENS AT PUBLISH, not here. `services/dashboard.resolve_dashboard` guarantees unique
   DOM-safe ids, known widget types, live action targets and in-grid layout; this file is written
   against those invariants rather than re-checking them, so there is one validator and not two
   disagreeing halves.

   REGISTERING A NEW WIDGET TYPE: add a renderer to `RENDERERS` below, keyed by the same type string
   `WIDGET_TYPES` uses in services/dashboard.py, and add the editor case in DashboardBuilder.vue.
   A renderer is `function (w, env) -> { el, refresh() }`; `env` carries the store, the api client
   and the map context. Nothing else in this file branches on widget type.
*/
window.GD_DASHBOARD = (function () {
  'use strict';

  // ── small helpers ──────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function svgEl(tag, attrs) {
    const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const k in (attrs || {})) if (attrs[k] != null) n.setAttribute(k, String(attrs[k]));
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  //: The categorical palette. Fixed rather than derived from --accent because a chart with eight
  //: shades of one hue is not readable, and it is checked against both themes (every entry keeps
  //: contrast on the light --bg and the dark one). A single-series chart uses --accent instead, so
  //: the portal's own colour is what a one-colour chart wears.
  const PALETTE = ['#2563eb', '#0ea5e9', '#059669', '#d97706', '#7c3aed',
                   '#db2777', '#dc2626', '#65a30d', '#0891b2', '#c026d3'];
  function accent() {
    try {
      return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#2563eb';
    } catch (e) { return '#2563eb'; }
  }

  // ── number + date formatting ───────────────────────────────────────────────
  function fmtNumber(value, style) {
    if (value == null || !isFinite(value)) return '—';
    const s = style || {};
    const dp = s.decimals == null ? 1 : s.decimals;
    if (s.format === 'integer') return Math.round(value).toLocaleString();
    if (s.format === 'percent') return (value * 100).toFixed(dp) + '%';
    if (s.format === 'decimal') return value.toFixed(dp);
    if (s.format === 'compact') return compact(value, dp);
    // 'auto': a whole number reads as a count and a fraction reads as a measurement, and a
    // dashboard shows both. Large values compact, because a 40px figure that overflows its card is
    // worse than a rounded one.
    if (Math.abs(value) >= 100000) return compact(value, dp);
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toFixed(Math.abs(value) < 1 ? Math.max(dp, 2) : dp);
  }
  function compact(value, dp) {
    const abs = Math.abs(value);
    const units = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']];
    for (let i = 0; i < units.length; i++) {
      if (abs >= units[i][0]) {
        return (value / units[i][0]).toFixed(dp).replace(/\.0+$/, '') + units[i][1];
      }
    }
    return Number.isInteger(value) ? String(value) : value.toFixed(dp);
  }
  //: A bucketed group key is an ISO timestamp; the axis label has to be readable at 9px, so it is
  //: cut to the precision the bucket actually carries rather than printed whole.
  function fmtBucket(key, bucket) {
    if (key == null) return '—';
    const d = new Date(key);
    if (isNaN(d.getTime())) return String(key);
    const y = d.getUTCFullYear(), m = d.getUTCMonth(), day = d.getUTCDate();
    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    if (bucket === 'year') return String(y);
    if (bucket === 'quarter') return 'Q' + (Math.floor(m / 3) + 1) + ' ' + y;
    if (bucket === 'month') return MON[m] + ' ' + String(y).slice(2);
    if (bucket === 'hour') return String(d.getUTCHours()).padStart(2, '0') + ':00 ' + day + ' ' + MON[m];
    return day + ' ' + MON[m];
  }
  function fmtCell(value) {
    if (value == null) return '';
    if (typeof value === 'number') return fmtNumber(value, { format: 'auto', decimals: 2 });
    return String(value);
  }
  function truncate(text, n) {
    const s = String(text == null ? '' : text);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  // ── query client ───────────────────────────────────────────────────────────
  // ONE in-flight request per widget. A dashboard re-queries every affected widget on every filter
  // change, and a visitor dragging a range slider produces a burst — without this the browser's
  // ~6-connection limit fills with answers nobody is waiting for any more, and the LAST response to
  // arrive wins even when it is not the newest question. Aborting is both the fix and the ordering
  // guarantee. (Same reasoning as PortalEditor's deck fetches; see views/README.)
  function createApi() {
    const inflight = {};
    function run(key, url, options) {
      if (inflight[key]) { try { inflight[key].abort(); } catch (e) {} }
      const ctrl = new AbortController();
      inflight[key] = ctrl;
      const opts = Object.assign({ signal: ctrl.signal }, options || {});
      return fetch(url, opts).then(function (r) {
        if (r.status === 204) return null;
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (body) {
            const err = new Error(body.detail || ('Request failed (' + r.status + ')'));
            err.status = r.status;
            throw err;
          });
        }
        return r.json();
      }).finally(function () { if (inflight[key] === ctrl) delete inflight[key]; });
    }
    function post(key, url, body) {
      return run(key, url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
    }
    return {
      aggregate: function (key, layerId, spec) {
        return post(key, '/api/data/vector/' + layerId + '/aggregate', spec);
      },
      table: function (key, layerId, spec) {
        return post(key, '/api/data/vector/' + layerId + '/table', spec);
      },
      pick: function (key, layerId, body) {
        return post(key, '/api/data/vector/' + layerId + '/pick', body);
      },
      zonal: function (key, layerId, body) {
        return post(key, '/api/data/raster/' + layerId + '/zonal-stats', body);
      },
      distinct: function (key, layerId, field, limit) {
        return run(key, '/api/data/vector/' + layerId + '/distinct?field='
          + encodeURIComponent(field) + '&limit=' + (limit || 200));
      },
      abortAll: function () {
        for (const k in inflight) { try { inflight[k].abort(); } catch (e) {} }
      },
    };
  }

  // ── the filter bus ─────────────────────────────────────────────────────────
  function layerKeyOf(w) {
    const ds = w && w.dataSource;
    return (ds && ds.layerId != null) ? (ds.layerType + ':' + ds.layerId) : null;
  }

  function createStore(widgets) {
    const byId = {};
    widgets.forEach(function (w) { byId[w.id] = w; });
    // `geomPinned` = the geometry came from a deliberate act (a click, a drawn polygon, a dragged
    // box) rather than from the map moving. See `publishGeom`.
    const state = { attr: {}, geom: null, geomPinned: false, selection: null };
    const listeners = {};      // widgetId → refresh fn (a TARGET re-queries)
    const selfListeners = {};  // widgetId → fn (a SOURCE redraws its own active state, no query)
    const barListeners = [];

    function targetsOf(sourceId) {
      const w = byId[sourceId];
      return (w && w.actions && w.actions.filters) || [];
    }
    function notify(ids) {
      // De-duplicated: two channels can name the same target in one interaction (a map click
      // publishes geometry AND an attribute filter), and refreshing a widget twice would fire two
      // requests where the first is immediately aborted by the second.
      const seen = {};
      (ids || []).forEach(function (id) {
        if (seen[id]) return;
        seen[id] = true;
        const fn = listeners[id];
        if (fn) { try { fn(); } catch (e) { console.warn('[geodeploy] widget refresh failed', id, e); } }
      });
      barListeners.forEach(function (fn) { try { fn(); } catch (e) {} });
    }
    //: The source's OWN redraw. Separate from `notify` because a chart clearing its own selection
    //: must un-dim its bars but must NOT re-ask the server the identical question — `filtersFor`
    //: already excludes a widget's own filter, so the answer could not have changed.
    function notifySelf(id) {
      const fn = selfListeners[id];
      if (fn) { try { fn(); } catch (e) {} }
    }
    //: The union of who WAS pointed at and who IS — clearing a filter has to refresh the widgets it
    //: used to narrow, and those are not always the ones it narrows now.
    function affected(prevTargets, nextTargets) {
      return (prevTargets || []).concat(nextTargets || []);
    }

    return {
      state: state,
      widget: function (id) { return byId[id]; },

      subscribe: function (widgetId, fn) { listeners[widgetId] = fn; },
      subscribeSelf: function (widgetId, fn) { selfListeners[widgetId] = fn; },
      onBarChange: function (fn) { barListeners.push(fn); },

      publishAttr: function (sourceId, expr, label) {
        const prev = state.attr[sourceId];
        const targets = targetsOf(sourceId);
        state.attr[sourceId] = { layerKey: layerKeyOf(byId[sourceId]), targets: targets,
                                 expr: expr, label: label };
        notify(affected(prev && prev.targets, targets));
        notifySelf(sourceId);
      },
      clearAttr: function (sourceId, quiet) {
        const prev = state.attr[sourceId];
        if (!prev) return;
        delete state.attr[sourceId];
        if (!quiet) { notify(prev.targets); notifySelf(sourceId); }
      },
      attrOf: function (sourceId) { return state.attr[sourceId] || null; },

      //: `soft` marks a geometry the visitor did not choose — the current map extent, republished
      //: on every pan. It shares the geom channel rather than getting a fourth one, because
      //: downstream it IS the same thing: one polygon every target intersects against. What it must
      //: not do is silently replace an area someone drew, so an explicit selection PINS the channel
      //: until it is cleared, and panning under a pinned selection changes nothing.
      publishGeom: function (sourceId, geometry, label, bbox, soft) {
        if (soft && state.geomPinned) return;
        const prev = state.geom;
        const targets = targetsOf(sourceId);
        state.geom = { sourceId: sourceId, targets: targets, geometry: geometry,
                       bbox: bbox || null, label: label, soft: !!soft };
        state.geomPinned = !soft;
        notify(affected(prev && prev.targets, targets));
      },
      clearGeom: function (quiet) {
        const prev = state.geom;
        state.geomPinned = false;
        if (!prev) return;
        state.geom = null;
        if (!quiet) notify(prev.targets);
      },

      publishSelection: function (sourceId, props, bbox, title) {
        const prev = state.selection;
        const targets = targetsOf(sourceId);
        state.selection = { sourceId: sourceId, targets: targets, props: props || {},
                            bbox: bbox || null, title: title || '' };
        notify(affected(prev && prev.targets, targets));
      },
      clearSelection: function (quiet) {
        const prev = state.selection;
        if (!prev) return;
        state.selection = null;
        if (!quiet) notify(prev.targets);
      },

      // Everything currently pointed at one widget, in the shape the endpoints take.
      filtersFor: function (w) {
        const key = layerKeyOf(w);
        const filters = [];
        for (const sid in state.attr) {
          const f = state.attr[sid];
          if (sid === w.id) continue;                       // a widget never filters itself
          if (f.targets.indexOf(w.id) < 0) continue;
          // Attribute filters are LAYER-SCOPED: a predicate on `region` is meaningless against a
          // different table and would silently return nothing. A join/relationship model would
          // widen this — that is the documented v2 seam, and it lands here.
          if (!key || f.layerKey !== key) continue;
          filters.push(f.expr);
        }
        const geom = (state.geom && state.geom.targets.indexOf(w.id) >= 0)
          ? state.geom.geometry : null;
        return { filters: filters, geometry: geom };
      },
      selectionFor: function (w) {
        const sel = state.selection;
        return (sel && sel.targets.indexOf(w.id) >= 0) ? sel : null;
      },

      // What the active-filter bar draws: one chip per live filter, each able to clear itself.
      chips: function () {
        const out = [];
        for (const sid in state.attr) {
          out.push({ key: 'a:' + sid, label: state.attr[sid].label || 'Filter', source: sid });
        }
        if (state.geom && !state.geom.soft) {
          out.push({ key: 'g', label: state.geom.label || 'Selected area', geom: true });
        }
        return out;
      },
      clearAll: function () {
        const touched = [];
        for (const sid in state.attr) {
          touched.push.apply(touched, state.attr[sid].targets);
          delete state.attr[sid];
        }
        if (state.geom) { touched.push.apply(touched, state.geom.targets); state.geom = null; }
        // Un-pin, or a Reset leaves the geom channel claimed by a selection that no longer exists
        // and the extent tool never publishes again.
        state.geomPinned = false;
        if (state.selection) { touched.push.apply(touched, state.selection.targets); state.selection = null; }
        // Every widget, not only the touched ones: a "reset dashboard" that leaves one card showing
        // a stale number is worse than no reset at all, and a full refresh costs one request each.
        notify(Object.keys(listeners));
        Object.keys(selfListeners).forEach(notifySelf);
        return touched;
      },
      hasAny: function () {
        return !!(state.geom || state.selection || Object.keys(state.attr).length);
      },
    };
  }

  // ── the responsive grid ────────────────────────────────────────────────────
  // The author lays out on 12 columns. A tablet gets 6 and a phone gets 1, and the mapping is
  // arithmetic (halve the span, re-flow the column) — which a media query cannot express, so it is
  // done here and written as inline grid-area. Row spans are kept, except on a phone where every
  // widget takes the full width and its authored height.
  function breakpointCols(width) {
    if (width < 720) return 1;
    if (width < 1100) return 6;
    return 12;
  }
  //: Row height per breakpoint, as a fraction of the height the author chose. A 12-column layout
  //: dropped onto 6 makes every widget about half as wide but exactly as tall, so the cards go from
  //: landscape to portrait and a dashboard that fitted one screen becomes three. Shrinking the row
  //: is the arithmetic counterpart of halving the span, and it is done here rather than in a media
  //: query for the same reason the placement is: the media query cannot see the authored geometry.
  const ROW_SCALE = { 12: 1, 6: 0.86, 1: 0.74 };
  //: The tallest a widget may be once it is full-width on a phone. An author's 8-row map is 8 rows
  //: because it sits beside eight rows of other widgets; stacked, that is most of a phone screen
  //: for one card and the visitor scrolls past everything else to find out the dashboard has more
  //: on it. The map keeps more of its height than the rest because a map that small is not a map.
  const PHONE_MAX_H = 5, PHONE_MAP_H = 7;

  function placeAll(cards, cols, baseRow) {
    document.documentElement.style.setProperty('--dash-cols', String(cols));
    document.documentElement.style.setProperty(
      '--dash-row', Math.round((baseRow || 90) * (ROW_SCALE[cols] || 1)) + 'px');
    // BOTH axes are written explicitly, never left to auto-placement. #map-wrap is a sibling of
    // #dashboard-panel in the DOM (it must never be re-parented), so it is always the LAST grid
    // item in document order however early the author placed the map — auto row placement would
    // therefore push the map to the bottom of every dashboard, whatever the layout said.
    if (cols === 1) {
      // Phone: one full-width column in reading order, with a running row cursor. `cards` arrives
      // sorted by y then x, so this is the author's own order.
      let row = 1;
      cards.forEach(function (c) {
        const cap = c.inst && c.inst.isMap ? PHONE_MAP_H : PHONE_MAX_H;
        const h = Math.max(2, Math.min(c.layout.h, cap));
        c.el.style.gridColumn = '1 / -1';
        c.el.style.gridRow = row + ' / span ' + h;
        row += h;
      });
      return;
    }
    const scale = cols / 12;
    cards.forEach(function (c) {
      // Scale the EDGES, not the origin and the width separately. Rounding each independently is
      // what makes a row of four 3-wide widgets collide at 6 columns: x=3 rounds up to 2 and w=3
      // rounds up to 2, so the second widget claims columns 3–4 and the third, at x=3, claims 4–5.
      // Two cards in one cell, overlapping, at exactly the width a tablet reports. Deriving the
      // span from two rounded edges is monotonic, so neighbours can touch but never overlap.
      const x = Math.max(0, Math.min(cols - 1, Math.round(c.layout.x * scale)));
      const right = Math.max(x + 1, Math.min(cols, Math.round((c.layout.x + c.layout.w) * scale)));
      c.el.style.gridColumn = (x + 1) + ' / span ' + (right - x);
      c.el.style.gridRow = (Math.max(0, c.layout.y) + 1) + ' / span ' + Math.max(2, c.layout.h);
    });
  }

  // ── widget chrome ──────────────────────────────────────────────────────────
  function card(w, opts) {
    const root = el('div', 'gd-w');
    root.id = 'gd-w-' + w.id;
    root.dataset.type = w.type;
    const head = el('div', 'gd-w-head');
    const title = el('span', 'gd-w-title', w.title || defaultTitle(w));
    head.appendChild(title);
    const sub = el('span', 'gd-w-sub');
    head.appendChild(sub);
    const body = el('div', 'gd-w-body' + ((opts && opts.bodyClass) ? ' ' + opts.bodyClass : ''));
    root.appendChild(head);
    root.appendChild(body);
    return { root: root, head: head, title: title, sub: sub, body: body };
  }
  function defaultTitle(w) {
    return { map: 'Map', indicator: 'Indicator', gauge: 'Gauge', chart: 'Chart',
             table: 'Records', selector: 'Filter', details: 'Details',
             rasterstats: 'Raster statistics' }[w.type] || 'Widget';
  }
  function unbound(body, what) {
    clear(body);
    const n = el('div', 'gd-w-unbound');
    n.textContent = what;
    body.appendChild(n);
  }
  function busy(root, on) { root.classList.toggle('gd-busy', !!on); }
  function showError(body, err) {
    clear(body);
    // An aborted request is not an error — it is this widget being asked a newer question.
    if (err && err.name === 'AbortError') return;
    body.appendChild(el('div', 'gd-w-error', (err && err.message) || 'Could not load this widget.'));
  }

  // The request body every vector-backed widget builds: its own binding plus whatever the bus has
  // pointed at it. One function, so "how a filter reaches a query" is a single fact.
  function specFor(w, store, extra) {
    const f = store.filtersFor(w);
    const spec = Object.assign({}, extra || {});
    if (f.filters.length) spec.filters = f.filters;
    if (f.geometry) spec.geometry = f.geometry;
    return spec;
  }

  // ── renderers ──────────────────────────────────────────────────────────────
  const RENDERERS = {};

  // INDICATOR — one number, optionally against a target, optionally clickable as a filter source.
  RENDERERS.indicator = function (w, env) {
    const c = card(w, { bodyClass: 'gd-w-center' });
    const ds = w.dataSource;
    if (!ds) {
      unbound(c.body, 'Pick a layer and an aggregation for this indicator.');
      return { el: c.root, refresh: function () {} };
    }
    const source = env.sources[layerKeyOf(w)];
    c.sub.textContent = opLabel(ds, source);

    if (ds.filterField && ds.filterValue != null) {
      c.root.dataset.clickable = '1';
      c.root.addEventListener('click', function () {
        if (env.store.attrOf(w.id)) { env.store.clearAttr(w.id); return; }
        env.store.publishAttr(w.id, { field: ds.filterField, op: 'in', values: [ds.filterValue] },
          (w.title || defaultTitle(w)) + ': ' + ds.filterField + ' = ' + ds.filterValue);
      });
      // The card's own on/off state is driven by the STORE, not by the click handler, so clearing
      // this filter from the reset bar un-highlights the card too.
      env.store.subscribeSelf(w.id, function () {
        c.root.dataset.active = env.store.attrOf(w.id) ? '1' : '0';
      });
    }

    function refresh() {
      busy(c.root, true);
      env.api.aggregate(w.id, ds.layerId, specFor(w, env.store, { op: ds.op, field: ds.field }))
        .then(function (data) {
          busy(c.root, false);
          clear(c.body);
          const value = data ? data.value : null;
          const v = el('div', 'gd-ind-value', fmtNumber(value, w.style));
          if (w.style && w.style.unit) {
            const u = el('span', 'gd-ind-unit', w.style.unit);
            v.appendChild(u);
          }
          if (w.style && w.style.color) v.style.color = w.style.color;
          c.body.appendChild(v);
          if (ds.field && ds.op !== 'count') c.body.appendChild(el('div', 'gd-ind-label', ds.field));
          const target = w.style && w.style.target;
          if (target != null && value != null && (w.style.compareMode || 'delta') !== 'none') {
            c.body.appendChild(deltaNode(value, target, w.style));
          }
        })
        .catch(function (err) { busy(c.root, false); showError(c.body, err); });
    }
    return { el: c.root, refresh: refresh };
  };

  function deltaNode(value, target, style) {
    const diff = value - target;
    const pct = target === 0 ? null : (diff / Math.abs(target)) * 100;
    const text = (style.compareMode === 'percent' && pct != null)
      ? (diff >= 0 ? '+' : '') + pct.toFixed(1) + '%'
      : (diff >= 0 ? '+' : '') + fmtNumber(diff, style);
    // "Good" is not always "up" — a dashboard of incident counts wants down to be green, and a
    // fixed colour rule would celebrate the wrong direction on half the dashboards there are.
    const dir = style.goodDirection || 'up';
    let cls = 'flat';
    if (Math.abs(diff) > 1e-9) {
      const good = dir === 'none' ? null : (dir === 'up' ? diff > 0 : diff < 0);
      cls = good === null ? 'flat' : (good ? 'up' : 'down');
    }
    return el('div', 'gd-ind-delta ' + cls, text + ' vs ' + fmtNumber(target, style));
  }

  function opLabel(ds, source) {
    const name = source ? source.name : ('layer ' + ds.layerId);
    const op = { count: 'count', sum: 'sum', avg: 'average', min: 'minimum', max: 'maximum' }[ds.op] || ds.op;
    return truncate(name, 26) + ' · ' + op;
  }

  // GAUGE — the same number as an indicator, drawn on a dial with threshold bands.
  RENDERERS.gauge = function (w, env) {
    const c = card(w, { bodyClass: 'gd-w-center' });
    const ds = w.dataSource;
    if (!ds) {
      unbound(c.body, 'Pick a layer and an aggregation for this gauge.');
      return { el: c.root, refresh: function () {} };
    }
    c.sub.textContent = opLabel(ds, env.sources[layerKeyOf(w)]);
    if (ds.filterField && ds.filterValue != null) {
      c.root.dataset.clickable = '1';
      c.root.addEventListener('click', function () {
        if (env.store.attrOf(w.id)) { env.store.clearAttr(w.id); return; }
        env.store.publishAttr(w.id, { field: ds.filterField, op: 'in', values: [ds.filterValue] },
          (w.title || defaultTitle(w)) + ': ' + ds.filterField + ' = ' + ds.filterValue);
      });
      env.store.subscribeSelf(w.id, function () {
        c.root.dataset.active = env.store.attrOf(w.id) ? '1' : '0';
      });
    }
    function refresh() {
      busy(c.root, true);
      env.api.aggregate(w.id, ds.layerId, specFor(w, env.store, { op: ds.op, field: ds.field }))
        .then(function (data) {
          busy(c.root, false);
          clear(c.body);
          c.body.appendChild(drawGauge(data ? data.value : null, w.style || {}));
        })
        .catch(function (err) { busy(c.root, false); showError(c.body, err); });
    }
    return { el: c.root, refresh: refresh };
  };

  //: A 240° arc rather than a full circle: a dial that closes has no visual start, so "half full"
  //: and "empty" look alike at small sizes. The bands are drawn as arc segments under the needle
  //: arc, so the colour says which zone the value is in without a legend.
  function drawGauge(value, style) {
    const min = style.min == null ? 0 : style.min;
    const max = style.max == null ? 100 : style.max;
    const W = 180, H = 116, cx = 90, cy = 96, r = 68;
    const START = -210, SWEEP = 240;
    const svg = svgEl('svg', { class: 'gd-gauge', viewBox: '0 0 ' + W + ' ' + H,
                               preserveAspectRatio: 'xMidYMid meet' });
    function pt(frac) {
      const a = (START + SWEEP * frac) * Math.PI / 180;
      return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    }
    function arc(f0, f1, colour, width) {
      const p0 = pt(f0), p1 = pt(f1);
      const large = (f1 - f0) * SWEEP > 180 ? 1 : 0;
      return svgEl('path', {
        d: 'M ' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) + ' A ' + r + ' ' + r + ' 0 '
           + large + ' 1 ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2),
        fill: 'none', stroke: colour, 'stroke-width': width, 'stroke-linecap': 'round',
      });
    }
    const track = arc(0, 1, 'currentColor', 12);
    track.setAttribute('class', 'gd-gauge-track');
    track.removeAttribute('stroke');
    svg.appendChild(track);

    const span = (max - min) || 1;
    const bands = (style.bands || []).slice();
    bands.forEach(function (b, i) {
      const from = Math.max(0, Math.min(1, (b.from - min) / span));
      const to = i + 1 < bands.length ? Math.max(0, Math.min(1, (bands[i + 1].from - min) / span)) : 1;
      if (to > from) svg.appendChild(arc(from, to, b.color, 12));
    });

    if (value != null && isFinite(value)) {
      const frac = Math.max(0, Math.min(1, (value - min) / span));
      svg.appendChild(arc(0, Math.max(frac, 0.001), style.color || accent(), 6));
      const p = pt(frac);
      svg.appendChild(svgEl('circle', { cx: p[0].toFixed(2), cy: p[1].toFixed(2), r: 5,
                                        fill: style.color || accent() }));
    }
    const label = svgEl('text', { x: cx, y: cy - 8, 'text-anchor': 'middle', class: 'gd-gauge-value' });
    label.textContent = fmtNumber(value, style) + (style.unit ? ' ' + style.unit : '');
    svg.appendChild(label);
    const lo = svgEl('text', { x: pt(0)[0], y: cy + 16, 'text-anchor': 'middle', class: 'gd-gauge-cap' });
    lo.textContent = fmtNumber(min, style);
    const hi = svgEl('text', { x: pt(1)[0], y: cy + 16, 'text-anchor': 'middle', class: 'gd-gauge-cap' });
    hi.textContent = fmtNumber(max, style);
    svg.appendChild(lo); svg.appendChild(hi);
    return svg;
  }

  // CHART — bar / hbar / line / area / pie / donut over a grouped aggregation. Segments are filter
  // SOURCES: clicking one publishes `groupBy IN (key)` to this widget's targets, clicking it again
  // clears. That is the interaction the whole archetype is named for, so it is not optional chrome.
  RENDERERS.chart = function (w, env) {
    const c = card(w, {});
    const ds = w.dataSource;
    if (!ds) {
      unbound(c.body, 'Pick a layer, an aggregation and a grouping field for this chart.');
      return { el: c.root, refresh: function () {} };
    }
    c.sub.textContent = opLabel(ds, env.sources[layerKeyOf(w)]);
    let groups = [];

    function onPick(key) {
      const current = env.store.attrOf(w.id);
      const same = current && current.expr.values && String(current.expr.values[0]) === String(key);
      if (same) { env.store.clearAttr(w.id); return; }
      env.store.publishAttr(w.id,
        { field: ds.groupBy, op: 'in', values: [key] },
        (w.title || defaultTitle(w)) + ': ' + truncate(key, 24));
    }
    // Re-DRAW on its own filter changing (dim the other bars), never re-query: `filtersFor`
    // excludes a widget's own filter, so the numbers behind this chart cannot have changed.
    env.store.subscribeSelf(w.id, function () { render(); });
    function selectedKey() {
      const cur = env.store.attrOf(w.id);
      return (cur && cur.expr.values && cur.expr.values[0] != null) ? String(cur.expr.values[0]) : null;
    }
    function render() {
      clear(c.body);
      if (!groups.length) {
        c.body.appendChild(el('div', 'gd-w-empty', 'No data for the current filters.'));
        return;
      }
      const kind = (w.style && w.style.chart) || 'bar';
      const node = (kind === 'pie' || kind === 'donut')
        ? drawPie(groups, ds, w.style, selectedKey(), onPick, kind === 'donut')
        : (kind === 'line' || kind === 'area')
          ? drawLine(groups, ds, w.style, kind === 'area', selectedKey(), onPick)
          : drawBars(groups, ds, w.style, selectedKey(), onPick, kind === 'hbar');
      c.body.appendChild(node);
      if (w.style && w.style.legend !== false && (kind === 'pie' || kind === 'donut')) {
        c.body.appendChild(pieLegend(groups, selectedKey(), onPick));
      }
    }
    function refresh() {
      busy(c.root, true);
      env.api.aggregate(w.id, ds.layerId, specFor(w, env.store, {
        op: ds.op, field: ds.field, groupBy: ds.groupBy, timeBucket: ds.timeBucket,
        limit: ds.limit, sort: ds.sort,
      })).then(function (data) {
        busy(c.root, false);
        groups = (data && data.groups) || [];
        render();
      }).catch(function (err) { busy(c.root, false); showError(c.body, err); });
    }
    // Re-render (not re-query) when the map is resized: an SVG chart is laid out in its own
    // coordinate space and scales with the card, so only the pie legend's wrapping actually moves.
    return { el: c.root, refresh: refresh };
  };

  function groupLabel(g, ds) { return ds.timeBucket ? fmtBucket(g.key, ds.timeBucket) : String(g.key == null ? '—' : g.key); }

  function drawBars(groups, ds, style, selected, onPick, horizontal) {
    const W = 300, H = 170, padL = horizontal ? 84 : 34, padR = 8, padT = 8, padB = horizontal ? 20 : 34;
    const svg = svgEl('svg', { class: 'gd-chart', viewBox: '0 0 ' + W + ' ' + H,
                               preserveAspectRatio: 'none' });
    const values = groups.map(function (g) { return g.value == null ? 0 : g.value; });
    const maxV = Math.max.apply(null, values.concat([0])) || 1;
    const minV = Math.min.apply(null, values.concat([0]));
    const span = (maxV - minV) || 1;
    const colour = style && style.color ? style.color : accent();
    const n = groups.length;

    if (horizontal) {
      const rowH = (H - padT - padB) / n;
      groups.forEach(function (g, i) {
        const v = values[i];
        const len = ((v - minV) / span) * (W - padL - padR);
        const y = padT + i * rowH;
        const rect = svgEl('rect', { class: 'bar', x: padL, y: y + rowH * 0.15,
                                     width: Math.max(1, len), height: rowH * 0.7, rx: 2,
                                     fill: colour });
        if (selected && String(g.key) !== selected) rect.setAttribute('class', 'bar dim');
        rect.addEventListener('click', function () { onPick(g.key); });
        const t = svgEl('title'); t.textContent = groupLabel(g, ds) + ': ' + fmtNumber(v, style);
        rect.appendChild(t);
        svg.appendChild(rect);
        const lab = svgEl('text', { x: padL - 5, y: y + rowH / 2 + 3, 'text-anchor': 'end', class: 'glabel' });
        lab.textContent = truncate(groupLabel(g, ds), 14);
        svg.appendChild(lab);
      });
      return svg;
    }

    const colW = (W - padL - padR) / n;
    const zeroY = H - padB - ((0 - minV) / span) * (H - padT - padB);
    groups.forEach(function (g, i) {
      const v = values[i];
      const y = H - padB - ((v - minV) / span) * (H - padT - padB);
      const top = Math.min(y, zeroY), height = Math.max(1, Math.abs(zeroY - y));
      const rect = svgEl('rect', { class: 'bar', x: padL + i * colW + colW * 0.15, y: top,
                                   width: Math.max(1, colW * 0.7), height: height, rx: 2, fill: colour });
      if (selected && String(g.key) !== selected) rect.setAttribute('class', 'bar dim');
      rect.addEventListener('click', function () { onPick(g.key); });
      const t = svgEl('title'); t.textContent = groupLabel(g, ds) + ': ' + fmtNumber(v, style);
      rect.appendChild(t);
      svg.appendChild(rect);
      // Every label at 9px in 300 units is unreadable past ~10 bars, so labels thin out rather
      // than overlap — the tooltip still names every bar.
      const every = Math.ceil(n / 8);
      if (i % every === 0) {
        const lab = svgEl('text', { x: padL + i * colW + colW / 2, y: H - padB + 11,
                                    'text-anchor': 'middle', class: 'glabel',
                                    transform: n > 6 ? 'rotate(-32 ' + (padL + i * colW + colW / 2)
                                      + ' ' + (H - padB + 11) + ')' : null });
        lab.textContent = truncate(groupLabel(g, ds), 12);
        svg.appendChild(lab);
      }
    });
    svg.appendChild(axis(padL, padT, W - padR, H - padB, minV, maxV, style));
    return svg;
  }

  function axis(x0, y0, x1, y1, minV, maxV, style) {
    const g = svgEl('g');
    g.appendChild(svgEl('line', { class: 'axis', x1: x0, y1: y1, x2: x1, y2: y1 }));
    const hi = svgEl('text', { x: x0 - 4, y: y0 + 8, 'text-anchor': 'end', class: 'tick' });
    hi.textContent = fmtNumber(maxV, style);
    const lo = svgEl('text', { x: x0 - 4, y: y1, 'text-anchor': 'end', class: 'tick' });
    lo.textContent = fmtNumber(minV, style);
    g.appendChild(hi); g.appendChild(lo);
    return g;
  }

  //: `selected` + `onPick` because a line chart is a filter SOURCE exactly like a bar chart —
  //: clicking March on a time series and clicking the March bar are the same question, and only one
  //: of them used to be answerable. The click target is a transparent circle far larger than the
  //: 2.6px dot: on a 30-point series the dots are ~9px apart and aiming at the dot itself is a test
  //: of the mouse, not an interaction.
  function drawLine(groups, ds, style, filled, selected, onPick) {
    const W = 300, H = 170, padL = 34, padR = 8, padT = 8, padB = 26;
    const svg = svgEl('svg', { class: 'gd-chart', viewBox: '0 0 ' + W + ' ' + H,
                               preserveAspectRatio: 'none' });
    const values = groups.map(function (g) { return g.value == null ? 0 : g.value; });
    const maxV = Math.max.apply(null, values), minV = Math.min.apply(null, values.concat([0]));
    const span = (maxV - minV) || 1;
    const n = groups.length;
    const x = function (i) { return padL + (n === 1 ? (W - padL - padR) / 2 : (i / (n - 1)) * (W - padL - padR)); };
    const y = function (v) { return H - padB - ((v - minV) / span) * (H - padT - padB); };
    const colour = style && style.color ? style.color : accent();
    let d = '';
    values.forEach(function (v, i) { d += (i ? ' L ' : 'M ') + x(i).toFixed(2) + ' ' + y(v).toFixed(2); });
    if (filled && n > 1) {
      const area = d + ' L ' + x(n - 1).toFixed(2) + ' ' + y(minV).toFixed(2)
        + ' L ' + x(0).toFixed(2) + ' ' + y(minV).toFixed(2) + ' Z';
      svg.appendChild(svgEl('path', { d: area, fill: colour, 'fill-opacity': .16, stroke: 'none' }));
    }
    svg.appendChild(svgEl('path', { d: d, fill: 'none', stroke: colour, 'stroke-width': 2,
                                    'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    values.forEach(function (v, i) {
      const key = groups[i].key == null ? '' : String(groups[i].key);
      const on = selected != null && String(selected) === key;
      const dot = svgEl('circle', { cx: x(i).toFixed(2), cy: y(v).toFixed(2),
                                    r: on ? 4.2 : 2.6, fill: colour });
      if (on) { dot.setAttribute('stroke', 'var(--bg)'); dot.setAttribute('stroke-width', '1.6'); }
      // Everything dims except the chosen point, the same feedback the bars give.
      if (selected != null && !on) dot.setAttribute('opacity', '.35');
      svg.appendChild(dot);
      const hit = svgEl('circle', { cx: x(i).toFixed(2), cy: y(v).toFixed(2), r: 9,
                                    fill: 'transparent', class: onPick ? 'gd-pick' : null });
      const t = svgEl('title'); t.textContent = groupLabel(groups[i], ds) + ': ' + fmtNumber(v, style);
      hit.appendChild(t);
      if (onPick) {
        hit.addEventListener('click', function () { onPick(groups[i].key); });
      }
      svg.appendChild(hit);
    });
    const every = Math.ceil(n / 6);
    groups.forEach(function (g, i) {
      if (i % every) return;
      const lab = svgEl('text', { x: x(i), y: H - padB + 12, 'text-anchor': 'middle', class: 'glabel' });
      lab.textContent = truncate(groupLabel(g, ds), 12);
      svg.appendChild(lab);
    });
    svg.appendChild(axis(padL, padT, W - padR, H - padB, minV, maxV, style));
    return svg;
  }

  function drawPie(groups, ds, style, selected, onPick, donut) {
    const S = 170, cx = S / 2, cy = S / 2, r = S / 2 - 8, inner = donut ? r * 0.58 : 0;
    const svg = svgEl('svg', { class: 'gd-chart', viewBox: '0 0 ' + S + ' ' + S,
                               preserveAspectRatio: 'xMidYMid meet' });
    const total = groups.reduce(function (a, g) { return a + Math.max(0, g.value || 0); }, 0);
    if (total <= 0) return svg;
    let angle = -Math.PI / 2;
    groups.forEach(function (g, i) {
      const frac = Math.max(0, g.value || 0) / total;
      const end = angle + frac * Math.PI * 2;
      const large = frac > 0.5 ? 1 : 0;
      const p = function (a, rad) { return [cx + rad * Math.cos(a), cy + rad * Math.sin(a)]; };
      const a0 = p(angle, r), a1 = p(end, r);
      let d = 'M ' + a0[0].toFixed(2) + ' ' + a0[1].toFixed(2)
        + ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 ' + a1[0].toFixed(2) + ' ' + a1[1].toFixed(2);
      if (inner) {
        const b1 = p(end, inner), b0 = p(angle, inner);
        d += ' L ' + b1[0].toFixed(2) + ' ' + b1[1].toFixed(2)
          + ' A ' + inner + ' ' + inner + ' 0 ' + large + ' 0 ' + b0[0].toFixed(2) + ' ' + b0[1].toFixed(2) + ' Z';
      } else {
        d += ' L ' + cx + ' ' + cy + ' Z';
      }
      const path = svgEl('path', { class: 'slice', d: d, fill: PALETTE[i % PALETTE.length] });
      if (selected && String(g.key) !== selected) path.setAttribute('class', 'slice dim');
      path.addEventListener('click', function () { onPick(g.key); });
      const t = svgEl('title');
      t.textContent = groupLabel(g, ds) + ': ' + fmtNumber(g.value, style)
        + ' (' + (frac * 100).toFixed(1) + '%)';
      path.appendChild(t);
      svg.appendChild(path);
      angle = end;
    });
    return svg;
  }

  function pieLegend(groups, selected, onPick) {
    const wrap = el('div', 'gd-chart-legend');
    groups.forEach(function (g, i) {
      const item = el('span');
      const swatch = el('i');
      swatch.style.background = PALETTE[i % PALETTE.length];
      if (selected && String(g.key) !== selected) item.style.opacity = '.45';
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(truncate(g.key == null ? '—' : g.key, 18)));
      item.addEventListener('click', function () { onPick(g.key); });
      wrap.appendChild(item);
    });
    return wrap;
  }

  // TABLE — scrollable attribute rows, sortable columns, click-to-zoom-and-highlight, and a filter
  // SOURCE on `keyField`. Paging is server-side (offset/limit) so a big layer never ships whole.
  RENDERERS.table = function (w, env) {
    const c = card(w, { bodyClass: 'gd-w-table' });
    const ds = w.dataSource;
    if (!ds) {
      unbound(c.body, 'Pick a layer and the columns this table should show.');
      return { el: c.root, refresh: function () {} };
    }
    const source = env.sources[layerKeyOf(w)];
    c.sub.textContent = truncate(source ? source.name : ('layer ' + ds.layerId), 26);
    const foot = el('div', 'gd-table-foot');
    const prev = el('button', null, '‹');
    const next = el('button', null, '›');
    const count = el('span');
    foot.appendChild(count);
    const nav = el('div');
    nav.appendChild(prev); nav.appendChild(next);
    foot.appendChild(nav);
    c.root.appendChild(foot);

    let sort = ds.sort || null, dir = ds.dir || 'asc', offset = 0, selectedRow = null;
    env.store.subscribeSelf(w.id, function () {
      if (!env.store.attrOf(w.id) && selectedRow) { selectedRow.classList.remove('sel'); selectedRow = null; }
    });
    prev.addEventListener('click', function () { offset = Math.max(0, offset - ds.pageSize); refresh(); });
    next.addEventListener('click', function () { offset += ds.pageSize; refresh(); });

    function onRow(row, tr) {
      if (selectedRow) selectedRow.classList.remove('sel');
      selectedRow = tr;
      tr.classList.add('sel');
      // Zoom + highlight: the bbox came with the row precisely so this needs no round trip.
      if (row.bbox) env.fitBbox(row.bbox);
      env.store.publishSelection(w.id, row.props, row.bbox,
        String(row.props[ds.keyField] == null ? '' : row.props[ds.keyField]));
      if (ds.keyField && row.props[ds.keyField] != null) {
        env.store.publishAttr(w.id, { field: ds.keyField, op: 'in', values: [row.props[ds.keyField]] },
          (w.title || defaultTitle(w)) + ': ' + truncate(row.props[ds.keyField], 24));
      }
    }
    function refresh() {
      busy(c.root, true);
      env.api.table(w.id, ds.layerId, specFor(w, env.store, {
        fields: ds.fields, sort: sort, dir: dir, limit: ds.pageSize, offset: offset,
      })).then(function (data) {
        busy(c.root, false);
        clear(c.body);
        const rows = (data && data.rows) || [];
        const fields = (data && data.fields) || ds.fields || [];
        if (!rows.length) {
          c.body.appendChild(el('div', 'gd-w-empty', 'No records match the current filters.'));
          count.textContent = '0 records';
          prev.disabled = next.disabled = true;
          return;
        }
        const table = el('table', 'gd-table');
        const thead = el('thead');
        const hr = el('tr');
        fields.forEach(function (f) {
          const th = el('th', null, f);
          if (sort === f) th.appendChild(el('span', 'sortmark', dir === 'asc' ? '▲' : '▼'));
          th.addEventListener('click', function () {
            if (sort === f) dir = dir === 'asc' ? 'desc' : 'asc';
            else { sort = f; dir = 'asc'; }
            offset = 0;
            refresh();
          });
          hr.appendChild(th);
        });
        thead.appendChild(hr);
        table.appendChild(thead);
        const tbody = el('tbody');
        rows.forEach(function (row) {
          const tr = el('tr');
          fields.forEach(function (f) {
            const td = el('td', null, fmtCell(row.props[f]));
            td.title = row.props[f] == null ? '' : String(row.props[f]);
            tr.appendChild(td);
          });
          tr.addEventListener('click', function () { onRow(row, tr); });
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        c.body.appendChild(table);
        const total = data.total || rows.length;
        count.textContent = (offset + 1) + '–' + (offset + rows.length) + ' of ' + total.toLocaleString();
        prev.disabled = offset <= 0;
        next.disabled = offset + rows.length >= total;
      }).catch(function (err) { busy(c.root, false); showError(c.body, err); });
    }
    return { el: c.root, refresh: function () { offset = 0; refresh(); } };
  };

  // SELECTOR — a filter source only. It is an INPUT: letting other widgets narrow its options would
  // make the control move under the hand using it, so it never listens (enforced in the resolver).
  RENDERERS.selector = function (w, env) {
    const c = card(w, {});
    const ds = w.dataSource;
    if (!ds || !ds.field) {
      unbound(c.body, 'Pick a layer and a field for this filter control.');
      return { el: c.root, refresh: function () {} };
    }
    c.sub.textContent = ds.field;
    const chosen = {};

    function publish() {
      const keys = Object.keys(chosen).filter(function (k) { return chosen[k]; });
      if (!keys.length) { env.store.clearAttr(w.id); return; }
      env.store.publishAttr(w.id, { field: ds.field, op: 'in', values: keys },
        ds.field + ' = ' + truncate(keys.join(', '), 40));
    }
    function buildCategory(values) {
      clear(c.body);
      if (!values.length) { c.body.appendChild(el('div', 'gd-w-empty', 'This field has no values.')); return; }
      if (!ds.multi) {
        const sel = el('select', 'gd-sel-field');
        sel.appendChild(new Option('All', ''));
        values.forEach(function (v) { sel.appendChild(new Option(v.value + ' (' + v.count + ')', v.value)); });
        sel.addEventListener('change', function () {
          for (const k in chosen) delete chosen[k];
          if (sel.value) chosen[sel.value] = true;
          publish();
        });
        c.body.appendChild(sel);
        return;
      }
      const chips = el('div', 'gd-sel-chips');
      values.forEach(function (v) {
        const b = el('button', 'gd-sel-chip', v.value + ' · ' + v.count);
        b.type = 'button';
        b.title = v.value;
        b.setAttribute('aria-pressed', 'false');
        b.addEventListener('click', function () {
          chosen[v.value] = !chosen[v.value];
          b.setAttribute('aria-pressed', chosen[v.value] ? 'true' : 'false');
          publish();
        });
        chips.appendChild(b);
      });
      c.body.appendChild(chips);
    }
    function buildRange(info, isDate) {
      clear(c.body);
      if (info.min == null || info.max == null) {
        c.body.appendChild(el('div', 'gd-w-empty', 'This field has no range.'));
        return;
      }
      if (isDate) {
        const from = el('input'); from.type = 'date';
        const to = el('input'); to.type = 'date';
        from.className = to.className = 'gd-sel-field';
        from.value = String(info.min).slice(0, 10);
        to.value = String(info.max).slice(0, 10);
        function pushDates() {
          env.store.publishAttr(w.id,
            { field: ds.field, op: 'daterange', min: from.value || null, max: to.value || null },
            ds.field + ' ' + (from.value || '…') + ' → ' + (to.value || '…'));
        }
        [from, to].forEach(function (input, i) {
          const row = el('div', 'gd-sel-row');
          row.appendChild(el('label', null, i ? 'To' : 'From'));
          row.appendChild(input);
          input.addEventListener('change', pushDates);
          c.body.appendChild(row);
        });
        return;
      }
      // Two sliders rather than a custom two-thumb control: a native <input type=range> is
      // keyboard-accessible, themeable with accent-color and needs no drag maths. They are clamped
      // against each other so the low handle can never cross the high one.
      const lo = el('input'); lo.type = 'range';
      const hi = el('input'); hi.type = 'range';
      const step = (info.max - info.min) / 100 || 1;
      [lo, hi].forEach(function (s) { s.min = info.min; s.max = info.max; s.step = step; });
      lo.value = info.min; hi.value = info.max;
      const loOut = el('span', 'gd-sel-num'), hiOut = el('span', 'gd-sel-num');
      function push() {
        if (Number(lo.value) > Number(hi.value)) lo.value = hi.value;
        loOut.textContent = fmtNumber(Number(lo.value), { format: 'auto', decimals: 1 });
        hiOut.textContent = fmtNumber(Number(hi.value), { format: 'auto', decimals: 1 });
        const full = Number(lo.value) <= info.min && Number(hi.value) >= info.max;
        if (full) { env.store.clearAttr(w.id); return; }
        env.store.publishAttr(w.id,
          { field: ds.field, op: 'between', min: Number(lo.value), max: Number(hi.value) },
          ds.field + ' ' + loOut.textContent + ' – ' + hiOut.textContent);
      }
      [[lo, loOut, 'Min'], [hi, hiOut, 'Max']].forEach(function (row) {
        const line = el('div', 'gd-sel-row');
        line.appendChild(el('label', null, row[2]));
        const box = el('div', 'gd-sel-range');
        box.appendChild(row[0]);
        box.appendChild(row[1]);
        line.appendChild(box);
        c.body.appendChild(line);
        // `input` for the readout, `change` for the query: re-querying on every pixel of a drag is
        // a request per frame, and the abort in the api client would spend them all cancelling.
        row[0].addEventListener('input', function () {
          if (Number(lo.value) > Number(hi.value)) lo.value = hi.value;
          loOut.textContent = fmtNumber(Number(lo.value), { format: 'auto', decimals: 1 });
          hiOut.textContent = fmtNumber(Number(hi.value), { format: 'auto', decimals: 1 });
        });
        row[0].addEventListener('change', push);
      });
      loOut.textContent = fmtNumber(info.min, { format: 'auto', decimals: 1 });
      hiOut.textContent = fmtNumber(info.max, { format: 'auto', decimals: 1 });
    }

    function load() {
      busy(c.root, true);
      env.api.distinct(w.id, ds.layerId, ds.field, 60).then(function (info) {
        busy(c.root, false);
        if (!info) return;
        // The FIELD's own type decides the control, not the author's guess: a "range" selector on a
        // text column would render two sliders over nothing.
        if (info.kind === 'numeric' && ds.kind !== 'category') buildRange(info, false);
        else if (info.kind === 'date') buildRange(info, true);
        else buildCategory(info.values || []);
      }).catch(function (err) { busy(c.root, false); showError(c.body, err); });
    }
    load();
    // A selector never listens, so its "refresh" only re-reads its option list — which the refresh
    // interval may want, for a layer that gains categories over time.
    return { el: c.root, refresh: load };
  };

  // DETAILS — the full attributes of whatever is selected across the dashboard.
  RENDERERS.details = function (w, env) {
    const c = card(w, {});
    function refresh() {
      const sel = env.store.selectionFor(w);
      clear(c.body);
      c.sub.textContent = sel && sel.title ? truncate(sel.title, 22) : '';
      if (!sel) {
        c.body.appendChild(el('div', 'gd-w-empty',
          'Select a feature on the map, or a row in a table, to see its attributes here.'));
        return;
      }
      const list = el('div', 'gd-det');
      const keys = Object.keys(sel.props || {});
      if (!keys.length) {
        c.body.appendChild(el('div', 'gd-w-empty', 'That feature carries no attributes.'));
        return;
      }
      keys.forEach(function (k) {
        const row = el('div');
        row.appendChild(el('b', null, k));
        row.appendChild(el('s', null, fmtCell(sel.props[k])));
        list.appendChild(row);
      });
      c.body.appendChild(list);
    }
    return { el: c.root, refresh: refresh };
  };

  // RASTER STATS — zonal statistics over the ACTIVE GEOMETRY SELECTION. Target-only: a spatial
  // selection drives it, and it has no attribute table of its own to filter anything else with.
  RENDERERS.rasterstats = function (w, env) {
    const c = card(w, {});
    const ds = w.dataSource;
    if (!ds) {
      unbound(c.body, 'Pick a raster layer and the statistics this panel should show.');
      return { el: c.root, refresh: function () {} };
    }
    const source = env.sources[layerKeyOf(w)];
    c.sub.textContent = truncate(source ? source.name : ('raster ' + ds.layerId), 24);

    function refresh() {
      const f = env.store.filtersFor(w);
      clear(c.body);
      if (!f.geometry) {
        // The prompt, not an error: nothing has been selected yet, and the widget's whole job is to
        // answer a selection. It names the three ways to make one, because a blank panel beside a
        // map is the single most common "is this broken?" moment on a dashboard.
        c.body.appendChild(el('div', 'gd-w-empty',
          'Click a feature, draw a polygon or drag a box on the map to see statistics for that area.'));
        return;
      }
      busy(c.root, true);
      c.body.appendChild(el('div', 'gd-w-empty', 'Reading the raster…'));
      env.api.zonal(w.id, ds.layerId, { geometry: f.geometry, stats: ds.stats, band: ds.band })
        .then(function (data) {
          busy(c.root, false);
          clear(c.body);
          const stats = (data && data.stats) || {};
          const grid = el('div', 'gd-rs-grid');
          let any = false;
          (ds.stats || []).forEach(function (key) {
            if (key === 'histogram') return;
            const cell = el('div', 'gd-rs-cell');
            cell.appendChild(el('div', 'gd-rs-k', STAT_LABELS[key] || key));
            const v = el('div', 'gd-rs-v', fmtNumber(stats[key], w.style));
            v.title = stats[key] == null ? 'no data in this area' : String(stats[key]);
            cell.appendChild(v);
            grid.appendChild(cell);
            any = true;
          });
          if (any) c.body.appendChild(grid);
          if ((ds.stats || []).indexOf('histogram') >= 0) {
            if (stats.histogram && stats.histogram.counts && stats.histogram.counts.length) {
              c.body.appendChild(drawHistogram(stats.histogram, w.style));
            } else {
              c.body.appendChild(el('div', 'gd-w-empty', 'No histogram for this area.'));
            }
          }
          if (!any && !(ds.stats || []).length) {
            c.body.appendChild(el('div', 'gd-w-empty', 'No statistics selected for this raster.'));
          }
        })
        .catch(function (err) { busy(c.root, false); showError(c.body, err); });
    }
    return { el: c.root, refresh: refresh };
  };

  const STAT_LABELS = { min: 'Min', max: 'Max', mean: 'Mean', sum: 'Sum', std: 'Std dev',
                        median: 'Median', count: 'Pixels' };

  function drawHistogram(hist, style) {
    const W = 260, H = 54;
    const svg = svgEl('svg', { class: 'gd-rs-hist', viewBox: '0 0 ' + W + ' ' + H,
                               preserveAspectRatio: 'none' });
    const counts = hist.counts || [];
    const max = Math.max.apply(null, counts.concat([0])) || 1;
    const bw = W / counts.length;
    counts.forEach(function (n, i) {
      const h = (n / max) * (H - 2);
      const bar = svgEl('rect', { x: (i * bw).toFixed(2), y: (H - h).toFixed(2),
                                  width: Math.max(0.6, bw - 0.6).toFixed(2), height: h.toFixed(2) });
      if (style && style.color) bar.setAttribute('fill', style.color);
      const edges = hist.edges || [];
      const t = svgEl('title');
      t.textContent = (edges[i] != null ? fmtNumber(edges[i], style) : '?') + ' – '
        + (edges[i + 1] != null ? fmtNumber(edges[i + 1], style) : '?') + ': ' + n.toLocaleString();
      bar.appendChild(t);
      svg.appendChild(bar);
    });
    return svg;
  }

  // MAP — the anchor widget, and the only one whose element already exists: #map-wrap is placed
  // into the grid by grid-area rather than being re-created here, because a re-parented MapLibre
  // container loses its measured size (the same rule the catalog archetype follows).
  RENDERERS.map = function (w, env) {
    const ds = w.dataSource || { tools: ['click', 'polygon', 'bbox'] };
    // There is exactly ONE MapLibre map on the page and exactly one #map-wrap to put it in, so only
    // the first map widget can be it. A second one gets a card that says so rather than silently
    // stealing the element from the first (which would move the container, and a moved MapLibre
    // container loses its measured size) or mounting a second set of selection handlers on the same
    // map, where two toolbars would fight over the active mode.
    if (env.mapTaken) {
      const c = card(w, { bodyClass: 'gd-w-center' });
      unbound(c.body, 'A dashboard has one map. Remove this widget, or replace the other map.');
      return { el: c.root, refresh: function () {} };
    }
    env.mapTaken = true;
    const wrap = document.getElementById('map-wrap');
    if (wrap) {
      wrap.dataset.dashParked = '0';
      mountSelectionTools(w, ds, env, wrap);
    }
    function refresh() {
      // The map as a TARGET: an attribute filter pointed at it is applied to the MapLibre layers
      // drawing its selection layer, so filtering the dashboard visibly narrows the map too.
      applyMapFilter(w, env);
    }
    return { el: wrap, refresh: refresh, isMap: true };
  };

  //: MapLibre's own `setFilter`, applied to every style layer that carries this layer's id in its
  //: metadata. GeoParquet layers render through deck.gl, which has no style layer to filter — those
  //: are left alone rather than half-filtered, and the widgets over them still narrow correctly
  //: because their filtering happens server-side.
  function applyMapFilter(w, env) {
    const ds = w.dataSource;
    if (!ds || ds.layerId == null) return;
    const map = env.map;
    const f = env.store.filtersFor(w);
    let expr = null;
    const clauses = f.filters.map(toMapLibreExpr).filter(Boolean);
    if (clauses.length) expr = ['all'].concat(clauses);
    (env.style.layers || []).forEach(function (lyr) {
      const meta = lyr.metadata || {};
      if (String(meta['geodeploy:layer_id']) !== String(ds.layerId)) return;
      if (meta['geodeploy:external']) return;
      // AND with the layer's OWN filter, never replace it. A raw-paint passthrough
      // (`style.maplibre.layers`, how a GeoLibre import carries data-driven symbology) emits
      // several style layers for one data layer, each filtered to the part it draws. Overwriting
      // that with the dashboard's predicate makes every part draw every feature — the polygon
      // layer would render the points too — and clearing the dashboard's filter would set it to
      // null and leave it that way. The baseline is captured once, from the baked style, so
      // repeated applies cannot compound it.
      if (lyr.__gdBaseFilter === undefined) {
        lyr.__gdBaseFilter = (lyr.filter !== undefined && lyr.filter !== null) ? lyr.filter : null;
      }
      const base = lyr.__gdBaseFilter;
      const next = (base && expr) ? ['all', base].concat(expr.slice(1)) : (expr || base);
      try { map.setFilter(lyr.id, next); } catch (e) { /* a layer the live style no longer has */ }
    });
  }
  function toMapLibreExpr(f) {
    if (f.op === 'in') {
      // `to-string` on the feature side: the selector's values are strings (that is what a control
      // holds) and MapLibre's `in` is type-strict, so an integer column would never match.
      return ['in', ['to-string', ['get', f.field]], ['literal', f.values.map(String)]];
    }
    if (f.op === 'between') {
      const parts = [];
      if (f.min != null) parts.push(['>=', ['to-number', ['get', f.field]], f.min]);
      if (f.max != null) parts.push(['<=', ['to-number', ['get', f.field]], f.max]);
      return parts.length ? ['all'].concat(parts) : null;
    }
    if (f.op === 'notnull') return ['!=', ['get', f.field], null];
    return null;   // a date range has no honest client-side equivalent; the server still applies it
  }

  // ── the three selection modes ──────────────────────────────────────────────
  // Click, polygon-draw and bbox-draw all end in ONE call to `store.publishGeom`. A bbox IS a
  // rectangular polygon, and giving it its own downstream path would be two implementations of the
  // same geometry filter — which is exactly how the raster and vector sides would drift apart.
  const SEL_SRC = 'gd-dash-sel';
  const DRAW_SRC = 'gd-dash-draw';

  function ensureSelectionLayers(map) {
    if (!map.getSource(SEL_SRC)) {
      map.addSource(SEL_SRC, { type: 'geojson', data: emptyFC() });
      map.addLayer({ id: SEL_SRC + '-fill', type: 'fill', source: SEL_SRC,
                     paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.14 } });
      map.addLayer({ id: SEL_SRC + '-line', type: 'line', source: SEL_SRC,
                     paint: { 'line-color': '#f59e0b', 'line-width': 2 } });
      map.addLayer({ id: SEL_SRC + '-pt', type: 'circle', source: SEL_SRC,
                     filter: ['==', ['geometry-type'], 'Point'],
                     paint: { 'circle-radius': 6, 'circle-color': '#f59e0b',
                              'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } });
    }
    if (!map.getSource(DRAW_SRC)) {
      map.addSource(DRAW_SRC, { type: 'geojson', data: emptyFC() });
      map.addLayer({ id: DRAW_SRC + '-fill', type: 'fill', source: DRAW_SRC,
                     paint: { 'fill-color': '#2563eb', 'fill-opacity': 0.10 } });
      map.addLayer({ id: DRAW_SRC + '-line', type: 'line', source: DRAW_SRC,
                     paint: { 'line-color': '#2563eb', 'line-width': 2, 'line-dasharray': [2, 1.5] } });
    }
  }
  function emptyFC() { return { type: 'FeatureCollection', features: [] }; }
  function setData(map, id, geometry) {
    const src = map.getSource(id);
    if (!src) return;
    src.setData(geometry
      ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: geometry }] }
      : emptyFC());
  }
  function bboxOf(geometry) {
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    (function walk(coords) {
      if (typeof coords[0] === 'number') {
        minx = Math.min(minx, coords[0]); maxx = Math.max(maxx, coords[0]);
        miny = Math.min(miny, coords[1]); maxy = Math.max(maxy, coords[1]);
        return;
      }
      coords.forEach(walk);
    })(geometry.coordinates || []);
    return isFinite(minx) ? [minx, miny, maxx, maxy] : null;
  }
  //: A screen radius expressed in DEGREES at one point on the map. Measured by unprojecting two
  //: pixels that far apart at the click itself, so it follows the zoom and the latitude together —
  //: the same number of pixels is a very different ground distance at 64N and at the equator.
  function degreesFor(map, point, ds) {
    const px = (ds && ds.tolPx != null) ? ds.tolPx : 6;
    let deg = 0;
    if (px > 0) {
      try {
        const a = map.unproject([point.x, point.y]);
        const b = map.unproject([point.x + px, point.y]);
        deg = Math.abs(b.lng - a.lng);
      } catch (e) { deg = 0; }
    }
    // An authored `tol` (degrees) is a FLOOR, not a replacement: it is how an author asks for a
    // fixed GROUND distance, and configs written against the original schema still carry one.
    return Math.max(deg, (ds && ds.tol) || 0);
  }

  //: The vector layers a click hit-tests, in order: the widget's own selection layer first, then
  //: every other vector layer in the published style, TOP-DOWN. MapLibre's `style.layers` is in
  //: draw order (last is drawn on top), so it is walked backwards — the layer a visitor sees on
  //: top is the one they meant to click.
  function pickCandidates(w, env) {
    const ds = w.dataSource || {};
    const out = [];
    const seen = {};
    if (ds.layerId != null) { out.push(ds.layerId); seen[String(ds.layerId)] = true; }
    const layers = (env.style && env.style.layers) || [];
    for (let i = layers.length - 1; i >= 0; i--) {
      const meta = layers[i].metadata || {};
      const id = meta['geodeploy:layer_id'];
      if (id == null) continue;
      if (meta['geodeploy:external']) continue;              // not ours to query
      // A raster's layer_id lives in a DIFFERENT id space from a vector's, so `raster-7` and a
      // vector layer 7 both report 7 — asking /data/vector/7/pick for the raster would either 404
      // or, worse, answer about an unrelated table. Both the metadata tag portal_generator writes
      // and the MapLibre layer type are checked, because an author's own style layer may carry only
      // one of them.
      if (meta['geodeploy:type'] === 'raster' || meta['geodeploy:geometry'] === 'raster') continue;
      const t = layers[i].type;
      if (t === 'raster' || t === 'hillshade' || t === 'background') continue;
      if (seen[String(id)]) continue;
      seen[String(id)] = true;
      out.push(id);
    }
    // Bounded: a portal with thirty layers must not answer one click with thirty round trips.
    return out.slice(0, 8);
  }

  function ringPolygon(points) {
    const ring = points.slice();
    const first = ring[0], last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) ring.push([first[0], first[1]]);
    return { type: 'Polygon', coordinates: [ring] };
  }

  const TOOL_ICONS = {
    click: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l7.5 18 2.5-7.5L20.5 11z"/></svg>',
    polygon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 3 21 9 18 20 6 20 3 9"/></svg>',
    bbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="1"/></svg>',
    extent: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V5a1 1 0 0 1 1-1h4"/><path d="M15 4h4a1 1 0 0 1 1 1v4"/><path d="M20 15v4a1 1 0 0 1-1 1h-4"/><path d="M9 20H5a1 1 0 0 1-1-1v-4"/></svg>',
    clear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><polyline points="3 4 3 10 9 10"/></svg>',
  };
  const TOOL_TITLES = { click: 'Select a feature', polygon: 'Draw a polygon',
                        bbox: 'Drag a box', extent: 'Filter to what is on screen',
                        clear: 'Clear the selection' };

  function mountSelectionTools(w, ds, env, wrap) {
    const map = env.map;
    const tools = ds.tools || ['click', 'polygon', 'bbox'];
    ensureSelectionLayers(map);

    const bar = el('div', 'gd-dash-tools');
    const buttons = {};
    let mode = null;
    // Declared up here, not beside the polygon handlers: `setMode` calls `resetDraw`, and a `let`
    // read before its declaration is a TDZ error, not an undefined.
    let pending = [];

    function setMode(next) {
      mode = (mode === next) ? null : next;
      for (const k in buttons) {
        if (k === 'extent') continue;   // a switch, not a mode — see below
        buttons[k].setAttribute('aria-pressed', k === mode ? 'true' : 'false');
      }
      document.body.dataset.dashDraw = mode && mode !== 'click' ? '1' : '0';
      // Box-drag and map-pan are the same gesture, so one has to yield while the other is armed.
      try {
        if (mode === 'bbox') map.dragPan.disable(); else map.dragPan.enable();
      } catch (e) {}
      resetDraw();
    }
    function resetDraw() {
      pending = [];
      setData(map, DRAW_SRC, null);
    }

    ['click', 'polygon', 'bbox'].forEach(function (t) {
      if (tools.indexOf(t) < 0) return;
      const b = el('button');
      b.type = 'button';
      b.title = TOOL_TITLES[t];
      b.setAttribute('aria-label', TOOL_TITLES[t]);
      b.setAttribute('aria-pressed', 'false');
      b.innerHTML = TOOL_ICONS[t];
      b.addEventListener('click', function () { setMode(t); });
      buttons[t] = b;
      bar.appendChild(b);
    });
    // ── extent: the map itself is a filter ────────────────────────────────────
    // NOT one of the modes above, and that is the point — it is a switch, not a gesture. It stays on
    // while the visitor clicks features and draws polygons, and it answers the plainest expectation
    // a dashboard sets: that panning the map changes the numbers beside it. Debounced on `moveend`
    // rather than `move`, because a single drag emits a frame's worth of events and every one of
    // them would be a round trip per listening widget.
    if (tools.indexOf('extent') >= 0) {
      const eb = el('button');
      eb.type = 'button';
      eb.title = TOOL_TITLES.extent;
      eb.setAttribute('aria-label', TOOL_TITLES.extent);
      eb.setAttribute('aria-pressed', 'false');
      eb.innerHTML = TOOL_ICONS.extent;
      let extentOn = false, extentTimer = null;
      const pushExtent = function () {
        if (!extentOn) return;
        let b;
        try { b = map.getBounds(); } catch (e) { return; }
        const w0 = b.getWest(), s0 = b.getSouth(), e0 = b.getEast(), n0 = b.getNorth();
        // A world-wrapped view can report a west past its east; clamping keeps the polygon a
        // polygon rather than a bow tie the server has to reject.
        const west = Math.max(-180, Math.min(w0, e0)), east = Math.min(180, Math.max(w0, e0));
        const south = Math.max(-85, Math.min(s0, n0)), north = Math.min(85, Math.max(s0, n0));
        const rect = { type: 'Polygon', coordinates: [[[west, south], [east, south],
                                                       [east, north], [west, north], [west, south]]] };
        env.store.publishGeom(w.id, rect, 'Map extent', [west, south, east, north], true);
      };
      map.on('moveend', function () {
        if (!extentOn) return;
        if (extentTimer) clearTimeout(extentTimer);
        extentTimer = setTimeout(pushExtent, 250);
      });
      // A cleared selection un-pins the channel, so the extent takes over again immediately rather
      // than waiting for the visitor to nudge the map.
      env.onClearSelection(function () { setTimeout(pushExtent, 0); });
      eb.addEventListener('click', function () {
        extentOn = !extentOn;
        eb.setAttribute('aria-pressed', extentOn ? 'true' : 'false');
        if (extentOn) pushExtent();
        else if (env.store.state.geom && env.store.state.geom.soft) env.store.clearGeom();
      });
      buttons.extent = eb;
      bar.appendChild(eb);
    }

    // SELECT IS THE RESTING STATE. Every mode started off, so a visitor's first instinct — click the
    // feature — did nothing at all until they found the arrow button, and "the details panel never
    // fills in" is the only conclusion available to them. Polygon and box are gestures you go
    // looking for; picking a feature is not, and it is also the one mode that cannot be triggered
    // by accident (a drag pans, and MapLibre does not emit `click` after a drag).
    if (tools.indexOf('click') >= 0) setMode('click');

    const clearBtn = el('button');
    clearBtn.type = 'button';
    clearBtn.title = TOOL_TITLES.clear;
    clearBtn.setAttribute('aria-label', TOOL_TITLES.clear);
    clearBtn.innerHTML = TOOL_ICONS.clear;
    clearBtn.addEventListener('click', function () {
      setMode(null);
      env.clearSelection();
    });
    bar.appendChild(clearBtn);
    wrap.appendChild(bar);

    function publish(geometry, label, props) {
      setData(map, SEL_SRC, geometry);
      setData(map, DRAW_SRC, null);
      const bbox = bboxOf(geometry);
      env.store.publishGeom(w.id, geometry, label, bbox);
      if (props) env.store.publishSelection(w.id, props, bbox, label);
    }
    env.onClearSelection(function () {
      setData(map, SEL_SRC, null);
      setData(map, DRAW_SRC, null);
    });

    // ── click: the exact geometry comes from the SERVER, not from the renderer ──
    // `queryRenderedFeatures` would give geometry clipped to the vector tile the click landed in,
    // so a parcel straddling a tile boundary would produce zonal statistics for the visible
    // fragment and report them as the parcel's. See routers/data/vector.py::vector_pick.
    //
    // TWO corrections live in here, and both presented as "the click does nothing, silently":
    //
    //  1. THE HIT RADIUS IS A SCREEN DISTANCE. The stored `tolPx` is converted to degrees at the
    //     click's own zoom and latitude on every click. The old code sent a fixed `tol` in degrees
    //     that defaulted to 0, which makes the server's pick an exact intersection with a zero-area
    //     point — that can only ever land on a polygon, so a POINT layer was unclickable at every
    //     zoom. A degree value is not zoom-invariant either: 0.0005 degrees is half the screen at
    //     z18 and invisible at z6.
    //
    //  2. A CLICK TRIES EVERY VECTOR LAYER, not only the widget's own. The map draws all of the
    //     portal's layers, so "the second layer I added is not selectable" is the only reading a
    //     visitor can make of a click that does nothing over a feature they can see. The widget's
    //     bound layer stays FIRST (it is the author's declared intent), then the rest top-down. The
    //     attribute channel is published only for the bound layer, because `store.attr` is scoped
    //     by layerKey — publishing another layer's value under this widget's key would silently
    //     filter nothing (see `filtersFor`).
    map.on('click', function (ev) {
      if (mode !== 'click') return;
      const candidates = pickCandidates(w, env);
      if (!candidates.length) return;
      const tol = degreesFor(map, ev.point, ds);
      // Sequential, stopping at the first hit: the topmost feature under the cursor is the one the
      // visitor pointed at, and firing every layer in parallel would race to report a lower one.
      (function attempt(i) {
        if (i >= candidates.length) return;
        const layerId = candidates[i];
        return env.api.pick(w.id + ':pick', layerId,
          { lng: ev.lngLat.lng, lat: ev.lngLat.lat, tol: tol })
          .then(function (hit) {
            if (!hit) return attempt(i + 1);   // 204 — nothing here; try the layer underneath
            const primary = ds.layerId != null && String(layerId) === String(ds.layerId);
            const label = primary && ds.field && hit.props && hit.props[ds.field] != null
              ? String(hit.props[ds.field]) : 'Selected feature';
            publish(hit.geometry, label, hit.props);
            // The ATTRIBUTE channel too, when the author named a field: one click both selects the
            // polygon (which drives raster statistics) and filters the attribute-backed widgets to
            // that feature's value. That is what makes a choropleth dashboard feel wired.
            if (primary && ds.field && hit.props && hit.props[ds.field] != null) {
              env.store.publishAttr(w.id, { field: ds.field, op: 'in', values: [hit.props[ds.field]] },
                ds.field + ' = ' + truncate(hit.props[ds.field], 24));
            }
          })
          .catch(function (err) {
            if (err.name === 'AbortError') return;
            console.warn('[geodeploy] pick failed', layerId, err);
            return attempt(i + 1);
          });
      })(0);
    });

    // ── polygon: click to add a vertex, double-click or Enter to close, Esc to cancel ──
    map.on('click', function (ev) {
      if (mode !== 'polygon') return;
      pending.push([ev.lngLat.lng, ev.lngLat.lat]);
      if (pending.length >= 2) {
        setData(map, DRAW_SRC, { type: 'LineString', coordinates: pending });
      }
    });
    map.on('dblclick', function (ev) {
      if (mode !== 'polygon') return;
      ev.preventDefault();
      finishPolygon();
    });
    document.addEventListener('keydown', function (ev) {
      if (mode !== 'polygon') return;
      if (ev.key === 'Enter') finishPolygon();
      if (ev.key === 'Escape') { resetDraw(); setMode(null); }
    });
    function finishPolygon() {
      if (pending.length < 3) return;      // two points is a line, and a line has no area to sample
      publish(ringPolygon(pending), 'Drawn area');
      pending = [];
      setMode(null);
    }

    // ── bbox: drag a rectangle. A rectangle is published as a POLYGON, deliberately — everything
    // downstream then has one shape to handle. ──
    let anchor = null;
    map.on('mousedown', function (ev) {
      if (mode !== 'bbox') return;
      anchor = [ev.lngLat.lng, ev.lngLat.lat];
      ev.preventDefault();
    });
    map.on('mousemove', function (ev) {
      if (mode !== 'bbox' || !anchor) return;
      setData(map, DRAW_SRC, rectangle(anchor, [ev.lngLat.lng, ev.lngLat.lat]));
    });
    map.on('mouseup', function (ev) {
      if (mode !== 'bbox' || !anchor) return;
      const corner = [ev.lngLat.lng, ev.lngLat.lat];
      const dragged = Math.abs(corner[0] - anchor[0]) > 1e-6 || Math.abs(corner[1] - anchor[1]) > 1e-6;
      const rect = dragged ? rectangle(anchor, corner) : null;
      anchor = null;
      if (rect) publish(rect, 'Drawn box');
      setMode(null);
    });
    function rectangle(a, b) {
      const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
      const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
      return { type: 'Polygon', coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] };
    }
  }

  // ── the active-filter bar ──────────────────────────────────────────────────
  // It answers "why is this number small?". Without it a dashboard with a live selection and a
  // live selector looks identical to one showing the whole dataset, and the only way back is a
  // page reload.
  function buildBar(store, onClear) {
    const bar = el('div');
    bar.id = 'gd-dash-bar';
    function render() {
      clear(bar);
      const chips = store.chips();
      if (!chips.length) { bar.style.display = 'none'; return; }
      bar.style.display = '';
      bar.appendChild(el('span', null, 'Filtered by'));
      chips.forEach(function (chip) {
        const node = el('span', 'gd-fchip');
        node.appendChild(el('span', null, chip.label));
        const x = el('button', null, '×');
        x.type = 'button';
        x.title = 'Remove this filter';
        x.addEventListener('click', function () {
          if (chip.geom) { store.clearGeom(); onClear.geom(); }
          else store.clearAttr(chip.source);
          render();
        });
        node.appendChild(x);
        bar.appendChild(node);
      });
      const reset = el('button', 'gd-reset', 'Reset dashboard');
      reset.type = 'button';
      reset.addEventListener('click', function () {
        store.clearAll();
        onClear.all();
        render();
      });
      bar.appendChild(reset);
    }
    store.onBarChange(render);
    render();
    return bar;
  }

  // ── setup ──────────────────────────────────────────────────────────────────
  function setup(ctx) {
    const cfg = ctx.style && ctx.style.geodeploy && ctx.style.geodeploy.dashboard;
    if (!cfg || !cfg.widgets || !cfg.widgets.length) return;

    const host = document.getElementById('dashboard-panel');
    const layoutEl = document.getElementById('layout');
    const mapWrap = document.getElementById('map-wrap');
    if (!host || !layoutEl) return;

    const grid = cfg.grid || {};
    const baseRow = grid.rowHeight || 90;      // `--dash-row` is written per breakpoint by placeAll
    document.documentElement.style.setProperty('--dash-gap', (grid.gap || 10) + 'px');

    const store = createStore(cfg.widgets);
    const api = createApi();
    const clearHandlers = [];
    const env = {
      map: ctx.map,
      maplibregl: ctx.maplibregl,
      style: ctx.style,
      layout: ctx.layout,
      sources: cfg.sources || {},
      store: store,
      api: api,
      absUrl: ctx.absUrl || function (u) { return u; },
      fitBbox: ctx.fitBbox || function () {},
      onClearSelection: function (fn) { clearHandlers.push(fn); },
      clearSelection: function () {
        // Clearing the geometry channel MUST also clear the raster results it produced — a stat
        // block left showing numbers for an area no longer outlined on the map is the dashboard
        // lying about what it measured.
        store.clearGeom();
        store.clearSelection();
        clearHandlers.forEach(function (fn) { try { fn(); } catch (e) {} });
      },
    };

    // Reading order, which is also the phone's stacking order: top row first, left to right.
    const ordered = cfg.widgets.slice().sort(function (a, b) {
      return (a.layout.y - b.layout.y) || (a.layout.x - b.layout.x);
    });

    const cards = [];
    const bar = buildBar(store, {
      geom: function () { env.clearSelection(); },
      all: function () {
        env.clearSelection();
        // A reset also drops the map's own filter — `clearAll` refreshes every widget, and the map
        // widget's refresh is what re-applies (now empty) filters to the style layers.
      },
    });
    // On the BODY, not in the grid. A grid row for it would be `grid-auto-rows` tall (90px of
    // mostly nothing) and would push every widget down by a row the moment a filter went live —
    // the page would jump under the click that filtered it. Fixed at the foot of the window, it
    // stays reachable however far the grid is scrolled.
    document.body.appendChild(bar);

    ordered.forEach(function (w) {
      const make = RENDERERS[w.type];
      if (!make) return;                 // resolve_dashboard guarantees this cannot happen; cheap insurance
      let inst;
      try { inst = make(w, env); }
      catch (e) {
        console.warn('[geodeploy] widget failed to build', w.id, w.type, e);
        return;
      }
      if (!inst || !inst.el) return;
      if (!inst.isMap) host.appendChild(inst.el);
      cards.push({ id: w.id, el: inst.el, layout: w.layout, inst: inst });
      store.subscribe(w.id, inst.refresh);
    });

    // A map widget that was declared but whose element is missing, or no map widget at all: the map
    // still exists (the runtime hangs off it) but has nowhere to be, so it is parked off-screen
    // rather than display:none — a display:none MapLibre container measures 0×0 and never recovers.
    const hasMapCard = cards.some(function (c) { return c.inst.isMap; });
    if (mapWrap && !hasMapCard) mapWrap.dataset.dashParked = '1';

    // portal.js hides #sidebar when `panels.layerCatalog` is off, but the header button that opens
    // it stays — a control that does nothing, on the one archetype where the layer list is off by
    // default. Hidden here rather than in portal.css so an author who turns the panel ON keeps it.
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    if (toggle && sidebar && sidebar.style.display === 'none') toggle.style.display = 'none';

    // The bar is a full-width grid row of its own; the cards are placed by the responsive mapper.
    let lastCols = null;
    function layoutNow() {
      const cols = breakpointCols(layoutEl.clientWidth || window.innerWidth);
      placeAll(cards, cols, baseRow);
      lastCols = cols;
      // The map's container changed size, so MapLibre has to be told — it measures on construction
      // and on its own resize observer only for the window, not for a grid re-flow.
      if (hasMapCard) { try { ctx.map.resize(); } catch (e) {} }
    }
    layoutNow();
    let resizeTimer = null;
    const relayout = function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(layoutNow, 120);
    };
    window.addEventListener('resize', relayout);
    // The WINDOW is not the only thing that changes the grid's width: the layer-list overlay, an
    // embed whose host resizes the iframe, and a phone's URL bar collapsing all move
    // `#layout.clientWidth` without a window resize event. Observing the element is the only way to
    // catch those, and it is also what makes the editor's preview iframe re-flow while it is being
    // dragged. Guarded on the column count actually changing, because a ResizeObserver fires for
    // every pixel and re-placing every card 60 times a second is worse than not re-placing at all.
    if (typeof ResizeObserver === 'function') {
      let roTimer = null;
      new ResizeObserver(function () {
        if (roTimer) clearTimeout(roTimer);
        roTimer = setTimeout(function () {
          if (breakpointCols(layoutEl.clientWidth || window.innerWidth) !== lastCols) layoutNow();
        }, 120);
      }).observe(layoutEl);
    }

    // First paint: every widget asks its own question once. They run in parallel — the api client's
    // per-widget abort keys mean they cannot cancel each other.
    cards.forEach(function (c) { try { c.inst.refresh(); } catch (e) { console.warn('[geodeploy] widget refresh failed', c.id, e); } });

    // AUTO-REFRESH, for a near-real-time layer. It re-asks every widget the question it is already
    // asking, which is deliberately NOT the same as clearing the filters: a wall-mounted board must
    // keep the operator's selection across a refresh. Paused while the tab is hidden, because a
    // background tab polling every 30 s for a day is a lot of requests nobody will ever read.
    if (cfg.refresh) {
      let timer = null;
      const tick = function () {
        cards.forEach(function (c) { try { c.inst.refresh(); } catch (e) {} });
      };
      const start = function () { if (!timer) timer = setInterval(tick, cfg.refresh * 1000); };
      const stop = function () { if (timer) { clearInterval(timer); timer = null; } };
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) stop(); else { tick(); start(); }
      });
      start();
    }
  }

  return { setup: setup, RENDERERS: RENDERERS };
})();
