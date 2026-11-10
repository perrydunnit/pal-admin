// Pure timeline geometry. Everything is expressed as fractions of the month in
// the API payload; the client only multiplies by a per-day width.

export const dayWidthFor = (containerWidth, leftWidth, zoom) =>
  Math.max(1, containerWidth - leftWidth) * zoom

export function paneWidths(days, dayWidth) {
  const W = days.length * dayWidth
  return { W, xs: days.map((d) => d.start_frac * W) }
}

export function dayIndexAt(xs, x) {
  let idx = 0
  for (let i = 0; i < xs.length; i += 1) if (xs[i] <= x + 0.5) idx = i
  return idx
}

export function barGeometry(session, W) {
  return {
    left: session.start_frac * W,
    width: Math.max(3, (session.end_frac - session.start_frac) * W),
  }
}

export function continuationMask(session) {
  const fades = session.continues_before && (session.continues_after || session.ongoing)
  if (fades) {
    return 'linear-gradient(90deg, transparent 0, #000 16px, #000 calc(100% - 16px), transparent 100%)'
  }
  if (session.continues_before) return 'linear-gradient(90deg, transparent 0, #000 16px)'
  if (session.continues_after) return 'linear-gradient(90deg, #000 calc(100% - 16px), transparent 100%)'
  return 'none'
}

export function pickStartDay(days, today) {
  if (!days.length) return null
  if (days.some((d) => d.date === today)) return today
  const withData = days.find((d) => d.total)
  return withData ? withData.date : days[0].date
}

// Calendar cells: leading blanks so the 1st lands on its weekday (weeks start
// on Monday), then every day of the month.
export function calendarCells(month, days, today, selected) {
  const [year, mon] = month.split('-').map(Number)
  const firstDow = (new Date(year, mon - 1, 1).getDay() + 6) % 7
  const cells = Array.from({ length: firstDow }, () => null)
  for (const day of days) {
    const classes = ['cell']
    if (day.date === today) classes.push('today')
    if (day.total) classes.push('has')
    if (day.date === selected) classes.push('sel')
    cells.push({ ...day, classes })
  }
  return cells
}
