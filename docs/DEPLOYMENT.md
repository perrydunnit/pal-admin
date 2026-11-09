# Deployment

Everything needed to run, update and recover this stack without reference to
prior conversation.

## Where it runs

| | |
|---|---|
| Host | `paladmin-host` — Ubuntu 24.04, kernel 7.x, Docker 29.0.4, Compose v2.40.3 |
| SSH | `ssh paladmin-host` (user `admin`) — configured in `~/.ssh/config` |
| Stack path | `/opt/stacks/paladmin` |
| Compose project | `paladmin`, working dir `/opt/stacks/paladmin`, file `compose.yaml` |
| Orchestrator | [Dockge](https://github.com/louislam/dockge) manages `/opt/stacks/*` — this stack appears in its UI |
| Container | `paladmin`, image `paladmin:latest` (built on the host, not pushed anywhere) |
| Network | external `media_default` (shared with Traefik and the game) |
| URL | `https://pal-admin.example.com` |
| Runtime data | `/opt/stacks/paladmin/data/paladmin.db` (+ `-wal`, `-shm`) |

The container listens on **8000** and is never published to the host — Traefik
reaches it over `media_default`.

`sudo` on the host requires a password, so everything here runs through the
`docker` group instead. The stack directory is owned by `admin` and can be
edited directly; the container runs as root and writes `data/`.

## DNS, TLS and the proxy

- `*.example.com` resolves to this box: `192.168.1.10` on the LAN (Pi-hole),
  `203.0.113.10` from the internet. `pal-admin.example.com` needs no extra record —
  the same is true of `pal.example.com`.
- Traefik is a separate stack at `/opt/stacks/infra` (static config under
  `/opt/traefik`, ACME state in `/opt/traefik/letsencrypt/acme.json`) with
  entrypoints `web` (80) and `websecure` (443) and a `letsencrypt` HTTP-01
  certresolver.
- Certificates are issued per hostname on first request and renewed by Traefik;
  this host's cert is `CN=pal-admin.example.com`.
- Routers, all declared as labels in `compose.yaml`:
  - `paladmin-secure` → `websecure`, TLS via `letsencrypt`, middleware `paladmin-auth`
  - `paladmin` → `web`, redirects to HTTPS (`paladmin-redirect`)
  - service port `8000`

If Traefik serves a stale config and ignores a new container, restart it
(`docker restart traefik`, or from `/opt/stacks/infra`) and it rebuilds from the
Docker provider — briefly disruptive to every proxied host.

## Authentication

HTTP basic auth at the proxy: user `admin`, password = the game's
`ADMIN_PASSWORD`.

- `compose.yaml` holds the bcrypt hash in the `paladmin-auth.basicauth.users`
  label. `$` is written as `$$` so Compose leaves the literal `$` intact.
- `.env` holds the plaintext, which the app uses to call the game REST API.

Rotating it:

```bash
# 1. new bcrypt hash
htpasswd -nbB admin 'NEWPASSWORD'
#    no htpasswd? python3 -c "import crypt;print(crypt.crypt('NEWPASSWORD', crypt.mksalt(crypt.METHOD_BLOWFISH)))"

# 2. escape $ as $$ and paste into the paladmin-auth label in compose.yaml
# 3. update ADMIN_PASSWORD in .env
# 4. apply (force-recreate, not restart)
docker compose up -d --force-recreate
```

Keep both in sync: the proxy gate accepts the hash, the app authenticates to the
game with the plaintext.

## Deploying a change

From a checkout on any machine that can `ssh paladmin-host`:

```bash
scripts/deploy.sh --dry-run    # preview what would be copied
scripts/deploy.sh              # copy, rebuild, recreate, wait for health
```

The script copies `app.py`, `templates/`, `docs/`, `compose.yaml`, `Dockerfile`,
`requirements.txt` and `README.md` to `/opt/stacks/paladmin` — **never `.env`** —
then runs `docker compose up -d --build` there.

Manual equivalent:

```bash
scp app.py paladmin-host:/opt/stacks/paladmin/
scp -r templates docs paladmin-host:/opt/stacks/paladmin/
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
