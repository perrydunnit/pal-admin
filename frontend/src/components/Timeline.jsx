import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { barGeometry, continuationMask, dayIndexAt, dayWidthFor, paneWidths, pickStartDay } from '../timeline.js'
import { colorFor } from '../world.js'
import { dayLong, dayShort, fmtFull, hms } from '../format.js'

function tipFor(session) {
  const rows = [`start ${fmtFull(session.start)}`]
  rows.push(session.ongoing
    ? `still online · ${hms(session.duration_seconds)} so far`
    : `end ${fmtFull(session.end)}`)
  if (!session.ongoing) rows.push(`duration ${hms(session.duration_seconds)}`)
  if (session.continues_before) rows.push('continued from the previous month')
  if (session.continues_after && !session.ongoing) rows.push('continues into the next month')
  return {
    title: `${session.name}${session.platform ? ` · ${session.platform}` : ''}`,
    rows,
  }
}

function cssPx(name) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name)
  return parseFloat(value) || 0
}

export default function Timeline({ timeline, today, selected, onSelect, zoom, apiRef }) {
  const scrollRef = useRef(null)
  const [width, setWidth] = useState(0)
  const [left, setLeft] = useState(() => (typeof window === 'undefined' ? 132 : cssPx('--leftw')))
  const [tip, setTip] = useState(null)
  const focusedMonth = useRef(null)
  const scrollTimer = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return undefined
    setWidth(el.clientWidth)
    const observer = new ResizeObserver(() => setWidth(el.clientWidth))
    observer.observe(el)
    const onResize = () => setLeft(cssPx('--leftw'))
    window.addEventListener('resize', onResize)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', onResize)
    }
  }, [timeline])

  const dayWidth = width ? dayWidthFor(width, left, zoom) : 0
  const { W, xs } = timeline && dayWidth
    ? paneWidths(timeline.days, dayWidth)
    : { W: 0, xs: [] }

  const focusDay = useCallback((date, smooth) => {
    const el = scrollRef.current
    if (!el || !timeline || !dayWidth || !date) return
    const idx = timeline.days.findIndex((d) => d.date === date)
    if (idx < 0) return
    const x = paneWidths(timeline.days, dayWidth).xs[idx]
    el.scrollTo({ left: x, behavior: smooth ? 'smooth' : 'auto' })
    onSelect(date)
  }, [timeline, dayWidth, onSelect])

  useImperativeHandle(apiRef, () => ({ focusDay }), [focusDay])

  const prevZoom = useRef(zoom)
  useEffect(() => {
    if (prevZoom.current === zoom) return
    prevZoom.current = zoom
    if (selected) focusDay(selected, false)
  }, [zoom, selected, focusDay])

  useEffect(() => {
    if (!timeline || !dayWidth) return
    if (focusedMonth.current === timeline.month) return
    focusedMonth.current = timeline.month
    focusDay(pickStartDay(timeline.days, today), false)
  }, [timeline, dayWidth, today, focusDay])

  const onScroll = () => {
    if (scrollTimer.current) clearTimeout(scrollTimer.current)
    scrollTimer.current = setTimeout(() => {
      if (!timeline || !dayWidth || !scrollRef.current) return
      const idx = dayIndexAt(xs, scrollRef.current.scrollLeft)
      const day = timeline.days[idx]
      if (day) onSelect(day.date)
    }, 120)
  }

  if (!timeline) {
    return (
      <div className="panel">
        <div className="tlx" ref={scrollRef}>
          <div className="empty">Loading timeline…</div>
        </div>
      </div>
    )
  }

  const byPlayer = new Map()
  for (const session of timeline.sessions) {
    if (!byPlayer.has(session.player_id)) byPlayer.set(session.player_id, [])
    byPlayer.get(session.player_id).push(session)
  }

  const showTip = (event, session) => {
    const text = tipFor(session)
    setTip({ ...text, x: event.clientX, y: event.clientY, below: event.clientY < 130 })
  }

  return (
    <div className="panel">
      <div className="tlx" ref={scrollRef} onScroll={onScroll}>
        <div className="tlx-inner">
          <div className="overlay" style={{ left: 'var(--leftw)', width: `${W}px` }}>
            {timeline.days.map((day, i) => (
              <span key={`${day.date}-lines`}>
                {i > 0 ? <div className="dayline" style={{ left: `${xs[i]}px` }} /> : null}
                <div className="noonline" style={{ left: `${day.mid_frac * W}px` }} />
              </span>
            ))}
            {timeline.now_frac != null
              ? <div className="nowline" style={{ left: `${timeline.now_frac * W}px` }} />
              : null}
          </div>

          <div className="tl-row head">
            <div className={`tl-left${selected === today ? ' is-today' : ''}`}>
              {selected ? dayLong(selected) : '—'}
            </div>
            <div className="tl-track" style={{ width: `${W}px` }}>
              {timeline.days.map((day, i) => (
                <span
                  key={day.date}
                  className={`daylabel${day.date === today ? ' today' : ''}`}
                  style={{ left: `${xs[i]}px` }}
                >
                  {dayShort(day.date)}
                </span>
              ))}
            </div>
          </div>

          {[...byPlayer.entries()].map(([pid, sessions]) => {
            const first = sessions[0]
            return (
              <div className="tl-row" key={pid} data-player={pid}>
                <div className="tl-left">
                  <span className="swatch" style={{ background: colorFor(pid) }} />
                  <b>{first.name}</b>
                  <span className="plat">{first.platform || ''}</span>
                </div>
                <div className="tl-track" style={{ width: `${W}px` }}>
                  {sessions.map((session) => {
                    const { left: barLeft, width: barWidth } = barGeometry(session, W)
                    const classes = `seg${session.ongoing ? ' open' : ''}`
                    return (
                      <i
                        key={session.id}
                        className={classes}
                        style={{
                          left: `${barLeft}px`,
                          width: `${barWidth}px`,
                          '--c': colorFor(pid),
                          '--mask': continuationMask(session),
                        }}
                        onMouseEnter={(event) => showTip(event, session)}
                        onMouseMove={(event) => showTip(event, session)}
                        onMouseLeave={() => setTip(null)}
                      />
                    )
                  })}
                </div>
              </div>
            )
          })}

          {byPlayer.size === 0 ? (
            <div className="tl-row">
              <div className="tl-left"><span className="plat">no data</span></div>
              <div className="tl-track" style={{ width: `${W}px` }} />
            </div>
          ) : null}
        </div>
      </div>

      <div
        className={`tip${tip ? ' on' : ''}${tip?.below ? ' below' : ''}`}
        style={tip ? { left: `${tip.x}px`, top: `${tip.below ? tip.y + 18 : tip.y - 14}px` } : undefined}
      >
        {tip ? (
          <>
            <b>{tip.title}</b>
            {tip.rows.map((row, i) => (
              <div className={`row${i > 1 && /continued|continues/.test(row) ? ' note' : ''}`} key={row}>{row}</div>
            ))}
          </>
        ) : null}
      </div>
    </div>
  )
}
