import { useEffect, useMemo, useRef, useState } from 'react'
import MapViewer from './MapViewer'
import AreaChart from './AreaChart'
import GlacierCompare from './GlacierCompare'
import { useArchiveIce } from '../hooks/useApi'
import { createMorpher } from '../utils/morph'
import { LEVELS, LEVEL_STYLE, fmtDate, fmtMonth, isoToOrd, ordToIso } from '../utils/format'

const BASE = import.meta.env.VITE_API_BASE ?? ''
const MORPH_DAYS = 18 // shoreline moves over the last N days before the next real observation
const RING_RAMP = ['#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef', '#9ec5f4', '#b7d3f6']

// What each level means for the people involved, in plain words.
const LEVEL_MEANING = {
  NORMAL: 'Nothing unusual in the satellite record on this date.',
  WATCH: 'The lake is growing. At this level the control room is asked to keep a close eye on it.',
  ADVISORY: 'The growth has kept going. At this level district officials are asked to get ready.',
  WARNING: 'The lake edge has moved a long way. At this level people living downstream are warned.',
  EVACUATE: 'Signs of an outburst. At this level people downstream are told to move to high ground.',
}

/* ------------------------------------------------------------------ data */
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
    // one outline per season: the frame nearest 15 Oct (end of melt, lake at its largest)
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
    const events = (archive.lake.events ?? []).map((e) => ({ ...e, day: isoToOrd(e.date) }))
    // other water touching the cut-out edge would drape down the slab walls: hide it
    const [w, s, e, n] = archive.lake.ice_bbox ?? archive.lake.bbox, m = 0.0008
    const inside = (f) => JSON.stringify(f.geometry.coordinates).match(/-?\d+\.\d+/g).every((v, k) => {
      const x = +v
      return k % 2 === 0 ? x > w + m && x < e - m : x > s + m && x < n - m
    })
    const context = frames.map((f) => f.water.features.filter(inside))
    return { frames, tl, days, morph, rings, baseFrame, series, events, context }
  }, [archive])
}

/* yearly perennial ice: a composite "exists" from the start of its late-season window */
export function useGlacierModel(ice) {
  return useMemo(() => {
    const pi = ice?.perennial_ice
    if (!pi) return null
    const start = pi.method.season.split(',')[0].trim()
    const years = pi.years.map((y) => ({ ...y, day: isoToOrd(`${y.year}-${start}`) }))
    const base = years.find((y) => y.year === pi.baseline_year)
    const rgi = ice.ice_reference
    const rgiDates = rgi ? [...new Set(rgi.features.map((f) => f.properties.src_date))].sort() : []
    return { pi, years, base, rgi, rgiDates, start }
  }, [ice])
}

export const glacierAt = (g, day) => {
  if (!g || day == null) return null
  let cur = null
  for (const y of g.years) if (y.day <= day) cur = y
  return cur
}

/* close a popover or sheet on Escape or a click outside it */
function useDismiss(open, onClose, { outside = true } = {}) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const key = (e) => e.key === 'Escape' && onClose()
    const click = (e) => ref.current && !ref.current.contains(e.target) && onClose()
    window.addEventListener('keydown', key)
    const t = outside ? setTimeout(() => window.addEventListener('pointerdown', click), 0) : 0
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('pointerdown', click); clearTimeout(t) }
  }, [open, onClose, outside])
  return ref
}

/* ------------------------------------------------------------------ summary card */
function Stat({ label, value, unit, note, warn }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`num text-[22px] leading-tight mt-0.5 ${warn ? 'text-[#ff8a80]' : ''}`}>
        {value}<span className="text-muted text-[13px]"> {unit}</span>
      </div>
      {note && <div className="text-[12px] text-muted mt-0.5">{note}</div>}
    </div>
  )
}

function SummaryCard({ archive, model, cur, day, started, glacier, gYear }) {
  const [charts, setCharts] = useState(false)
  const [how, setHow] = useState(false)
  const level = cur?.level ?? 'NORMAL'
  const st = LEVEL_STYLE[level]
  const ind = cur?.indicators
  const th = model.tl.thresholds
  const base = model.tl.baseline.area_m2 / 1e4
  const held = cur && LEVELS.indexOf(cur.level) > LEVELS.indexOf(cur.raw_level) && LEVELS.indexOf(cur.level) >= 3
  const span = [model.days[0] - 25, model.days.at(-1) + 10]

  return (
    <section className="panel absolute z-10 left-4 top-[72px] w-[360px] max-h-[calc(100%-180px)] overflow-y-auto max-[1100px]:hidden" aria-label="Lake status">
      <div className="p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h1 className="text-[17px] font-semibold tracking-tight">{archive.lake.name}</h1>
          <span className="label">{archive.lake.country}</span>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <span key={level} className="slide-in rounded-lg px-3 py-1 text-[18px] font-semibold" style={{ background: st.bg, color: st.fg }}>{st.label}</span>
          <span className="text-[13px] text-muted">{day != null ? `on ${fmtDate(ordToIso(day))}` : ''}</span>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-[#d3d9e0]">{LEVEL_MEANING[level]}</p>
        {held && <p className="mt-2 text-[13px] text-muted">The warning stays on until an operator clears it, even if readings ease.</p>}
        {cur?.flags?.map((f, i) => <p key={i} className="mt-2 text-[13px] text-[#f5d06b]">{f}</p>)}

        {ind && (
          <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4">
            <Stat label="Lake size" value={(ind.smoothed_area_m2 / 1e4).toFixed(0)} unit="ha"
              note={`${ind.growth_pct >= 0 ? '+' : ''}${ind.growth_pct.toFixed(0)}% vs ${model.tl.baseline.label}`} warn={ind.growth_pct >= th.growth_watch_pct} />
            <Stat label="Shoreline moved" value={ind.advance_m.toFixed(0)} unit="m" note={`since ${model.tl.baseline.label}`} warn={ind.advance_m >= th.advance_watch_m} />
            {glacier && gYear && (
              <Stat label={`Glacier ice ${gYear.year}`} value={gYear.ice_area_km2.toFixed(1)} unit="km²"
                note={gYear.season_complete === false ? 'season still in progress' : `${gYear.lost_km2.toFixed(1)} km² lost since ${glacier.pi.baseline_year}`} />
            )}
          </div>
        )}

        {!started && (
          <ol className="mt-5 space-y-2 text-[13px] text-[#c5ccd4] list-decimal pl-4 marker:text-muted">
            <li>Press <b className="text-ink font-medium">Play</b> to watch nine years of real satellite images of this lake.</li>
            <li>When the lake crosses a risk level, an alert is sent automatically. Open <b className="text-ink font-medium">Alerts</b> to see it.</li>
            <li>Use <b className="text-ink font-medium">Compare years</b> to put two years side by side.</li>
          </ol>
        )}
      </div>

      <div className="border-t border-white/[.06] px-5 py-3 flex gap-4 text-[13px]">
        <button className="text-[#9ec5f4] hover:text-white" aria-expanded={charts} onClick={() => setCharts((v) => !v)}>{charts ? 'Hide charts' : 'Show charts'}</button>
        <button className="text-[#9ec5f4] hover:text-white" aria-expanded={how} onClick={() => setHow((v) => !v)}>{how ? 'Hide how it works' : 'How it works'}</button>
      </div>

      {charts && (
        <div className="px-5 pb-5 space-y-5">
          <div>
            <h2 className="panel-title mb-2">Lake size, hectares</h2>
            <AreaChart label="Lake size" points={model.series} span={span} playDay={day} color="#3d9bff" unit="ha"
              thresholds={[
                { value: base, label: `${model.tl.baseline.label} size`, color: '#f5d06b', align: 'left' },
                { value: base * (1 + th.growth_watch_pct / 100), label: `+${th.growth_watch_pct}%`, color: LEVEL_STYLE.WATCH.dot },
                { value: base * (1 + th.growth_warning_pct / 100), label: `+${th.growth_warning_pct}%`, color: LEVEL_STYLE.WARNING.dot },
              ]} />
            <p className="text-[12px] text-muted mt-1">Dots are single images; the line smooths out cloud and shadow noise.</p>
          </div>
          {glacier && (
            <div>
              <h2 className="panel-title mb-2">Change since {glacier.pi.baseline_year}, percent</h2>
              <AreaChart label="Change since baseline" span={span} playDay={day} height={110}
                series={[
                  { key: 'lake', label: 'Lake size', color: '#3d9bff',
                    points: model.tl.frames.map((t, i) => ({ day: model.days[i], value: t.indicators.growth_pct })),
                    fmt: (p) => `lake ${p.value >= 0 ? '+' : ''}${p.value.toFixed(1)}%` },
                  { key: 'ice', label: 'Glacier ice', color: '#c9d4df', step: true,
                    points: glacier.years.map((y) => ({ day: y.day, value: (y.ice_area_km2 / glacier.base.ice_area_km2 - 1) * 100, hollow: y.low_confidence || y.season_complete === false, title: `${y.year}${y.season_complete === false ? ' (in progress)' : ''}`, area: y.ice_area_km2 })),
                    fmt: (p) => `ice ${p.value >= 0 ? '+' : ''}${p.value.toFixed(1)}% (${p.area.toFixed(1)} km²)` },
                ]} />
              <p className="text-[12px] text-muted mt-1">Hollow points: few images or a season still in progress. Big jumps in ice are usually fresh snow.</p>
            </div>
          )}
        </div>
      )}

      {how && (
        <div className="px-5 pb-5 text-[13px] text-[#c5ccd4] space-y-2 leading-relaxed">
          <p><b className="text-ink font-medium">Images:</b> Sentinel-2 satellite images from the AWS Open Data registry, read straight from Amazon S3.</p>
          <p><b className="text-ink font-medium">Outlines:</b> water and ice are traced from the pixels of every clear image. Between images the shoreline is drawn smoothly for display only.</p>
          <p><b className="text-ink font-medium">Daily check:</b> an AWS Lambda function, run every day by Amazon EventBridge, looks for new images and stores the outlines in Amazon S3.</p>
          <p><b className="text-ink font-medium">Alerts:</b> sent through Amazon SNS to email and SMS, each audience getting only its own messages.</p>
          <p className="text-muted">Risk levels use placeholder thresholds that still need checking with the local disaster authority.</p>
        </div>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------ legend */
function Swatch({ kind, color }) {
  if (kind === 'fill') return <span className="w-4 h-2.5 rounded-[3px] shrink-0" style={{ background: color }} />
  if (kind === 'outline') return <span className="w-4 h-2.5 rounded-[3px] shrink-0 border" style={{ borderColor: color }} />
  if (kind === 'ramp') return <span className="w-4 h-[3px] rounded shrink-0" style={{ background: `linear-gradient(90deg, ${RING_RAMP[0]}, ${RING_RAMP.at(-1)})` }} />
  return <span className="w-4 shrink-0 border-t-2" style={{ borderColor: color, borderStyle: kind }} />
}

function Row({ swatch, children, sub }) {
  return (
    <div className="flex gap-2.5">
      <span className="h-[18px] flex items-center">{swatch}</span>
      <span>{children}{sub && <span className="block text-muted text-[12px]">{sub}</span>}</span>
    </div>
  )
}

function Toggle({ checked, onChange, children }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer font-medium">
      <input type="checkbox" checked={checked} onChange={onChange} />{children}
    </label>
  )
}

function Legend({ layers, setLayers, glacier, gYear, ice, hasIce, baselineLabel }) {
  const [open, setOpen] = useState(true)
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }))
  const by = glacier?.pi.baseline_year ?? 2018
  return (
    <section className="panel absolute z-10 right-4 bottom-[112px] w-[288px] text-[13px] max-h-[calc(100%-300px)] overflow-y-auto max-[1100px]:hidden" aria-label="Map legend">
      <button className="w-full flex items-center justify-between px-4 py-3 font-semibold" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        Map key <span className="text-muted font-normal text-[12px]">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3">
          <div className="space-y-1.5">
            <Row swatch={<Swatch kind="fill" color="#3d9bff" />}>Lake on this image</Row>
            <Toggle checked={layers.rings} onChange={() => toggle('rings')}>Lake in past years</Toggle>
            {layers.rings && (
              <div className="pl-6 space-y-1.5">
                <Row swatch={<Swatch kind="ramp" />}>One line per year, darker is older</Row>
                <Row swatch={<Swatch kind="dashed" color="#f5d06b" />}>{baselineLabel} lake (baseline)</Row>
              </div>
            )}
          </div>

          {hasIce && (
            <div className="space-y-1.5 pt-3 border-t border-white/[.06]">
              <Toggle checked={layers.glacier} onChange={() => toggle('glacier')}>
                Glacier ice{gYear && layers.glacier ? `, ${gYear.year}` : ''}
              </Toggle>
              {layers.glacier && (
                <div className="pl-6 space-y-1.5">
                  <Row swatch={<Swatch kind="fill" color="rgb(230 238 246 / .45)" />}>Ice that lasted through autumn</Row>
                  <Row swatch={<Swatch kind="dashed" color="#ffffff" />}>{by} ice (baseline)</Row>
                  <Row swatch={<Swatch kind="fill" color="#ec7f2c" />}>Ice lost since {by}</Row>
                  <Row swatch={<Swatch kind="outline" color="#3fd39a" />} sub="usually fresh snow, not real growth">Ice gained since {by}</Row>
                  <p className="text-[12px] text-muted leading-snug pt-1">Seasonal snow removed as far as possible. Ice under rock debris is not detected, so the glacier tongue may look smaller than it is.</p>
                  {gYear?.low_confidence && <p className="text-[12px] text-[#f5d06b]">Few clear images this year: low confidence.</p>}
                  {gYear?.season_complete === false && <p className="text-[12px] text-[#f5d06b]">{gYear.year} season still in progress.</p>}
                  {ice.loading && <p className="text-[12px] text-muted">Loading glacier layer...</p>}
                  {ice.error && <p className="text-[12px] text-[#ff8a80]">{ice.error.message}</p>}
                </div>
              )}
              <Toggle checked={layers.rgi} onChange={() => toggle('rgi')}>Glacier inventory outline</Toggle>
              {layers.rgi && (
                <div className="pl-6">
                  <Row swatch={<Swatch kind="dotted" color="#9085e9" />}
                    sub="independent dataset, different method; reference only">
                    RGI 7.0, image of {glacier?.rgiDates.length ? glacier.rgiDates.map(fmtDate).join(', ') : '...'}
                  </Row>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------ alerts sheet */
const STATUS = {
  simulated: { text: 'Simulated', cls: 'text-[#aeb6c0]' },
  sent: { text: 'Real SMS accepted by Amazon SNS', cls: 'text-[#7fdb9a]' },
  pending_approval: { text: 'Waiting for approval', cls: 'text-[#f5d06b]' },
  rejected: { text: 'Rejected', cls: 'text-[#aeb6c0]' },
  failed: { text: 'Delivery failed', cls: 'text-[#ff8a80]' },
}

function AlertsSheet({ alerts, system, opKey, onOpKey, onDecide, error, onClose, autopilot, setAutopilot, started }) {
  const ref = useDismiss(true, onClose, { outside: false })
  const latestPublic = [...alerts].reverse().find((a) => a.audience === 'public' && ['sent', 'simulated'].includes(a.status))
  const live = system?.sns?.mode === 'live'
  return (
    <aside ref={ref} className="sheet sheet-in absolute z-30 right-0 top-14 bottom-0 w-[400px] max-w-full overflow-y-auto" aria-label="Alerts">
      <div className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-[17px] font-semibold">Alerts</h2>
            <p className={`text-[13px] mt-1 ${live ? 'text-[#7fdb9a]' : 'text-muted'}`}>
              {live ? system.sns.label : 'Practice mode: messages are prepared but not sent.'}
            </p>
            {live && (
              <p className="text-[13px] mt-1 text-muted">
                When the replay reaches Warning, one real drill SMS goes to the registered phones automatically, once per run.
                {system.demo_sms && !system.demo_sms.armed && system.demo_sms.sent_at ? ` Last sent ${new Date(system.demo_sms.sent_at).toLocaleTimeString()}.` : ''}
              </p>
            )}
          </div>
          <button className="btn" onClick={onClose}>Close</button>
        </div>

        <label className="mt-4 flex gap-3 text-[13px] cursor-pointer">
          <input type="checkbox" className="mt-0.5" checked={autopilot} disabled={started} onChange={(e) => setAutopilot(e.target.checked)} />
          <span>Send public warnings without waiting for an operator<span className="block text-muted">{started ? 'Reset the replay to change this.' : 'Untick to require approval first.'}</span></span>
        </label>

        {latestPublic && (
          <div className="mt-6">
            <h3 className="text-[13px] font-medium mb-2">What residents receive</h3>
            <div key={latestPublic.id} className="slide-in rounded-2xl rounded-tl-md px-4 py-3 text-[13px] leading-snug whitespace-pre-wrap"
              style={latestPublic.level === 'EVACUATE' ? { background: '#b3122a', color: '#fff' } : { background: '#1d242c' }}>
              {latestPublic.message}
            </div>
          </div>
        )}

        <h3 className="text-[13px] font-medium mt-6 mb-3">Sent so far</h3>
        {alerts.some((a) => a.status === 'pending_approval') && (
          <input type="password" value={opKey} onChange={(e) => onOpKey(e.target.value)} placeholder="Operator key"
            aria-label="Operator key" className="field w-full mb-3" autoComplete="off" />
        )}
        {error && <p className="text-[#ff8a80] mb-3 text-[13px]">{error}</p>}
        <ol className="space-y-5">
          {[...alerts].reverse().map((a) => {
            const ls = LEVEL_STYLE[a.level], stt = STATUS[a.status] ?? STATUS.simulated
            return (
              <li key={a.id} className="slide-in">
                <div className="flex items-center gap-2 text-[13px]">
                  <span className="chip" style={{ background: ls.bg, color: ls.fg }}>{ls.label}</span>
                  <span className="font-medium">{a.audience_label}</span>
                  <span className="text-muted ml-auto">{a.date ? fmtMonth(a.date) : ''}</span>
                </div>
                <p className="mt-1.5 text-[13px] leading-snug text-[#c5ccd4] line-clamp-3">{a.message}</p>
                <div className="flex items-center gap-3 mt-1.5 text-[12px] flex-wrap">
                  <span className={stt.cls}>{stt.text}</span>
                  {a.message_ids?.[0] && <span className="num text-muted" title={a.message_ids.join(', ')}>MessageId {a.message_ids[0]}</span>}
                  {a.accepted_at && <span className="text-muted">{new Date(a.accepted_at).toLocaleTimeString()}</span>}
                  {a.status === 'pending_approval' && (
                    <span className="ml-auto flex gap-2">
                      <button className="btn btn-primary py-0.5 text-xs" disabled={!opKey} onClick={() => onDecide(a.id, 'approve')}>Approve</button>
                      <button className="btn py-0.5 text-xs" disabled={!opKey} onClick={() => onDecide(a.id, 'reject')}>Reject</button>
                    </span>
                  )}
                </div>
                {a.note && <p className="mt-1 text-[12px] text-muted">{a.note}</p>}
              </li>
            )
          })}
          {!alerts.length && <li className="text-muted text-[13px]">Nothing yet. Press Play; alerts appear here as the lake crosses each risk level.</li>}
        </ol>
      </div>
    </aside>
  )
}

/* ------------------------------------------------------------------ timeline */
function Timeline({ model, state, day, controls, speed, setSpeed, autopilot, onCompare, canCompare }) {
  const bar = useRef(null)
  const [drag, setDrag] = useState(false)
  if (!state) return null
  const { start_day: d0, end_day: d1 } = state.span
  const pct = (d) => ((d - d0) / (d1 - d0)) * 100
  const p = day != null ? pct(day) : state.progress * 100
  const started = state.progress > 0
  const seg = model.tl.frames.map((f, i) => ({ a: model.days[i], b: model.days[i + 1] ?? d1, level: f.level }))
  const years = []
  for (let yr = +state.span.start.slice(0, 4) + 1; yr <= +state.span.end.slice(0, 4); yr++) years.push({ yr, d: isoToOrd(`${yr}-01-01`) })
  const seek = (e) => { const r = bar.current.getBoundingClientRect(); controls.seek((e.clientX - r.left) / r.width) }

  return (
    <section className="panel absolute z-10 left-4 right-4 bottom-4 px-5 py-4 flex items-center gap-5" aria-label="Replay">
      <button className="btn btn-primary w-[88px] h-10 text-[14px]" onClick={() => (state.running ? controls.pause() : controls.play(speed, autopilot))}>
        {state.running ? 'Pause' : state.finished ? 'Replay' : started ? 'Resume' : 'Play'}
      </button>
      <div className="w-[128px] shrink-0">
        <div className="num text-[19px] leading-none tracking-tight">{day != null ? fmtDate(ordToIso(day)) : ''}</div>
        <button className="text-[12px] text-muted hover:text-ink mt-1" onClick={controls.reset} disabled={!started}>{started ? 'Back to start' : '9 years in 90 s'}</button>
      </div>

      <div ref={bar} className="relative flex-1 h-12 cursor-pointer select-none touch-none"
        onPointerDown={(e) => { setDrag(true); e.currentTarget.setPointerCapture(e.pointerId); seek(e) }}
        onPointerMove={(e) => drag && seek(e)} onPointerUp={() => setDrag(false)}
        role="slider" aria-label="Replay position" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)}>
        <div className="absolute inset-x-0 top-[18px] h-2 rounded-full overflow-hidden bg-white/10">
          {seg.map((s, i) => (
            <div key={i} className="absolute top-0 bottom-0" style={{ left: `${pct(s.a)}%`, width: `${Math.max(pct(s.b) - pct(s.a), 0.15)}%`, background: LEVEL_STYLE[s.level].dot, opacity: s.level === 'NORMAL' ? 0.5 : 0.95 }} />
          ))}
          <div className="absolute top-0 bottom-0 right-0 bg-[#0b0e12]/65" style={{ left: `${p}%` }} />
        </div>
        {model.events.map((e) => (
          <div key={e.date} className="absolute top-0 -translate-x-1/2 text-[11px] font-medium text-[#ff8a80] whitespace-nowrap" style={{ left: `${pct(e.day)}%` }} title={e.detail}>
            {e.label}
          </div>
        ))}
        {model.events.map((e) => <div key={`l${e.date}`} className="absolute top-[14px] h-4 w-px bg-[#ff8a80]" style={{ left: `${pct(e.day)}%` }} />)}
        <div className="absolute top-[11px] size-[22px] -ml-[11px] rounded-full bg-ink border-4 border-[#10141a] shadow" style={{ left: `${p}%` }} />
        {years.map(({ yr, d }) => (
          <div key={yr} className="absolute top-[32px] -translate-x-1/2 text-[11px] text-muted num" style={{ left: `${pct(d)}%` }}>{yr}</div>
        ))}
      </div>

      <label className="text-[13px] text-muted flex items-center gap-2">Speed
        <select className="field py-1" value={speed} onChange={(e) => { const v = +e.target.value; setSpeed(v); controls.setSpeed(v) }}>
          {[0.5, 1, 2, 4].map((v) => <option key={v} value={v}>{v}x</option>)}
        </select>
      </label>
      {canCompare && <button className="btn h-10" onClick={onCompare}>Compare years</button>}
    </section>
  )
}

/* ------------------------------------------------------------------ toast */
function Toast({ alert }) {
  if (!alert) return null
  const st = LEVEL_STYLE[alert.level]
  return (
    <div key={alert.id} className="slide-in absolute z-20 top-[72px] left-1/2 -translate-x-1/2 rounded-xl px-4 py-2.5 shadow-2xl text-[13px]"
      style={{ background: st.bg, color: st.fg, border: `1px solid ${st.dot}` }} role="status">
      <b className="font-semibold">{st.label}</b>: {alert.status === 'sent' ? 'real SMS accepted by Amazon SNS' : `${alert.audience_label.toLowerCase()} notified`}{alert.date ? `, ${fmtMonth(alert.date)}` : ''}
    </div>
  )
}

/* ------------------------------------------------------------------ view */
export default function ArchiveView({ archive, replay, system, orbit, onToggleOrbit }) {
  const model = useArchiveModel(archive)
  const [opKey, setOpKey] = useState('')
  const [err, setErr] = useState(null)
  const [layers, setLayers] = useState(() => ({ rings: true, glacier: false, rgi: new URLSearchParams(location.search).has('rgi') }))
  const [compare, setCompare] = useState(() => new URLSearchParams(location.search).has('compare'))
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [autopilot, setAutopilot] = useState(true)
  const [toast, setToast] = useState(null)
  const seen = useRef(new Set())
  const ice = useArchiveIce(archive.has_ice_layer && (layers.glacier || layers.rgi || compare) ? archive.lake.id : null)
  const glacier = useGlacierModel(ice.data)

  const day = replay.day ?? replay.state?.day ?? null
  const i = model && day != null ? frameAt(model.days, day) : -1
  const fi = Math.max(i, 0)
  const cur = model?.tl.frames[fi]
  const frame = model?.frames[fi]
  const gYear = glacierAt(glacier, day)
  const baselineFeature = useMemo(() => (model?.baseFrame ? { type: 'Feature', properties: {}, geometry: model.baseFrame.lake.geometry } : null), [model])
  const glacierLayers = useMemo(() => (layers.glacier && gYear ? {
    outline: gYear.outline, base: glacier.base.outline, lost: gYear.lost, gained: gYear.gained,
  } : null), [layers.glacier, gYear, glacier])

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

  const alerts = useMemo(() => replay.state?.alerts ?? [], [replay.state?.alerts])
  useEffect(() => {
    for (const a of alerts) {
      if (seen.current.has(a.id)) continue
      seen.current.add(a.id)
      if (!alertsOpen) setToast(a)  // eslint-disable-line react-hooks/set-state-in-effect
    }
    if (!alerts.length) seen.current.clear()
  }, [alerts, alertsOpen])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 4000); return () => clearTimeout(t) }, [toast])

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
  const started = (replay.state?.progress ?? 0) > 0
  const lastLevel = alerts.length ? LEVEL_STYLE[alerts.at(-1).level] : null

  return (
    <>
      <MapViewer key={lakeMeta.id} lake={lakeMeta} water={water} rings={rings} slabBbox={lakeMeta.ice_bbox}
        glacier={glacierLayers} rgi={layers.rgi ? glacier?.rgi : null} baseline={baselineFeature}
        showRings={layers.rings} orbit={orbit && !compare} waterEdge={level !== 'NORMAL' ? LEVEL_STYLE[level].dot : '#e8f3ff'} />
      {level === 'EVACUATE' || level === 'WARNING'
        ? <div key={level} className="pulse-edge pointer-events-none absolute inset-0 z-[5]" style={{ '--pulse': `${LEVEL_STYLE[level].dot}66` }} /> : null}

      <SummaryCard archive={archive} model={model} cur={cur} day={day} started={started} glacier={layers.glacier ? glacier : null} gYear={gYear} />

      <div className="absolute z-20 right-4 top-[72px] flex items-center gap-2 max-[1100px]:hidden">
        <button className="pill" onClick={onToggleOrbit} aria-pressed={orbit}>{orbit ? 'Stop turning' : 'Turn view'}</button>
        <button className="pill" aria-expanded={alertsOpen} onClick={() => setAlertsOpen((v) => !v)}>
          Alerts
          <span className="num rounded-full min-w-6 h-6 px-1.5 grid place-items-center text-[12px]"
            style={lastLevel ? { background: lastLevel.bg, color: lastLevel.fg } : { background: 'rgb(255 255 255 / .1)' }}>{alerts.length}</span>
        </button>
      </div>

      {frame && (
        <p className="absolute z-10 left-1/2 -translate-x-1/2 bottom-[104px] text-[12px] text-muted pointer-events-none max-[1100px]:hidden">
          Satellite image of {fmtDate(frame.date)}
        </p>
      )}

      <Legend layers={layers} setLayers={setLayers} glacier={glacier} gYear={gYear} ice={ice} hasIce={archive.has_ice_layer} baselineLabel={model.tl.baseline.label} />
      <Timeline model={model} state={replay.state} day={day} controls={replay} speed={speed} setSpeed={setSpeed} autopilot={autopilot}
        onCompare={() => setCompare(true)} canCompare={archive.has_ice_layer} />
      {alertsOpen && (
        <AlertsSheet alerts={alerts} system={system} opKey={opKey} onOpKey={setOpKey} onDecide={decide} error={err}
          onClose={() => setAlertsOpen(false)} autopilot={autopilot} setAutopilot={setAutopilot} started={started} />
      )}
      <Toast alert={toast} />
      {compare && glacier && <GlacierCompare lake={lakeMeta} glacier={glacier} model={model} onClose={() => setCompare(false)} />}
      {compare && !glacier && (
        <div className="absolute inset-0 z-40 grid place-items-center bg-void/80"><p className="text-muted">{ice.error ? ice.error.message : 'Loading glacier layer...'}</p></div>
      )}
    </>
  )
}
