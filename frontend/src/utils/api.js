// In dev the Vite proxy forwards /api to the backend; in production set
// VITE_API_BASE to the deployed API origin.
const BASE = import.meta.env.VITE_API_BASE ?? ''

async function parse(res) {
  if (res.ok) return res.json()
  let detail = `HTTP ${res.status}`
  try {
    const body = await res.json()
    if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  } catch {
    /* non-JSON error body */
  }
  const err = new Error(detail)
  err.status = res.status
  throw err
}

export async function getJson(path, { signal } = {}) {
  return parse(await fetch(`${BASE}${path}`, { signal }))
}

export async function postAlert({ lake, level, apiKey }) {
  const res = await fetch(`${BASE}/api/alert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
    body: JSON.stringify({ lake, level }),
  })
  return parse(res)
}
