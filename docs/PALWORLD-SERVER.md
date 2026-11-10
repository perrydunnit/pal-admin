# The game server this reads

Context for the Palworld stack on the same host. This dashboard only reads from
it, but changes there affect what appears here.

## Game stack

| | |
|---|---|
| Path | `/opt/stacks/palworld` |
| Image | `thijsvanloef/palworld-server-docker:latest` |
| Container | `palworld-server` |
| Compose | `/opt/stacks/palworld/compose.yaml` + `.env` |
| Data | `/opt/stacks/palworld/palworld/` (world saves, config, logs) |
| Backups | `/opt/stacks/palworld/backups/` |
| World id | `ABCDEF0123456789` |
| Address | `pal.example.com:8211` |

Ports:

| Port | Proto | Exposure |
|---|---|---|
| 8211 | UDP | published; game traffic |
| 27015 | UDP | published; Steam query / server browser |
| 25575 | TCP | bound to `127.0.0.1` only; RCON |
| 8212 | TCP | **not published**; REST API, reachable only on `media_default` |

The REST API is what this dashboard uses. It is deliberately not exposed to the
host; `https://pal.example.com` is a separate Traefik-fronted route to the same
port, behind basic auth.

## Settings that matter here

Read from `/opt/stacks/palworld/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini`,
which the container regenerates from `.env` on every boot — **changes must go in
`.env`**, hand-edits are lost on restart.

Required for this dashboard:

```
REST_API_ENABLED=true
REST_API_PORT=8212
ADMIN_PASSWORD=…        # must equal ADMIN_PASSWORD in this repo's .env
ENABLE_PLAYER_LOGGING=true   # default; produces the join/leave lines
```

Current state of the interesting ones:

```
COMMUNITY=true
ENABLE_VOICE_CHAT=false
ALLOW_GLOBAL_PALBOX_EXPORT=true
ALLOW_GLOBAL_PALBOX_IMPORT=true
AUTO_UPDATE_ENABLED=false
BACKUP_ENABLED=true
OLD_BACKUP_DAYS=14
TZ=America/Denver
```

`TZ` must match this stack's `TZ`, or session times shift.

If `ENABLE_PLAYER_LOGGING` is turned off, history stops accruing: the app still
shows live state from the REST API, but the log regexes find nothing and
`sessions` will only contain poll-derived rows. The chat panel goes quiet too.

## What the REST API exposes

Verified against `v1.0.5.102999`. Every route is request/response — there is no
websocket, SSE or event endpoint.

| Route | Contents |
|---|---|
| `GET /v1/api/info` | `version`, `servername`, `description`, `worldguid` |
| `GET /v1/api/metrics` | `currentplayernum`, `serverfps`, `serverfpsaverage`, `serverframetime`, `days`, `maxplayernum`, `basecampnum`, `uptime` |
| `GET /v1/api/players` | `name`, `accountName`, `playerId`, `userId`, `iP`, `ping`, `location_x`, `location_y`, `level` |
| `GET /v1/api/settings` | the whole `PalWorldSettings.ini` — **including `AdminPassword` and `ServerPassword`**, so never proxy it raw |
| `GET /v1/api/game-data` | world actor snapshot — **disabled** (see below) |
| `POST` | `announce`, `kick`, `ban`, `unban`, `save`, `shutdown`, `stop` |

RCON (`:25575`, bound to loopback) answers `ShowPlayers`, `Info`, `Broadcast`,
`KickPlayer`, `BanPlayer`, `Save`, `Shutdown`, `DoExit`. It carries nothing the
REST API does not.

## game-data (world snapshot)

`GET /v1/api/game-data` returns `Time`, `FPS`, `AverageFPS` and an actor array of
`Character` (`Player`, `OtomoPal`, `BaseCampPal`, `WildPal`, `NPC`) and `PalBox`
objects, with names, trainer/guild ids, level, HP, coordinates, rotation,
action and active state. That is the only source of guilds, base camps and pals.

It is off by default: the endpoint answers
`404 PalGameDataBridge GameData API is not enabled`. Enable it with the
`-EnableGameDataAPI` launch argument (some builds also need
`-enable-gamedata-api`) — which means editing this stack's `.env`/compose and
**restarting the game server**. Ask the user first; see "Restarting the game
safely" below.

## Community listing (why consoles can connect)

`COMMUNITY=true` makes the entrypoint add `-publiclobby`, which registers the
server with Epic Online Services so it appears in the in-game community browser —
the only way a PS5/Xbox player can find it, since consoles have no direct-connect
field. `PUBLIC_IP`/`PUBLIC_PORT` pin the advertised address behind NAT.

NAT: UDP 8211 and 27015 are forwarded to `192.168.1.10` on the ASUS router via
UPnP (entries `palworld-game`, `palworld-query`). A router reboot usually clears
those; re-add with `upnpc -a 192.168.1.10 8211 8211 UDP` (and the same for
27015). Without them the server is unreachable from outside even though it is
healthy locally.

## Backups

Two independent mechanisms:

- **Rolling snapshots** in `palworld/Pal/Saved/SaveGames/0/<world>/backup/world/`,
  written roughly every 30 s by the game, ~22 kept.
- **Nightly tarballs** in `/opt/stacks/palworld/backups/` at 00:00 local, 14-day
  retention.
- Plus ad-hoc pre-change tarballs and `coop-original.tar.gz`, the untouched
  pre-migration co-op world.

## Restarting the game safely

`docker compose stop` from `/opt/stacks/palworld` sends SIGTERM; the game saves
and exits cleanly (`Shutdown handler: cleanup` → `****Ending Server****`).
`stop_grace_period: 30s` allows for it. **Do not use `docker restart`** — it
applies its own 10 s timeout and can SIGKILL mid-save.

Recreating the game container has a side effect for this dashboard: its log is
reset, so un-ingested history is lost. Ingested events live in `data/paladmin.db`
and are unaffected.

## Player identity

Palworld player ids are deterministic per platform account:

- Steam: `uid = u32(low32(cityhash64(steamid_utf16le)) + high32 * 23)`, e.g.
  `76561190000000000` → `abcdef01-0000-0000-0000-000000000000`.
- PSN ids come from the console; the two current ones are `0123456789ABCDEF…` (exampleplayer) and
  `FEDCBA98…` (Player2).

The dashboard stores whatever the REST API reports as `playerId`, which is why
the same person keeps one row across sessions.
