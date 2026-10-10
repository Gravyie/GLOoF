/** Scale every polygon of a FeatureCollection about its own centroid so that its
 *  AREA changes by `areaFactor` (linear scale = sqrt). Visualisation only -
 *  used by the replay to show the lake filling/draining. Returns the input
 *  unchanged when areaFactor === 1. */
export function scaleAboutCentroid(fc, areaFactor) {
  if (!fc || Math.abs(areaFactor - 1) < 1e-3) return fc
  const k = Math.sqrt(Math.max(areaFactor, 0.01))
  const scaleRing = (ring, cx, cy) => ring.map(([x, y]) => [cx + (x - cx) * k, cy + (y - cy) * k])
  const centroid = (ring) => {
    let sx = 0, sy = 0
    for (const [x, y] of ring) { sx += x; sy += y }
    return [sx / ring.length, sy / ring.length]
  }
  const polys = (g) => (g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [])
  return {
    ...fc,
    features: fc.features.map((f) => {
      const out = polys(f.geometry).map((rings) => {
        const [cx, cy] = centroid(rings[0])
        return rings.map((r) => scaleRing(r, cx, cy))
      })
      return {
        ...f,
        geometry: f.geometry.type === 'Polygon'
          ? { type: 'Polygon', coordinates: out[0] }
          : { type: 'MultiPolygon', coordinates: out },
      }
    }),
  }
}
