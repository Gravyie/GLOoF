import { useEffect, useState } from 'react'

const BASE = import.meta.env.VITE_API_BASE ?? ''
import { LEVEL_STYLE, clockLabel } from '../utils/format'
import { postAlert } from '../utils/api'

const STATUS = {
  simulated: { text: 'Simulated delivery', bg: '#232a33', fg: '#aeb6c0' },
  sent: { text: 'Accepted by SNS', bg: '#13301f', fg: '#7fdb9a' },
  pending_approval: { text: 'Awaiting approval', bg: '#3a3010', fg: '#f5d06b' },
  rejected: { text: 'Rejected', bg: '#232a33', fg: '#aeb6c0' },
  failed: { text: 'Delivery failed', bg: '#45161a', fg: '#ff8a80' },
}

function Phone({ alert }) {
  return (
    <div className="mx-auto w-[220px] rounded-[26px] border-[5px] border-[#2b323b] bg-[#06080a] p-3 pt-5 min-h-[170px] relative">
      <div className="absolute top-1.5 left-1/2 -translate-x-1/2 h-1 w-10 rounded bg-[#2b323b]" />
      <div className="text-center text-[10px] text-muted mb-2">Messages · GLOF-ALERT</div>
      {alert ? (
        <div key={alert.id} className="slide-in rounded-xl rounded-tl-sm bg-[#1f262e] px-2.5 py-2 text-[11px] leading-snug whitespace-pre-wrap"
          style={alert.level === 'EVACUATE' ? { background: '#a3121f', color: '#fff' } : undefined}>
          {alert.message}
        </div>
      ) : <div className="text-center text-muted text-[11px] mt-8">No public alert sent yet</div>}
    </div>
  )
}

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' }) : '')

/** Real-delivery controls for the drill: mode, latch, Send test, Re-arm. */
function DrillPanel({ system, opKey, onOpKey, reloadSystem }) {
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const sns = system?.sns
  const live = sns?.mode === 'live'
  const latch = system?.demo_sms
  const call = async (path, label) => {
    setBusy(label); setMsg(null)
    try {
      const r = await fetch(`${BASE}${path}`, { method: 'POST', headers: { 'X-API-Key': opKey } })
      const body = await r.json()
      if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`)
      setMsg(label === 'test' ? { ok: true, text: `Test accepted by SNS: ${body.message_id} at ${fmtTime(body.accepted_at)}` } : { ok: true, text: 'Drill re-armed: the History replay can send one more real SMS (cooldown still applies).' })
      reloadSystem?.()
    } catch (e) { setMsg({ ok: false, text: e.message }) } finally { setBusy(null) }
  }
  return (
    <div className="rounded-lg border border-white/[.07] p-3 mb-4 text-[12px] space-y-2">
      <div className={live ? 'text-[#7fdb9a] font-medium' : 'text-muted'}>{sns ? sns.label : 'Checking alert mode...'}</div>
      {live && (
        <div className="text-muted">
          Real SMS trigger: {sns.trigger ?? 'History replay'}. This replay stays practice only.
          {latch?.sent_at ? ` Last drill SMS ${fmtTime(latch.sent_at)}${latch.message_id ? `, accepted by SNS as ${latch.message_id}` : ''}.` : ''}
        </div>
      )}
      <input type="password" value={opKey} onChange={(e) => onOpKey(e.target.value)} placeholder="Operator key"
        aria-label="Operator key" className="field w-full" autoComplete="off" />
      <div className="flex gap-2">
        <button className="btn flex-1" disabled={!opKey || !live || busy} onClick={() => call('/api/notify/test', 'test')}>{busy === 'test' ? 'Sending...' : 'Send test'}</button>
        <button className="btn flex-1" disabled={!opKey || busy} onClick={() => call('/api/demo-sms/rearm', 'rearm')}>{busy === 'rearm' ? 'Re-arming...' : 'Re-arm drill'}</button>
      </div>
      {msg && <p className={msg.ok ? 'text-[#7fdb9a]' : 'text-[#ff8a80]'}>{msg.text}</p>}
    </div>
  )
}

export default function AlertFeed({ alerts, onApprove, onReject, opKey, onOpKey, error, system, reloadSystem }) {
  const latestPublic = [...alerts].reverse().find((a) => a.audience === 'public' && a.status !== 'pending_approval' && a.status !== 'rejected')
  const list = [...alerts].reverse()
  const sentCount = alerts.filter((a) => a.status === 'sent').length
  useEffect(() => { if (sentCount) reloadSystem?.() }, [sentCount, reloadSystem])  // latch state changed on the server
  return (
    <aside className="panel absolute z-10 right-3 top-[72px] bottom-3 w-[330px] max-w-[calc(100vw-1.5rem)] overflow-y-auto p-4 max-[1100px]:hidden">
      <div className="panel-title mb-2">Real alerts</div>
      <DrillPanel system={system} opKey={opKey} onOpKey={onOpKey} reloadSystem={reloadSystem} />
      <div className="panel-title mb-2">Public alert, as received</div>
      <Phone alert={latestPublic} />

      <div className="flex items-baseline justify-between mt-5 mb-2">
        <div className="panel-title">Alert log</div>
        <div className="text-muted text-[11px]">{alerts.length} sent or queued</div>
      </div>
      {error && <p className="text-[#ff8a80] mb-2">{error}</p>}
      <ol className="space-y-2">
        {list.map((a) => {
          const ls = LEVEL_STYLE[a.level], st = STATUS[a.status] ?? STATUS.simulated
          return (
            <li key={a.id} className="slide-in border border-line rounded bg-raise p-2.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="chip" style={{ background: ls.bg, color: ls.fg }}>{ls.label}</span>
                <span className="font-medium">{a.audience_label}</span>
                <span className="num text-muted text-[11px] ml-auto">{clockLabel(a.sim_h)}</span>
              </div>
              <p className="mt-1.5 text-[12px] leading-snug whitespace-pre-wrap">{a.message}</p>
              <div className="flex items-center gap-2 mt-2">
                <span className="chip" style={{ background: st.bg, color: st.fg }}>{st.text}</span>
                <span className="text-muted text-[11px]">{a.recipients} recipient{a.recipients === 1 ? '' : 's'}</span>
                {a.status === 'pending_approval' && (
                  <span className="ml-auto flex gap-1.5">
                    <button className="btn btn-primary py-0.5" disabled={!opKey} onClick={() => onApprove(a.id)}>Approve</button>
                    <button className="btn py-0.5" disabled={!opKey} onClick={() => onReject(a.id)}>Reject</button>
                  </span>
                )}
              </div>
              {a.status === 'sent' && a.message_ids?.[0] && (
                <p className="mt-1.5 text-[11px] text-[#7fdb9a]">
                  {a.channel}: MessageId <span className="num">{a.message_ids[0]}</span>, accepted by SNS {fmtTime(a.accepted_at)}
                </p>
              )}
              {a.note && <p className="mt-1.5 text-[11px] text-muted">{a.note}</p>}
            </li>
          )
        })}
        {!alerts.length && <li className="text-muted">Alerts appear here automatically as the risk level rises.</li>}
      </ol>
    </aside>
  )
}

/** Live mode: manual alert from an operator (dry-run unless the server is configured otherwise). */
export function ManualAlert({ lake }) {
  const [level, setLevel] = useState('WARNING')
  const [key, setKey] = useState('')
  const [armed, setArmed] = useState(false)
  const [res, setRes] = useState({ busy: false, data: null, error: null })
  const send = async () => {
    if (!armed) return setArmed(true)
    setArmed(false); setRes({ busy: true, data: null, error: null })
    try { setRes({ busy: false, data: await postAlert({ lake: lake.id, level, apiKey: key }), error: null }) }
    catch (e) { setRes({ busy: false, data: null, error: e.message }) }
  }
  return (
    <aside className="panel absolute z-10 right-3 top-[72px] w-[300px] max-w-[calc(100vw-1.5rem)] p-4 max-[1100px]:hidden">
      <div className="panel-title mb-2">Manual alert</div>
      <div className="flex gap-2 mb-2">
        {['WARNING', 'EVACUATE'].map((l) => (
          <button key={l} onClick={() => { setLevel(l); setArmed(false) }} aria-pressed={level === l}
            className="btn flex-1" style={level === l ? { background: LEVEL_STYLE[l].bg, color: LEVEL_STYLE[l].fg, borderColor: LEVEL_STYLE[l].dot } : undefined}>
            {LEVEL_STYLE[l].label}
          </button>
        ))}
      </div>
      <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Operator key"
        className="field w-full mb-2" autoComplete="off" />
      <button className={`btn w-full ${armed ? 'btn-primary' : ''}`} disabled={!key || res.busy} onClick={send}>
        {res.busy ? 'Sending...' : armed ? 'Click again to confirm' : 'Send alert'}
      </button>
      {res.error && <p className="text-[#ff8a80] mt-2">{res.error}</p>}
      {res.data && (
        <div className="mt-2 text-[12px]">
          <p className="font-medium">{res.data.dry_run ? 'Dry run: nothing was sent' : `Sent to ${res.data.recipients} recipient(s)`}</p>
          <pre className="whitespace-pre-wrap font-sans text-muted mt-1">{res.data.message}</pre>
        </div>
      )}
    </aside>
  )
}
