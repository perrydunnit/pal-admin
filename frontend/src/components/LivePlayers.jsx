import { useEffect, useState } from 'react'
import { onlineFor } from '../format.js'

function useNow(intervalMs = 15000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

export default function LivePlayers({ summary }) {
  const now = useNow()
  const live = (summary?.players ?? []).filter((p) => p.online)

  if (!live.length) return <div className="panel x"><div className="empty">Nobody online right now.</div></div>

  return (
    <div className="panel x">
      <table>
        <thead>
          <tr>
            <th>Player</th>
            <th>Platform</th>
            <th className="num">Level</th>
            <th>Online for</th>
            <th className="num">Ping</th>
            <th className="num">Map</th>
          </tr>
        </thead>
        <tbody>
          {live.map((p) => (
            <tr key={p.player_id}>
              <td title={[p.account_name, p.ip].filter(Boolean).join(' · ')}>
                <span className="dot on" /> {p.name}
              </td>
              <td><span className="pill">{p.platform}</span></td>
              <td className="num">{p.level ?? '—'}</td>
              <td>{onlineFor(p.session_started, now)}</td>
              <td className="num">{p.ping != null ? `${Math.round(p.ping)} ms` : '—'}</td>
              <td className="num muted">{p.map ? `${p.map.map_x}, ${p.map.map_y}` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
