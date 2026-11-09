# Architecture

## Shape

Two loops and a schema, all in `app.py`:

```
                 ┌──────────────────────────────┐
  docker.sock ──▶│ ingest_logs()   every poll    │──┐
  (read-only)    │  join/leave lines → events    │  │
                 └──────────────────────────────┘  │
                                                   ├──▶ SQLite ──▶ endpoints ──▶ templates/index.html
                 ┌──────────────────────────────┐  │
  REST API    ──▶│ poll_api()      every poll   │──┘
  :8212          │  roster/metrics → sessions    │
                 └──────────────────────────────┘
```

The collector is a daemon thread started at import (`init_db()` runs there too),
so it also runs under gunicorn. **One worker only** — more would poll and write
concurrently, and the election logic assumes a single writer.

Everything is read-only with respect to the game: the app never writes to the
game container, its files, or its REST API beyond GET.

## Data sources

### 1. Game REST API

Base URL from `PAL_API` (default `http://palworld-server:8212`, resolved by
Docker DNS on `media_default`), authenticated with `ADMIN_USER` /
`ADMIN_PASSWORD`.

| Endpoint | Fields used |
|---|---|
| `/v1/api/players` | `name`, `playerId`, `userId`, `level`, `iP` |
| `/v1/api/metrics` | `currentplayernum`, `serverfpsaverage` (fallback `serverfps`), `days`, `basecampnum`, `uptime` |

`playerId` is the Palworld UID (`ABCDEF01…`). It can be `"None"` for a moment
during a player's first connect; in that case the app falls back to `userId`
(`steam_…` / `ps5_…`) as the identity key.

Levels are only exposed while a player is online, which is why the UI shows `—`
for offline players rather than a stale number.

### 2. Game container log

Read through the Docker socket (`docker.from_env().containers.get(PAL_CONTAINER)`,
`logs()`), then parsed with two regexes:

```
[2026-11-08 13:21:01] [LOG] exampleplayer joined the server. (User id: ps5_0000000000000000001, Player id: 0123456789ABCDEF0000000000000000)
[2026-11-08 15:35:07] [LOG] Player2 left the server. (User id: ps5_0000000000000000002)
```

Timestamps are **local to the game container** (`TZ` — `America/Denver`) and are
converted to UTC on ingest. `TZ` must therefore match the game stack's `TZ`, or
sessions will be offset.

This is the only source of exact history. Docker retains only the *current*
container's log, so a game-container recreate loses anything not yet ingested.

## Ingestion rules

Each pass does log ingest, then API reconciliation, in that order.

**Log events.** Every parsed event is inserted into `events` with
`INSERT OR IGNORE` on `(ts, kind, name)`. New rows drive the state machine; rows
already present are skipped. That is what makes repeated passes — and re-reading
a window that overlaps the last one — harmless. The read window starts 120 s
before the newest stored event, so a restart of this container does not miss
lines.

- **join** → open a session for that player. If an open one exists whose start is
  within 180 s (the poll got there first), it is *retimed* to the log's exact
  timestamp and its `source` set to `log`; otherwise the old one is closed and a
  new one opened.
- **left** → close that player's open session. The log line carries no player id,
  so it is matched by display name against the most recent matching player row.
  A name change or two players sharing a name can therefore mis-attribute a
  close; the API reconciliation is the backstop.

**API reconciliation.** For each returned player, upsert `players`, and open a
session if none is open (`source='poll'`). Then close any open session whose
player was not returned — unless it started less than 90 s ago, which avoids
closing a session that a concurrent join just created.

Net effect: exact boundaries when the log has them, a correct live view when it
does not.

## Schema

`data/paladmin.db`, WAL mode.

```sql
sessions(id, player_id, user_id, started_at, ended_at, source)
-- partial unique index: at most one OPEN session per player
CREATE UNIQUE INDEX ux_open_session ON sessions(player_id) WHERE ended_at IS NULL;

players(player_id PK, name, platform, level, first_seen, last_seen, last_ip)

metrics(ts PK, playernum, fps, days, basecamps, uptime)   -- one row per poll

events(ts, kind, name, user_id, player_id,
       PRIMARY KEY (ts, kind, name))                      -- replay guard
```

Timestamps are ISO-8601 UTC with second precision. A session is "open" when
`ended_at IS NULL` — that is the only representation of "currently playing", so
an unclosed session from a crash shows as ongoing until the next poll closes it.

`metrics` keeps one row per poll and is never pruned: about 2,880 rows/day at the
default 30 s poll. `GET /api/summary` reads the last 240 for the trend sparkline.

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
- Refreshes are diffed: panes/cells are rebuilt only when their JSON changes, and
  updates are skipped while the user is scrolling — so selection, scroll position
  and hover survive the 30 s poll.
- Ongoing sessions render with a gradient fade, a pulse animation and a dot at the
  leading edge, plus a green "now" line on today's row.

## Dependencies

`requirements.txt`: `flask`, `gunicorn`, `requests`, `docker`. Runtime image is
`python:3.12-slim`. The `docker` Python package needs nothing from the host but
the socket, which is mounted read-only.
