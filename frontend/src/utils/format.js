export const LEVELS = ['NORMAL', 'WATCH', 'ADVISORY', 'WARNING', 'EVACUATE']

// Status colours (dark surface). Always shown with a text label, never colour alone.
export const LEVEL_STYLE = {
  NORMAL:   { label: 'Normal',   bg: '#13301f', fg: '#7fdb9a', dot: '#2fa35a' },
  WATCH:    { label: 'Watch',    bg: '#3a3010', fg: '#f5d06b', dot: '#e0b020' },
  ADVISORY: { label: 'Advisory', bg: '#40260f', fg: '#f7a65c', dot: '#ec7f2c' },
  WARNING:  { label: 'Warning',  bg: '#45161a', fg: '#ff8a80', dot: '#e5483b' },
  EVACUATE: { label: 'Evacuate', bg: '#b3122a', fg: '#ffffff', dot: '#ff2d55' },
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

// Proleptic Gregorian ordinal (Python date.toordinal) <-> JS dates, so the client
// can extrapolate the server clock between polls.
const ORD_EPOCH = 719163 // date(1970,1,1).toordinal()
export const ordToDate = (ord) => new Date((ord - ORD_EPOCH) * 864e5)
export const isoToOrd = (iso) => Math.floor(Date.parse(iso) / 864e5) + ORD_EPOCH
export const ordToIso = (ord) => ordToDate(Math.floor(ord)).toISOString().slice(0, 10)
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const fmtDate = (iso) => { const [y, m, d] = iso.split('-'); return `${+d} ${MONTHS[+m - 1]} ${y}` }
export const fmtMonth = (iso) => { const [y, m] = iso.split('-'); return `${MONTHS[+m - 1]} ${y}` }
