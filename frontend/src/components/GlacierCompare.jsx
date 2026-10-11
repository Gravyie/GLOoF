import { useMemo, useState } from 'react'
import MapViewer from './MapViewer'
import { fmtDate, isoToOrd } from '../utils/format'

/**
 * Two years side by side. Each side shows its own full terrain block; the
 * cameras move together. On each map the dashed lines are the OTHER year, so a
 * difference is visible without flicking between them. Below, the two cards
 * mirror each other row for row, with the differences in the middle.
 */
export default function GlacierCompare({ lake, glacier, model, onClose }) {
  const years = glacier.years.map((y) => y.year)
  const [a, setA] = useState(years[0])
  const [b, setB] = useState(years.findLast((y) => glacier.years.find((g) => g.year === y).season_complete !== false) ?? years.at(-1))
  const [view, setView] = useState(null)

  // the lake on the archived image nearest 15 Oct of a year (end of melt season)
  const lakeFrame = useMemo(() => (yr) => {
    let best = null
    model.frames.forEach((f) => {
      if (+f.date.slice(0, 4) !== yr) return
      const d = Math.abs(isoToOrd(f.date) - isoToOrd(`${yr}-10-15`))
      if (!best || d < best.d) best = { f, d }
    })
    return best?.f ?? null
  }, [model])

  const side = (yr) => {
    const g = glacier.years.find((y) => y.year === yr)
    const f = lakeFrame(yr)
    return { g, f, lakeFc: f ? { type: 'FeatureCollection', features: [f.lake] } : null }
  }
  const A = useMemo(() => side(a), [a, glacier, lakeFrame]) // eslint-disable-line react-hooks/exhaustive-deps
  const B = useMemo(() => side(b), [b, glacier, lakeFrame]) // eslint-disable-line react-hooks/exhaustive-deps
  // each map: its own ice + lake, with the other year's outlines dashed
  const layersA = useMemo(() => ({ outline: A.g.outline, base: B.g.outline }), [A, B])
  const layersB = useMemo(() => ({ outline: B.g.outline, base: A.g.outline }), [A, B])
  const ghostA = useMemo(() => (B.f ? { type: 'Feature', properties: {}, geometry: B.f.lake.geometry } : null), [B])
  const ghostB = useMemo(() => (A.f ? { type: 'Feature', properties: {}, geometry: A.f.lake.geometry } : null), [A])

  const lakeHa = (s) => (s.f ? s.f.stats.lake_area_m2 / 1e4 : null)
  const rows = [
    { k: 'Lake size', unit: 'ha', a: lakeHa(A), b: lakeHa(B), d: 0 },
    { k: 'Glacier ice', unit: 'km²', a: A.g.ice_area_km2, b: B.g.ice_area_km2, d: 1 },
    { k: `Ice lost since ${glacier.pi.baseline_year}`, unit: 'km²', a: A.g.lost_km2, b: B.g.lost_km2, d: 1 },
  ]
  const fmt = (v, d) => (v == null ? '-' : v.toFixed(d))
  const delta = (r) => (r.a == null || r.b == null ? null : r.b - r.a)
  const note = (s) => [
    s.f ? `Lake image ${fmtDate(s.f.date)}` : 'No lake image this year',
    `${s.g.scenes_used} images for ice`,
    s.g.low_confidence ? 'low confidence' : null,
    s.g.season_complete === false ? 'season in progress' : null,
  ].filter(Boolean).join(', ')

  const yearSelect = (value, onChange, label) => (
    <select aria-label={label} className="field text-[17px] font-semibold num" value={value} onChange={(e) => onChange(+e.target.value)}>
      {years.map((y) => <option key={y} value={y}>{y}</option>)}
    </select>
  )
  // mirrored: on the left card values sit on the right (facing the middle), on the right card on the left
  const card = (S, value, onChange, label, side) => {
    const left = side === 'left'
    return (
      <div className="panel px-5 py-4">
        <div className={`flex items-center gap-3 ${left ? 'flex-row-reverse' : ''}`}>
          {yearSelect(value, onChange, label)}
          <p className={`text-[12px] text-muted flex-1 ${left ? 'text-left' : 'text-right'}`}>{note(S)}</p>
        </div>
        <dl className="mt-3">
          {rows.map((r) => (
            <div key={r.k} className={`h-9 flex items-center justify-between border-t border-white/[.05] ${left ? 'flex-row' : 'flex-row-reverse'}`}>
              <dt className="label">{r.k}</dt>
              <dd className="num text-[18px]">{fmt(left ? r.a : r.b, r.d)}<span className="text-muted text-[12px]"> {r.unit}</span></dd>
            </div>
          ))}
        </dl>
      </div>
    )
  }

  return (
    <div className="absolute inset-x-0 top-14 bottom-0 z-40 bg-void flex flex-col">
      <div className="flex items-center gap-6 px-6 py-4">
        <div>
          <h2 className="text-[17px] font-semibold">Compare two years</h2>
          <p className="text-[13px] text-muted mt-0.5">Solid lines show each side's own year. Dashed lines show the other year. Drag either map; both move.</p>
        </div>
        <button className="btn btn-primary ml-auto" onClick={onClose}>Back to history</button>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-2 gap-3 px-3">
        {[[A, layersA, ghostA, 'left'], [B, layersB, ghostB, 'right']].map(([S, layers, ghost, k]) => (
          <div key={k} className="relative rounded-2xl overflow-hidden border border-white/[.06]">
            <MapViewer lake={lake} slabBbox={lake.ice_bbox} water={S.lakeFc} glacier={layers} baseline={ghost}
              orbit={false} view={view} onMove={setView} zoomOffset={-0.72} pitch={48} />
            <div className={`absolute top-3 ${k === 'left' ? 'left-4' : 'right-4'} num text-[28px] font-semibold tracking-tight pointer-events-none`}>{S.g.year}</div>
          </div>
        ))}
      </div>

      {/* mirrored cards: left values face right, right values face left, differences between */}
      <div className="grid grid-cols-[1fr_200px_1fr] gap-3 px-3 py-3">
        {card(A, a, setA, 'Left year', 'left')}
        <div className="flex flex-col items-center text-center pt-[57px]">
          <div className="label h-0 -translate-y-6">Change</div>
          {rows.map((r) => {
            const d = delta(r)
            const same = d == null || Math.abs(d) < 10 ** -r.d
            return (
              <div key={r.k} className="h-9 w-full flex items-center justify-center gap-2 border-t border-white/[.05]">
                <span className="num text-[16px]" style={{ color: same ? '#8b95a1' : '#e7eaee' }}>
                  {d == null ? '-' : same ? 'no change' : `${d > 0 ? '+' : '−'}${Math.abs(d).toFixed(r.d)} ${r.unit}`}
                </span>
                {!same && r.a ? <span className="text-[11px] text-muted">{d >= 0 ? '+' : '−'}{Math.abs((d / r.a) * 100).toFixed(0)}%</span> : null}
              </div>
            )
          })}
        </div>
        {card(B, b, setB, 'Right year', 'right')}
      </div>
      <p className="px-6 pb-3 text-[12px] text-muted">
        Glacier ice is ice that lasted through autumn. Fresh snow can still make a year look icier than it was; ice under rock debris is not detected.
      </p>
    </div>
  )
}
