# paladmin — Palworld admin dashboard

Live player state for a Palworld dedicated server, plus a timeline of who was
online when. Built for this deployment, portable to any
[thijsvanloef/palworld-server-docker](https://github.com/thijsvanloef/palworld-server-docker)
server with the REST API enabled.

- **Live**: stat cards, who is online now, all-time per-player stats
- **History**: monthly calendar, and a continuous month timeline with a sticky
  player column where a session crossing midnight is one unbroken bar

Served at **https://pal-admin.example.com** behind HTTP basic auth.

## Quick start

```bash
cp .env.example .env      # fill in ADMIN_PASSWORD (the game's admin password)
docker compose up -d --build
```

The game stack must already be running — this reads from it and from its
container log.

## Data sources

| Source | Used for |
|---|---|
| `http://palworld-server:8212` (REST API) | live roster, level, IP, platform, fps, world day, base camps |
| the game container's log, via `docker.sock` (read-only) | exact join/leave timestamps |

Both are reconciled every `POLL_SECONDS` into SQLite at `./data/paladmin.db`.

## Repository layout

```
app.py                 Flask app: collector thread + JSON endpoints
templates/index.html   the whole UI (no build step, no external deps)
compose.yaml           Dockge stack: service, Traefik labels, basic auth
Dockerfile             python:3.12-slim + gunicorn, single worker
requirements.txt       flask, gunicorn, requests, docker
.env.example           every setting, placeholder values only
scripts/deploy.sh      publish this checkout to the server
docs/                  see below
data/                  SQLite runtime state (gitignored)
```

## Documentation

| Doc | Contents |
|---|---|
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | **The process**: branch → change → verify → commit → deploy → confirm → merge |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Where it runs, host paths, DNS, TLS, deploy/rollback, backups |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data sources, ingestion rules, DB schema, timing |
| [docs/API.md](docs/API.md) | Endpoints and every field they return |
| [docs/PALWORLD-SERVER.md](docs/PALWORLD-SERVER.md) | The game stack this reads from: settings, discovery, NAT |
| [docs/ENHANCEMENTS.md](docs/ENHANCEMENTS.md) | Gotchas and known gaps to read before changing things |

Agents working here should start with [AGENTS.md](AGENTS.md) (and
[.github/copilot-instructions.md](.github/copilot-instructions.md), which points
at it).

## The loop

```bash
scripts/install-hooks.sh     # once per clone: verify on every commit
git switch -c feat/whatever  # 1. branch
scripts/verify.sh            # 2. verify
git commit -am "feat: …"     # 3. commit (conventional, one idea)
scripts/deploy.sh            # 4. deploy: verifies, refuses a dirty tree, rebuilds, checks health
```

Deploys record `commit`, `subject`, `deployed`, `deployed_by` in
`/opt/stacks/paladmin/DEPLOYED`, so what is live is always identifiable.

## Ground rules

- **Never commit `.env`.** `.gitignore` covers it; `.env.example` is the
  documented template, and `verify.sh` fails if `.env` ever becomes tracked.
- **One gunicorn worker.** The collector is a thread started at import; extra
  workers would poll and write concurrently.
- **Deploying this does not touch the game.** They are separate stacks; this one
  can be rebuilt freely at any time.

## Behaviour notes

The log is the only source of history, and Docker only keeps the log of the
*current* container — so history starts when this stack is first started, and
new events are missed while the game container is down. Events already ingested
are kept in `data/paladmin.db`, so recorded history survives restarts of either
container.

Player **levels** are only obtainable while a player is online, so the level
column reads `—` until that player next logs in.

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

Updates only rewrite the panes and cells whose data actually changed, so
selection, scroll position and hover survive a refresh. The page is not polled:
`GET /api/events` pushes a fresh summary whenever state changes (join, leave,
chat, or the 30 s metric heartbeat), with a 30 s fetch as the fallback if the
stream is unavailable.

## Live updates and chat

The game offers no push API, so the freshness comes from two places:

- the game container's log, streamed over the Docker socket — joins, leaves and
  chat lines arrive within a few seconds (the game buffers stdout);
- the REST API, polled on a 30 s heartbeat and immediately after a join/leave.

Chat is stored in its own table and shown in the dashboard's chat panel. It
comes from `[CHAT] <player> message` log lines, so it only exists from the point
this stack has been running.

## Auth

HTTP basic auth at Traefik, user `admin`, using the same password as the game's
`ADMIN_PASSWORD`. The middleware hash lives in `compose.yaml`; the plaintext in
`.env` is used by the app to call the game REST API.

To change the password: update `ADMIN_PASSWORD` in `.env`, regenerate the
bcrypt hash, and replace the value in the `paladmin-auth` label.

## Operations

From a checkout, publish and rebuild in one step — it verifies, refuses a dirty
tree, rebuilds, waits for health, and records the commit:

```bash
scripts/deploy.sh            # --dry-run to preview, --rollback to redeploy the recorded commit
ssh paladmin-host 'cat /opt/stacks/paladmin/DEPLOYED'
```

On the server:

```bash
cd /opt/stacks/paladmin
docker compose up -d --build     # apply code/UI changes
docker compose logs -f           # collector warnings show here
```

Restarting this stack has no effect on the game server. Full runbook, including
rollback: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
