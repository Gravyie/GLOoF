export default function TopBar({ lakes, lakeId, onSelectLake, mode, onMode, stale }) {
  const tab = (active) => `px-3 h-full border-b-2 ${active ? 'border-white text-white' : 'border-transparent text-[#9aa4ae] hover:text-white'}`
  return (
    <header className="absolute top-0 inset-x-0 h-11 z-20 flex items-center gap-6 px-4 bg-ink text-white border-b border-[#2a3037]">
      <div className="font-semibold tracking-tight">Glacial Lake Monitor</div>
      <nav className="flex h-full" aria-label="Lake">
        {lakes.map((l) => (
          <button key={l.id} disabled={mode === 'replay' && l.id !== 'south_lhonak'}
            className={tab(l.id === lakeId) + ' disabled:opacity-30'} onClick={() => onSelectLake(l.id)}>
            {l.name}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {stale && <span className="text-[#e8c36a] text-xs">Showing saved satellite data (offline)</span>}
        <div className="flex rounded overflow-hidden border border-[#3a424b]" role="group" aria-label="Mode">
          {[['live', 'Live'], ['replay', 'Replay: 2023 outburst']].map(([m, label]) => (
            <button key={m} onClick={() => onMode(m)} aria-pressed={mode === m}
              className={`px-3 py-1 text-xs ${mode === m ? 'bg-white text-ink font-semibold' : 'text-[#c4cbd2] hover:bg-[#2a3037]'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}
