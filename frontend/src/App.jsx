import { useMemo, useState } from 'react'
import MapViewer from './components/MapViewer'
import TopBar from './components/TopBar'
import StatusPanel from './components/StatusPanel'
import Timeline from './components/Timeline'
import AlertFeed, { ManualAlert } from './components/AlertFeed'
import ArchiveView from './components/ArchiveView'
import { useArchive, useLakes, useSystem, useTelemetry, useTimeseries } from './hooks/useApi'
import { useSim } from './hooks/useSim'
import { useReplay } from './hooks/useReplay'
import { scaleAboutCentroid } from './utils/scaleGeoJSON'
import { LEVEL_STYLE } from './utils/format'

const BASE = import.meta.env.VITE_API_BASE ?? ''

export default function App() {
  const lakes = useLakes()
  const system = useSystem()
  const [lakeId, setLakeId] = useState('south_lhonak')
  const [mode, setMode] = useState(new URLSearchParams(location.search).get('mode') ?? 'archive')
  const [orbit, setOrbit] = useState(true)
  const [layers, setLayers] = useState({ water: true, ice: true })
  const [opKey, setOpKey] = useState('')
  const [feedError, setFeedError] = useState(null)

  const archiveLakes = Object.keys(system.data?.archive?.lakes ?? {})
  const effectiveLakeId = mode === 'replay' ? 'south_lhonak' : mode === 'archive' && archiveLakes.length && !archiveLakes.includes(lakeId) ? archiveLakes[0] : lakeId
  const archive = useArchive(mode === 'archive' ? effectiveLakeId : null)
  const replay = useReplay(effectiveLakeId, mode === 'archive' && Boolean(archive.data))
  const telemetry = useTelemetry(lakes.data && mode !== 'archive' ? effectiveLakeId : null, 0)
  const series = useTimeseries(lakes.data && mode === 'live' ? effectiveLakeId : null)
  const sim = useSim(mode === 'replay')

  const water = telemetry.data?.water ?? null
  const ice = telemetry.data?.ice ?? null
  const areaFactor = mode === 'replay' ? sim.state?.area_factor ?? 1 : 1
  const shownWater = useMemo(() => scaleAboutCentroid(water, areaFactor), [water, Math.round(areaFactor * 100)])  // eslint-disable-line
  const level = mode === 'replay' ? sim.state?.level : null

  const decide = async (id, verb) => {
    setFeedError(null)
    try {
      const r = await fetch(`${BASE}/api/alerts/${id}/${verb}`, { method: 'POST', headers: { 'X-API-Key': opKey } })
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`)
    } catch (e) { setFeedError(e.message) }
  }

  if (lakes.error) {
    return (
      <main className="h-full grid place-items-center">
        <div className="panel p-6 max-w-md space-y-2">
          <div className="panel-title">Cannot reach the backend</div>
          <p>{lakes.error.message}</p>
          <p className="text-muted">Start it with <code>./run.sh</code> from the repo root.</p>
          <button className="btn btn-primary" onClick={lakes.reload}>Retry</button>
        </div>
      </main>
    )
  }
  if (!lakes.data) return <main className="h-full grid place-items-center text-muted">Connecting...</main>

  const lake = lakes.data.find((l) => l.id === effectiveLakeId) ?? lakes.data[0]
  const top = <TopBar lakes={lakes.data} lakeId={effectiveLakeId} onSelectLake={setLakeId} mode={mode} onMode={setMode}
    stale={mode !== 'archive' && telemetry.data?.stale} system={system.data} archiveLakes={archiveLakes} />

  if (mode === 'archive') {
    return (
      <main className="relative h-full w-full">
        {archive.data
          ? <ArchiveView archive={archive.data} replay={replay} system={system.data} orbit={orbit} />
          : (
            <div className="h-full grid place-items-center">
              <div className="panel p-6 max-w-md space-y-2">
                <div className="panel-title">{archive.error ? 'Boundary archive unavailable' : 'Loading 2017-2026 boundary archive'}</div>
                <p className="text-muted">{archive.error ? archive.error.message : 'Fetching every archived water and ice outline...'}</p>
              </div>
            </div>
          )}
        {top}
        <button className="btn absolute z-10 left-[364px] top-[60px] text-xs max-[1100px]:hidden" onClick={() => setOrbit((o) => !o)}>
          {orbit ? 'Stop rotation' : 'Rotate view'}
        </button>
      </main>
    )
  }

  return (
    <main className="relative h-full w-full">
      <MapViewer key={lake.id} lake={lake} water={shownWater} ice={ice}
        showWater={layers.water} showIce={layers.ice} orbit={orbit}
        waterEdge={level && level !== 'NORMAL' ? LEVEL_STYLE[level].dot : '#e8f3ff'} />
      {top}
      <StatusPanel mode={mode} lake={lake} sim={sim.state} telemetry={telemetry} series={series}
        layers={layers} onToggleLayer={(k) => setLayers((l) => ({ ...l, [k]: !l[k] }))}
        orbit={orbit} onToggleOrbit={() => setOrbit((o) => !o)} />
      {mode === 'replay' ? (
        <>
          <Timeline sim={sim.state} controls={sim} />
          <AlertFeed alerts={sim.alerts} opKey={opKey} onOpKey={setOpKey} error={feedError}
            onApprove={(id) => decide(id, 'approve')} onReject={(id) => decide(id, 'reject')} />
        </>
      ) : <ManualAlert lake={lake} />}
    </main>
  )
}
