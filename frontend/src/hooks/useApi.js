import { useCallback, useEffect, useState } from 'react'
import { getJson } from '../utils/api'

/**
 * Fetch `path` on mount / when it changes. Pass null to skip.
 * Returns { data, error, loading, reload }.
 *
 * Results are stored with the request key they belong to, so a stale response
 * (previous lake) is never shown and `loading` is derived, not set in an effect.
 */
export function useApi(path) {
  const [nonce, setNonce] = useState(0)
  const [result, setResult] = useState({ key: null, data: null, error: null })
  const key = path ? `${path}#${nonce}` : null

  useEffect(() => {
    if (!key) return
    const ctl = new AbortController()
    getJson(path, { signal: ctl.signal })
      .then((data) => setResult({ key, data, error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setResult({ key, data: null, error })
      })
    return () => ctl.abort()
  }, [key, path])

  const current = result.key === key
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return {
    data: current ? result.data : null,
    error: current ? result.error : null,
    loading: Boolean(key) && !current,
    reload,
  }
}

export const useLakes = () => useApi('/api/lakes')
export const useTelemetry = (lakeId, refreshKey = 0) =>
  useApi(lakeId ? `/api/telemetry?lake=${lakeId}${refreshKey ? '&refresh=true' : ''}` : null)
export const useTimeseries = (lakeId) => useApi(lakeId ? `/api/timeseries?lake=${lakeId}&n=6` : null)
