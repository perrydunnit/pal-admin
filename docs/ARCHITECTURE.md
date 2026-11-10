# Architecture

## Shape

Two loops, a push fan-out and a schema, all in `app.py`:

```
                 ┌────────────────────────────────────┐
  docker.sock ──▶│ follow_logs()   streaming          │──┐ join/leave ─▶ wake
  (read-only)    │  join/leave/chat lines → tables    │  │ chat ───────▶ publish()
                 └────────────────────────────────────┘  │
                                                         ├──▶ SQLite ──▶ endpoints ──▶ React app
                 ┌────────────────────────────────────┐  │                  │
  REST API    ──▶│ poll_api()  heartbeat + on wake    │──┘                  ▼
  :8212          │  roster/metrics/info → tables      │            /api/events (SSE)
                 ├────────────────────────────────────┤
                 │ poll_world()  every 20 s           │──▶ world snapshot (memory)
                 │  /v1/api/game-data → projection    │
                 └────────────────────────────────────┘
```

The collector and the log follower are daemon threads started at import
(`init_db()` runs there too), so they also run under gunicorn. **One worker
only** — more would poll and write concurrently, and the election logic assumes
a single writer.

Everything is read-only with respect to the game: the app never writes to the
game container, its files, or its REST API beyond GET.

## Data sources

### 1. Game REST API

Base URL from `PAL_API` (default `http://palworld-server:8212`, resolved by
Docker DNS on `media_default`), authenticated with `ADMIN_USER` /
`ADMIN_PASSWORD`.

| Endpoint | Fields used |
|---|---|
| `/v1/api/players` | `name`, `accountName`, `playerId`, `userId`, `level`, `iP`, `ping`, `location_x`, `location_y` |
| `/v1/api/metrics` | `currentplayernum`, `serverfpsaverage` (fallback `serverfps`), `serverframetime`, `days`, `basecampnum`, `maxplayernum`, `uptime` |
| `/v1/api/info` | `version`, `servername`, `description`, `worldguid` — refreshed every 30 min |

`playerId` is the Palworld UID (`ABCDEF01…`). It can be `"None"` for a moment
during a player's first connect; in that case the app falls back to `userId`
(`steam_…` / `ps5_…`) as the identity key.

Levels, pings and coordinates are only exposed while a player is online, which
is why the UI shows `—` for offline players rather than a stale number.

The game's `game-data` endpoint (full actor snapshot: players, pals, base camps,
guilds) exists in this server version but is disabled; it requires the
`-EnableGameDataAPI` launch flag and a game restart. See
[PALWORLD-SERVER.md](PALWORLD-SERVER.md).

### 2. Game container log

Streamed through the Docker socket (`docker.from_env().containers.get(PAL_CONTAINER)`,
`logs(stream=True, follow=True)`), then parsed line by line with three regexes:

```
[2026-11-08 13:21:01] [LOG] exampleplayer joined the server. (User id: ps5_0000000000000000001, Player id: 0123456789ABCDEF0000000000000000)
[2026-11-08 15:35:07] [LOG] Player2 left the server. (User id: ps5_0000000000000000002)
[2026-11-08 15:36:40] [CHAT] <Player2> Lvl 18 ancient tech unlock
```

Timestamps are **local to the game container** (`TZ` — `America/Denver`) and are
converted to UTC on ingest. `TZ` must therefore match the game stack's `TZ`, or
sessions will be offset.

This is the only source of exact history and the only push channel the game
offers. The game buffers stdout, so a line can lag its event by up to a few
seconds (measured 18 ms–4.1 s), but that is still far below the old 30 s poll.

Docker retains only the *current* container's log, so a game-container recreate
loses anything not yet ingested.

## Ingestion rules

The log follower streams continuously; the poller runs on a heartbeat and is
woken by log events.

**Log events.** Every parsed event is inserted with `INSERT OR IGNORE` — into
`events` keyed on `(ts, kind, name)`, into `chat` keyed on
`(ts, name, message)`. New rows drive the state machine; rows already present
are skipped, which makes the `since` replay on reconnect harmless. On connect
the stream starts 120 s before the newest stored event, so a restart of this
container does not miss lines.

- **join** → open a session for that player. If an open one exists whose start is
  within 180 s (the poll got there first), it is *retimed* to the log's exact
  timestamp and its `source` set to `log`; otherwise the old one is closed and a
  new one opened.
- **left** → close that player's open session. The log line carries no player id,
  so it is matched by display name against the most recent matching player row.
  A name change or two players sharing a name can therefore mis-attribute a
  close; the API reconciliation is the backstop.
- **chat** → stored for the dashboard feed. Not a presence event, so it does not
  trigger an API poll, only a push to connected browsers.
- A join or leave sets `wake`, so the poller reconciles the roster immediately
  instead of waiting for the heartbeat (debounced by `MIN_POLL_GAP`, 2 s).

**API reconciliation.** For each returned player, upsert `players` (including
`accountName`, `ping`, coordinates), and open a session if none is open
(`source='poll'`). Then close any open session whose player was not returned —
unless it started less than 90 s ago, which avoids closing a session that a
concurrent join just created.

Net effect: exact boundaries when the log has them, a correct live view when it
does not, and both within about a second.

## Schema

`data/paladmin.db`, WAL mode. Columns added after the first release are patched
in by `_add_missing_columns()` at startup.

```sql
sessions(id, player_id, user_id, started_at, ended_at, source)
-- partial unique index: at most one OPEN session per player
CREATE UNIQUE INDEX ux_open_session ON sessions(player_id) WHERE ended_at IS NULL;

players(player_id PK, name, platform, level, first_seen, last_seen, last_ip,
        account_name, ping, last_x, last_y)

metrics(ts PK, playernum, fps, fps_avg, frametime, days, basecamps,
        maxplayernum, uptime)                             -- one row per poll

events(ts, kind, name, user_id, player_id,
       PRIMARY KEY (ts, kind, name))                      -- replay guard

chat(ts, name, message, PRIMARY KEY (ts, name, message))  -- replay guard

server(key PK, value)                                     -- /v1/api/info cache
```

Timestamps are ISO-8601 UTC with second precision. A session is "open" when
`ended_at IS NULL` — that is the only representation of "currently playing", so
an unclosed session from a crash shows as ongoing until the next poll closes it.

`metrics` keeps one row per poll and is never pruned: about 2,880 rows/day at the
default 30 s heartbeat, plus one per log-triggered poll. `GET /api/summary`
reads the last 240 for the trend sparkline.

## Time handling

- Everything is stored in UTC; the UI renders in the browser's locale.
- Day boundaries for the calendar, `/api/day` and `/api/range` are computed from
  **real local midnights** (`datetime.combine(date, time.min, tzinfo=TZ)`), so a
  25-hour DST day is positioned correctly instead of being assumed to be 24 h.
- `/api/timeline` returns fractions of the whole month (`start_frac`, `mid_frac`,
  `now_frac`) rather than pixel positions; the UI multiplies by a per-day width.

## UI notes

`frontend/` is a React app (Vite, no router, no UI library) compiled to static
files by the Docker build and served from `/static`. The only runtime HTTP it
knows about is `/api/*`.

```
frontend/src/
  App.jsx            state owner: month being viewed, selection, zoom
  api.js             useLiveSummary() — one EventSource, 30 s fetch fallback
  timeline.js        pure geometry (unit-tested)
  world.js           guild colours, health, marker projection (unit-tested)
  components/        Header, Cards, WorldPanel, MapPlot, LivePlayers, ChatPanel,
                     PlayerStats, Calendar, Timeline
```

- The page is pushed, not polled: one `EventSource` on `/api/events`. Each frame
  is a full `/api/summary`, so components render from the same shape as on first
  load, and React's reconciliation is what keeps scroll position and selection
  stable. A pushed frame for a month the user is not looking at updates the live
  panes only; the calendar keeps showing the month being browsed.
- The timeline is still one continuous axis per month, with a `position: sticky`
  left column inside a `width: max-content` inner wrapper, fractions for
  positions and bars that are never clipped per day. Scroll drives the selected
  day; the sticky date follows whichever day is at the left edge.
- The timeline's scroll container is mounted from the first render (an empty
  panel until the first fetch lands) so the `ResizeObserver` can measure it.

## World data and the map

`poll_world()` in `app.py` fetches `/v1/api/game-data` every
`GAME_DATA_SECONDS` (20 s) while it works, and every `GAME_DATA_RETRY_SECONDS`
(5 min) while it 404s, so enabling the flag on the game side is picked up
without restarting this stack. The response is
`{Time, FPS, AverageFPS, InGameTime, InGameDays, ActorData[]}`; `ActorData` is
reduced to a bounded payload:

- `players[]` — name, level, HP, guild, position, `party[]`
- `bases[]` — PalBox name, guild, position, `workers[]`
- `guilds[]` — rolled-up player/base/pal counts
- `counts` — players, party/base/wild pals, NPCs, bases
- `days`, `time` — from `InGameDays` / `InGameTime`

Two quirks of the real payload shape the parser:

- actor ids are compound (`<playerId> : <runtimeActorId>`); the stable half is
  kept as `id` and the raw value as `runtime_id`. Party pals attach through
  `TrainerInstanceID`, compared on the stable half.
- **base workers carry no trainer/base id** — `TrainerInstanceID` is empty. They
  are attached to the nearest PalBox of the same guild by map position, which is
  sound because the game keeps workers inside their camp. PalBoxes have no
  `InstanceID` either, so a base id is synthesised from guild + raw name, and
  the untranslated `新規生成拠点テンプレート名N(仮)` placeholder becomes
  "Unnamed base N+1".

Unknown or future actor fields are ignored rather than fatal, and both
snake_case and PascalCase are accepted.

Positions use the community Palpagos calibration:

```
bounds = (maxX, maxY, minX, minY) = (349400, 724400, -1099400, -724400)
nx = (y - minY) / (maxY - minY)          # screen X follows world Y
ny = (maxX - x) / (maxX - minX)          # screen Y follows world X
map_x = (ny * 8192 - 158000) / 459       # the in-game read-out
map_y = (nx * 8192 + 123888) / 459
```

`map_point()` returns `null` for coordinates outside the map (instances, the
World Tree), and the UI simply omits those markers. The map itself is a
schematic grid — the Palpagos texture is copyrighted, so it is neither bundled
nor hotlinked; an external interactive map is linked instead.

## Dependencies

`requirements.txt`: `flask`, `gunicorn`, `requests`, `docker`. Runtime image is
`python:3.12-slim`. The `docker` Python package needs nothing from the host but
the socket, which is mounted read-only.

The frontend adds a **build-time only** toolchain: `react`, `react-dom`, `vite`,
`@vitejs/plugin-react`, `vitest` (see `frontend/package.json`, locked by
`package-lock.json`). Nothing from node ships in the runtime image — the Docker
build compiles `frontend/` in a `node:22-alpine` stage and copies `dist/`.
