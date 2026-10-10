import { useState } from 'react'
import { LEVEL_STYLE, clockLabel } from '../utils/format'
import { postAlert } from '../utils/api'

const STATUS = {
  simulated: { text: 'Simulated delivery', bg: '#e7e5dd', fg: '#444' },
  sent: { text: 'Sent via SNS', bg: '#dcefe2', fg: '#1f5a36' },
  pending_approval: { text: 'Awaiting approval', bg: '#f7edbd', fg: '#5e4800' },
  rejected: { text: 'Rejected', bg: '#e7e5dd', fg: '#444' },
  failed: { text: 'Delivery failed', bg: '#f6c9bc', fg: '#7a1c0a' },
}

function Phone({ alert }) {
  return (
    <div className="mx-auto w-[220px] rounded-[26px] border-[5px] border-[#14181d] bg-[#fbfbf9] p-3 pt-5 min-h-[170px] relative">
      <div className="absolute top-1.5 left-1/2 -translate-x-1/2 h-1 w-10 rounded bg-[#14181d]" />
      <div className="text-center text-[10px] text-muted mb-2">Messages · GLOF-ALERT</div>
      {alert ? (
        <div key={alert.id} className="slide-in rounded-xl rounded-tl-sm bg-[#e9e8e3] px-2.5 py-2 text-[11px] leading-snug whitespace-pre-wrap"
          style={alert.level === 'EVACUATE' ? { background: '#a3121f', color: '#fff' } : undefined}>
          {alert.message}
        </div>
      ) : <div className="text-center text-muted text-[11px] mt-8">No public alert sent yet</div>}
    </div>
  )
}

export default function AlertFeed({ alerts, onApprove, onReject, opKey, onOpKey, error }) {
  const latestPublic = [...alerts].reverse().find((a) => a.audience === 'public' && a.status !== 'pending_approval' && a.status !== 'rejected')
  const list = [...alerts].reverse()
  return (
    <aside className="panel absolute z-10 right-3 top-14 bottom-3 w-[330px] max-w-[calc(100vw-1.5rem)] overflow-y-auto p-4 max-[1100px]:hidden">
      <div className="panel-title mb-2">Public alert, as received</div>
      <Phone alert={latestPublic} />

      <div className="flex items-baseline justify-between mt-5 mb-2">
        <div className="panel-title">Alert log</div>
        <div className="text-muted text-[11px]">{alerts.length} sent or queued</div>
      </div>
      {alerts.some((a) => a.status === 'pending_approval') && (
        <input type="password" value={opKey} onChange={(e) => onOpKey(e.target.value)} placeholder="Operator key to approve"
          className="w-full border border-line rounded px-2 py-1 mb-2 bg-white" autoComplete="off" />
      )}
      {error && <p className="text-[#a3121f] mb-2">{error}</p>}
      <ol className="space-y-2">
        {list.map((a) => {
          const ls = LEVEL_STYLE[a.level], st = STATUS[a.status] ?? STATUS.simulated
          return (
            <li key={a.id} className="slide-in border border-line rounded bg-white p-2.5">
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
    <aside className="panel absolute z-10 right-3 top-14 w-[300px] max-w-[calc(100vw-1.5rem)] p-4 max-[1100px]:hidden">
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
        className="w-full border border-line rounded px-2 py-1 mb-2 bg-white" autoComplete="off" />
      <button className={`btn w-full ${armed ? 'btn-primary' : ''}`} disabled={!key || res.busy} onClick={send}>
        {res.busy ? 'Sending...' : armed ? 'Click again to confirm' : 'Send alert'}
      </button>
      {res.error && <p className="text-[#a3121f] mt-2">{res.error}</p>}
      {res.data && (
        <div className="mt-2 text-[12px]">
          <p className="font-medium">{res.data.dry_run ? 'Dry run: nothing was sent' : `Sent to ${res.data.recipients} recipient(s)`}</p>
          <pre className="whitespace-pre-wrap font-sans text-muted mt-1">{res.data.message}</pre>
        </div>
      )}
    </aside>
  )
}
