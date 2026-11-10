# Architecture

## Shape

Two loops, a push fan-out and a schema, all in `app.py`:

```
                 ┌────────────────────────────────────┐
  docker.sock ──▶│ follow_logs()   streaming          │──┐ join/leave ─▶ wake
  (read-only)    │  join/leave/chat lines → tables    │  │ chat ───────▶ publish()
                 └────────────────────────────────────┘  │
                                                         ├──▶ SQLite ──▶ endpoints ──▶ index.html
                 ┌────────────────────────────────────┐  │                  │
  REST API    ──▶│ poll_api()  heartbeat + on wake    │──┘                  ▼
  :8212          │  roster/metrics/info → tables      │            /api/events (SSE)
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

`templates/index.html` is self-contained — no build, no dependencies, no CDN.

- One continuous timeline per month: a row per player, `position: sticky` left
  column for the date + names, day boundaries as vertical rules labelled with the
  date that starts there. No `scroll-snap`: scrolling is free-form and the sticky
  date follows whichever day is at the left edge.
- Sessions crossing midnight are single bars because bars are positioned against
  the month axis, not clipped per day.
- Refreshes are pushed, not polled: the page holds one `EventSource` on
  `/api/events`, applies each frame exactly like a fetch, and refetches the
  timeline at most once per burst. Panes/cells are rebuilt only when their JSON
  changes, so selection, scroll position and hover survive an update. If the
  stream drops, a 30 s fetch takes over until it reconnects.
- Ongoing sessions render with a gradient fade, a pulse animation and a dot at the
  leading edge, plus a green "now" line on today's row.
- The chat panel is fed by the same push frames and keeps its own digest, so it
  only re-renders when a message arrives.

## Dependencies

`requirements.txt`: `flask`, `gunicorn`, `requests`, `docker`. Runtime image is
`python:3.12-slim`. The `docker` Python package needs nothing from the host but
the socket, which is mounted read-only.
