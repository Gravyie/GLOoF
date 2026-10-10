import { useEffect, useMemo, useRef, useState } from 'react'
import MapViewer from './MapViewer'
import AreaChart from './AreaChart'
import { createMorpher } from '../utils/morph'
import { LEVELS, LEVEL_STYLE, fmtDate, fmtMonth, isoToOrd, ordToIso } from '../utils/format'

const BASE = import.meta.env.VITE_API_BASE ?? ''
const MORPH_DAYS = 18 // shoreline moves over the last N days before the next real observation
const RING_RAMP = ['#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef', '#9ec5f4', '#b7d3f6']

/* --------------------------------------------------------------- helpers */
function frameAt(days, day) {
  let i = -1
  for (let k = 0; k < days.length; k++) if (days[k] <= day + 1e-9) i = k
  return i
}

function useArchiveModel(archive) {
  return useMemo(() => {
    if (!archive) return null
    const frames = archive.frames
    const tl = archive.timeline
    const days = frames.map((f) => isoToOrd(f.date))
    const morph = createMorpher(frames.map((f) => f.lake.geometry))
    // one ring per season: the frame nearest 15 Oct (end of melt, lake at its largest)
    const byYear = new Map()
    frames.forEach((f, i) => {
      const yr = f.date.slice(0, 4)
      const score = Math.abs(isoToOrd(f.date) - isoToOrd(`${yr}-10-15`))
      if (!byYear.has(yr) || score < byYear.get(yr).score) byYear.set(yr, { i, score })
    })
    const years = [...byYear.keys()].sort()
    const rings = years.map((yr, k) => ({ yr, i: byYear.get(yr).i, color: RING_RAMP[Math.round((k / Math.max(years.length - 1, 1)) * (RING_RAMP.length - 1))] }))
    const baseFrame = frames.find((f) => f.date === tl.baseline?.outline_date)
    const series = tl.frames.map((t, i) => ({
      day: days[i], value: t.indicators.smoothed_area_m2 / 1e4, raw: t.indicators.lake_area_m2 / 1e4, level: t.level,
    }))
    const ice = frames.map((f, i) => ({ day: days[i], value: (f.stats.ice_area_m2 ?? 0) / 1e6, raw: null }))
    const events = (archive.lake.events ?? []).map((e) => ({ ...e, day: isoToOrd(e.date) }))
    // context water touching the window edge would drape down the slab walls: hide it
    const [w, s, e, n] = archive.lake.bbox, m = 0.0008
    const inside = (f) => JSON.stringify(f.geometry.coordinates).match(/-?\d+\.\d+/g).every((v, k) => {
      const x = +v
      return k % 2 === 0 ? x > w + m && x < e - m : x > s + m && x < n - m
    })
    const context = frames.map((f) => f.water.features.filter(inside))
    return { frames, tl, days, morph, rings, baseFrame, series, ice, events, context }
  }, [archive])
}

/* --------------------------------------------------------------- left panel */
function Metric({ k, v, unit, hint, warn }) {
  return (
    <div className="rounded-lg bg-raise border border-line px-3 py-2">
      <div className="text-[11px] text-muted">{k}</div>
      <div className={`num text-lg leading-tight ${warn ? 'text-[#ff8a80]' : ''}`}>{v}<span className="text-muted text-xs"> {unit}</span></div>
      {hint && <div className="text-[10px] text-muted mt-0.5">{hint}</div>}
    </div>
  )
}

function RiskPanel({ model, cur, day, archive }) {
  const level = cur?.level ?? 'NORMAL'
  const st = LEVEL_STYLE[level]
  const ind = cur?.indicators
  const th = model.tl.thresholds
  const base = model.tl.baseline.area_m2 / 1e4
  const idx = LEVELS.indexOf(level)
  return (
    <aside className="panel absolute z-10 left-3 top-[60px] bottom-[148px] w-[340px] overflow-y-auto p-4 max-[1100px]:hidden">
      <div className="flex items-baseline justify-between">
        <div className="text-[15px] font-semibold">{archive.lake.name}</div>
        <div className="text-muted text-[11px]">{archive.lake.country}</div>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <span key={level} className="slide-in rounded-md px-3 py-1 text-xl font-semibold" style={{ background: st.bg, color: st.fg }}>{st.label}</span>
        <span className="text-muted text-[11px] leading-tight">Risk level a monitor would<br />have shown on this date</span>
      </div>
      <div className="flex gap-0.5 mt-3" aria-label={`Level ${level}`}>
        {LEVELS.map((l, i) => (
          <div key={l} className="flex-1">
            <div className="h-1 rounded-sm transition-colors" style={{ background: i <= idx ? LEVEL_STYLE[l].dot : '#262d36' }} />
            <div className={`text-[10px] mt-1 ${i === idx ? 'text-ink font-semibold' : 'text-muted'}`}>{LEVEL_STYLE[l].label}</div>
          </div>
        ))}
      </div>

      <ul className="mt-3 space-y-1.5 min-h-[24px]">
        {(cur?.reasons ?? []).map((r, i) => (
          <li key={i} className="flex gap-2 items-start text-[12px]">
            <span className="mt-1.5 size-1.5 rounded-full shrink-0" style={{ background: LEVEL_STYLE[r.level].dot }} />
            <span>{r.text}</span>
          </li>
        ))}
        {!cur?.reasons?.length && <li className="text-muted text-[12px]">All indicators below threshold.</li>}
        {cur && LEVELS.indexOf(cur.level) > LEVELS.indexOf(cur.raw_level) && LEVELS.indexOf(cur.level) >= 3 && (
          <li className="text-[11px] text-muted border-l-2 border-line pl-2">
            Held at {st.label}: Warning and Evacuate are cleared by an operator, not automatically, even when readings ease.
          </li>
        )}
        {cur?.flags?.map((f, i) => <li key={`f${i}`} className="text-[11px] text-[#f5d06b] bg-[#3a3010] rounded px-2 py-1">{f}</li>)}
      </ul>

      {ind && (
        <div className="grid grid-cols-2 gap-2 mt-4">
          <Metric k="Lake extent" v={(ind.smoothed_area_m2 / 1e4).toFixed(1)} unit="ha" hint={`baseline ${base.toFixed(1)} ha (${model.tl.baseline.label})`} />
          <Metric k="Growth vs baseline" v={`${ind.growth_pct >= 0 ? '+' : ''}${ind.growth_pct.toFixed(1)}`} unit="%" warn={ind.growth_pct >= th.growth_watch_pct} hint={`Watch at +${th.growth_watch_pct}%`} />
          <Metric k="Shoreline advance" v={ind.advance_m.toFixed(0)} unit="m" warn={ind.advance_m >= th.advance_watch_m} hint="beyond the baseline outline" />
          <Metric k="2-year trend" v={ind.growth_rate_pct_yr == null ? '-' : `${ind.growth_rate_pct_yr >= 0 ? '+' : ''}${ind.growth_rate_pct_yr.toFixed(1)}`} unit="%/yr" warn={(ind.growth_rate_pct_yr ?? 0) >= th.rate_advisory_pct_yr} hint="robust slope of lake extent" />
        </div>
      )}

      <div className="mt-5">
        <div className="flex items-baseline justify-between mb-1">
          <div className="panel-title">Lake extent, ha</div>
          <div className="text-[10px] text-muted">line: 3-scene median, dots: each scene</div>
        </div>
        <AreaChart label="Lake extent" points={model.series} span={[model.days[0] - 25, model.days.at(-1) + 10]} playDay={day} color="#3d9bff" unit="ha"
          thresholds={[
            { value: base, label: 'baseline', color: '#f5d06b' },
            { value: base * (1 + th.growth_watch_pct / 100), label: `+${th.growth_watch_pct}% watch`, color: LEVEL_STYLE.WATCH.dot },
            { value: base * (1 + th.growth_warning_pct / 100), label: `+${th.growth_warning_pct}% warning`, color: LEVEL_STYLE.WARNING.dot },
          ]} />
      </div>
      <div className="mt-3">
        <div className="flex items-baseline justify-between mb-1">
          <div className="panel-title">Snow and ice in view, km²</div>
          <div className="text-[10px] text-muted">includes seasonal snow</div>
        </div>
        <AreaChart label="Snow and ice" points={model.ice} span={[model.days[0] - 25, model.days.at(-1) + 10]} playDay={day} color="#c9d4df" unit="km²" height={84} />
      </div>
      <p className="text-muted text-[10.5px] mt-4 leading-snug">
        Every outline is a real Sentinel-2 L2A scene (ESA, via AWS Earth Search on AWS Open Data); water from NDWI, ice from NDSI.
        Between scenes the shoreline is interpolated for display only. Risk rules are placeholders pending calibration.
      </p>
    </aside>
  )
}

/* --------------------------------------------------------------- timeline */
function Timeline({ model, state, day, controls }) {
  const bar = useRef(null)
  const [speed, setSpeed] = useState(1)
  const [autopilot, setAutopilot] = useState(true)
  const [drag, setDrag] = useState(false)
  if (!state) return null
  const { start_day: d0, end_day: d1 } = state.span
  const pct = (d) => ((d - d0) / (d1 - d0)) * 100
  const p = day != null ? pct(day) : state.progress * 100
  const started = state.progress > 0

  // level band: one segment per frame interval, coloured by the tracked level
  const seg = model.tl.frames.map((f, i) => ({ a: model.days[i], b: model.days[i + 1] ?? d1, level: f.level }))
  const years = []
  for (let yr = +state.span.start.slice(0, 4) + 1; yr <= +state.span.end.slice(0, 4); yr++) years.push({ yr, d: isoToOrd(`${yr}-01-01`) })

  const seekFromEvent = (e) => {
    const r = bar.current.getBoundingClientRect()
    controls.seek((e.clientX - r.left) / r.width)
  }

  return (
    <section className="panel absolute z-10 left-[364px] right-[344px] bottom-3 px-4 pt-3 pb-2.5 max-[1100px]:left-3 max-[1100px]:right-3">
      <div className="flex items-center gap-3">
        <button className="btn btn-primary w-[84px]" onClick={() => (state.running ? controls.pause() : controls.play(speed, autopilot))}>
          {state.running ? 'Pause' : state.finished ? 'Replay' : started ? 'Resume' : 'Play'}
        </button>
        <button className="btn" onClick={controls.reset} disabled={!started}>Reset</button>
        <div className="flex rounded-md overflow-hidden border border-[#323a45]" role="group" aria-label="Speed">
          {[0.5, 1, 2, 4].map((v) => (
            <button key={v} aria-pressed={speed === v} onClick={() => { setSpeed(v); controls.setSpeed(v) }}
              className={`px-2 py-1 text-xs num ${speed === v ? 'bg-ink text-void font-semibold' : 'text-muted hover:bg-raise'}`}>{v}x</button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-[12px] text-muted" title="Off: public alerts wait for an operator to approve them">
          <input type="checkbox" checked={autopilot} disabled={started} onChange={(e) => setAutopilot(e.target.checked)} />
          Auto-approve public alerts
        </label>
        <div className="ml-auto text-right">
          <div className="num text-[22px] font-medium leading-none tracking-tight">{day != null ? fmtDate(ordToIso(day)) : ''}</div>
          <div className="text-muted text-[11px] mt-0.5">server clock, {Math.round((d1 - d0) / 365.25)} years in {state.duration_s / state.speed | 0}s</div>
        </div>
      </div>

      <div ref={bar} className="relative mt-3 h-11 cursor-pointer select-none"
        onPointerDown={(e) => { setDrag(true); e.currentTarget.setPointerCapture(e.pointerId); seekFromEvent(e) }}
        onPointerMove={(e) => drag && seekFromEvent(e)} onPointerUp={() => setDrag(false)}
        role="slider" aria-label="Replay position" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)}>
        {/* level band */}
        <div className="absolute inset-x-0 top-4 h-2.5 rounded-sm overflow-hidden bg-line">
          {seg.map((s, i) => (
            <div key={i} className="absolute top-0 bottom-0" style={{ left: `${pct(s.a)}%`, width: `${Math.max(pct(s.b) - pct(s.a), 0.15)}%`, background: LEVEL_STYLE[s.level].dot, opacity: s.level === 'NORMAL' ? 0.45 : 0.9 }} />
          ))}
          <div className="absolute top-0 bottom-0 right-0 bg-[#0b0e12]/60" style={{ left: `${p}%` }} />
        </div>
        {/* observation ticks */}
        {model.days.map((d, i) => <div key={i} className="absolute top-[30px] w-px h-1.5 bg-[#5b6573]" style={{ left: `${pct(d)}%` }} />)}
        {/* alert crossings (where the level escalated) */}
        {model.tl.events.filter((e) => e.escalation).map((e) => (
          <div key={e.frame} className="absolute top-0 -translate-x-1/2 flex flex-col items-center" style={{ left: `${pct(model.days[e.frame])}%` }}
            title={`${fmtDate(e.date)}: ${LEVEL_STYLE[e.from].label} to ${LEVEL_STYLE[e.to].label}`}>
            <div className="size-2.5 rotate-45 border border-[#0b0e12]" style={{ background: LEVEL_STYLE[e.to].dot }} />
          </div>
        ))}
        {/* documented real events */}
        {model.events.map((e) => (
          <div key={e.date} className="absolute -top-0.5 bottom-1 -translate-x-1/2 flex flex-col items-center" style={{ left: `${pct(e.day)}%` }} title={e.detail}>
            <div className="text-[10px] font-semibold text-[#ff8a80] whitespace-nowrap -mt-3.5">{e.label}</div>
            <div className="w-px flex-1 bg-[#ff2d55]" />
          </div>
        ))}
        {/* playhead */}
        <div className="absolute top-2 h-[26px] w-0.5 -ml-px bg-ink rounded" style={{ left: `${p}%` }} />
        {years.map(({ yr, d }) => (
          <div key={yr} className="absolute bottom-[-4px] -translate-x-1/2 text-[10px] text-muted num" style={{ left: `${pct(d)}%` }}>{yr}</div>
        ))}
      </div>
    </section>
  )
}

/* --------------------------------------------------------------- notifications */
const STATUS = {
  simulated: { text: 'Simulated delivery', cls: 'bg-[#232a33] text-[#aeb6c0]' },
  sent: { text: 'Delivered to SNS', cls: 'bg-[#13301f] text-[#7fdb9a]' },
  pending_approval: { text: 'Awaiting approval', cls: 'bg-[#3a3010] text-[#f5d06b]' },
  rejected: { text: 'Rejected', cls: 'bg-[#232a33] text-[#aeb6c0]' },
  failed: { text: 'Delivery failed', cls: 'bg-[#45161a] text-[#ff8a80]' },
}

function Notifications({ alerts, system, opKey, onOpKey, onDecide, error }) {
  const latestPublic = [...alerts].reverse().find((a) => a.audience === 'public' && ['sent', 'simulated'].includes(a.status))
  const live = system?.sns?.mode === 'live'
  return (
    <aside className="panel absolute z-10 right-3 top-[60px] bottom-3 w-[320px] overflow-y-auto p-4 max-[1100px]:hidden">
      <div className="panel-title">Notifications</div>
      <p className={`text-[11px] mt-1 ${live ? 'text-[#7fdb9a]' : 'text-muted'}`}>{system?.sns?.label ?? '...'}</p>

      <div className="mt-3 mx-auto w-[236px] rounded-[30px] border-[6px] border-[#2b323b] bg-[#06080a] p-3 pt-6 min-h-[178px] relative">
        <div className="absolute top-2 left-1/2 -translate-x-1/2 h-1.5 w-14 rounded-full bg-[#2b323b]" />
        <div className="text-center text-[10px] text-muted mb-2">GLOF-ALERT</div>
        {latestPublic ? (
          <div key={latestPublic.id} className="slide-in rounded-2xl rounded-tl-md px-3 py-2 text-[11px] leading-snug whitespace-pre-wrap"
            style={latestPublic.level === 'EVACUATE' ? { background: '#b3122a', color: '#fff' } : { background: '#1f262e' }}>
            {latestPublic.message}
          </div>
        ) : <div className="text-center text-muted text-[11px] mt-9 px-4">Public alerts appear here when the replay crosses Warning.</div>}
      </div>

      <div className="flex items-baseline justify-between mt-5 mb-2">
        <div className="panel-title">Dispatch log</div>
        <div className="text-muted text-[11px]">{alerts.length} message{alerts.length === 1 ? '' : 's'}</div>
      </div>
      {alerts.some((a) => a.status === 'pending_approval') && (
        <input type="password" value={opKey} onChange={(e) => onOpKey(e.target.value)} placeholder="Operator key to approve"
          aria-label="Operator key" className="field w-full mb-2" autoComplete="off" />
      )}
      {error && <p className="text-[#ff8a80] mb-2 text-[12px]">{error}</p>}
      <ol className="space-y-2">
        {[...alerts].reverse().map((a) => {
          const ls = LEVEL_STYLE[a.level], st = STATUS[a.status] ?? STATUS.simulated
          return (
            <li key={a.id} className="slide-in rounded-lg border border-line bg-raise p-2.5">
              <div className="flex items-center gap-2">
                <span className="chip" style={{ background: ls.bg, color: ls.fg }}>{ls.label}</span>
                <span className="font-medium text-[12px]">{a.audience_label}</span>
                <span className="num text-muted text-[11px] ml-auto">{a.date ? fmtMonth(a.date) : ''}</span>
              </div>
              <p className="mt-1.5 text-[11.5px] leading-snug text-[#c5ccd4] line-clamp-3">{a.message}</p>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <span className={`chip ${st.cls}`}>{st.text}</span>
                {a.message_ids?.[0] && <span className="num text-[10px] text-muted truncate max-w-[150px]" title={a.message_ids.join(', ')}>id {a.message_ids[0].slice(0, 8)}</span>}
                {a.status === 'pending_approval' && (
                  <span className="ml-auto flex gap-1.5">
                    <button className="btn btn-primary py-0.5 text-xs" disabled={!opKey} onClick={() => onDecide(a.id, 'approve')}>Approve</button>
                    <button className="btn py-0.5 text-xs" disabled={!opKey} onClick={() => onDecide(a.id, 'reject')}>Reject</button>
                  </span>
                )}
              </div>
            </li>
          )
        })}
        {!alerts.length && <li className="text-muted text-[12px]">Press play. As the replay passes a date where the outline crossed a risk threshold, the alert is dispatched automatically by the server.</li>}
      </ol>
    </aside>
  )
}

/* --------------------------------------------------------------- toast */
function Toast({ alert }) {
  if (!alert) return null
  const st = LEVEL_STYLE[alert.level]
  return (
    <div key={alert.id} className="slide-in absolute z-30 top-[60px] left-1/2 -translate-x-1/2 rounded-lg px-4 py-2.5 shadow-2xl flex items-center gap-3"
      style={{ background: st.bg, color: st.fg, border: `1px solid ${st.dot}` }} role="status">
      <span className="font-semibold">{st.label} issued</span>
      <span className="text-[12px] opacity-90">{alert.date ? fmtDate(alert.date) : ''}, {alert.audience_label.toLowerCase()} notified ({alert.status === 'sent' ? 'via SNS' : alert.status.replace('_', ' ')})</span>
    </div>
  )
}

/* --------------------------------------------------------------- view */
export default function ArchiveView({ archive, replay, system, orbit }) {
  const model = useArchiveModel(archive)
  const [opKey, setOpKey] = useState('')
  const [err, setErr] = useState(null)
  const [layers, setLayers] = useState({ rings: true, ice: true })
  const [toast, setToast] = useState(null)
  const seen = useRef(new Set())

  const day = replay.day ?? replay.state?.day ?? null
  const i = model && day != null ? frameAt(model.days, day) : -1
  const fi = Math.max(i, 0)
  const cur = model?.tl.frames[fi]
  const frame = model?.frames[fi]

  // shoreline: hold the current real outline, then morph into the next one
  const water = useMemo(() => {
    if (!model || !frame) return null
    let t = 0
    const next = model.days[fi + 1]
    if (i >= 0 && next != null) t = Math.max(0, 1 - (next - day) / Math.min(MORPH_DAYS, next - model.days[fi]))
    const geom = model.morph(fi, fi + 1 < model.frames.length ? fi + 1 : null, t)
    return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: geom }, ...model.context[fi]] }
  }, [model, frame, fi, i, day])

  const rings = useMemo(() => {
    if (!model || day == null) return null
    const yr = ordToIso(day).slice(0, 4)
    const past = model.rings.filter((r) => r.yr < yr || (r.yr === yr && model.days[r.i] <= day))
    return {
      type: 'FeatureCollection',
      features: past.map((r, k) => ({ type: 'Feature', properties: { color: r.color, opacity: 0.45 + 0.5 * (k / Math.max(past.length - 1, 1)) }, geometry: model.frames[r.i].lake.geometry })),
    }
  }, [model, day])

  const alerts = replay.state?.alerts ?? []
  useEffect(() => {
    for (const a of alerts) {
      if (seen.current.has(a.id)) continue
      seen.current.add(a.id)
      if (a.audience === 'public' || a.level === 'WATCH' || a.level === 'ADVISORY') setToast(a)
    }
    if (!alerts.length) seen.current.clear()
  }, [alerts])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 4200); return () => clearTimeout(t) }, [toast])

  const decide = async (id, verb) => {
    setErr(null)
    try {
      const r = await fetch(`${BASE}/api/alerts/${id}/${verb}`, { method: 'POST', headers: { 'X-API-Key': opKey } })
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`)
    } catch (e) { setErr(e.message) }
  }

  if (!model) return null
  const level = cur?.level ?? 'NORMAL'
  const lakeMeta = archive.lake

  return (
    <>
      <MapViewer key={lakeMeta.id} lake={lakeMeta} water={water} ice={layers.ice ? frame?.ice : null} rings={rings}
        baseline={model.baseFrame ? { type: 'Feature', properties: {}, geometry: model.baseFrame.lake.geometry } : null}
        showRings={layers.rings} orbit={orbit} waterEdge={level !== 'NORMAL' ? LEVEL_STYLE[level].dot : '#e8f3ff'} />
      {level === 'EVACUATE' || level === 'WARNING'
        ? <div key={level} className="pulse-edge pointer-events-none absolute inset-0 z-[5]" style={{ '--pulse': `${LEVEL_STYLE[level].dot}88` }} /> : null}

      {/* scene caption + legend, top-centre of the map */}
      <div className="absolute z-10 top-[60px] left-1/2 -translate-x-1/2 text-center pointer-events-none max-[1100px]:hidden">
        <div className="text-[11px] text-muted">Outline shown</div>
        <div className="num text-[13px]">{frame ? `${fmtDate(frame.date)}  ·  Sentinel-2 ${frame.scene.split('_')[0]}  ·  cloud ${(frame.stats.cloud_fraction * 100).toFixed(1)}%` : ''}</div>
      </div>
      <div className="panel absolute z-10 right-[344px] bottom-[148px] px-3 py-2 text-[11px] space-y-1 max-[1100px]:hidden">
        <label className="flex items-center gap-2"><input type="checkbox" checked={layers.rings} onChange={() => setLayers((l) => ({ ...l, rings: !l.rings }))} />
          <span className="inline-flex gap-px">{RING_RAMP.filter((_, k) => k % 2 === 0).map((c) => <span key={c} className="w-2 h-0.5" style={{ background: c }} />)}</span>
          Past seasons (old to new)</label>
        <div className="flex items-center gap-2 pl-[22px]"><span className="w-4 border-t border-dashed border-[#f5d06b]" />Baseline {model.tl.baseline.label}</div>
        <label className="flex items-center gap-2"><input type="checkbox" checked={layers.ice} onChange={() => setLayers((l) => ({ ...l, ice: !l.ice }))} />
          <span className="w-3 h-2 rounded-sm bg-[#e6eef6]/60" />Snow and ice</label>
        <div className="flex items-center gap-2 pl-[22px]"><span className="w-3 h-2 rounded-sm bg-water" />Water (this scene)</div>
      </div>

      <RiskPanel model={model} cur={cur} day={day} archive={archive} />
      <Timeline model={model} state={replay.state} day={day} controls={replay} />
      <Notifications alerts={alerts} system={system} opKey={opKey} onOpKey={setOpKey} onDecide={decide} error={err} />
      <Toast alert={toast} />
    </>
  )
}
