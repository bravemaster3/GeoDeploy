// Build MapLibre glyph PBFs for one font face, from a TTF.
//
// Labels are drawn from glyphs, and a fontstack the server does not have renders as NOTHING at all
// — no error, no text. So adding a font to a GeoDeploy instance means generating its glyphs and
// dropping them into `templates/shared/fonts/<Face Name>/`, which `routers/fonts.py` then serves
// and lists with no rebuild, no config and no restart.
//
// Shipped already: Noto Sans in Regular, Bold and Italic. Noto Serif and Noto Sans Mono are the
// obvious next two — QGIS users reach for a serif or a monospace often enough that mapping them
// onto a sans is a visible substitution.
//
//   docker run --rm -v "$PWD":/w -w /w node:20-bookworm //     bash -lc 'npm i --no-audit --no-fund fontnik && node scripts/build_glyphs.js NotoSerif-Regular.ttf "templates/shared/fonts/Noto Serif Regular"'
//
// SIZE. A face over full Unicode is ~1.2 MB; over the nine ranges labels actually use it is ~780 KB.
// The shipped set is trimmed to those ranges:
//
//   0-255      ASCII and Latin-1        768-1023    combining diacriticals, Greek
//   256-511    Latin Extended-A         1024-1279   Cyrillic
//   512-767    Latin Extended-B         1280-1535   Cyrillic supplement, Armenian, Hebrew
//   7680-7935  Latin Extended Additional (Vietnamese)
//   8192-8447  punctuation              8448-8703   letterlike and number forms
//
// Generate everything and keep what you need; a range with no glyphs is skipped either way.
//
// Ranges with no glyphs are skipped: an empty range is ~10 bytes of protobuf envelope, and writing
// 250 of them per face would triple the file count for nothing. `routers/fonts.py` answers a
// missing range with a 404, which is how the spec says "no glyphs here" — MapLibre draws nothing
// for those codepoints and carries on.
const fontnik = require('fontnik')
const fs = require('fs')
const path = require('path')

const [ttf, outDir] = process.argv.slice(2)
const buf = fs.readFileSync(ttf)
fs.mkdirSync(outDir, { recursive: true })

let written = 0
let bytes = 0
let pending = 0
let queued = false

function report () {
  if (queued && pending === 0) {
    console.log(path.basename(outDir) + '\t' + written + ' ranges\t' + bytes + ' bytes')
  }
}

for (let start = 0; start < 65536; start += 256) {
  pending++
  fontnik.range({ font: buf, start: start, end: start + 255 }, function (err, res) {
    pending--
    if (!err && res && res.length > 32) {
      fs.writeFileSync(path.join(outDir, start + '-' + (start + 255) + '.pbf'), res)
      written++
      bytes += res.length
    }
    report()
  })
}
queued = true
report()
