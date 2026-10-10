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
 * Single-series time chart over the replay span. Past (left of the playhead) is
 * full strength, the future is faded so the history reveals as the replay runs.
 * points: [{ day, value, raw }], thresholds: [{ value, label }]
 */
export default function AreaChart({ points, span, playDay, color, unit, thresholds = [], height = 112, label }) {
  const [hover, setHover] = useState(null)
  const ref = useRef(null)
  const [d0, d1] = span
  const geo = useMemo(() => {
    const vals = points.flatMap((p) => [p.value, p.raw]).concat(thresholds.map((t) => t.value)).filter((v) => v != null)
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
    for (let yr = y0 + 1; yr <= y1; yr++) {
      const ord = Math.floor(Date.UTC(yr, 0, 1) / 864e5) + 719163
      years.push({ yr, x: x(ord) })
    }
    return { x, y, ticks, years }
  }, [points, thresholds, d0, d1, height])

  const { x, y } = geo
  const px = playDay != null ? x(Math.min(Math.max(playDay, d0), d1)) : null
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.day).toFixed(1)},${y(p.value).toFixed(1)}`).join('')
  const clip = `clip-${label?.replace(/\W/g, '')}`

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect()
    const mx = ((e.clientX - r.left) / r.width) * W
    let best = null
    for (const p of points) if (!best || Math.abs(x(p.day) - mx) < Math.abs(x(best.day) - mx)) best = p
    setHover(best)
  }

  return (
    <figure className="relative m-0">
      <svg ref={ref} viewBox={`0 0 ${W} ${height}`} className="w-full block" style={{ height }} role="img"
        aria-label={label} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <clipPath id={clip}><rect x="0" y="0" width={px ?? W} height={height} /></clipPath>
        </defs>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="#262d36" strokeWidth="1" />
            <text x={PAD.l - 5} y={y(t) + 3} textAnchor="end" className="num" fontSize="9" fill="#8b95a1">{t}</text>
          </g>
        ))}
        {geo.years.map(({ yr, x: gx }) => (
          <text key={yr} x={gx} y={height - 5} textAnchor="middle" fontSize="9" fill="#8b95a1" className="num">
            {`'${String(yr).slice(2)}`}
          </text>
        ))}
        {thresholds.map((t) => (
          <g key={t.label}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t.value)} y2={y(t.value)} stroke={t.color ?? '#8b95a1'} strokeWidth="1" strokeDasharray="3 3" opacity="0.8" />
            <text x={W - PAD.r} y={y(t.value) - 3} textAnchor="end" fontSize="9" fill="#aeb6c0">{t.label}</text>
          </g>
        ))}
        {/* future: faded */}
        <path d={path} fill="none" stroke={color} strokeWidth="1.5" opacity="0.22" />
        {points.map((p, i) => p.raw != null && <circle key={i} cx={x(p.day)} cy={y(p.raw)} r="1.6" fill={color} opacity="0.18" />)}
        {/* past: full strength */}
        <g clipPath={`url(#${clip})`}>
          {points.map((p, i) => p.raw != null && <circle key={i} cx={x(p.day)} cy={y(p.raw)} r="1.8" fill={color} opacity="0.45" />)}
          <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        </g>
        {px != null && <line x1={px} x2={px} y1={PAD.t - 4} y2={height - PAD.b} stroke="#e7eaee" strokeWidth="1" />}
        {hover && (
          <g>
            <line x1={x(hover.day)} x2={x(hover.day)} y1={PAD.t} y2={height - PAD.b} stroke="#8b95a1" strokeWidth="1" />
            <circle cx={x(hover.day)} cy={y(hover.value)} r="4" fill={color} stroke="#12161b" strokeWidth="2" />
          </g>
        )}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute -top-1 rounded-md border border-line bg-raise px-2 py-1 text-[11px] shadow-lg whitespace-nowrap"
          style={{ left: `${Math.min(Math.max((x(hover.day) / W) * 100, 20), 70)}%`, transform: 'translate(-50%,-100%)' }}>
          <div className="text-muted">{fmtDate(ordToIso(hover.day))}</div>
          <div className="num">{hover.value.toFixed(1)} {unit}{hover.raw != null && hover.raw !== hover.value && <span className="text-muted"> (scene {hover.raw.toFixed(1)})</span>}</div>
        </div>
      )}
    </figure>
  )
}
