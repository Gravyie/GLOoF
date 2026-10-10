/**
 * Slab DEM: a custom `slab://` tile protocol that wraps the AWS Terrarium
 * elevation tiles and flattens every pixel OUTSIDE the lake bounding box to a
 * floor elevation.
 *
 * Why: the inverted-polygon mask (terrainMask.js) only *paints* the outside
 * black. The 3D terrain mesh is still there, so tall neighbouring peaks would
 * occlude the slab at low camera pitch. Flattening the DEM turns the cut-out
 * into a genuine free-standing block with real cliff walls.
 *
 * Terrarium encoding: elevation_m = (R * 256 + G + B / 256) - 32768
 */
import { addProtocol } from 'maplibre-gl'

export const AWS_TERRARIUM = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
const TILE = 256

const registry = new Map() // lakeId -> { bbox, floorMeters }
let protocolRegistered = false

/** Longitude of the left edge of tile column x at zoom z. */
export function tileLon(z, x) {
  return (x / 2 ** z) * 360 - 180
}

/** Latitude of the top edge of tile row y at zoom z (Web Mercator). */
export function tileLat(z, y) {
  const n = Math.PI - (2 * Math.PI * y) / 2 ** z
  return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)))
}

/** Encode an elevation (m) as Terrarium bytes. */
export function encodeTerrarium(meters) {
  const v = meters + 32768
  const r = Math.floor(v / 256)
  const g = Math.floor(v - r * 256)
  const b = Math.floor((v - Math.floor(v)) * 256)
  return [r, g, b]
}

/**
 * Pure, in-place: set every pixel of an RGBA Terrarium tile whose centre lies
 * outside `bbox` to `floorMeters`. Returns the number of pixels kept.
 */
export function clipTileToBox(rgba, z, x, y, bbox, floorMeters, size = TILE) {
  const [west, south, east, north] = bbox
  const [fr, fg, fb] = encodeTerrarium(floorMeters)

  // Longitude is linear in x; latitude is not, so precompute both axes once.
  const lons = new Float64Array(size)
  const lats = new Float64Array(size)
  for (let i = 0; i < size; i++) {
    lons[i] = tileLon(z, x + (i + 0.5) / size)
    lats[i] = tileLat(z, y + (i + 0.5) / size)
  }

  let kept = 0
  for (let py = 0; py < size; py++) {
    const latIn = lats[py] >= south && lats[py] <= north
    for (let px = 0; px < size; px++) {
      if (latIn && lons[px] >= west && lons[px] <= east) {
        kept++
        continue
      }
      const o = (py * size + px) * 4
      rgba[o] = fr
      rgba[o + 1] = fg
      rgba[o + 2] = fb
      rgba[o + 3] = 255
    }
  }
  return kept
}

async function loadSlabTile(params, abortController) {
  // slab://<lakeId>/<z>/<x>/<y>
  const [lakeId, z, x, y] = params.url.replace('slab://', '').split('/')
  const cfg = registry.get(lakeId)
  if (!cfg) throw new Error(`slab: lake "${lakeId}" is not registered`)

  const upstream = AWS_TERRARIUM.replace('{z}', z).replace('{x}', x).replace('{y}', y)
  const res = await fetch(upstream, { signal: abortController.signal })
  if (!res.ok) throw new Error(`terrain tile ${z}/${x}/${y}: HTTP ${res.status}`)

  const bitmap = await createImageBitmap(await res.blob(), {
    premultiplyAlpha: 'none',
    colorSpaceConversion: 'none',
  })
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height)
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(bitmap, 0, 0)
  const img = ctx.getImageData(0, 0, bitmap.width, bitmap.height)
  clipTileToBox(img.data, +z, +x, +y, cfg.bbox, cfg.floorMeters, bitmap.width)

  // raster-dem tiles are 'image' requests: an ImageBitmap is used as-is.
  const out = await createImageBitmap(img, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' })
  return { data: out }
}

/** Register a lake's slab and return the tile URL template for a raster-dem source. */
export function registerSlab(lakeId, { bbox, floorMeters }) {
  registry.set(lakeId, { bbox, floorMeters })
  if (!protocolRegistered) {
    addProtocol('slab', loadSlabTile)
    protocolRegistered = true
  }
  return `slab://${lakeId}/{z}/{x}/{y}`
}
