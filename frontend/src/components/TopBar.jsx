const MODES = [
  ['archive', 'History'],
  ['live', 'Latest image'],
  ['replay', 'Flood night'],
]

export default function TopBar({ lakes, lakeId, onSelectLake, mode, onMode, stale, system, archiveLakes }) {
  const live = system?.sns?.mode === 'live'
  return (
    <header className="absolute top-0 inset-x-0 h-14 z-30 flex items-center gap-6 px-5 bg-[#0b0e12]/85 border-b border-white/[.06] backdrop-blur-md">
      <div className="flex items-center gap-2">
        <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden="true"><path d="M3 25 12 9l5 8 3-4 9 12z" fill="none" stroke="#e7eaee" strokeWidth="2.2" strokeLinejoin="round" /><path d="M9 25c3-2 6-2 9 0s6 2 9 0" stroke="#3d9bff" strokeWidth="2.2" fill="none" /></svg>
        <span className="font-semibold tracking-tight text-[15px]">Glacial Lake Monitor</span>
      </div>
      <nav className="flex h-full" aria-label="Lake">
        {lakes.map((l) => {
          const disabled = (mode === 'replay' && l.id !== 'south_lhonak') || (mode === 'archive' && !archiveLakes?.includes(l.id))
          return (
            <button key={l.id} disabled={disabled} title={disabled ? 'No archive built for this lake yet' : undefined}
              className={`px-3 h-full border-b-2 text-[14px] disabled:opacity-30 ${l.id === lakeId ? 'border-water text-ink' : 'border-transparent text-muted hover:text-ink'}`}
              onClick={() => onSelectLake(l.id)}>
              {l.name}
            </button>
          )
        })}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {stale && <span className="text-[#f5d06b] text-xs">Showing saved satellite data (offline)</span>}
        {system && (
          <span className="hidden xl:inline text-[12px] text-muted" title={system.sns.label}>
            {live ? 'Alerts live on Amazon SNS' : 'Alerts in practice mode'}
          </span>
        )}
        <div className="flex rounded-lg overflow-hidden border border-white/10" role="group" aria-label="View">
          {MODES.map(([m, label]) => (
            <button key={m} onClick={() => onMode(m)} aria-pressed={mode === m}
              className={`px-4 py-1.5 text-[13px] ${mode === m ? 'bg-ink text-void font-semibold' : 'text-muted hover:bg-raise'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}
