import { describe, expect, it } from 'vitest'
import { buildChanges, currentValue, isDirty, matchesFilter, originalOf } from './config.js'

const row = (over = {}) => ({
  key: 'ExpRate',
  kind: 'float',
  description: 'Experience multiplier',
  value: '1',
  default: '1',
  ...over,
})

describe('originalOf', () => {
  it('falls back to the default when the file has no value', () => {
    expect(originalOf(row({ value: null, default: '1.5' }))).toBe('1.5')
    expect(originalOf(row({ value: '2', default: '1' }))).toBe('2')
  })

  it('never exposes a secret', () => {
    expect(originalOf(row({ secret: true, value: null }))).toBe('')
  })
})

describe('isDirty', () => {
  it('ignores untouched rows', () => {
    expect(isDirty(row(), {}, new Set())).toBe(false)
    expect(isDirty(row({ value: null, default: '1.5' }), {}, new Set())).toBe(false)
  })

  it('flags an edited value, including one returned to the original', () => {
    expect(isDirty(row(), { ExpRate: '2' }, new Set())).toBe(true)
    expect(isDirty(row(), { ExpRate: '1' }, new Set())).toBe(false)
  })

  it('treats a blank secret as unchanged and a typed one as a change', () => {
    const secret = row({ key: 'ServerPassword', secret: true })
    expect(isDirty(secret, {}, new Set())).toBe(false)
    expect(isDirty(secret, { ServerPassword: 'hunter2' }, new Set())).toBe(true)
    expect(isDirty(secret, {}, new Set(['ServerPassword']))).toBe(true)
  })
})

describe('buildChanges', () => {
  it('sends only the edited rows', () => {
    const rows = [row(), row({ key: 'bIsPvP', kind: 'bool', value: 'False' })]
    const changes = buildChanges(rows, { bIsPvP: 'True' }, new Set())
    expect(changes).toEqual({ bIsPvP: 'True' })
  })

  it('sends an empty string for a cleared secret', () => {
    const secret = row({ key: 'ServerPassword', secret: true })
    expect(buildChanges([secret], {}, new Set(['ServerPassword']))).toEqual({
      ServerPassword: '',
    })
  })

  it('sends nothing when nothing changed', () => {
    expect(buildChanges([row()], {}, new Set())).toEqual({})
  })
})

describe('currentValue', () => {
  it('shows the draft, then the file value, then the default', () => {
    expect(currentValue(row({ value: '2' }), { ExpRate: '3' })).toBe('3')
    expect(currentValue(row({ value: '2' }), {})).toBe('2')
    expect(currentValue(row({ value: null, default: '1.5' }), {})).toBe('1.5')
  })
})

describe('matchesFilter', () => {
  it('matches the key or the description', () => {
    expect(matchesFilter(row(), '')).toBe(true)
    expect(matchesFilter(row(), 'exprate')).toBe(true)
    expect(matchesFilter(row(), 'multiplier')).toBe(true)
    expect(matchesFilter(row(), 'guild')).toBe(false)
  })
})
