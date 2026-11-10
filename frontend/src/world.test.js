import { describe, expect, it } from 'vitest'
import { colorFor, healthPct, mapMarkers, worldStatus } from './world.js'

const restSummary = {
  players: [
    { player_id: 'p1', name: 'exampleplayer', online: true, level: 43, platform: 'ps5', map: { px: 0.68, py: 0.48, map_x: -336, map_y: 282 } },
    { player_id: 'p2', name: 'Player2', online: false, level: null, platform: 'ps5', map: null },
  ],
}

describe('world helpers', () => {
  it('maps a guild to a stable css colour', () => {
    expect(colorFor('guild-1')).toBe(colorFor('guild-1'))
    expect(colorFor('guild-1')).toMatch(/^var\(--p[1-6]\)$/)
  })

  it('computes clamped health percentages', () => {
    expect(healthPct(50, 100)).toBe(50)
    expect(healthPct(200, 100)).toBe(100)
    expect(healthPct(-5, 100)).toBe(0)
    expect(healthPct(1, 0)).toBe(null)
    expect(healthPct(undefined, 100)).toBe(null)
  })

  it('plots REST players while game-data is disabled', () => {
    const { source, players, bases } = mapMarkers(restSummary)
    expect(source).toBe('rest')
    expect(players).toHaveLength(1)
    expect(players[0]).toMatchObject({ name: 'exampleplayer', mapX: -336, mapY: 282 })
    expect(bases).toEqual([])
  })

  it('plots game-data actors and bases once the snapshot is available', () => {
    const summary = {
      players: restSummary.players,
      world: {
        available: true,
        players: [{ id: 'P1', name: 'exampleplayer', guild: 'Pals', guild_id: 'G1', px: 0.68, py: 0.48, map_x: -336, map_y: 282, level: 43 }],
        bases: [{ id: 'B1', name: 'Main base', guild: 'Pals', guild_id: 'G1', px: 0.67, py: 0.49, map_x: -330, map_y: 300, workers: [{}, {}] }],
      },
    }
    const { source, players, bases } = mapMarkers(summary)
    expect(source).toBe('game-data')
    expect(players[0].guild).toBe('Pals')
    expect(bases[0]).toMatchObject({ name: 'Main base', workers: 2 })
  })

  it('explains why the snapshot is missing', () => {
    expect(worldStatus({ available: false, reason: 'disabled' }).tone).toBe('off')
    expect(worldStatus({ available: false, reason: 'disabled' }).text).toContain('-EnableGameDataAPI')
    expect(worldStatus({ available: true, ts: 'now' }).tone).toBe('on')
    expect(worldStatus({ available: false, reason: 'error', detail: 'HTTP 500' }).text).toContain('HTTP 500')
    expect(worldStatus(null).tone).toBe('wait')
  })
})
