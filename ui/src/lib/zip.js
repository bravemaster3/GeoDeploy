/**
 * A minimal ZIP writer, so a shapefile can be uploaded by picking its files.
 *
 * A shapefile is a SET — .shp is unreadable without .shx and .dbf, and lands in the wrong place
 * without .prj — but a browser cannot read the files sitting next to the one you chose. There is no
 * API for it, and there should not be: a page that could read adjacent files could read anything.
 * So the user selects the set, and we make the .zip here, which is the format the server already
 * ingests. Nothing changes on the backend.
 *
 * Written by hand rather than pulled in: a zip library is a large dependency for ~90 lines of
 * well-specified format, and this runs on files the user just handed us, not on hostile input.
 *
 * DEFLATE comes from the platform (`CompressionStream`) when it is there, which is every browser
 * we support, and falls back to storing. A stored entry is still a completely valid archive — it is
 * bigger on the wire, not broken.
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let i = 0; i < 256; i++) {
    let c = i
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[i] = c >>> 0
  }
  return table
})()

function crc32(bytes) {
  let c = 0xffffffff
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

async function deflateRaw(bytes) {
  // `deflate-raw` is the members-only form ZIP wants — `deflate` would add a zlib header and every
  // reader would reject the entry.
  if (typeof CompressionStream === 'undefined') return null
  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'))
    return new Uint8Array(await new Response(stream).arrayBuffer())
  } catch {
    return null
  }
}

function dosDateTime(date) {
  // ZIP carries MS-DOS timestamps: two-second resolution, and no year before 1980.
  const year = Math.max(1980, date.getFullYear())
  const time = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1)
  const day = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
  return { time, day }
}

/** Maximum total we will build in memory. Beyond this, zipping in a tab is not the right tool. */
export const MAX_ZIP_BYTES = 2 * 1024 * 1024 * 1024

/**
 * Build a ZIP from `[{ name, data: Uint8Array }]`. Returns a Blob.
 *
 * Everything is held in memory, which is why the cap above exists: the alternative is a streaming
 * writer, and a shapefile large enough to need one is one the user should be zipping themselves.
 */
export async function buildZip(entries) {
  const total = entries.reduce((n, e) => n + e.data.length, 0)
  if (total > MAX_ZIP_BYTES) {
    throw new Error(
      `These files total ${(total / 1024 / 1024 / 1024).toFixed(1)} GB, which is too large to ` +
      'package in the browser. Zip them yourself and upload the .zip.',
    )
  }

  const encoder = new TextEncoder()
  const { time, day } = dosDateTime(new Date())
  const chunks = []
  const central = []
  let offset = 0

  for (const entry of entries) {
    const nameBytes = encoder.encode(entry.name)
    // Bit 11 declares the NAME is UTF-8. Without it a reader is entitled to decode a non-ASCII
    // filename as CP437, so `parcelles_élevage.dbf` arrives mangled and the .shp can no longer
    // find its sidecar. Only set when it is actually needed, since some old readers dislike it.
    const utf8Name = nameBytes.length !== entry.name.length
    const flags = utf8Name ? 0x800 : 0
    const crc = crc32(entry.data)
    const deflated = await deflateRaw(entry.data)
    // Storing beats a "compressed" copy that came out larger — which happens with small files.
    const useDeflate = deflated !== null && deflated.length < entry.data.length
    const body = useDeflate ? deflated : entry.data
    const method = useDeflate ? 8 : 0

    const header = new DataView(new ArrayBuffer(30))
    header.setUint32(0, 0x04034b50, true)
    header.setUint16(4, 20, true)          // version needed
    header.setUint16(6, flags, true)       // bit 11 = UTF-8 filename
    header.setUint16(8, method, true)
    header.setUint16(10, time, true)
    header.setUint16(12, day, true)
    header.setUint32(14, crc, true)
    header.setUint32(18, body.length, true)
    header.setUint32(22, entry.data.length, true)
    header.setUint16(26, nameBytes.length, true)
    header.setUint16(28, 0, true)          // extra field length

    chunks.push(new Uint8Array(header.buffer), nameBytes, body)
    central.push({ nameBytes, crc, method, flags, time, day, csize: body.length,
                   usize: entry.data.length, offset })
    offset += 30 + nameBytes.length + body.length
  }

  const cdStart = offset
  for (const e of central) {
    const header = new DataView(new ArrayBuffer(46))
    header.setUint32(0, 0x02014b50, true)
    header.setUint16(4, 20, true)          // version made by
    header.setUint16(6, 20, true)          // version needed
    header.setUint16(8, e.flags, true)     // must match the local header
    header.setUint16(10, e.method, true)
    header.setUint16(12, e.time, true)
    header.setUint16(14, e.day, true)
    header.setUint32(16, e.crc, true)
    header.setUint32(20, e.csize, true)
    header.setUint32(24, e.usize, true)
    header.setUint16(28, e.nameBytes.length, true)
    header.setUint16(30, 0, true)          // extra
    header.setUint16(32, 0, true)          // comment
    header.setUint16(34, 0, true)          // disk number start
    header.setUint16(36, 0, true)          // internal attributes
    header.setUint32(38, 0, true)          // external attributes
    header.setUint32(42, e.offset, true)
    chunks.push(new Uint8Array(header.buffer), e.nameBytes)
    offset += 46 + e.nameBytes.length
  }

  const end = new DataView(new ArrayBuffer(22))
  end.setUint32(0, 0x06054b50, true)
  end.setUint16(4, 0, true)                // this disk
  end.setUint16(6, 0, true)                // disk with central directory
  end.setUint16(8, central.length, true)
  end.setUint16(10, central.length, true)
  end.setUint32(12, offset - cdStart, true)
  end.setUint32(16, cdStart, true)
  end.setUint16(20, 0, true)               // comment length
  chunks.push(new Uint8Array(end.buffer))

  return new Blob(chunks, { type: 'application/zip' })
}

/** Every extension that can belong to a shapefile, so nothing useful is left behind. */
export const SHAPEFILE_PARTS = [
  '.shp', '.shx', '.dbf', '.prj', '.cpg', '.qpj', '.sbn', '.sbx', '.qix', '.fix',
  '.idm', '.ind', '.ain', '.aih', '.atx', '.shp.xml',
]
const REQUIRED_PARTS = ['.shx', '.dbf']

const stemOf = (name) => name.slice(0, name.lastIndexOf('.'))
const extOf = (name) => name.slice(name.lastIndexOf('.')).toLowerCase()

/**
 * Inspect a user's selection for a shapefile.
 *
 * Returns `null` when there is no .shp involved — the ordinary single-file path is untouched.
 * Otherwise `{ stem, files, missing }`, so the caller can refuse with something useful rather than
 * uploading a fragment that cannot possibly ingest.
 */
export function inspectShapefileSelection(fileList) {
  const files = Array.from(fileList || [])
  const shp = files.filter((f) => extOf(f.name) === '.shp')
  if (shp.length === 0) return null
  if (shp.length > 1) {
    return { stem: null, files: [], missing: [], error:
      'Select one shapefile at a time — this selection has ' + shp.length + ' .shp files.' }
  }

  const stem = stemOf(shp[0].name)
  // Case-insensitively: half the world's shapefiles are called ROADS.SHP.
  const lower = stem.toLowerCase()
  const members = files.filter((f) => stemOf(f.name).toLowerCase() === lower)
  const present = new Set(members.map((f) => extOf(f.name)))
  const missing = REQUIRED_PARTS.filter((p) => !present.has(p))
  return { stem, files: members, missing, error: null }
}
