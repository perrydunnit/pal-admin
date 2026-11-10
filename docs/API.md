# API

All endpoints are served by the Flask app on port 8000 inside the network and
are reached over HTTPS through Traefik, which requires HTTP basic auth.
Responses are JSON with ISO-8601 UTC timestamps at second precision.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the dashboard |
| GET | `/healthz` | liveness, `{"status": "ok"}` |
| GET | `/favicon.ico` | `204`, keeps the console clean |
| GET | `/api/summary` | stats, live players, calendar rollup, chat tail |
| GET | `/api/events` | `text/event-stream`; pushes `/api/summary` on change |
| GET | `/api/timeline?month=YYYY-MM` | continuous month timeline |
| GET | `/api/day?date=YYYY-MM-DD` | one day's sessions |
| GET | `/api/range?start=…&end=…` | per-day sessions for a range |

`month` defaults to the current month when omitted on `/api/summary`.

## GET /api/summary

```jsonc
{
  "month": "2026-11",            // month being rolled up (YYYY-MM)
  "month_label": "November 2026",
  "today": "2026-11-09",         // local date, drives the calendar highlight
  "now": "2026-11-09T22:51:57+00:00",
  "metrics": {                   // latest poll, or null before the first one
    "ts": "2026-11-09T22:51:57+00:00",
    "playernum": 1,
    "fps": 59.2,
    "fps_avg": 59.36,            // serverfpsaverage
    "frametime": 16.78,          // milliseconds
    "days": 204,                 // in-game day counter
    "basecamps": 2,
    "maxplayernum": 16,          // server slot count
    "uptime": 86778              // seconds the game process has been up
  },
  "server": {                    // /v1/api/info, refreshed every 30 min
    "version": "v1.0.5.102999",
    "servername": "Palworld Admin",
    "description": "…",
    "worldguid": "ABCDEF01…"
  },
  "chat": [                      // last 60 lines, oldest first
    { "ts": "2026-11-09T20:21:44+00:00", "name": "Player2", "message": "Lvl 18 ancient tech unlock" }
  ],
  "players": [
    {
      "player_id": "0123456789ABCDEF0000000000000000",
      "name": "exampleplayer",
      "account_name": "exampleplayer-account",   // platform account, not the character
      "platform": "ps5",         // parsed from userId prefix
      "level": null,             // only known while online
      "last_seen": "2026-11-08T01:05:44+00:00",
      "ip": "203.0.113.42",
      "ping": 32.2,              // ms, last reported while online
      "x": -347954.16,           // world coordinates, last reported online
      "y": 263915.38,
      "online": false,
      "session_started": null,   // ISO start of the open session when online
      "total_seconds": 17548,    // all-time, all sessions
      "sessions": 3,
      "avg_seconds": 5853
    }
  ],
  "days": [
    {
      "date": "2026-11-08",
      "total": 44905,            // seconds of play across all players
      "players": [
        { "player_id": "0123456789ABCDEF…", "name": "exampleplayer", "seconds": 17548 }
      ]
    }
  ],
  "trend": [                     // last 240 polls, oldest first
    { "ts": "2026-11-09T22:51:57+00:00", "playernum": 1, "fps": 59.2 }
  ]
}
```

`players` is sorted online-first, then by playtime. `days` covers every day of
the month, including empty ones, and `players[]` within a day is sorted by
seconds descending. `x`/`y`/`ping` are the last values seen while the player was
online, so only render them for `online: true`.

## GET /api/events

Server-sent events. On connect it sends one `event: update` frame containing the
current `/api/summary` payload (current month), then another frame whenever
stored state changes — a join, a leave, a chat line, or the 30 s metric
heartbeat. A `: keepalive` comment goes out every 10 s while nothing happens.

The browser uses this instead of a timer. If the stream cannot be opened, or
drops, the page falls back to fetching `/api/summary` every 30 s. At most
`MAX_STREAMS` (default 4) connections are served; beyond that the endpoint
returns `503 {"error": "too many live streams"}`.

## GET /api/timeline?month=YYYY-MM

One axis for the whole month.

```jsonc
{
  "month": "2026-11",
  "month_label": "November 2026",
  "days": [
    { "date": "2026-11-01", "start_frac": 0.0, "mid_frac": 0.017, "…": "…" }
  ],
  "sessions": [ /* same session shape as below, fractions over the month */ ],
  "now_frac": 0.2899,            // null when now is outside the month
  "total_seconds": 54900
}
```

`start_frac` is where the day begins on the month axis, `mid_frac` its midpoint
(used for the noon gridline), both 0–1. Sessions here are **not** clipped per
day, so a session crossing midnight is one bar.

## Session shape

Shared by `/api/timeline`, `/api/day` and `/api/range`:

| Field | Meaning |
|---|---|
| `id` | row id in `sessions` |
| `player_id`, `name`, `platform` | identity |
| `start`, `end` | ISO UTC; `end` is `null` while ongoing |
| `ongoing` | session has not been closed |
| `start_frac`, `end_frac` | position on the axis, 0–1 |
| `continues_before` | began before the window |
| `continues_after` | ran past the window (always true while ongoing, unless the window includes now) |
| `day_seconds` | seconds falling inside the window |
| `duration_seconds` | whole session, even if it spans days |

## GET /api/day?date=YYYY-MM-DD

```jsonc
{
  "date": "2026-11-08",
  "sessions": [ /* clipped to that local day, fractions over 24h */ ],
  "total_seconds": 44905,
  "player_count": 3,
  "now": "2026-11-09T22:51:57+00:00"
}
```

Missing `date` → `400 {"error": "date required"}`.

## GET /api/range?start=YYYY-MM-DD&end=YYYY-MM-DD

Inclusive range, capped at 92 days.

```jsonc
{
  "start": "2026-11-01",
  "end": "2026-11-30",
  "days": [ /* same shape as one /api/day response, per day */ ],
  "now": "…"
}
```

Missing parameters or a range wider than 92 days → `400 {"error": "…"}`.

## Errors

- `400` only for bad query parameters, always `{"error": "message"}`.
- `5xx` means an unexpected failure; check `docker logs paladmin`.
- The UI treats a dropped push stream as non-fatal: it logs to the console and
  falls back to a 30 s refresh until `EventSource` reconnects.
