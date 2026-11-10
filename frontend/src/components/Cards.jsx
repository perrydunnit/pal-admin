import { hms } from '../format.js'

export default function Cards({ summary }) {
  const players = summary?.players ?? []
  const metrics = summary?.metrics
  const online = players.filter((p) => p.online).length
  const totalSeconds = players.reduce((sum, p) => sum + (p.total_seconds || 0), 0)
  const sessions = players.reduce((sum, p) => sum + (p.sessions || 0), 0)
  const cap = metrics?.maxplayernum
  const fps = metrics?.fps
  const fpsAvg = metrics?.fps_avg

  const cards = [
    ['Online', online, cap ? `of ${cap}` : `${players.length} known`],
    ['Server FPS', fps != null ? Math.round(fps) : '—', fpsAvg != null ? `avg ${Math.round(fpsAvg)}` : ''],
    ['World day', metrics?.days ?? '—', ''],
    ['Base camps', metrics?.basecamps ?? '—', ''],
    ['Total playtime', hms(totalSeconds), 'all players'],
    ['Sessions', sessions, 'tracked'],
  ]

  return (
    <div className="cards">
      {cards.map(([key, value, small]) => (
        <div className="card" key={key}>
          <div className="k">{key}</div>
          <div className="v">
            {value}
            {small ? <small> {small}</small> : null}
          </div>
        </div>
      ))}
    </div>
  )
}
