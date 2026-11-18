# Deployment

Everything needed to run, update and recover this stack without reference to
prior conversation.

## Where it runs

Fill these in for your own host; the values below are examples.

| | |
|---|---|
| Host | any Linux box with Docker Engine + Compose v2 (`paladmin-host` in the commands here) |
| SSH | an alias in `~/.ssh/config`, so `scripts/deploy.sh` can reach it |
| Stack path | `/opt/stacks/paladmin` (override with `PALADMIN_REMOTE_DIR`) |
| Compose project | `paladmin`, working dir `/opt/stacks/paladmin`, file `compose.yaml` |
| Orchestrator | [Dockge](https://github.com/louislam/dockge) manages `/opt/stacks/*` — optional, but the paths assume its layout |
| Container | `paladmin`, image `paladmin:latest` (built on the host, not pushed anywhere) |
| Network | external `media_default`, shared with Traefik and the game (`TRAEFIK_NETWORK`) |
| URL | whatever `PALADMIN_HOSTNAME` names |
| Runtime data | `/opt/stacks/paladmin/data/paladmin.db` (+ `-wal`, `-shm`) |
| Game settings | the game stack's `Pal/Saved/Config/LinuxServer`, mounted as `/palconfig` (read-write) |

The container listens on **8000** and is never published to the host — Traefik
reaches it over the shared network.

Everything here runs through the `docker` group rather than `sudo`. The stack
directory is owned by the account that deploys it and can be edited directly;
the container runs as root and writes `data/` and the game's settings directory.

The one game path in the mount list is deliberate: the config panel needs the
settings directory, and nothing else from the game stack is visible to this
container. `PAL_SETTINGS_DIR` in `.env` overrides it if the game stack ever
moves.

## DNS, TLS and the proxy

- Point a hostname (`PALADMIN_HOSTNAME`) at the box — a LAN record and, if you
  want it reachable from outside, a public one. Nothing here depends on which
  DNS you use.
- Traefik is a separate stack (the labels assume its static config lives under
  `/opt/traefik`, with ACME state in `/opt/traefik/letsencrypt/acme.json`) with
  entrypoints `web` (80) and `websecure` (443) and a `letsencrypt` HTTP-01
  certresolver.
- Certificates are issued per hostname on first request and renewed by Traefik.
- Routers, all declared as labels in `compose.yaml`:
  - `paladmin-secure` → `websecure`, TLS via `letsencrypt`, middleware `paladmin-auth`
  - `paladmin` → `web`, redirects to HTTPS (`paladmin-redirect`)
  - service port `8000`

If Traefik serves a stale config and ignores a new container, restart it
(`docker restart traefik`, or from its own stack directory) and it rebuilds from
the Docker provider — briefly disruptive to every proxied host.

## Authentication

HTTP basic auth at the proxy, with a bcrypt hash supplied through `.env`:

- `PALADMIN_BASICAUTH_USERS` holds an htpasswd `user:bcrypt` pair. Single-quote
  it in `.env` so the `$` signs survive; `compose.yaml` only references the
  variable, so no credential is committed.
- `ADMIN_PASSWORD` holds the plaintext, which the app uses to call the game REST
  API.

Rotating it:

```bash
# 1. new bcrypt hash (htpasswd ships with apache2-utils)
htpasswd -nbB admin 'NEWPASSWORD'
#    no htpasswd? python3 -c "import bcrypt;print('admin:' + bcrypt.hashpw(b'NEWPASSWORD', bcrypt.gensalt()).decode())"

# 2. put it in .env, single-quoted: PALADMIN_BASICAUTH_USERS='admin:$2y$05$…'
# 3. update ADMIN_PASSWORD too if the game's password changed
# 4. apply (force-recreate, not restart)
docker compose up -d --force-recreate
```

Keep both in sync: the proxy gate accepts the hash, the app authenticates to the
game with the plaintext.

## Deploying a change

From a checkout on any machine that can `ssh` the host:

```bash
export PALADMIN_REMOTE=paladmin-host   # ssh alias of the box that runs the stack
scripts/deploy.sh --dry-run            # preview what would be copied
scripts/deploy.sh                      # copy, rebuild, recreate, wait for health
```

The script copies `app.py`, `palconfig.py`, `frontend/`, `docs/`, `compose.yaml`,
`Dockerfile`, `requirements.txt` and `README.md` to `$PALADMIN_REMOTE_DIR`
(default `/opt/stacks/paladmin`) — **never `.env`** — then runs
`docker compose up -d --build` there.

Manual equivalent:

```bash
scp app.py palconfig.py paladmin-host:/opt/stacks/paladmin/
scp -r frontend docs paladmin-host:/opt/stacks/paladmin/
ssh paladmin-host 'cd /opt/stacks/paladmin && docker compose up -d --build'
```

Notes:

- Use `--force-recreate` (not `restart`) to apply `.env` changes; Compose does
  not always diff env-file *values* on a plain `up -d`.
- Builds happen on the host; there is no registry. The first build pulls
  `python:3.12-slim`.
- A rebuild takes ~10–20 s including the healthcheck start period.

## Health and logs

```bash
ssh paladmin-host 'docker ps --filter name=paladmin'
ssh paladmin-host 'docker exec paladmin python -c "import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8000/healthz\").read())"'
ssh paladmin-host 'docker compose -f /opt/stacks/paladmin/compose.yaml logs -f --tail 100'
```

The container healthcheck calls `/healthz` every 60 s. Collector problems
(unreachable docker socket, failed REST auth) log a warning and the loop
continues — the UI keeps serving last-known data.

## Rollback

One container, no schema migrations:

```bash
git revert <commit>            # or: git checkout <good-commit> -- .
scripts/deploy.sh

# only if a change damaged the database:
ssh paladmin-host 'cd /opt/stacks/paladmin && docker compose down'
# restore data/paladmin.db from a copy, then:
ssh paladmin-host 'cd /opt/stacks/paladmin && docker compose up -d'
```

### Back up or copy the database

The DB holds all history — take a copy before structural changes:

```bash
# consistent copy (checkpoints the WAL)
ssh paladmin-host 'docker exec paladmin python -c "
import sqlite3
src = sqlite3.connect(\"/data/paladmin.db\")
dst = sqlite3.connect(\"/data/paladmin.db.bak\")
src.backup(dst); dst.close()"'

scp paladmin-host:/opt/stacks/paladmin/data/paladmin.db.bak .
```

The container runs as root, so files in `data/` are root-owned. If local
ownership needs fixing: `docker run --rm -v /opt/stacks/paladmin/data:/d alpine chown -R 1000:1000 /d`.

## Inspecting state

```bash
# current metrics
ssh paladmin-host 'docker exec paladmin python -c "
import urllib.request, json
print(json.dumps(json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/api/summary\"))[\"metrics\"], indent=1))"'

# table counts
ssh paladmin-host 'docker exec paladmin python -c "
import sqlite3; c = sqlite3.connect(\"/data/paladmin.db\")
for t in (\"sessions\", \"events\", \"players\", \"metrics\"):
    print(t, c.execute(\"select count(*) from \" + t).fetchone()[0])"'
```

## If the dashboard is empty or stale

| Symptom | Check |
|---|---|
| No players at all | Game REST API reachable and credentials valid: `docker exec paladmin python -c "import requests;print(requests.get('http://palworld-server:8212/v1/api/players',auth=('admin','PASS'),timeout=5).status_code)"` |
| History missing | `docker logs palworld-server` only holds the current container's log; history before the last game-container recreate is gone unless already ingested |
| Every level shows `—` | Levels exist only while a player is online; they fill in on next login |
| 502 through the proxy | Container unhealthy, or `traefik.docker.network` not matching the network the container joined |
| 401 with the right password | Label hash and `.env` plaintext out of sync |

## If the whole host stops answering

When the box itself freezes, work from the outside first: ping and ARP tell you
whether it is the network, and a TCP connect that resets before the sshd banner
means sshd cannot start a session at all. A journal that ends mid-stream with no
shutdown sequence points at the write path, and `df -h` plus `dmesg` rule out a
full disk before you start replacing hardware. Recovery from a hard freeze is a
power cycle: ext4 replays its journal, and the game autosaves every 30 s, so at
most seconds of world state are lost.

**Backups.** The Palworld backups and `data/paladmin.db` normally live on the
host's own disk, so a drive failure takes the backups with it. Keep a copy
elsewhere.
