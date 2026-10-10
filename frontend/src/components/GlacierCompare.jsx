import { useMemo, useState } from 'react'
import MapViewer from './MapViewer'
import { isoToOrd } from '../utils/format'

/**
 * Swipe comparison of two years' perennial ice (and that year's late-season
 * lake outline). The lower map drives the camera; the upper map follows it and
 * is clipped at the slider.
 */
export default function GlacierCompare({ lake, glacier, model, onClose }) {
  const years = glacier.years.map((y) => y.year)
  const [a, setA] = useState(years[0])
  const [b, setB] = useState(years.at(-1))
  const [split, setSplit] = useState(50)
  const [view, setView] = useState(null)

  // lake outline for a year: the archived frame nearest 15 Oct of that year
  const lakeOf = useMemo(() => (yr) => {
    let best = null
    model.frames.forEach((f) => {
      if (+f.date.slice(0, 4) !== yr) return
      const d = Math.abs(isoToOrd(f.date) - isoToOrd(`${yr}-10-15`))
      if (!best || d < best.d) best = { f, d }
    })
    return best ? { type: 'FeatureCollection', features: [best.f.lake] } : null
  }, [model])

  // memoised so the (large) GeoJSON is only re-uploaded when the year changes,
  // not on every parent re-render driven by the replay clock
  const layersA = useMemo(() => ({ outline: glacier.years.find((g) => g.year === a).outline, base: glacier.base.outline }), [glacier, a])
  const layersB = useMemo(() => ({ outline: glacier.years.find((g) => g.year === b).outline, base: glacier.base.outline }), [glacier, b])
  const lakeA = useMemo(() => lakeOf(a), [lakeOf, a])
  const lakeB = useMemo(() => lakeOf(b), [lakeOf, b])
  const yA = glacier.years.find((y) => y.year === a)
  const yB = glacier.years.find((y) => y.year === b)
  const card = (y, side) => (
    <div className={`panel absolute z-20 top-3 ${side}-3 px-3 py-2 text-[12px] pointer-events-none`}>
      <div className="font-semibold num">{y.year}</div>
      <div className="num">{y.ice_area_km2.toFixed(2)} km² perennial ice</div>
      <div className="text-muted text-[11px]">{y.scenes_used} late-season scene{y.scenes_used === 1 ? '' : 's'}{y.low_confidence ? ', low confidence' : ''}{y.season_complete === false ? ', season in progress' : ''}</div>
    </div>
  )

  return (
    <div className="absolute inset-0 z-40 bg-void">
      <div className="absolute inset-0">
        <MapViewer lake={lake} slabBbox={lake.ice_bbox} water={lakeA} glacier={layersA} orbit={false} onMove={setView} />
      </div>
      <div className="absolute inset-0 pointer-events-none" style={{ clipPath: `inset(0 0 0 ${split}%)` }}>
        <MapViewer lake={lake} slabBbox={lake.ice_bbox} water={lakeB} glacier={layersB} orbit={false} view={view} />
      </div>
      <div className="absolute top-0 bottom-0 w-0.5 bg-ink z-20 pointer-events-none" style={{ left: `${split}%` }} />
      {card(yA, 'left')}
      {card(yB, 'right')}

      <div className="panel absolute z-30 bottom-4 left-1/2 -translate-x-1/2 px-4 py-3 flex items-center gap-4 text-[12px]">
        <label className="flex items-center gap-2">Left
          <select className="field" value={a} onChange={(e) => setA(+e.target.value)}>{years.map((y) => <option key={y}>{y}</option>)}</select>
        </label>
        <input type="range" min="0" max="100" value={split} onChange={(e) => setSplit(+e.target.value)} aria-label="Swipe position" className="w-56" />
        <label className="flex items-center gap-2">Right
          <select className="field" value={b} onChange={(e) => setB(+e.target.value)}>{years.map((y) => <option key={y}>{y}</option>)}</select>
        </label>
        <span className="text-muted">Change: <span className="num text-ink">{(yB.ice_area_km2 - yA.ice_area_km2 >= 0 ? '+' : '')}{(yB.ice_area_km2 - yA.ice_area_km2).toFixed(2)} km²</span></span>
        <button className="btn btn-primary" onClick={onClose}>Close</button>
      </div>
      <p className="absolute z-30 bottom-[76px] left-1/2 -translate-x-1/2 text-[11px] text-muted text-center pointer-events-none">
        Dashed: {glacier.pi.baseline_year} baseline. Perennial ice from late-season composites; debris-covered ice not detected. Drag the left map to move both.
      </p>
    </div>
  )
}
