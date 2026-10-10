import { useState } from 'react'
import { LEVEL_STYLE, clockLabel } from '../utils/format'

export default function Timeline({ sim, controls }) {
  const [speed, setSpeed] = useState(1)
  const [autopilot, setAutopilot] = useState(true)
  const s = sim
  const sc = s?.scenario
  const span = sc ? sc.duration_s : 120

  // position of an event tick on the bar: invert the server's piecewise clock approx. via linear search is overkill;
  // the server sends progress, so place ticks by simulated-hour -> progress using the same waypoints.
  const WAYPOINTS = sc?.clock ?? [[0, -720], [120, 12]]
  const hToProgress = (h) => {
    for (let i = 1; i < WAYPOINTS.length; i++) {
      const [w0, h0] = WAYPOINTS[i - 1], [w1, h1] = WAYPOINTS[i]
      if (h <= h1) return (w0 + ((h - h0) / (h1 - h0)) * (w1 - w0)) / span
    }
    return 1
  }

  const running = s?.running
  const started = s && s.progress > 0
  const ev = sc?.events.filter((e) => e.h <= s.sim_h).slice(-1)[0]

  return (
    <section className="panel absolute z-10 left-[354px] right-[354px] bottom-3 p-3 max-[1100px]:left-3 max-[1100px]:right-3">
      <div className="flex items-center gap-3 flex-wrap">
        <button className="btn btn-primary w-24" disabled={!s || s.finished}
          onClick={() => (running ? controls.pause() : controls.play(speed, autopilot))}>
          {running ? 'Pause' : started ? 'Resume' : 'Start replay'}
        </button>
        <button className="btn" onClick={() => controls.reset()} disabled={!started}>Reset</button>
        <label className="flex items-center gap-1.5">Speed
          <select className="btn py-1" value={speed} onChange={(e) => { const v = +e.target.value; setSpeed(v); controls.setSpeed(v) }}>
            {[0.5, 1, 2, 4].map((v) => <option key={v} value={v}>{v}x</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5" title="Off: public alerts wait for an operator to approve them">
          <input type="checkbox" checked={autopilot} disabled={started} onChange={(e) => setAutopilot(e.target.checked)} />
          Auto-approve public alerts
        </label>
        <div className="ml-auto text-right">
          <div className="num text-lg font-medium leading-none">{s ? s.clock : 'T - 30 days'}</div>
          <div className="text-muted text-[11px]">{sc?.t0 ?? ''}</div>
        </div>
      </div>

      <div className="relative mt-3 h-7" aria-label="Replay progress">
        <div className="absolute inset-x-0 top-3 h-1.5 rounded bg-line" />
        {s && <div className="absolute left-0 top-3 h-1.5 rounded" style={{ width: `${s.progress * 100}%`, background: LEVEL_STYLE[s.level].dot }} />}
        {sc?.events.map((e, i) => {
          const left = hToProgress(e.h) * 100
          return <div key={i} className="absolute top-1.5 w-px h-5 bg-[#5b6573]" style={{ left: `${left}%` }} title={`${clockLabel(e.h)}: ${e.label}`} />
        })}
        {s && <div className="absolute top-1.5 size-3.5 -ml-[7px] rounded-full border-2 border-[#0b0e12] shadow" style={{ left: `${s.progress * 100}%`, background: LEVEL_STYLE[s.level].dot, top: 7 }} />}
      </div>
      <div className="flex justify-between text-[11px] text-muted -mt-0.5">
        <span>{ev ? <><b className="text-ink font-medium">{ev.label}</b></> : 'Press Start to replay'}</span>
        <span>{s?.finished ? 'Replay finished' : ''}</span>
      </div>
      {sc && <p className="text-[11px] text-muted mt-1.5">{sc.disclaimer}</p>}
    </section>
  )
}
