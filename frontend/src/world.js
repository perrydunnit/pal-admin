const GUILD_COLORS = ['--p1', '--p2', '--p3', '--p4', '--p5', '--p6']

export function colorFor(seed) {
  let h = 0
  for (const ch of String(seed ?? '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return `var(${GUILD_COLORS[h % GUILD_COLORS.length]})`
}

export function healthPct(hp, maxHp) {
  if (!Number.isFinite(hp) || !Number.isFinite(maxHp) || maxHp <= 0) return null
  return Math.max(0, Math.min(100, Math.round((hp / maxHp) * 100)))
}

// The world panel plots game-data actors when the snapshot is available and
// falls back to the REST player list (positions are always reported there), so
// the map works today and gains bases/pals the moment the flag is enabled.
export function mapMarkers(summary) {
  const world = summary?.world
  if (world?.available) {
    return {
      source: 'game-data',
      players: (world.players || [])
        .filter((p) => p.px != null && p.py != null)
        .map((p) => ({
          id: p.id || p.name,
          name: p.name || '?',
          level: p.level,
          guild: p.guild,
          hp: p.hp,
          maxHp: p.max_hp,
          px: p.px,
          py: p.py,
          mapX: p.map_x,
          mapY: p.map_y,
          active: p.active,
          color: colorFor(p.guild_id || p.guild || p.id),
        })),
      bases: (world.bases || [])
        .filter((b) => b.px != null && b.py != null)
        .map((b) => ({
          id: b.id || b.name,
          name: b.name || 'Base camp',
          guild: b.guild,
          px: b.px,
          py: b.py,
          mapX: b.map_x,
          mapY: b.map_y,
          workers: (b.workers || []).length,
          color: colorFor(b.guild_id || b.guild || b.id),
        })),
    }
  }
  return {
    source: 'rest',
    players: (summary?.players || [])
      .filter((p) => p.online && p.map)
      .map((p) => ({
        id: p.player_id,
        name: p.name,
        level: p.level,
        guild: p.platform,
        px: p.map.px,
        py: p.map.py,
        mapX: p.map.map_x,
        mapY: p.map.map_y,
        active: true,
        color: colorFor(p.player_id),
      })),
    bases: [],
  }
}

export function worldStatus(world) {
  if (!world) return { tone: 'wait', text: 'Checking for world data…' }
  if (world.available) return { tone: 'on', text: `Live world snapshot · ${world.ts || ''}` }
  if (world.reason === 'disabled') {
    return {
      tone: 'off',
      text: 'World snapshot needs the game server to run with -EnableGameDataAPI (then restart it).',
    }
  }
  if (world.reason === 'unsupported') {
    return { tone: 'off', text: 'This server build does not expose /v1/api/game-data.' }
  }
  if (world.reason === 'error') {
    return { tone: 'warn', text: `World snapshot unavailable: ${world.detail || 'request failed'}` }
  }
  return { tone: 'wait', text: 'Checking for world data…' }
}
