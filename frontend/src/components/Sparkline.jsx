/** Minimal dependency-free line chart. */
export default function Sparkline({ values, color = '#14181d', height = 44 }) {
  if (!values || values.length < 2) return <div className="text-muted text-xs">Not enough observations</div>
  const w = 300, pad = 4
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1
  const pts = values.map((v, i) => [pad + (i / (values.length - 1)) * (w - pad * 2), height - pad - ((v - min) / span) * (height - pad * 2)])
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full" style={{ height }} role="img" aria-label="trend">
      <polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke={color} strokeWidth="1.5" />
      {pts.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={i === pts.length - 1 ? 3 : 2} fill={color} />)}
    </svg>
  )
}
