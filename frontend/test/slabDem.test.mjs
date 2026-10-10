// Pure-math tests for the slab clipping. Run: node --test test/
import test from 'node:test'
import assert from 'node:assert/strict'

// slabDem.js imports maplibre-gl (browser-only), so test the pure functions
// by loading the source with that one import stubbed out.
import { readFileSync } from 'node:fs'
const src = readFileSync(new URL('../src/utils/slabDem.js', import.meta.url), 'utf8').replace(
  /^import \{ addProtocol \} from 'maplibre-gl'$/m,
  'const addProtocol = () => {}',
)
const mod = await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'))
const { tileLon, tileLat, encodeTerrarium, clipTileToBox } = mod

const decode = ([r, g, b]) => r * 256 + g + b / 256 - 32768

test('tile math matches slippy-map conventions', () => {
  assert.equal(tileLon(0, 0), -180)
  assert.equal(tileLon(1, 1), 0)
  assert.ok(Math.abs(tileLat(1, 1)) < 1e-9) // equator
  assert.ok(Math.abs(tileLat(0, 0) - 85.0511287798) < 1e-6) // mercator limit
})

test('terrarium encode/decode round-trips', () => {
  for (const m of [0, 4500, 5200, 8848, -100]) {
    assert.ok(Math.abs(decode(encodeTerrarium(m)) - m) < 1 / 256 + 1e-9, String(m))
  }
})

test('clip keeps pixels inside the box and floors the rest', () => {
  const z = 13, x = 5915, y = 3395 // a tile near 88.2E, 27.9N
  const size = 256
  const w = tileLon(z, x), e = tileLon(z, x + 1), n = tileLat(z, y), s = tileLat(z, y + 1)
  const bbox = [w + (e - w) * 0.25, s + (n - s) * 0.25, w + (e - w) * 0.75, s + (n - s) * 0.75]

  const rgba = new Uint8ClampedArray(size * size * 4)
  const mountain = encodeTerrarium(6000)
  for (let i = 0; i < size * size; i++) rgba.set([...mountain, 255], i * 4)

  const kept = clipTileToBox(rgba, z, x, y, bbox, 4500, size)
  // the box covers ~25% of the tile (lat is slightly non-linear, so allow slack)
  assert.ok(kept > size * size * 0.22 && kept < size * size * 0.28, `kept=${kept}`)

  const px = (cx, cy) => decode([...rgba.slice((cy * size + cx) * 4, (cy * size + cx) * 4 + 3)])
  assert.ok(Math.abs(px(128, 128) - 6000) < 1) // centre untouched
  assert.ok(Math.abs(px(2, 2) - 4500) < 1) // corner floored
  assert.ok(Math.abs(px(253, 253) - 4500) < 1)
})
