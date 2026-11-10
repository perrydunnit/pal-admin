export const hms = (seconds) => {
  let s = Math.max(0, Math.round(Number(seconds) || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m`
  return `${s}s`
}

export const clock = (iso) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

export const clockTime = (iso) =>
  iso ? new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—'

export const fmtFull = (iso) =>
  iso ? new Date(iso).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

export const dayShort = (value) =>
  new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

export const dayLong = (value) =>
  new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })

export const monthLabel = (month) =>
  new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

export const onlineFor = (startIso, now = Date.now()) =>
  startIso ? hms((now - new Date(startIso)) / 1000) : '—'
