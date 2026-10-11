import Sparkline from './Sparkline'
import { LEVELS, LEVEL_STYLE, clockLabel, fmtHa, ageDays } from '../utils/format'

function LevelBadge({ level, big }) {
  const s = LEVEL_STYLE[level]
  return (
    <span className={`inline-block rounded ${big ? 'px-3 py-1 text-2xl font-semibold' : 'chip'}`} style={{ background: s.bg, color: s.fg }}>
      {s.label}
    </span>
  )
}

function Row({ k, v, unit, warn }) {
  return (
    <div className="flex justify-between gap-3 py-1 border-b border-line last:border-0">
      <dt className="text-muted">{k}</dt>
      <dd className={`num text-right ${warn ? 'font-semibold text-[#ff8a80]' : ''}`}>{v}{unit && <span className="text-muted"> {unit}</span>}</dd>
    </div>
  )
}

function LevelScale({ level }) {
  const idx = LEVELS.indexOf(level)
  return (
    <div className="flex gap-0.5 mt-3" aria-label={`Level ${level}`}>
      {LEVELS.map((l, i) => (
        <div key={l} className="flex-1">
          <div className="h-1.5 rounded-sm" style={{ background: i <= idx ? LEVEL_STYLE[l].dot : '#262d36' }} />
          <div className={`text-[10px] mt-1 ${i === idx ? 'font-semibold' : 'text-muted'}`}>{LEVEL_STYLE[l].label}</div>
        </div>
      ))}
    </div>
  )
}

function Reasons({ reasons, flags }) {
  if (!reasons?.length && !flags?.length) return <p className="text-muted mt-3">No indicators above threshold.</p>
  return (
    <ul className="mt-3 space-y-1.5">
      {reasons.map((r, i) => (
        <li key={i} className="flex gap-2 items-start">
          <span className="mt-1.5 size-2 rounded-full shrink-0" style={{ background: LEVEL_STYLE[r.level].dot }} />
          <span>{r.text}</span>
        </li>
      ))}
      {flags?.map((f, i) => <li key={`f${i}`} className="text-[#f5d06b] bg-[#3a3010] rounded px-2 py-1">{f}</li>)}
    </ul>
  )
}

function ReplayBody({ sim }) {
  const s = sim
  const i = s.indicators
  return (
    <>
      <div className="panel-title mt-4 mb-1">Indicators now</div>
      <dl>
        <Row k="Lake area vs. baseline" v={`${i.growth_pct >= 0 ? '+' : ''}${i.growth_pct.toFixed(0)}`} unit="%" warn={i.growth_pct >= 10} />
        <Row k="Rainfall, last 24 h" v={i.rain_24h_mm.toFixed(0)} unit="mm" warn={i.rain_24h_mm >= 40} />
        <Row k="Lake level change" v={`${i.lake_rise_m_per_h >= 0 ? '+' : ''}${i.lake_rise_m_per_h.toFixed(1)}`} unit="m/h" warn={i.lake_rise_m_per_h >= 0.5} />
        <Row k="Downstream gauge change" v={`${i.gauge_rise_m_per_h >= 0 ? '+' : ''}${i.gauge_rise_m_per_h.toFixed(1)}`} unit="m/h" warn={i.gauge_rise_m_per_h >= 1} />
        <Row k="Field reports" v={i.incidents_verified ? '1 verified' : i.incidents_unverified ? '1 unverified' : 'none'} warn={i.incidents_verified > 0} />
      </dl>
      {s.level_history.length > 0 && (
        <>
          <div className="panel-title mt-4 mb-1">Level changes</div>
          <ol className="space-y-1">
            {s.level_history.map((h, k) => (
              <li key={k} className="flex items-center gap-2">
                <span className="num text-muted w-36 shrink-0 whitespace-nowrap">{clockLabel(h.h)}</span>
                <LevelBadge level={h.to} />
              </li>
            ))}
          </ol>
        </>
      )}
    </>
  )
}

function LiveBody({ telemetry, series }) {
  const t = telemetry.data
  const trend = series.data?.trend
  return (
    <>
      <div className="panel-title mt-4 mb-1">Latest clear satellite scene</div>
      {telemetry.loading && <p className="text-muted">Reading Sentinel-2 bands and computing water and ice outlines...</p>}
      {telemetry.error && <p className="text-[#ff8a80]">{telemetry.error.message}</p>}
      {t && (
        <dl>
          <Row k="Main lake area" v={fmtHa(t.stats.primary_area_m2)} unit="ha" />
          <Row k="All water bodies" v={`${fmtHa(t.stats.water_area_m2)} (${t.stats.polygon_count})`} unit="ha" />
          <Row k="Snow and ice" v={t.stats.ice_area_ha != null ? t.stats.ice_area_ha.toFixed(0) : '-'} unit="ha" />
          <Row k="Scene date" v={new Date(t.scene.acquired).toISOString().slice(0, 10)} unit={`(${ageDays(t.scene.acquired)} d ago)`} />
          <Row k="Cloud in area" v={(t.stats.cloud_fraction * 100).toFixed(1)} unit="%" />
        </dl>
      )}
      <div className="panel-title mt-4 mb-1">Same-season trend</div>
      {series.loading && <p className="text-muted">Building history...</p>}
      {series.error && <p className="text-muted">History unavailable</p>}
      {trend && (
        <>
          <p>{trend.growth_pct != null && trend.level !== 'UNRELIABLE' ? `${trend.growth_pct > 0 ? '+' : ''}${trend.growth_pct}%. ` : ''}<span className="text-muted">{trend.basis}</span></p>
          <Sparkline values={series.data.series.map((s) => s.water_area_m2 / 1e4)} />
        </>
      )}
    </>
  )
}

export default function StatusPanel({ mode, lake, sim, telemetry, series, layers, onToggleLayer, orbit, onToggleOrbit }) {
  const liveTrend = series.data?.trend?.level
  const liveLevel = { ELEVATED: 'WATCH', CRITICAL: 'ADVISORY' }[liveTrend] ?? 'NORMAL'
  const level = mode === 'replay' ? (sim?.level ?? 'NORMAL') : liveLevel
  const reasons = mode === 'replay' ? sim?.reasons ?? [] :
    liveLevel !== 'NORMAL' ? [{ level: liveLevel, text: `Lake area trend ${series.data.trend.growth_pct}% (same-season)` }] : []
  const flags = mode === 'replay' ? sim?.flags : liveTrend === 'UNRELIABLE' ? ['Trend looks implausible; treated as a data artefact, not growth'] : []

  return (
    <aside className="panel absolute z-10 left-3 top-[72px] bottom-3 w-[330px] max-w-[calc(100vw-1.5rem)] overflow-y-auto p-4">
      <div className="panel-title">{lake.name} <span className="font-normal normal-case tracking-normal">· {lake.country}</span></div>
      <div className="mt-2 flex items-center gap-2">
        <LevelBadge level={level} big />
      </div>
      <div className="text-muted mt-1">
        {mode === 'replay' ? 'Risk level from the rule engine' : 'Satellite trend only (no live sensors connected)'}
      </div>
      <LevelScale level={level} />
      <Reasons reasons={reasons} flags={flags} />
      {mode === 'replay' && sim && LEVELS.indexOf(sim.level) > LEVELS.indexOf(sim.raw_level) && LEVELS.indexOf(sim.level) >= 3 && (
        <p className="mt-3 text-muted">Held at {LEVEL_STYLE[sim.level].label}: Warning and Evacuate only clear by operator action, even when readings ease.</p>
      )}
      {mode === 'replay' ? (sim ? <ReplayBody sim={sim} /> : <p className="text-muted mt-4">Connecting to simulation...</p>)
        : <LiveBody telemetry={telemetry} series={series} />}

      <div className="panel-title mt-5 mb-2">Map</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {[['water', 'Water'], ['ice', 'Snow and ice']].map(([k, label]) => (
          <label key={k} className="flex items-center gap-1.5"><input type="checkbox" checked={layers[k]} onChange={() => onToggleLayer(k)} />{label}</label>
        ))}
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={orbit} onChange={onToggleOrbit} />Slow rotation</label>
      </div>
      <p className="text-muted text-[11px] mt-4 leading-snug">
        Sentinel-2 L2A (ESA, via AWS Earth Search). Water: NDWI. Snow and ice: NDSI; debris-covered ice is not detected.
        Terrain: AWS Open Data. Thresholds are placeholders pending calibration with the local authority.
      </p>
    </aside>
  )
}
