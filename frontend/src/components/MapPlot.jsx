const GRID = [10, 20, 30, 40, 50, 60, 70, 80, 90]

// A coordinate plot, not the game's map texture: the Palpagos artwork is
// Pocketpair's and is deliberately not bundled or hotlinked. Positions use the
// community calibration (see docs/ARCHITECTURE.md), so the dots still land in
// the right place relative to each other.
export default function MapPlot({ players, bases, selectedId, onSelect }) {
  return (
    <div className="mapwrap">
      <svg className="map" viewBox="0 0 100 100" role="img" aria-label="Live positions, schematic coordinate plot">
        <rect x="0" y="0" width="100" height="100" fill="none" stroke="#232b36" strokeWidth="0.4" />
        {GRID.map((v) => (
          <g key={v}>
            <line className="grid-line" x1={v} y1="0" x2={v} y2="100" />
            <line className="grid-line" x1="0" y1={v} x2="100" y2={v} />
          </g>
        ))}
        <text className="map-label" x="2" y="5">N</text>
        <text className="map-label" x="94" y="5">E</text>

        {bases.map((base) => (
          <g className="base" key={base.id} transform={`translate(${base.px * 100} ${base.py * 100})`}>
            <rect x="-2.6" y="-2.6" width="5.2" height="5.2" rx="1" style={{ stroke: base.color }} />
            <text x="0" y="-4.2" textAnchor="middle">{base.name}</text>
          </g>
        ))}

        {players.map((player) => {
          const selected = player.id === selectedId
          return (
            <g
              className="player"
              key={player.id}
              transform={`translate(${player.px * 100} ${player.py * 100})`}
              onClick={() => onSelect?.(player)}
            >
              <title>{`${player.name}${player.guild ? ` · ${player.guild}` : ''} · X ${player.mapX} Y ${player.mapY}`}</title>
              <circle r={selected ? 2.3 : 1.7} style={{ fill: player.color }} />
              <text x="0" y={selected ? -3.6 : -3.2} textAnchor="middle">{player.name}</text>
            </g>
          )
        })}
      </svg>
      <div className="map-hint">
        schematic coordinate plot ·{' '}
        <a href="https://docs.palworldgame.com/api/rest-api/game-data/" target="_blank" rel="noreferrer">game-data</a>
      </div>
    </div>
  )
}
