import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchJSON, useLiveSummary } from './api.js'
import Header from './components/Header.jsx'
import Cards from './components/Cards.jsx'
import WorldPanel from './components/WorldPanel.jsx'
import LivePlayers from './components/LivePlayers.jsx'
import ChatPanel from './components/ChatPanel.jsx'
import MusicPanel from './components/MusicPanel.jsx'
import PlayerStats from './components/PlayerStats.jsx'
import Calendar from './components/Calendar.jsx'
import Timeline from './components/Timeline.jsx'
import ServerConfig from './components/ServerConfig.jsx'

const MIN_ZOOM = 0.5
const MAX_ZOOM = 3

export default function App() {
  const { summary, live, tick } = useLiveSummary()
  const [month, setMonth] = useState(null)
  const [view, setView] = useState({ month: null, days: [] })
  const [timeline, setTimeline] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [selected, setSelected] = useState(null)
  const [zoom, setZoom] = useState(1)
  const timelineApi = useRef(null)

  useEffect(() => {
    if (summary && !month) {
      setMonth(summary.month)
      setView({ month: summary.month, days: summary.days })
    }
  }, [summary, month])

  // The calendar follows the month being viewed, not the live one.
  useEffect(() => {
    if (!month) return undefined
    let cancelled = false
    fetchJSON(`/api/summary?month=${month}`)
      .then((data) => {
        if (!cancelled) {
          setView({ month: data.month, days: data.days })
          setLoadError(null)
        }
      })
      .catch((err) => {
        console.error('month load failed', err)
        if (!cancelled) setLoadError(`calendar month failed to load (${err.message})`)
      })
    return () => { cancelled = true }
  }, [month, reloadKey])

  // A push that belongs to the visible month refreshes its cells in place.
  useEffect(() => {
    if (!summary || summary.month !== view.month) return
    setView({ month: summary.month, days: summary.days })
  }, [summary, view.month])

  const refreshTimeline = useCallback(async (target) => {
    try {
      const data = await fetchJSON(`/api/timeline?month=${target}`)
      if (data.month === target) {
        setTimeline(data)
        setLoadError(null)
      }
    } catch (err) {
      console.error('timeline load failed', err)
      setLoadError(`timeline failed to load (${err.message})`)
    }
  }, [])

  useEffect(() => {
    if (month) refreshTimeline(month)
  }, [month, refreshTimeline, reloadKey])

  // Sessions change on every push: refetch once per burst rather than per frame.
  useEffect(() => {
    if (!month || tick === 0 || summary?.month !== month) return undefined
    const timer = setTimeout(() => refreshTimeline(month), 400)
    return () => clearTimeout(timer)
  }, [tick, month, summary?.month, refreshTimeline])

  const zoomBy = (delta) =>
    setZoom((value) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Number((value + delta).toFixed(2)))))

  if (!summary) {
    return (
      <div className="wrap">
        <div className="empty">Connecting to the dashboard…</div>
      </div>
    )
  }

  return (
    <div className="wrap">
      <Header summary={summary} live={live} />
      <Cards summary={summary} />

      <WorldPanel summary={summary} />

      <section>
        <h2>Online now</h2>
        <LivePlayers summary={summary} />
      </section>

      <section>
        <h2>Chat</h2>
        <ChatPanel summary={summary} />
      </section>

      <section>
        <h2>Music library</h2>
        <MusicPanel summary={summary} />
      </section>

      <section>
        <h2>Player stats</h2>
        <PlayerStats summary={summary} />
      </section>

      <section>
        <h2>Who was online</h2>
        {loadError && (
          <div className="notice error small">
            {loadError}. The live panes above are unaffected.{' '}
            <button type="button" className="btn ghost small" onClick={() => setReloadKey((n) => n + 1)}>
              Retry
            </button>
          </div>
        )}
        <Calendar
          month={month}
          days={view.days}
          today={summary.today}
          selected={selected}
          onMonthChange={setMonth}
          onSelectDay={(date) => timelineApi.current?.focusDay(date, true)}
        />
      </section>

      <section>
        <div className="detail-head">
          <h2>Day detail{timeline ? ` — ${timeline.month_label}` : ''}</h2>
          <details className="zoom" aria-label="Timeline zoom">
            <summary id="zoomSummary" title="Adjust timeline zoom">{Math.round(zoom * 100)}%</summary>
            <div className="zoom-controls">
              <button
                type="button"
                aria-label="Zoom out"
                title="Zoom out"
                disabled={zoom <= MIN_ZOOM}
                onClick={() => zoomBy(-0.25)}
              >
                −
              </button>
              <input
                type="range"
                min={MIN_ZOOM * 100}
                max={MAX_ZOOM * 100}
                step="25"
                value={Math.round(zoom * 100)}
                aria-label="Zoom level"
                onChange={(event) => setZoom(Number(event.target.value) / 100)}
              />
              <output>{Math.round(zoom * 100)}%</output>
              <button
                type="button"
                aria-label="Zoom in"
                title="Zoom in"
                disabled={zoom >= MAX_ZOOM}
                onClick={() => zoomBy(0.25)}
              >
                +
              </button>
            </div>
          </details>
        </div>
        <Timeline
          timeline={timeline}
          today={summary.today}
          selected={selected}
          onSelect={setSelected}
          zoom={zoom}
          apiRef={timelineApi}
        />
      </section>

      <section>
        <h2>Server config</h2>
        <ServerConfig />
      </section>

      <div className="foot">
        <span className={`dot ${live ? 'on' : 'off'}`} />
        <span>{live ? 'Live — updates are pushed as they happen' : 'Reconnecting — falling back to a 30 s refresh'}</span>
        <span>· last update {new Date().toLocaleTimeString()}</span>
        {summary.server?.version ? <span>· server {summary.server.version}</span> : null}
      </div>
    </div>
  )
}
