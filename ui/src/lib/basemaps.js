/**
 * The basemaps every map in GeoDeploy can sit on.
 *
 * Here rather than in a view because more than one place needs them now, and a second copy of a
 * tile-URL list is a second place to fix when a provider changes a path — which they do.
 */
export const BASEMAPS = [
  { id: 'positron', name: 'Positron',
    tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png', 'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png', 'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'],
    attribution: '© OpenStreetMap © CARTO',
    thumb: 'https://a.basemaps.cartocdn.com/light_all/4/8/5.png' },
  { id: 'voyager', name: 'Voyager',
    tiles: ['https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png', 'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png', 'https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png'],
    attribution: '© OpenStreetMap © CARTO',
    thumb: 'https://a.basemaps.cartocdn.com/rastertiles/voyager/4/8/5.png' },
  { id: 'dark', name: 'Dark Matter',
    tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png', 'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
    attribution: '© OpenStreetMap © CARTO',
    thumb: 'https://a.basemaps.cartocdn.com/dark_all/4/8/5.png' },
  { id: 'osm', name: 'OpenStreetMap',
    tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png', 'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution: '© OpenStreetMap contributors',
    thumb: 'https://a.tile.openstreetmap.org/4/8/5.png' },
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
