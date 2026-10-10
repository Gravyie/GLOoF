/**
 * "Tabletop slab" geometry helpers.
 *
 * Inverted polygon mask: a GeoJSON polygon whose OUTER ring covers the whole
 * Web-Mercator world and whose INNER ring (a hole) is the lake's bounding box.
 * Painted solid void-black, it hides everything except the rectangular cut-out.
 *
 * Winding follows RFC 7946: exterior ring counter-clockwise, hole clockwise.
 * Latitude is clamped to +/-85.0511 (the Web-Mercator limit); +/-90 would
 * project to infinity and break tessellation.
 */

export const VOID = '#101418'

const MERCATOR_MAX_LAT = 85.0511
const WORLD_RING = [
  [-180, -MERCATOR_MAX_LAT],
  [180, -MERCATOR_MAX_LAT],
  [180, MERCATOR_MAX_LAT],
  [-180, MERCATOR_MAX_LAT],
  [-180, -MERCATOR_MAX_LAT],
]

/** bbox = [west, south, east, north] */
export function bboxRing([w, s, e, n], clockwise = false) {
  const ccw = [[w, s], [e, s], [e, n], [w, n], [w, s]]
  return clockwise ? [...ccw].reverse() : ccw
}

/** World-with-a-hole polygon. Everything outside `bbox` is covered. */
export function buildInvertedMask(bbox) {
  return {
    type: 'Feature',
    properties: { kind: 'slab-mask' },
    geometry: { type: 'Polygon', coordinates: [WORLD_RING, bboxRing(bbox, true)] },
  }
}

/** Glowing rim around the slab. */
export function buildSlabOutline(bbox) {
  return {
    type: 'Feature',
    properties: { kind: 'slab-outline' },
    geometry: { type: 'LineString', coordinates: bboxRing(bbox) },
  }
}

export function bboxCenter([w, s, e, n]) {
  return [(w + e) / 2, (s + n) / 2]
}

export function bboxContains([w, s, e, n], lng, lat) {
  return lng >= w && lng <= e && lat >= s && lat <= n
}

/** Default cinematic camera for a slab. */
export function slabCamera(bbox) {
  const [longitude, latitude] = bboxCenter(bbox)
  return { longitude, latitude, zoom: 12.9, pitch: 62, bearing: -20 }
}
