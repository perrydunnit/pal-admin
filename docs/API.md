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
| GET | `/api/server-config` | every game setting, its value and its default |
| POST | `/api/server-config` | write settings (local network only) |
| POST | `/api/server-restart` | restart now or queue for an empty server (local network only) |
| DELETE | `/api/server-restart` | cancel a queued restart (local network only) |
| GET | `/api/pal-icon/{species}` | an operator-supplied pal portrait, or 404 |

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
    "servername": "Default Palworld Server",
    "description": "…",
    "worldguid": "ABCDEF0123456789…"
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

## GET /api/pal-icon/{species}

Serves a portrait from `PAL_ICON_DIR` when the file exists, matched
case-insensitively by species key (`Anubis`, `ElecPanda`,
`FlowerDinosaur_Electric`), and `404 {"error": …}` when it does not. No artwork
is bundled or proxied: the directory is whatever the operator put there.
`Cache-Control: public, max-age=3600`.

`/api/summary` carries `pal_icons`, the lowercased species keys that have a
portrait, so the UI requests only the ones that exist. Every pal in `world`
carries a `species` derived from the game's `Class` (`BP_Anubis_MiddleBoss_C` →
`Anubis`), which is the key to use for both.

## Server config

`PalWorldSettings.ini` is read from `PAL_SETTINGS_FILE` (default
`/palconfig/PalWorldSettings.ini`, the game stack's settings directory mounted
in `compose.yaml`). Reading is allowed from anywhere the page is reachable;
every write is refused unless the request came from `LAN_CIDRS`.

### GET /api/server-config

```jsonc
{
  "available": true,             // the settings file is mounted and readable
  "writable": true,
  "editable": true,              // writable *and* the caller is on the LAN
  "client_ip": "192.168.0.42",
  "lan": "192.168.0.0/24",
  "file": "/palconfig/PalWorldSettings.ini",
  "warnings": [],                // e.g. the image would regenerate the file
  "locked_reason": "Managed from the stack's .env (see docs/PALWORLD-SERVER.md).",
  "players_online": 0,           // last collected metric, null before the first poll
  "players_at": "2026-11-16T22:51:57+00:00",
  "restart": {
    "queued": { "at": "…", "by": "192.168.0.42", "message": "…" },
    "issued": null,              // set while the game is coming back
    "empty_since": null,         // when the server was first seen empty
    "error": null,
    "idle_seconds": 300,
    "countdown": 30
  },
  "groups": [
    {
      "name": "Survival & rates",
      "settings": [
        {
          "key": "ExpRate",
          "group": "Survival & rates",
          "kind": "float",        // bool | int | float | string | enum | list | raw
          "description": "Experience multiplier for every player.",
          "choices": [],          // enum and list values
          "tuple": false,         // lists written as (a,b) instead of "a,b"
          "locked": false,
          "warning": null,
          "secret": false,
          "default": "1",
          "value": "2.5",         // null for secrets, or when the file omits it
          "is_set": null,         // secrets only: is a value present at all
          "is_default": false,
          "live": "1",            // what the running game reports
          "pending": true          // the file and the game disagree: restart needed
        }
      ]
    }
  ]
}
```

`value` may be `null` while `default` is not: the key is absent from the file
and the game is using its own default. `AdminPassword` and `ServerPassword` are
never sent — `is_set` says whether one exists.

### POST /api/server-config

```jsonc
{ "changes": { "ExpRate": "2.5", "Difficulty": "Hard" } }
```

Values are validated per type (booleans accept `true/false/yes/no/1/0`, enums
accept any case, free text may not contain a double quote or a line break,
because the ini has no escape syntax). All or nothing: one bad value rejects the
whole request with `400 {"error": "ExpRate must be a number"}`. A successful
save rewrites the file atomically, keeps the previous version beside it as
`PalWorldSettings.ini.bak`, and returns the same payload as `GET` with
`saved: ["ExpRate"]` added.

`403` when the caller is not on `LAN_CIDRS`, `500` when the file cannot be
written. Locked keys (`AdminPassword`, `RESTAPIEnabled`, `RESTAPIPort`) and
unknown keys are rejected with `400`.

### POST /api/server-restart

```jsonc
{ "mode": "queue" }        // or "now"
```

`now` announces the message and calls the game's own `shutdown` endpoint, which
saves the world and exits; the container's `restart: unless-stopped` policy
brings it back. `queue` stores the request and the collector fires it after
`RESTART_IDLE_SECONDS` of a server with `currentplayernum == 0`. Returns the
`GET` payload; `502` when the game refuses the shutdown. `403` off the LAN.

### DELETE /api/server-restart

Clears a queued restart (`cancelled: true|false` in the response). `403` off the
LAN.

## Errors

- `400` only for bad query parameters, always `{"error": "message"}`.
- `5xx` means an unexpected failure; check `docker logs paladmin`.
- The UI treats a dropped push stream as non-fatal: it logs to the console and
  falls back to a 30 s refresh until `EventSource` reconnects.
