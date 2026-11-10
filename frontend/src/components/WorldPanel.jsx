import { useState } from 'react'
import MapPlot from './MapPlot.jsx'
import { colorFor, healthPct, mapMarkers, worldStatus } from '../world.js'

function HealthBar({ hp, maxHp }) {
  const pct = healthPct(hp, maxHp)
  if (pct == null) return null
  const tone = pct < 25 ? ' crit' : pct < 60 ? ' low' : ''
  return <div className={`bar hp${tone}`}><i style={{ width: `${pct}%` }} /></div>
}

function PalChips({ pals, emptyLabel }) {
  if (!pals?.length) return <span className="muted">{emptyLabel}</span>
  return (
    <div className="chips">
      {pals.map((pal, i) => (
        <span className="pal" key={`${pal.id || pal.name}-${i}`} style={{ '--c': colorFor(pal.name) }}>
          {pal.name || 'Pal'}
          {pal.level != null ? <small>Lv {Math.round(pal.level)}</small> : null}
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
                  <PalChips pals={base.workers} emptyLabel="no workers in range" />
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
                      <td><PalChips pals={player.party} emptyLabel="solo" /></td>
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
