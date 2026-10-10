import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson } from '../utils/api'

const BASE = import.meta.env.VITE_API_BASE ?? ''
const post = (path) => fetch(`${BASE}${path}`, { method: 'POST' }).then((r) => r.json())

/**
 * Archive replay. The server owns the clock and fires the alerts (it keeps
 * running with no browser open); this hook polls it and, between polls,
 * extrapolates the day so the shoreline moves smoothly at 60 fps.
 * `day` is a float proleptic ordinal, like the server's.
 */
export function useReplay(lakeId, active) {
  const [state, setState] = useState(null)
  const [day, setDay] = useState(null)
  const anchor = useRef(null) // { day, rate, at, end }

  const accept = useCallback((s) => {
    if (!s || s.detail) return
    anchor.current = { day: s.day, rate: s.running ? s.days_per_second : 0, at: performance.now(), end: s.span.end_day }
    setState(s)
  }, [])

  useEffect(() => {
    if (!active || !lakeId) return
    let stop = false
    const poll = async () => {
      try { const s = await getJson(`/api/archive/replay/state?lake=${lakeId}`); if (!stop) accept(s) } catch { /* keep last */ }
    }
    poll()
    const id = setInterval(poll, 500)
    return () => { stop = true; clearInterval(id) }
  }, [active, lakeId, accept])

  useEffect(() => {
    if (!active) return
    let raf = 0, last = 0
    const loop = (now) => {
      const a = anchor.current
      if (a && now - last > 33) { // ~30 fps is plenty for a map source update
        last = now
        setDay(Math.min(a.day + (a.rate * (now - a.at)) / 1000, a.end))
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [active])

  const q = `lake=${lakeId}`
  const act = (path) => post(path).then(accept)
  return {
    state, day,
    play: (speed = 1, autopilot = true) => act(`/api/archive/replay/start?${q}&speed=${speed}&autopilot=${autopilot}`),
    pause: () => act(`/api/archive/replay/pause?${q}`),
    reset: () => act(`/api/archive/replay/reset?${q}`),
    setSpeed: (speed) => act(`/api/archive/replay/speed?${q}&speed=${speed}`),
    seek: (progress) => act(`/api/archive/replay/seek?${q}&progress=${Math.max(0, Math.min(1, progress)).toFixed(5)}`),
  }
}
