import { useState } from 'react'
import MapPlot from './MapPlot.jsx'
import { apiUrl } from '../api.js'
import { actionLabel, colorFor, emblemGrid, healthPct, mapMarkers, speciesKey, worldStatus } from '../world.js'

function HealthBar({ hp, maxHp }) {
  const pct = healthPct(hp, maxHp)
  if (pct == null) return null
  const tone = pct < 25 ? ' crit' : pct < 60 ? ' low' : ''
  return <div className={`bar hp${tone}`}><i style={{ width: `${pct}%` }} /></div>
}

// A portrait the operator dropped into PAL_ICON_DIR, or a generated sigil. No
// artwork is bundled: the game's art is Pocketpair's, so a missing file is the
// normal state and the sigil carries the identity instead.
function PalFace({ pal, icons, size = 16 }) {
  const [broken, setBroken] = useState(false)
  const key = speciesKey(pal)
  if (key && icons.has(key) && !broken) {
    return (
      <img
        className="pal-icon"
        src={apiUrl(`/api/pal-icon/${encodeURIComponent(key)}`)}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        onError={() => setBroken(true)}
      />
    )
  }
  const grid = emblemGrid(key || pal.name)
  const cell = size / grid.length
  return (
    <svg
      className="pal-emblem"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden="true"
      style={{ '--c': colorFor(key || pal.name || '?') }}
    >
      <rect className="pal-emblem-bg" width={size} height={size} rx={size * 0.24} />
      {grid.map((row, y) =>
        row.map((on, x) =>
          on ? (
            <rect
              key={`${x}-${y}`}
              className="pal-emblem-ink"
              x={x * cell + cell * 0.1}
              y={y * cell + cell * 0.1}
              width={cell * 0.8}
              height={cell * 0.8}
              rx={cell * 0.2}
            />
          ) : null,
        ),
      )}
    </svg>
  )
}

function PalCard({ pal, icons, anchor }) {
  const level = pal.level != null ? Math.round(pal.level) : null
  const doing = actionLabel(pal.action)
  const above = anchor.top > (typeof window === 'undefined' ? 0 : window.innerHeight) / 2
  const left = Math.max(8, Math.min(anchor.left, (typeof window === 'undefined' ? 0 : window.innerWidth) - 268))
  return (
    <div
      className={`pal-card${above ? ' above' : ''}`}
      style={above ? { left, bottom: `calc(100% - ${anchor.top - 8}px)` } : { left, top: anchor.bottom + 8 }}
    >
      <div className="pal-card-head">
        <PalFace pal={pal} icons={icons} size={44} />
        <div>
          <b>{pal.name || 'Pal'}</b>
          <div className="muted small">
            {pal.species && pal.species !== pal.name ? `${pal.species} · ` : ''}
            {level != null ? `Lv ${level}` : 'level unknown'}
          </div>
        </div>
      </div>
      {pal.hp != null && pal.max_hp ? (
        <>
          <HealthBar hp={pal.hp} maxHp={pal.max_hp} />
          <div className="muted small">HP {Math.round(pal.hp)} / {Math.round(pal.max_hp)}</div>
        </>
      ) : null}
      <div className="pal-card-rows">
        {doing ? <span>{doing}</span> : null}
        {pal.guild ? <span title="guild">{pal.guild}</span> : null}
        {pal.trainer ? <span title="owner">trained by {pal.trainer}</span> : null}
        {pal.active === false ? <span>idle</span> : null}
      </div>
    </div>
  )
}

function PalChips({ pals, emptyLabel, icons }) {
  const [tip, setTip] = useState(null)
  if (!pals?.length) return <span className="muted">{emptyLabel}</span>

  const show = (event, pal) => {
    const rect = event.currentTarget.getBoundingClientRect()
    setTip({ pal, anchor: { left: rect.left, top: rect.top, bottom: rect.bottom } })
  }
  const hide = () => setTip(null)
  const toggle = (event, pal) => {
    if (tip?.pal === pal) hide()
    else show(event, pal)
  }

  return (
    <div className="chips">
      {pals.map((pal, i) => (
        <span
          className={`pal${tip?.pal === pal ? ' on' : ''}`}
          key={`${pal.id || pal.name}-${i}`}
          style={{ '--c': colorFor(pal.name) }}
          tabIndex={0}
          onMouseEnter={(event) => show(event, pal)}
          onMouseLeave={hide}
          onFocus={(event) => show(event, pal)}
          onBlur={hide}
          onClick={(event) => toggle(event, pal)}
        >
          <PalFace pal={pal} icons={icons} />
          {pal.name || 'Pal'}
          {pal.level != null ? <small>Lv {Math.round(pal.level)}</small> : null}
          {tip?.pal === pal ? <PalCard pal={pal} icons={icons} anchor={tip.anchor} /> : null}
        </span>
      ))}
    </div>
  )
}

export default function WorldPanel({ summary }) {
  const world = summary?.world
  const status = worldStatus(world)
  const markers = mapMarkers(summary)
  const [selectedId, setSelectedId] = useState(null)
  const counts = world?.counts
  const icons = new Set(summary?.pal_icons ?? [])
  const selected = markers.players.find((p) => p.id === selectedId)
  const hasWorld = Boolean(world?.available)

  return (
    <section>
      <div className="world-head">
        <h2>World</h2>
        <div className="world-status">
          <span className={`dot ${status.tone}`} />
          <span>{status.text}</span>
        </div>
      </div>

      <div className="world-grid">
        <div className="world-col">
          <div className="panel">
            <MapPlot
              players={markers.players}
              bases={markers.bases}
              selectedId={selectedId}
              onSelect={(player) => setSelectedId(player.id === selectedId ? null : player.id)}
            />
          </div>

          {selected ? (
            <div className="panel" style={{ padding: '10px 14px' }}>
              <div className="guild-row" style={{ marginBottom: 6 }}>
                <span className="swatch" style={{ background: selected.color }} />
                <b>{selected.name}</b>
                <span className="pill">{selected.guild || 'unknown'}</span>
                {selected.level != null ? <span className="muted">Lv {Math.round(selected.level)}</span> : null}
                <span className="muted">X {selected.mapX} · Y {selected.mapY}</span>
              </div>
              <HealthBar hp={selected.hp} maxHp={selected.maxHp} />
            </div>
          ) : null}

          <div className="chips">
            <span className="stat-chip"><b>{markers.players.length}</b> plotted</span>
            <span className="stat-chip"><b>{markers.bases.length}</b> bases plotted</span>
            {counts ? (
              <>
                <span className="stat-chip">party pals <b>{counts.party_pals}</b></span>
                <span className="stat-chip">base pals <b>{counts.base_pals}</b></span>
                <span className="stat-chip">wild pals <b>{counts.wild_pals}</b></span>
                <span className="stat-chip">NPCs <b>{counts.npcs}</b></span>
              </>
            ) : null}
            <span className="stat-chip">
              <a href="https://mapgenie.io/palworld" target="_blank" rel="noreferrer">interactive map ↗</a>
            </span>
          </div>
        </div>

        <div className="world-col">
          {!hasWorld ? (
            <div className="notice">
              <b>Full world data is waiting on one game-server flag.</b>
              <div style={{ marginTop: 6 }}>
                The REST API exposes <code>/v1/api/game-data</code> — guilds, bases, base workers, party
                pals and HP — but only when the game starts with <code>-EnableGameDataAPI</code>. Until
                then this panel plots live player positions and fills in automatically the moment the
                snapshot appears (checked every {world?.reason === 'disabled' ? '5 minutes' : '20 seconds'}).
              </div>
              {world?.detail ? <div style={{ marginTop: 6 }}><code>{world.detail}</code></div> : null}
            </div>
          ) : null}

          {world?.guilds?.length ? (
            <div className="panel x">
              <table>
                <thead>
                  <tr>
                    <th>Guild</th>
                    <th className="num">Players</th>
                    <th className="num">Bases</th>
                    <th className="num">Pals</th>
                  </tr>
                </thead>
                <tbody>
                  {world.guilds.map((guild) => (
                    <tr key={guild.id || guild.name}>
                      <td>
                        <span className="guild-row">
                          <span className="swatch" style={{ background: colorFor(guild.id || guild.name) }} />
                          {guild.name}
                        </span>
                      </td>
                      <td className="num">{guild.players}</td>
                      <td className="num">{guild.bases}</td>
                      <td className="num">{guild.pals}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {world?.bases?.length ? (
            <div className="base-list">
              {world.bases.map((base) => (
                <div className="base-card" key={base.id || base.name}>
                  <h3>
                    <span className="swatch" style={{ background: colorFor(base.guild_id || base.guild || base.id) }} />
                    {base.name}
                  </h3>
                  <div className="meta">
                    {base.guild || 'unknown guild'} · X {base.map_x} Y {base.map_y} · {base.workers?.length ?? 0} workers
                  </div>
                  <PalChips pals={base.workers} emptyLabel="no workers in range" icons={icons} />
                </div>
              ))}
            </div>
          ) : null}

          {world?.players?.length ? (
            <div className="panel x">
              <table>
                <thead>
                  <tr>
                    <th>Player</th>
                    <th className="num">Level</th>
                    <th>Health</th>
                    <th>Party</th>
                  </tr>
                </thead>
                <tbody>
                  {world.players.map((player) => (
                    <tr key={player.id || player.name}>
                      <td title={player.guild || ''}>
                        <span className="guild-row">
                          <span className="swatch" style={{ background: colorFor(player.guild_id || player.guild || player.id) }} />
                          {player.name}
                        </span>
                      </td>
                      <td className="num">{player.level != null ? Math.round(player.level) : '—'}</td>
                      <td style={{ minWidth: 90 }}>
                        {player.hp != null && player.max_hp ? (
                          <>
                            <div className="muted" style={{ fontSize: 11 }}>{Math.round(player.hp)} / {Math.round(player.max_hp)}</div>
                            <HealthBar hp={player.hp} maxHp={player.max_hp} />
                          </>
                        ) : '—'}
                      </td>
                      <td><PalChips pals={player.party} emptyLabel="solo" icons={icons} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {!hasWorld ? (
            <div className="notice">
              Pal and guild links:{' '}
              <a href="https://paldb.cc/en/Pals" target="_blank" rel="noreferrer">Paldeck ↗</a>
              {' · '}
              <a href="https://palworld.wiki.gg/wiki/Pals" target="_blank" rel="noreferrer">Wiki ↗</a>
            </div>
          ) : null}

          {hasWorld ? (
            <div className="notice">
              {world.days != null ? `Day ${Math.round(world.days)}` : 'World'}
              {world.time ? ` · ${world.time}` : ''}
              {world.counts?.wild_pals ? ` · ${world.counts.wild_pals} wild pals loaded` : ''}
              {world.fps_avg || world.fps ? ` · ${Math.round(world.fps_avg || world.fps)} FPS` : ''}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  )
}
