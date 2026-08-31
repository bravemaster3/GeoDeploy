/**
 * The basemaps every map in GeoDeploy can sit on.
 *
 * Here rather than in a view because more than one place needs them now, and a second copy of a
 * tile-URL list is a second place to fix when a provider changes a path — which they do.
 *
 * That warning came true: CARTO began requiring an API key and answered unauthenticated requests
 * with watermarked tiles, and this copy went on serving them after the server's catalog had moved.
 * MIRROR OF `api/geodeploy/services/portal_generator.BASEMAP_CATALOG` — same ids, same order, same
 * URLs. Change that one first, then this.
 */
export const BASEMAPS = [
  { id: 'osm', name: 'OpenStreetMap',
    tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png', 'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution: '© OpenStreetMap contributors',
    thumb: 'https://a.tile.openstreetmap.org/4/8/5.png' },
  { id: 'positron', name: 'Light Gray',
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}'],
    attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
    thumb: 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/4/5/8' },
  { id: 'voyager', name: 'Humanitarian',
    tiles: ['https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', 'https://b.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', 'https://c.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png'],
    attribution: '© OpenStreetMap contributors | Tiles: Humanitarian OSM Team, hosted by OpenStreetMap France',
    thumb: 'https://a.tile.openstreetmap.fr/hot/4/8/5.png' },
  { id: 'dark', name: 'Dark Gray',
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}'],
    attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
    thumb: 'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/4/5/8' },
  { id: 'topo', name: 'OpenTopoMap',
    tiles: ['https://a.tile.opentopomap.org/{z}/{x}/{y}.png', 'https://b.tile.opentopomap.org/{z}/{x}/{y}.png'],
    attribution: '© OpenStreetMap, SRTM | © OpenTopoMap (CC-BY-SA)',
    thumb: 'https://a.tile.opentopomap.org/4/8/5.png' },
  { id: 'satellite', name: 'Satellite',
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
    thumb: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/4/5/8' },
  { id: 'esri-topo', name: 'Esri Topographic',
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'],
    attribution: '© Esri',
    thumb: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/4/5/8' },
]

/** What a map uses when nobody has chosen: the first entry, by definition of "default". */
export const DEFAULT_BASEMAP = BASEMAPS[0]

/** The catalog entry for an id, falling back to the default rather than returning nothing. */
export function basemapById(id) {
  return BASEMAPS.find(b => b.id === id) || DEFAULT_BASEMAP
}
