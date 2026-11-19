// The basemap picker as a MapLibre control — one implementation for every map in the app (the portal
// editor's preview and the layer page). It mirrors the published portal's control in
// templates/shared/portal.js: a grid-icon button opening a flyout of basemaps with thumbnails. Same
// markup and classes, so the shared `.gd-basemap-*` rules in style.css style both identically.
//
// The control owns no state. It is handed three functions — the catalog to offer, the id currently
// chosen, and what to do when another is picked — so each page keeps its basemap where it already
// lives (a portal's saved setting; a viewer's preference on the layer page).

const escHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
  'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>'
const GRID = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">' +
  '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>' +
  '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>'

export class BasemapControl {
  /**
   * @param {{catalog: () => Array<{id,name,thumb}>, current: () => string, pick: (id: string) => void,
   *          group?: string}} opts  `group` names the radio group; one per map on a page.
   */
  constructor({ catalog, current, pick, group = 'gd-basemap' }) {
    this._catalog = catalog
    this._current = current
    this._pick = pick
    this._group = group
  }

  /** Rebuild the rows — on open, and whenever the catalog or the selection changed elsewhere. */
  refresh() {
    if (!this._menu) return
    const cur = this._current()
    this._menu.innerHTML = '<div class="gd-basemap-title">Basemap</div>' +
      (this._catalog() || []).map(bm =>
        '<label class="gd-basemap-opt"><input type="radio" name="' + escHtml(this._group) +
        '" value="' + escHtml(bm.id) + '"' + (bm.id === cur ? ' checked' : '') + '>' +
        '<img class="gd-basemap-thumb" src="' + escHtml(bm.thumb || '') + '" alt="" loading="lazy">' +
        '<span class="gd-basemap-name">' + escHtml(bm.name || bm.id) + '</span>' +
        '<span class="gd-basemap-check">' + CHECK + '</span></label>').join('')
  }

  onAdd(map) {
    this._map = map
    const c = document.createElement('div')
    c.className = 'maplibregl-ctrl maplibregl-ctrl-group gd-basemap-ctrl'
    c.innerHTML = '<button type="button" class="gd-basemap-btn" title="Basemaps" aria-label="Choose basemap">' +
      GRID + '</button><div class="gd-basemap-menu"></div>'
    const btn = c.querySelector('.gd-basemap-btn')
    this._menu = c.querySelector('.gd-basemap-menu')
    this.refresh()
    btn.addEventListener('click', ev => {
      ev.stopPropagation()
      this.refresh()                  // the choice may have changed since the menu was built
      c.classList.toggle('open')
      this._fit()
    })
    this._menu.addEventListener('change', ev => this._pick(ev.target.value))
    this._menu.addEventListener('click', ev => ev.stopPropagation())
    this._docClose = () => c.classList.remove('open')
    document.addEventListener('click', this._docClose)
    this._c = c
    return c
  }

  /**
   * Keep the open menu INSIDE the map. Eight basemaps with thumbnails are taller than a map that is
   * half a screen high (the layer page's), and the last ones — "None" among them — were cut off by
   * the map's edge with no way to reach them. The menu scrolls instead.
   */
  _fit() {
    if (!this._menu || !this._c?.classList.contains('open')) return
    const box = this._map?.getContainer?.().getBoundingClientRect()
    const top = this._menu.getBoundingClientRect().top
    const room = box ? Math.floor(box.bottom - top - 12) : 0
    this._menu.style.maxHeight = room > 120 ? room + 'px' : ''
    this._menu.style.overflowY = room > 120 ? 'auto' : ''
  }

  onRemove() {
    document.removeEventListener('click', this._docClose)
    this._c?.remove()
  }
}
