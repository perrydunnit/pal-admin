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

// `BP_ActionSleep` -> "Sleep": the game reports blueprint names for what a pal
// is doing, which is not something to show a person verbatim.
export function actionLabel(action) {
  const raw = String(action ?? '').replace(/^BP_Action/, '').replace(/_C$/, '')
  if (!raw) return ''
  const spaced = raw.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

// The species key an icon file would be named after, lowercased.
export function speciesKey(pal) {
  return String(pal?.species || pal?.name || '').toLowerCase()
}

// A symmetric five-by-five grid derived from the seed. It is the fallback
// portrait: distinct per pal, identical on every reload, and generated here
// rather than copied from anyone's artwork. Cells are mirrored so the tile reads
// as a sigil instead of noise.
export function emblemGrid(seed, size = 5) {
  let h = 2166136261 >>> 0
  for (const ch of String(seed ?? '')) {
    h ^= ch.charCodeAt(0)
    h = Math.imul(h, 16777619) >>> 0
  }
  const next = () => {
    h = (Math.imul(h, 1664525) + 1013904223) >>> 0
    return ((h >>> 17) & 1) === 1
  }
  const half = Math.ceil(size / 2)
  const grid = []
  for (let y = 0; y < size; y += 1) {
    const row = []
    for (let x = 0; x < half; x += 1) row[x] = next()
    for (let x = half; x < size; x += 1) row[x] = row[size - 1 - x]
    grid.push(row)
  }
  // An almost-empty tile is unreadable at 16px, so guarantee a mark.
  const filled = grid.flat().filter(Boolean).length
  if (filled < 4) {
    const mid = Math.floor(size / 2)
    grid[mid][mid] = true
    grid[mid][mid - 1] = true
    grid[mid][mid + 1] = true
  }
  return grid
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
