import { describe, expect, it } from 'vitest'
import {
  barGeometry,
  calendarCells,
  continuationMask,
  dayIndexAt,
  dayWidthFor,
  paneWidths,
  pickStartDay,
} from './timeline.js'

describe('timeline geometry', () => {
  it('sizes a day pane from the scroll width and zoom', () => {
    expect(dayWidthFor(1000, 132, 1)).toBe(868)
    expect(dayWidthFor(1000, 132, 2)).toBe(1736)
    expect(dayWidthFor(100, 132, 1)).toBe(1)
  })

  it('places day boundaries against the month width', () => {
    const { W, xs } = paneWidths([{ start_frac: 0 }, { start_frac: 0.5 }], 100)
    expect(W).toBe(200)
    expect(xs).toEqual([0, 100])
  })

  it('finds the day under a scroll offset', () => {
    expect(dayIndexAt([0, 100, 200], 0)).toBe(0)
    expect(dayIndexAt([0, 100, 200], 99)).toBe(0)
    expect(dayIndexAt([0, 100, 200], 100)).toBe(1)
    expect(dayIndexAt([0, 100, 200], 999)).toBe(2)
  })

  it('never renders a zero-width bar', () => {
    expect(barGeometry({ start_frac: 0.2, end_frac: 0.2 }, 1000)).toEqual({ left: 200, width: 3 })
  })

  it('fades the edges of a session that leaves the window', () => {
    expect(continuationMask({ continues_before: true, continues_after: true })).toContain('transparent 0')
    expect(continuationMask({ continues_before: true })).toBe('linear-gradient(90deg, transparent 0, #000 16px)')
    expect(continuationMask({ continues_after: true })).toContain('calc(100% - 16px)')
    expect(continuationMask({})).toBe('none')
  })

  it('focuses today, else the first day with data', () => {
    const days = [{ date: '2026-11-01', total: 0 }, { date: '2026-11-02', total: 10 }]
    expect(pickStartDay(days, '2026-11-09')).toBe('2026-11-02')
    expect(pickStartDay([{ date: '2026-11-01', total: 0 }, { date: '2026-11-09', total: 0 }], '2026-11-09')).toBe('2026-11-09')
    expect(pickStartDay([], '2026-11-09')).toBe(null)
  })
})

describe('calendar cells', () => {
  it('pads the first week so the 1st lands on its weekday', () => {
    // 2026-11-01 is a Sunday -> 6 blanks in a Monday-first grid
    const days = [{ date: '2026-11-01', total: 0, players: [] }, { date: '2026-11-02', total: 5, players: [] }]
    const cells = calendarCells('2026-11', days, '2026-11-02', '2026-11-01')
    expect(cells.slice(0, 6).every((c) => c === null)).toBe(true)
    expect(cells[6].classes).toContain('sel')
    expect(cells[7].classes).toContain('today')
    expect(cells[7].classes).toContain('has')
  })
})
