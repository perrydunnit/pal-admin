# paladmin — Palworld admin dashboard

Shows live player state and a calendar of who was online when.
Managed as a Dockge stack; served at **https://pal-admin.example.com**.

## Data sources

| Source | Used for |
|---|---|
| Game REST API `/v1/api/players` | live roster, level, IP, platform |
| Game REST API `/v1/api/metrics` | fps, world day, base camps |
| Game container log (via Docker socket) | exact join/leave timestamps |

Both are reconciled every `POLL_SECONDS` into SQLite at `./data/paladmin.db`.

The log is the only source of history, and Docker only keeps the log of the
*current* container — so history starts when this stack is first started and
resets if the game container is recreated. Events already ingested are kept in
`data/paladmin.db`, so the calendar survives restarts of the game.

Player **levels** are only obtainable while a player is online, so the level
column reads `—` until that player next logs in.

## Files

- `compose.yaml` — service, Traefik labels, basic-auth middleware
- `app.py` — collector + Flask app (single worker; background thread)
- `templates/index.html` — dashboard UI
- `data/` — SQLite database (persisted)

## Timeline

`GET /api/timeline?month=YYYY-MM` returns one continuous timeline for the month:
`days` (each with `start_frac` and `mid_frac` on the whole-month axis),
`sessions` positioned by `start_frac`/`end_frac` across the entire month, and
`now_frac` for the live marker.

`GET /api/day?date=YYYY-MM-DD` and `GET /api/range?start=…&end=…` return the
same sessions clipped per day, which is handy for other consumers.

| Field | Meaning |
|---|---|
| `start_frac` / `end_frac` | position on the axis (0–1 over the month) |
| `ongoing` | session has not been closed yet (`end` is null) |
| `continues_before` / `continues_after` | session began before / ran past the month window |
| `day_seconds` | time falling inside the window |
| `duration_seconds` | whole session, even if it spans days |

The UI renders a single row per player against one continuous time axis, so a
session that crosses midnight is one uninterrupted bar. The left column (current
date at the top, player names beneath) is `position: sticky`, and the strip does
**not** scroll-snap — scrolling is free-form and continuous. Day boundaries are
drawn as vertical rules labelled with the date of the day that starts there; the
sticky date shows whichever day is at the left edge. Clicking a calendar day
scrolls the timeline to that day.

Refreshes only rewrite the panes and cells whose data actually changed, and skip
entirely while the user is scrolling, so selection, scroll position and hover
survive the 30-second poll.

## Auth

HTTP basic auth at Traefik, user `admin`, using the same password as the game's
`ADMIN_PASSWORD`. The middleware hash lives in `compose.yaml`; the plaintext in
`.env` is used by the app to call the game REST API.

To change the password: update `ADMIN_PASSWORD` in `.env`, regenerate the
bcrypt hash, and replace the value in the `paladmin-auth` label.

## Operations

```bash
cd /opt/stacks/paladmin
docker compose up -d --build     # apply code/UI changes
docker compose logs -f           # collector warnings show here
```

Restarting this stack has no effect on the game server.
