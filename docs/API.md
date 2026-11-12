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
  "world": {                     // /v1/api/game-data; see below
    "available": false,
    "reason": "disabled",
    "detail": "PalGameDataBridge GameData API is not enabled",
    "checked": "2026-11-09T22:51:57+00:00",
    "ts": null, "counts": null, "players": [], "bases": [], "guilds": []
  },
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
      "map": {                   // null when offline or outside the map
        "px": 0.6822, "py": 0.4813,
        "map_x": 231, "map_y": -488
      },
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

## The `world` block

Read from `GET /v1/api/game-data`, reduced from its `ActorData` array to the
shapes below. When the game was not started with `-EnableGameDataAPI` the
endpoint 404s and the block stays in its unavailable form, so clients can render
a placeholder and keep polling — it flips to `available: true` on its own once
the flag is enabled.

`reason` is one of `disabled` (404 — feature off), `error` (request or parse
failure), `unsupported` (reserved) or `unknown` (not probed yet).

`px`/`py` are normalised map positions (0–1, origin top-left) and `map_x`/
`map_y` the in-game map read-out. Both are `null` for coordinates outside
Palpagos (instances, the World Tree).

```jsonc
{
  "available": true,
  "ts": "2026-11-09T23:00:00+00:00",
  "time": "18:19",              // InGameTime
  "days": 207,                   // InGameDays
  "fps": 59, "fps_avg": 59.3,
  "counts": { "players": 2, "party_pals": 3, "base_pals": 3,
              "wild_pals": 41, "npcs": 5, "bases": 2, "other": 0 },
  "players": [
    { "id": "P1", "name": "exampleplayer", "level": 43, "hp": 880, "max_hp": 1000,
      "guild_id": "G1", "guild": "Pals", "active": true,
      "px": 0.68, "py": 0.48, "map_x": 231, "map_y": -488,
      "party": [ { "id": "O1", "name": "Lamball", "level": 12, "hp": 80, "max_hp": 90 } ] }
  ],
  "bases": [
    { "id": "BOX1", "name": "Lakeside base", "guild": "Pals",
      "px": 0.67, "py": 0.49, "map_x": 228, "map_y": -485,
      "workers": [ { "id": "B1", "name": "Digtoise", "level": 22 } ] }
  ],
  "guilds": [ { "id": "G1", "name": "Pals", "players": 1, "bases": 1, "pals": 4 } ],
  "unattributed_pals": 0        // pals whose trainer/base was not in the snapshot
}
```

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

## Music library

Uploaded audio lives in `MUSIC_DIR` (default `/data/music`, beside the database),
so it is on the same volume as the SQLite state and is covered by the same
backups. Files are stored under their content hash, so re-uploading the same
bytes is a no-op.

### GET /api/music

```jsonc
{
  "tracks": [
    {
      "id": "a533b8bea6f0e2155b99d7d6fffecb06",  // sha256[:32]
      "name": "Ode to Joy",
      "ext": ".mid",
      "kind": "midi",                            // midi | audio
      "bytes": 2048,
      "uploaded": "2026-11-12T07:27:08+00:00",
      "source": "Ode to Joy.mid",                // original filename
      "url": "/api/music/<id>/file"
    }
  ],
  "count": 1,
  "bytes": 2048,
  "limit_bytes": 67108864
}
```

`?format=tsv` returns the same list for clients without a JSON parser (the
in-game mod), one track per line, tab separated, after a
`#id\tkind\text\tbytes\tname` header.

### POST /api/music

`multipart/form-data` with a `file` field. Accepts `.mid .midi .wav .mp3 .flac
.ogg .oga .opus .m4a .aac .wma`, up to `MUSIC_MAX_BYTES` (default 256 MB).

The body is streamed to a temp file beside the library and hashed in 256 KB
chunks, so memory per upload is constant no matter how large the limit is. The
practical ceilings are free space in `/data` and gunicorn's request timeout —
raise `GUNICORN_TIMEOUT` (default 120 s) alongside `MUSIC_MAX_BYTES`, because a
slow upload counts against it: 256 MB needs ~3.5 minutes above 10 Mbit/s.

- `201` with `{"track": {…}, "duplicate": false}` when stored.
- `200` with `"duplicate": true` when the same bytes are already present.
- `400 {"error": "…"}` for an unsupported extension, an empty file, or content
  that does not match its extension (checked by magic bytes, not by name).

### DELETE /api/music/{id}

Removes the row and the file. `404` for an unknown id.

### GET /api/music/{id}/file

Downloads the file with a content type derived from the extension. `410` when
the row exists but the file is missing on disk.

The live summary also carries `music: {count, bytes, limit_bytes}` so open
browsers refresh the panel when someone else uploads.

## Errors

- `400` only for bad query parameters, always `{"error": "message"}`.
- `5xx` means an unexpected failure; check `docker logs paladmin`.
- The UI treats a dropped push stream as non-fatal: it logs to the console and
  falls back to a 30 s refresh until `EventSource` reconnects.
