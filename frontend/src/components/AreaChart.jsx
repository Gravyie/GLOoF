import { useMemo, useRef, useState } from 'react'
import { fmtDate, ordToIso } from '../utils/format'

const W = 300
const PAD = { l: 34, r: 8, t: 8, b: 18 }

function niceStep(span) {
  const raw = span / 4
  const p = 10 ** Math.floor(Math.log10(raw))
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? p * 10
}

/**
 * Time chart over the replay span, one shared y-axis. Past (left of the
 * playhead) is full strength, the future is faded so the history reveals as
 * the replay runs.
 *
 * Single series (original API): points=[{ day, value, raw }], color, unit.
 * Several series: series=[{ key, label, color, unit, points, step, fmt }]; a
 *   point may carry `hollow: true` (e.g. a low-confidence year).
 * thresholds: [{ value, label, color }]
 */
export default function AreaChart({ points, span, playDay, color, unit, thresholds = [], height = 112, label, series }) {
  const [hover, setHover] = useState(null)
  const ref = useRef(null)
  const [d0, d1] = span
  const all = useMemo(() => series ?? [{ key: 'v', label, color, unit, points }], [series, points, color, unit, label])

  const geo = useMemo(() => {
    const vals = all.flatMap((s) => s.points.flatMap((p) => [p.value, p.raw])).concat(thresholds.map((t) => t.value)).filter((v) => v != null)
    let lo = Math.min(...vals), hi = Math.max(...vals)
    const step = niceStep(hi - lo || 1)
    lo = Math.floor(lo / step) * step
    hi = Math.ceil(hi / step) * step
    const ticks = []
    for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6))
    const x = (d) => PAD.l + ((d - d0) / (d1 - d0)) * (W - PAD.l - PAD.r)
    const y = (v) => height - PAD.b - ((v - lo) / (hi - lo || 1)) * (height - PAD.t - PAD.b)
    const years = []
    const y0 = +ordToIso(d0).slice(0, 4), y1 = +ordToIso(d1).slice(0, 4)
    for (let yr = y0 + 1; yr <= y1; yr++) years.push({ yr, x: x(Math.floor(Date.UTC(yr, 0, 1) / 864e5) + 719163) })
    return { x, y, ticks, years }
  }, [all, thresholds, d0, d1, height])

  const { x, y } = geo
  const px = playDay != null ? x(Math.min(Math.max(playDay, d0), d1)) : null
  const pathOf = (s) => s.points.map((p, i) => {
    const X = x(p.day).toFixed(1), Y = y(p.value).toFixed(1)
    if (!i) return `M${X},${Y}`
    return s.step ? `H${X}V${Y}` : `L${X},${Y}`
  }).join('') + (s.step && s.points.length ? `H${x(d1).toFixed(1)}` : '')
  const clip = `clip-${label?.replace(/\W/g, '')}`

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect()
    const mx = ((e.clientX - r.left) / r.width) * W
    const hits = all.map((s) => {
      // step series: the value in force at the cursor; line series: nearest point
      let best = null
      if (s.step) {
        for (const p of s.points) if (x(p.day) <= mx) best = p
      } else {
        for (const p of s.points) if (!best || Math.abs(x(p.day) - mx) < Math.abs(x(best.day) - mx)) best = p
      }
      return best && { s, p: best }
    }).filter(Boolean)
    setHover(hits.length ? { mx, hits } : null)
  }

  return (
    <figure className="relative m-0">
      {series && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 mb-1 text-[10.5px] text-muted">
          {series.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span className="w-3 h-0.5 rounded" style={{ background: s.color }} />{s.label}
            </span>
          ))}
        </div>
      )}
      <svg ref={ref} viewBox={`0 0 ${W} ${height}`} className="w-full block" style={{ height }} role="img"
        aria-label={label} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <clipPath id={clip}><rect x="0" y="0" width={px ?? W} height={height} /></clipPath>
        </defs>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke={t === 0 && series ? '#4a5462' : '#262d36'} strokeWidth="1" />
            <text x={PAD.l - 5} y={y(t) + 3} textAnchor="end" className="num" fontSize="9" fill="#8b95a1">{t}</text>
          </g>
        ))}
        {geo.years.map(({ yr, x: gx }) => (
          <text key={yr} x={gx} y={height - 5} textAnchor="middle" fontSize="9" fill="#8b95a1" className="num">{`'${String(yr).slice(2)}`}</text>
        ))}
        {thresholds.map((t) => (
          <g key={t.label}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t.value)} y2={y(t.value)} stroke={t.color ?? '#8b95a1'} strokeWidth="1" strokeDasharray="3 3" opacity="0.8" />
            <text x={W - PAD.r} y={y(t.value) - 3} textAnchor="end" fontSize="9" fill="#aeb6c0">{t.label}</text>
          </g>
        ))}
        {all.map((s) => (
          <g key={s.key}>
            {/* future: faded */}
            <path d={pathOf(s)} fill="none" stroke={s.color} strokeWidth="1.5" opacity="0.22" />
            {s.points.map((p, i) => p.raw != null && <circle key={i} cx={x(p.day)} cy={y(p.raw)} r="1.6" fill={s.color} opacity="0.18" />)}
            {/* past: full strength */}
            <g clipPath={`url(#${clip})`}>
              {s.points.map((p, i) => p.raw != null && <circle key={i} cx={x(p.day)} cy={y(p.raw)} r="1.8" fill={s.color} opacity="0.45" />)}
              <path d={pathOf(s)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
              {s.step && s.points.map((p, i) => (
                <circle key={`m${i}`} cx={x(p.day)} cy={y(p.value)} r="3" fill={p.hollow ? '#12161b' : s.color} stroke={s.color} strokeWidth="1.5" />
              ))}
            </g>
          </g>
        ))}
        {px != null && <line x1={px} x2={px} y1={PAD.t - 4} y2={height - PAD.b} stroke="#e7eaee" strokeWidth="1" />}
        {hover && (
          <g>
            <line x1={hover.mx} x2={hover.mx} y1={PAD.t} y2={height - PAD.b} stroke="#8b95a1" strokeWidth="1" />
            {hover.hits.map(({ s, p }) => <circle key={s.key} cx={s.step ? hover.mx : x(p.day)} cy={y(p.value)} r="4" fill={s.color} stroke="#12161b" strokeWidth="2" />)}
          </g>
        )}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute -top-1 rounded-md border border-line bg-raise px-2 py-1 text-[11px] shadow-lg whitespace-nowrap z-10"
          style={{ left: `${Math.min(Math.max((hover.mx / W) * 100, 22), 68)}%`, transform: 'translate(-50%,-100%)' }}>
          {hover.hits.map(({ s, p }) => (
            <div key={s.key}>
              <span className="text-muted">{s.step ? p.title ?? '' : fmtDate(ordToIso(p.day))}</span>{' '}
              <span className="num">{s.fmt ? s.fmt(p) : `${p.value.toFixed(1)} ${s.unit ?? ''}`}</span>
              {p.raw != null && p.raw !== p.value && !s.fmt && <span className="text-muted"> (scene {p.raw.toFixed(1)})</span>}
            </div>
          ))}
        </div>
      )}
    </figure>
  )
}
