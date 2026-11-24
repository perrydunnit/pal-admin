import { clock, hms } from '../format.js'

export default function PlayerStats({ summary }) {
  const players = summary?.players ?? []
  if (!players.length) return <div className="panel x"><div className="empty">No players recorded yet.</div></div>

  const max = Math.max(1, ...players.map((p) => p.total_seconds || 0))

  return (
    <div className="panel x">
      <table>
        <thead>
          <tr>
            <th>Player</th>
            <th>Platform</th>
            <th className="num">Level</th>
            <th className="num" title="Levels gained each time playtime doubles. Stays comparable between players because each level costs more than the last">Advancement</th>
            <th className="num">Playtime</th>
            <th>Sessions</th>
            <th className="num">Avg session</th>
            <th className="num">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {players.map((p) => (
            <tr key={p.player_id}>
              <td title={[p.account_name, p.ip].filter(Boolean).join(' · ')}>
                <span className={`dot ${p.online ? 'on' : 'off'}`} /> {p.name}
              </td>
              <td><span className="pill">{p.platform}</span></td>
              <td className="num">{p.level ?? '—'}</td>
              <td className="num">
                {p.advancement ? (
                  <span title={`Level ${p.advancement.level} after ${p.advancement.hours} h of play. Playing twice as long would gain about ${p.advancement.levels_per_doubling.toFixed(1)} more levels.`}>
                    {p.advancement.levels_per_doubling.toFixed(1)}
                    <div className="sub" style={{ whiteSpace: 'nowrap' }}>per doubling</div>
                  </span>
                ) : '—'}
              </td>
              <td className="num">
                {hms(p.total_seconds)}
                <div className="bar"><i style={{ width: `${Math.round((100 * p.total_seconds) / max)}%` }} /></div>
              </td>
              <td className="num">{p.sessions}</td>
              <td className="num">{p.sessions ? hms(p.avg_seconds) : '—'}</td>
              <td className="num">{p.last_seen ? clock(p.last_seen) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
