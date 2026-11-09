# API

All endpoints are served by the Flask app on port 8000 inside the network and
are reached over HTTPS through Traefik, which requires HTTP basic auth.
Responses are JSON with ISO-8601 UTC timestamps at second precision.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the dashboard |
| GET | `/healthz` | liveness, `{"status": "ok"}` |
| GET | `/favicon.ico` | `204`, keeps the console clean |
| GET | `/api/summary` | stats, live players, calendar rollup |
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
    "days": 204,                 // in-game day counter
    "basecamps": 2,
    "uptime": 86778              // seconds the game process has been up
  },
  "players": [
    {
      "player_id": "0123456789ABCDEF0000000000000000",
      "name": "exampleplayer",
      "platform": "ps5",         // parsed from userId prefix
      "level": null,             // only known while online
      "last_seen": "2026-11-08T01:05:44+00:00",
      "ip": "203.0.113.42",
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
seconds descending.

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
- The UI treats a failed refresh as non-fatal, logs it to the console and retries
  on the next 30 s tick.
