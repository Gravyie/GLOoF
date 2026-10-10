import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson } from '../utils/api'

const BASE = import.meta.env.VITE_API_BASE ?? ''
const post = (path) => fetch(`${BASE}${path}`, { method: 'POST' }).then((r) => r.json())

/** Polls the server-side replay clock. The server owns time and fires the alerts;
 *  this hook only displays the state and sends play/pause/reset/speed. */
export function useSim(active) {
  const [state, setState] = useState(null)
  const [alerts, setAlerts] = useState([])
  const lastId = useRef(0)

  const merge = useCallback((s) => {
    if (s.alerts?.length) {
      setAlerts((prev) => {
        const known = new Map(prev.map((a) => [a.id, a]))
        s.alerts.forEach((a) => known.set(a.id, a))
        return [...known.values()].sort((a, b) => a.id - b.id)
      })
      lastId.current = Math.max(lastId.current, ...s.alerts.map((a) => a.id))
    }
    setState(s)
  }, [])

  useEffect(() => {
    if (!active) return
    let stop = false
    const tick = async () => {
      try {
        // re-fetch from 0 so status changes (pending -> sent) on old alerts show up
        const s = await getJson('/api/sim/state?after_id=0')
        if (!stop) merge(s)
      } catch { /* backend briefly unavailable: keep last state */ }
    }
    tick()
    const id = setInterval(tick, 400)
    return () => { stop = true; clearInterval(id) }
  }, [active, merge])

  const act = (path) => post(path).then((s) => { if (path.includes('reset')) { setAlerts([]); lastId.current = 0 } merge(s) })
  return {
    state, alerts,
    play: (speed = 1, autopilot = true) => act(`/api/sim/start?speed=${speed}&autopilot=${autopilot}`),
    pause: () => act('/api/sim/pause'),
    reset: () => act('/api/sim/reset'),
    setSpeed: (speed) => act(`/api/sim/speed?speed=${speed}`),
  }
}
