import { calendarCells } from '../timeline.js'
import { colorFor } from '../world.js'
import { dayLong, hms, monthLabel } from '../format.js'

function shiftMonth(month, delta) {
  const [year, mon] = month.split('-').map(Number)
  const d = new Date(year, mon - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function Cell({ day, onSelectDay }) {
  if (!day) return <div className="cell blank" />
  const chips = day.players.slice(0, 3).map((p) => (
    <span className="chip" key={p.player_id} style={{ borderLeftColor: colorFor(p.player_id) }}>
      {p.name} · {hms(p.seconds)}
    </span>
  ))
  const more = day.players.length > 3
    ? <span className="chip" style={{ borderLeftColor: '#3a4150', color: '#8b93a4' }}>+{day.players.length - 3} more</span>
    : null

  return (
    <div
      className={day.classes.join(' ')}
      data-date={day.date}
      onClick={() => onSelectDay(day.date)}
      title={day.total ? `${dayLong(day.date)} · ${hms(day.total)}` : dayLong(day.date)}
    >
      <div className="d">{Number(day.date.slice(-2))}</div>
      {day.total ? <span className="tot">{hms(day.total)}</span> : null}
      <div className="chips">{chips}{more}</div>
    </div>
  )
}

export default function Calendar({ month, days, today, selected, onMonthChange, onSelectDay }) {
  const cells = month ? calendarCells(month, days, today, selected) : []
  const label = month ? monthLabel(month) : '…'

  return (
    <div className="panel">
      <div className="cal-head">
        <button type="button" onClick={() => onMonthChange(shiftMonth(month, -1))} aria-label="Previous month">‹</button>
        <span className="label">{label}</span>
        <button type="button" onClick={() => onMonthChange(shiftMonth(month, 1))} aria-label="Next month">›</button>
      </div>
      <div className="dow">
        <span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span>
      </div>
      <div className="grid">
        {cells.map((day, i) => (
          <Cell key={day ? day.date : `blank-${i}`} day={day} onSelectDay={onSelectDay} />
        ))}
      </div>
    </div>
  )
}
