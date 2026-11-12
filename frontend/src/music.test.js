import { describe, expect, it } from 'vitest'
import {
  extensionOf,
  formatBytes,
  isAccepted,
  kindLabel,
  sortTracks,
  usage,
  validateFile,
} from './music.js'

const MB = 1024 * 1024

describe('music helpers', () => {
  it('formats byte counts', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2 KB')
    expect(formatBytes(5 * MB)).toBe('5.0 MB')
    expect(formatBytes(3 * 1024 * MB)).toBe('3.00 GB')
    expect(formatBytes(undefined)).toBe('0 B')
  })

  it('reads extensions case-insensitively', () => {
    expect(extensionOf('Clair de Lune.MP3')).toBe('.mp3')
    expect(extensionOf('noext')).toBe('')
    expect(extensionOf(undefined)).toBe('')
  })

  it('accepts the formats the mod plays', () => {
    expect(isAccepted('a.mid')).toBe(true)
    expect(isAccepted('a.FLAC')).toBe(true)
    expect(isAccepted('a.jpg')).toBe(false)
    expect(isAccepted('a')).toBe(false)
  })

  it('labels kinds', () => {
    expect(kindLabel('midi')).toBe('MIDI')
    expect(kindLabel('audio')).toBe('Audio')
  })

  it('validates before uploading', () => {
    expect(validateFile(null)).toMatch(/no file/)
    expect(validateFile({ name: 'x.exe', size: 10 })).toMatch(/unsupported/)
    expect(validateFile({ name: 'x.mp3', size: 0 })).toMatch(/empty/)
    expect(validateFile({ name: 'x.mp3', size: 2 * MB }, MB)).toMatch(/larger/)
    expect(validateFile({ name: 'x.mp3', size: 1024 })).toBeNull()
  })

  it('sorts tracks case-insensitively and drops junk', () => {
    const sorted = sortTracks([
      { id: '2', name: 'banana' },
      { id: '1', name: 'Apple' },
      null,
      { name: 'no id' },
    ])
    expect(sorted.map((t) => t.name)).toEqual(['Apple', 'banana'])
  })

  it('summarises usage', () => {
    const result = usage([{ bytes: MB }, { bytes: 3 * MB }], 8 * MB)
    expect(result.count).toBe(2)
    expect(result.bytes).toBe(4 * MB)
    expect(result.label).toBe('4.0 MB of 8.0 MB')
    expect(result.share).toBeCloseTo(0.5)
    expect(usage(null).count).toBe(0)
  })
})
