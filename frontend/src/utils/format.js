export const LEVELS = ['NORMAL', 'WATCH', 'ADVISORY', 'WARNING', 'EVACUATE']

export const LEVEL_STYLE = {
  NORMAL:   { label: 'Normal',   bg: '#e1eee5', fg: '#1f5a36', dot: '#2f7d4f' },
  WATCH:    { label: 'Watch',    bg: '#f7edbd', fg: '#5e4800', dot: '#c99a00' },
  ADVISORY: { label: 'Advisory', bg: '#fadcb8', fg: '#6e3500', dot: '#e07b00' },
  WARNING:  { label: 'Warning',  bg: '#f6c9bc', fg: '#7a1c0a', dot: '#d23b1f' },
  EVACUATE: { label: 'Evacuate', bg: '#a3121f', fg: '#ffffff', dot: '#a3121f' },
}

/** Simulated hours relative to the outburst -> "T - 1 h 30 min". Mirrors the backend. */
export function clockLabel(h) {
  if (h == null) return ''
  if (Math.abs(h) < 0.05) return 'T = outburst'
  const sign = h < 0 ? '-' : '+'
  const a = Math.abs(h)
  if (a >= 48) return `T ${sign} ${Math.round(a / 24)} days`
  if (a >= 1) {
    const m = Math.round((a % 1) * 60)
    return m ? `T ${sign} ${Math.floor(a)} h ${String(m).padStart(2, '0')} min` : `T ${sign} ${Math.floor(a)} h`
  }
  return `T ${sign} ${Math.round(a * 60)} min`
}

export const fmtHa = (m2) => (m2 == null ? '-' : (m2 / 1e4).toFixed(1))
export const ageDays = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 864e5))
