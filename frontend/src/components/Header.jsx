export default function Header({ summary, live }) {
  const players = summary?.players ?? []
  const online = players.filter((p) => p.online).length
  const cap = summary?.metrics?.maxplayernum
  const version = summary?.server?.version

  return (
    <header className="top">
      <h1>Palworld Admin</h1>
      <span className="sub">
        <span className={`dot ${online ? 'on' : 'off'}`} /> {online}
        {cap ? `/${cap}` : ''} online · {players.length} players · world day {summary?.metrics?.days ?? '—'}
      </span>
      {version ? <span className="badge">{version}</span> : null}
      <span className="badge">{live ? 'live stream' : 'reconnecting'}</span>
    </header>
  )
}
