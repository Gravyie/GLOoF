/**
 * Shoreline morphing between two real lake outlines (display only).
 *
 * Both outlines are resampled to the same number of points by arc length,
 * given the same winding, and rotated so corresponding points are as close as
 * possible. Linear interpolation then gives a smooth boundary that moves the
 * way the lake did, instead of a cross-fade.
 */

const N = 256

/** Largest polygon's exterior ring of a GeoJSON Polygon / MultiPolygon geometry. */
export function outerRing(geom) {
  if (!geom) return null
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.type === 'MultiPolygon' ? geom.coordinates : []
  let best = null
  let bestA = -1
  for (const p of polys) {
    const a = Math.abs(signedArea(p[0]))
    if (a > bestA) { bestA = a; best = p[0] }
  }
  return best
}

export function signedArea(ring) {
  let s = 0
  for (let i = 0; i < ring.length - 1; i++) s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
  return s / 2
}

/** Resample a closed ring to n points evenly spaced along its perimeter (counter-clockwise). */
export function resample(ring, n = N) {
  let pts = ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1] ? ring.slice(0, -1) : ring.slice()
  if (signedArea([...pts, pts[0]]) < 0) pts = pts.reverse()
  const seg = []
  let total = 0
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length]
    const d = Math.hypot(b[0] - a[0], b[1] - a[1])
    seg.push(d)
    total += d
  }
  const out = []
  let i = 0, acc = 0
  for (let k = 0; k < n; k++) {
    const target = (k / n) * total
    while (acc + seg[i] < target && i < pts.length - 1) { acc += seg[i]; i++ }
    const a = pts[i], b = pts[(i + 1) % pts.length]
    const t = seg[i] ? (target - acc) / seg[i] : 0
    out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
  }
  return out
}

/** Rotate `b` (same length as `a`) so that sum of squared distances to `a` is minimal. */
export function align(a, b) {
  const n = a.length
  let best = 0, bestCost = Infinity
  for (let s = 0; s < n; s += 2) {
    let c = 0
    for (let k = 0; k < n; k += 4) {
      const p = a[k], q = b[(k + s) % n]
      c += (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
    }
    if (c < bestCost) { bestCost = c; best = s }
  }
  return best ? [...b.slice(best), ...b.slice(0, best)] : b
}

export function lerpRing(a, b, t) {
  return a.map((p, k) => [p[0] + (b[k][0] - p[0]) * t, p[1] + (b[k][1] - p[1]) * t])
}

const close = (r) => [...r, r[0]]

/** Memoising morpher over an array of lake geometries. */
export function createMorpher(geoms) {
  const cache = new Map()
  const sampled = (i) => {
    if (!cache.has(i)) {
      const ring = outerRing(geoms[i])
      cache.set(i, ring ? resample(ring) : null)
    }
    return cache.get(i)
  }
  const pairCache = new Map()
  return function at(i, j, t) {
    const a = sampled(i)
    if (!a) return null
    if (j == null || j === i || t <= 0) return { type: 'Polygon', coordinates: [close(a)] }
    const key = `${i}:${j}`
    if (!pairCache.has(key)) {
      const b = sampled(j)
      pairCache.set(key, b ? align(a, b) : null)
    }
    const b = pairCache.get(key)
    if (!b || t >= 1) return { type: 'Polygon', coordinates: [close(b ?? a)] }
    return { type: 'Polygon', coordinates: [close(lerpRing(a, b, t))] }
  }
}
