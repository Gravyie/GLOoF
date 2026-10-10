import { useEffect, useMemo, useRef, useState } from 'react'
import MapGL, { Source, Layer } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

import { VOID, buildInvertedMask, buildSlabOutline, slabCamera } from '../utils/terrainMask'
import { registerSlab } from '../utils/slabDem'

const EXAGGERATION = 1.5
const SLAB_DEPTH_M = 700 // how far below the lake the slab's floor sits
const MAPTILER_KEY = import.meta.env.VITE_MAPTILER_KEY

// No basemap at all: just the void. Everything visible is terrain + our layers.
const VOID_STYLE = {
  version: 8,
  name: 'void',
  sources: {},
  layers: [{ id: 'void', type: 'background', paint: { 'background-color': VOID } }],
}

/**
 * 3D slab viewer.
 * Props: lake  - { id, bbox, center, lake_elevation_m } from /api/lakes
 *        water - GeoJSON FeatureCollection of water polygons (or null while loading)
 *        ice   - GeoJSON FeatureCollection of snow/ice polygons (or null)
 *        orbit - boolean, run the cinematic orbit
 */
export default function MapViewer({ lake, water, ice, showWater = true, showIce = true, waterEdge = '#e8f3ff', orbit, orbitDegPerSec = 6 }) {
  const mapRef = useRef(null)
  const [ready, setReady] = useState(false)

  // Derived, per-lake geometry. All memoised on the lake so they're stable.
  const demUrl = useMemo(
    () => registerSlab(lake.id, { bbox: lake.bbox, floorMeters: lake.lake_elevation_m - SLAB_DEPTH_M }),
    [lake],
  )
  const mask = useMemo(() => buildInvertedMask(lake.bbox), [lake])
  const outline = useMemo(() => buildSlabOutline(lake.bbox), [lake])
  const camera = useMemo(() => slabCamera(lake.bbox), [lake])

  // Turn on 3D terrain once the DEM source exists. Done by hand (rather than
  // the `terrain` prop) so we never reference a source before it is added.
  useEffect(() => {
    if (!ready || !mapRef.current) return
    const map = mapRef.current.getMap()
    const enable = () => map.setTerrain({ source: 'terrain-dem', exaggeration: EXAGGERATION })
    if (map.getSource('terrain-dem')) {
      enable()
      return
    }
    const onSource = (e) => {
      if (e.sourceId === 'terrain-dem' && map.getSource('terrain-dem')) {
        map.off('sourcedata', onSource)
        enable()
      }
    }
    map.on('sourcedata', onSource)
    return () => map.off('sourcedata', onSource)
  }, [ready, lake])

  // Cinematic orbit: advance the bearing every animation frame, scaled by the
  // real frame time so speed is identical at 30, 60 or 144 Hz. Pauses while the
  // user is dragging/zooming and resumes a few seconds after they let go.
  useEffect(() => {
    if (!ready || !orbit || !mapRef.current) return
    const map = mapRef.current.getMap()
    const el = map.getCanvasContainer()
    let paused = false
    let resumeTimer = 0
    let raf = 0
    let last = performance.now()

    const hold = () => {
      paused = true
      clearTimeout(resumeTimer)
    }
    const release = (delay = 3500) => {
      clearTimeout(resumeTimer)
      resumeTimer = setTimeout(() => {
        paused = false
        last = performance.now()
      }, delay)
    }
    const onWheel = () => {
      hold()
      release()
    }
    const onPointerUp = () => release()

    const tick = (now) => {
      const dt = Math.min(now - last, 100) // clamp after tab switches
      last = now
      if (!paused) map.setBearing(map.getBearing() + (orbitDegPerSec * dt) / 1000)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    el.addEventListener('pointerdown', hold)
    window.addEventListener('pointerup', onPointerUp)
    el.addEventListener('wheel', onWheel, { passive: true })
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(resumeTimer)
      el.removeEventListener('pointerdown', hold)
      window.removeEventListener('pointerup', onPointerUp)
      el.removeEventListener('wheel', onWheel)
    }
  }, [ready, orbit, orbitDegPerSec])

  return (
    <MapGL
      ref={mapRef}
      initialViewState={camera}
      mapStyle={VOID_STYLE}
      maxPitch={85}
      attributionControl={false}
      onLoad={() => setReady(true)}
      style={{ width: '100%', height: '100%' }}
    >
      {/* Terrain mesh source */}
      <Source id="terrain-dem" type="raster-dem" tiles={[demUrl]} tileSize={256} maxzoom={15} encoding="terrarium" />

      {/* Stark relief shading. Separate source avoids sharing the terrain tile cache. */}
      <Source id="hillshade-dem" type="raster-dem" tiles={[demUrl]} tileSize={256} maxzoom={15} encoding="terrarium">
        <Layer
          id="hillshade"
          type="hillshade"
          paint={{
            'hillshade-exaggeration': 0.8,
            'hillshade-shadow-color': '#05070a',
            'hillshade-highlight-color': '#d9dde2',
            'hillshade-accent-color': '#2a3138',
            'hillshade-illumination-direction': 315,
            'hillshade-illumination-anchor': 'map',
          }}
        />
      </Source>

      {/* Structural grid lines from MapTiler contours (needs VITE_MAPTILER_KEY). */}
      {MAPTILER_KEY && (
        <Source id="contours" type="vector" url={`https://api.maptiler.com/tiles/contours-v2/tiles.json?key=${MAPTILER_KEY}`}>
          <Layer
            id="contours-minor"
            type="line"
            source-layer="contour"
            paint={{ 'line-color': '#aab4be', 'line-opacity': 0.16, 'line-width': 0.5 }}
          />
          <Layer
            id="contours-major"
            type="line"
            source-layer="contour"
            filter={['>=', ['to-number', ['get', 'level'], 0], 1]}
            paint={{ 'line-color': '#aab4be', 'line-opacity': 0.38, 'line-width': 0.9 }}
          />
        </Source>
      )}

      {/* Inverted polygon mask: world minus the lake box, painted void-black */}
      <Source id="slab-mask" type="geojson" data={mask}>
        <Layer id="slab-mask-fill" type="fill" paint={{ 'fill-color': VOID, 'fill-opacity': 1, 'fill-antialias': false }} />
      </Source>

      {/* Neon rim marking the cut */}
      <Source id="slab-outline" type="geojson" data={outline}>
        <Layer id="slab-outline-line" type="line" paint={{ 'line-color': '#c9d1d9', 'line-width': 1, 'line-opacity': 0.55 }} />
      </Source>

      {/* Live snow/ice polygons derived from Sentinel-2 NDSI (under the water) */}
      {ice && (
        <Source id="ice" type="geojson" data={ice}>
          <Layer
            id="ice-fill"
            type="fill"
            layout={{ visibility: showIce ? 'visible' : 'none' }}
            paint={{ 'fill-color': '#e6eef6', 'fill-opacity': 0.5 }}
          />
          <Layer
            id="ice-line"
            type="line"
            layout={{ visibility: showIce ? 'visible' : 'none' }}
            paint={{ 'line-color': '#ffffff', 'line-width': 0.8, 'line-opacity': 0.7 }}
          />
        </Source>
      )}

      {/* Live water polygons derived from Sentinel-2 NDWI */}
      {water && (
        <Source id="water" type="geojson" data={water}>
          <Layer
            id="water-fill"
            type="fill"
            layout={{ visibility: showWater ? 'visible' : 'none' }}
            paint={{ 'fill-color': '#3d9bff', 'fill-opacity': 0.85 }}
          />
          <Layer
            id="water-line"
            type="line"
            layout={{ visibility: showWater ? 'visible' : 'none' }}
            paint={{ 'line-color': waterEdge, 'line-width': 1.6, 'line-opacity': 1 }}
          />
        </Source>
      )}
    </MapGL>
  )
}
