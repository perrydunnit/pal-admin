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

Read from `/opt/stacks/palworld/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini`.

The image writes that file from its `.env` at **every boot**
(`/home/steam/server/compile-settings.sh`), which is why hand edits used to be
lost on restart. The dashboard's config panel edits the file in place, so this
stack sets `DISABLE_GENERATE_SETTINGS=true` in the game's `.env` to stop the
regeneration. With that set, the ini is the source of truth and `.env` only
holds container-level settings (backups, updates, autopause, ports, `TZ`).

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

## Changing settings

Two ways, and they differ in what survives:

| Path | Used for |
|---|---|
| Dashboard → **Server config** → save | every `PalWorldSettings.ini` key; applied on the next restart |
| `/opt/stacks/palworld/.env` → `docker compose up -d --force-recreate` | container behaviour: backups, updates, autopause, ports, `TZ` |

The panel writes the ini atomically (temp file + rename) and keeps the previous
version as `PalWorldSettings.ini.bak` beside it. It reads the running values from
the game's `/v1/api/settings`, so a key that is saved but not yet in effect is
marked as pending instead of pretending it is live.

Order of operations the first time, on the host:

```bash
cd /opt/stacks/palworld
# one line, appended if missing
grep -q '^DISABLE_GENERATE_SETTINGS=' .env \
  || echo 'DISABLE_GENERATE_SETTINGS=true' >> .env
docker compose stop            # never `docker restart`: it can SIGKILL mid-save
docker compose up -d           # recreates, so the new env var takes effect
```

After that, saves from the dashboard only need the restart the panel offers,
because nothing rewrites the file any more. A container **recreate** would still
pick up the same ini, since `DISABLE_GENERATE_SETTINGS` is in `.env` too.

Verify from the host:

```bash
docker exec palworld-server env | grep DISABLE_GENERATE_SETTINGS
tail -c 200 palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini
```

**Do not** delete `DISABLE_GENERATE_SETTINGS` without also removing the panel
from service: the next boot would silently reset every setting to the values in
`.env`.

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

## game-data (world snapshot) — enabled

`GET /v1/api/game-data` returns `Time`, `FPS`, `AverageFPS`, `InGameTime`,
`InGameDays` and an `ActorData` array of `Character` (`Player`, `OtomoPal`,
`BaseCampPal`, `WildPal`, `NPC`) and `PalBox` objects, with names, guild ids,
level, HP, coordinates, rotation, action and active state. That is the only
source of guilds, base camps and pals.

It is **on**. Two things had to change on this stack, because
`thijsvanloef/palworld-server-docker` assembles the launch command from a fixed
set of env vars and has no pass-through for extra arguments:

1. `/opt/stacks/palworld/enable-gamedata.sh` — a small entrypoint wrapper that
   appends `-EnableGameDataAPI -enable-gamedata-api` to the image's
   `start.sh` (the lowercase switch is needed by some 1.0.x builds) and refuses
   to start if it cannot find the argument block rather than silently running
   without the flag.
2. `compose.yaml` — `entrypoint: ["/bin/bash", "/usr/local/bin/enable-gamedata.sh"]`
   and a read-only mount of that file at `/usr/local/bin/enable-gamedata.sh`.

The pre-change file is kept next to it as `compose.yaml.bak-<timestamp>`.

Restart used (never `docker restart`):

```bash
cd /opt/stacks/palworld
docker compose stop      # SIGTERM, 30 s grace; log shows "Shutdown handler: cleanup"
docker compose up -d     # recreates because the entrypoint changed
```

Revert: remove the `entrypoint:` line and the wrapper mount from `compose.yaml`
(or restore the `.bak`), then `docker compose stop && docker compose up -d`.
The dashboard degrades on its own — it keeps plotting REST positions and shows
the "needs -EnableGameDataAPI" notice.

Real payload quirks worth knowing (the dashboard's parser handles them):

- `InstanceID` is compound: `<playerId> : <runtimeActorId>`.
- Base workers (`BaseCampPal`) have an **empty** `TrainerInstanceID`; only
  `GuildID` and position distinguish them. The dashboard attaches them to the
  nearest PalBox in the same guild.
- `PalBox` has no `InstanceID` and reports the untranslated template name
  `新規生成拠点テンプレート名N(仮)`.
- Actors only include what is currently loaded: party pals appear when summoned,
  wild pals depend on where players are.

## Community listing (why consoles can connect)

`COMMUNITY=true` makes the entrypoint add `-publiclobby`, which registers the
server with Epic Online Services so it appears in the in-game community browser —
the only way a PS5/Xbox player can find it, since consoles have no direct-connect
field. `PUBLIC_IP`/`PUBLIC_PORT` pin the advertised address behind NAT.

NAT: UDP 8211 and 27015 must be forwarded to the game host, by hand or over
UPnP (entries `palworld-game`, `palworld-query` if your router creates them).
A router reboot usually clears UPnP entries; re-add with
`upnpc -a <game-host-lan-ip> 8211 8211 UDP` (and the same for 27015). Without
them the server is unreachable from outside even though it is healthy locally.

## Backups

Two independent mechanisms:

- **Rolling snapshots** in `palworld/Pal/Saved/SaveGames/0/<world>/backup/world/`,
  written roughly every 30 s by the game, ~22 kept.
- **Nightly tarballs** in `/opt/stacks/palworld/backups/` at 00:00 local, 14-day
  retention.
- Plus ad-hoc pre-change tarballs.

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
